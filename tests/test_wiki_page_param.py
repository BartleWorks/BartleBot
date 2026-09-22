"""The 'wiki_page' param: the node-level record of where a bot's drops came from.

A dungeon with two bosses has two combat nodes, each fetched from its own wiki
page, so provenance belongs on the node rather than on the bot. The import button
already lives on the combat node's panel — this is the association that flow
implied and used to discard.
"""

from __future__ import annotations

import pytest

from bartlebot.gui.canvas.param_panel import _DynamicParamForm, _WikiPageLineEdit
from bartlebot.nodes.combat.handle_combat import PARAM_SCHEMA
from bartlebot.wiki_pages import WIKI_HOST, WIKI_PAGE_PARAM, resolve_wiki_page


def _form(params=None):
    form = _DynamicParamForm()
    form.load_schema(PARAM_SCHEMA, params or {})
    return form


def _widget(form) -> _WikiPageLineEdit:
    return form._widgets[WIKI_PAGE_PARAM]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_handle_combat_has_a_wiki_page_param():
    field = next(f for f in PARAM_SCHEMA if f.key == WIKI_PAGE_PARAM)
    assert field.type == "wiki_page"
    assert field.default == ""
    assert not field.required


def test_the_node_does_not_read_it_at_run_time():
    # Editor-only. If this ever changes, the param stops being safe to leave blank.
    import inspect

    from bartlebot.nodes.combat import handle_combat

    assert WIKI_PAGE_PARAM not in inspect.getsource(handle_combat.run)


# ---------------------------------------------------------------------------
# The widget
# ---------------------------------------------------------------------------


def test_an_absent_param_reads_as_empty(qapp):
    assert _form().get_params()[WIKI_PAGE_PARAM] == ""


def test_an_existing_title_loads(qapp):
    form = _form({WIKI_PAGE_PARAM: "Creature:Lost Soul"})
    assert form.get_params()[WIKI_PAGE_PARAM] == "Creature:Lost Soul"


def test_a_pasted_url_is_stored_as_a_title(qapp):
    # Whichever form the user has to hand, the bot file gets the same thing — and
    # never a host of its own.
    form = _form()
    w = _widget(form)
    w.setText(f"https://{WIKI_HOST}/wiki/Creature:Lost_Soul")
    w.normalise()
    assert form.get_params()[WIKI_PAGE_PARAM] == "Creature:Lost Soul"


def test_an_offsite_url_is_left_for_the_user_to_see_and_fix(qapp):
    # Not silently blanked: the user pasted something, and it should be visible
    # that it was not accepted rather than vanishing.
    form = _form()
    w = _widget(form)
    w.setText("https://evil.co/wiki/Creature:X")
    w.normalise()
    assert not w.is_usable()
    assert resolve_wiki_page(form.get_params()) is None


def test_a_blank_value_is_usable(qapp):
    # Mob fights have no single page, so empty must not read as an error.
    form = _form()
    assert _widget(form).is_usable()


def test_a_title_is_usable(qapp):
    form = _form({WIKI_PAGE_PARAM: "Creature:Lord Nightshade"})
    assert _widget(form).is_usable()


# ---------------------------------------------------------------------------
# Provenance recorded from the import flow
# ---------------------------------------------------------------------------


def test_set_param_value_fills_the_field(qapp):
    form = _form()
    form.set_param_value(WIKI_PAGE_PARAM, "Creature:Lost Soul")
    assert form.get_params()[WIKI_PAGE_PARAM] == "Creature:Lost Soul"


def test_setting_the_same_page_again_changes_nothing(qapp):
    # Re-importing the same page should not dirty the bot.
    form = _form({WIKI_PAGE_PARAM: "Creature:Lost Soul"})
    changes = []
    form.changed.connect(lambda: changes.append(1))
    form.set_param_value(WIKI_PAGE_PARAM, "Creature:Lost Soul")
    assert changes == []


def test_setting_a_different_page_emits_changed(qapp):
    form = _form({WIKI_PAGE_PARAM: "Creature:Lost Soul"})
    changes = []
    form.changed.connect(lambda: changes.append(1))
    form.set_param_value(WIKI_PAGE_PARAM, "Creature:Lord Nightshade")
    assert changes
    assert form.get_params()[WIKI_PAGE_PARAM] == "Creature:Lord Nightshade"


def test_the_recorded_page_resolves_back_to_a_url(qapp):
    form = _form()
    form.set_param_value(WIKI_PAGE_PARAM, "Creature:Lost Soul")
    url = resolve_wiki_page(form.get_params())
    assert url == f"https://{WIKI_HOST}/wiki/Creature:Lost%20Soul"


# ---------------------------------------------------------------------------
# The dialog reports which page it fetched
# ---------------------------------------------------------------------------


class _StubWorker:
    """Stands in for _FetchWorker so no network call happens."""

    def __init__(self, url, parent=None):
        self.url = url

    def start(self):
        pass

    class _Sig:
        def connect(self, _fn):
            pass

    finished = _Sig()
    error = _Sig()


def _dialog(qapp, monkeypatch, url):
    from bartlebot.gui import wiki_import_dialog

    monkeypatch.setattr(wiki_import_dialog, "_FetchWorker", _StubWorker)
    dlg = wiki_import_dialog.WikiImportDialog()
    dlg._url_edit.setText(url)
    dlg._on_fetch()
    return dlg


def test_no_page_before_a_fetch(qapp):
    from bartlebot.gui.wiki_import_dialog import WikiImportDialog

    assert WikiImportDialog().selected_page() is None


def test_the_fetched_page_is_reported_as_a_title(qapp, monkeypatch):
    dlg = _dialog(qapp, monkeypatch, f"https://{WIKI_HOST}/wiki/Creature:Lost_Soul")
    assert dlg.selected_page() == "Creature:Lost Soul"


def test_a_rejected_url_never_becomes_a_page(qapp, monkeypatch):
    # _on_fetch validates before recording, so a bad URL leaves no provenance.
    dlg = _dialog(qapp, monkeypatch, "https://evil.co/wiki/Creature:X")
    assert dlg.selected_page() is None


# ---------------------------------------------------------------------------
# Import on a locked subgraph member (#68) -- the page must survive a recompose
# ---------------------------------------------------------------------------


@pytest.fixture
def combat_canvas(qapp, tmp_path):
    from bartlebot.bot.schema import (
        BotConfig,
        RegionModel,
        StateModel,
        SubgraphDefinition,
        SubgraphSource,
        TransitionModel,
    )
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415
    from bartlebot.subgraph_store import SubgraphLibrary
    from bartlebot.subgraphs import materialize_region

    region_id = "aabbccdd"
    # Two members, not one: with a single-member region, selecting it also selects
    # "the whole region", which the canvas treats as a region-page press rather
    # than a member press -- an artifact of the fixture, not of the bug under test.
    defn = SubgraphDefinition(
        id="one-fight",
        name="One fight",
        description="A single combat encounter.",
        version=1,
        states=[
            StateModel(id="fight", node_type="handle_combat", position={"x": 0.0, "y": 0.0}, params={}),
            StateModel(id="loot", node_type="handle_potions", position={"x": 440.0, "y": 0.0}, params={}),
        ],
        transitions=[TransitionModel(trigger="next", source="fight", dest="loot")],
    )
    library = SubgraphLibrary(tmp_path / "library")
    library.save(defn)

    source = SubgraphSource(subgraph_id=defn.id, version=defn.version, anchor={"x": 0.0, "y": 0.0})
    mat = materialize_region(region_id, source, defn, library)
    fight_id = next(sid for sid in mat.member_ids if sid.endswith("_fight"))
    bot = BotConfig(
        id="bot-1",
        name="Combat bot",
        initial=fight_id,
        recovery_state=fight_id,
        states=mat.states,
        transitions=mat.transitions,
        regions=[RegionModel(id=region_id, label="Fight", members=mat.member_ids, color=1, source=source)],
    )

    c = BotCanvas()
    c.set_subgraph_library(library)
    c.setup(bot)
    c.enter_edit_mode()
    c.select_state(fight_id)
    yield c, region_id
    c.prepare_for_shutdown()


def test_importing_on_a_locked_member_overrides_the_page(combat_canvas):
    # Reproduces #68: the field starts locked and un-overridden, so setting its
    # text alone (the old behaviour) was discarded on the next recompose.
    canvas, region_id = combat_canvas
    panel = canvas.param_panel
    assert panel.is_locked
    assert not panel.param_form.override_button(WIKI_PAGE_PARAM).isChecked()

    panel.apply_wiki_import([], "Creature:Lost Soul")

    region = next(r for r in canvas.scene.config.regions if r.id == region_id)
    assert region.source.param_overrides == {"fight": {WIKI_PAGE_PARAM: "Creature:Lost Soul"}}
    assert panel.shown_state.params[WIKI_PAGE_PARAM] == "Creature:Lost Soul"
    assert panel.param_form.override_button(WIKI_PAGE_PARAM).isChecked()


def test_importing_with_no_page_does_not_force_an_override(combat_canvas):
    canvas, region_id = combat_canvas
    panel = canvas.param_panel

    panel.apply_wiki_import([], None)

    region = next(r for r in canvas.scene.config.regions if r.id == region_id)
    assert region.source.param_overrides == {}
    assert not panel.param_form.override_button(WIKI_PAGE_PARAM).isChecked()
