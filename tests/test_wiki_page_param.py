"""The 'wiki_page' param: the node-level record of where a bot's drops came from.

A dungeon with two bosses has two combat nodes, each fetched from its own wiki
page, so provenance belongs on the node rather than on the bot. The import button
already lives on the combat node's panel — this is the association that flow
implied and used to discard.
"""

from __future__ import annotations

import sys

import pytest
from PyQt6.QtWidgets import QApplication

from bartlebot.gui.canvas.param_panel import _DynamicParamForm, _WikiPageLineEdit
from bartlebot.nodes.combat.handle_combat import PARAM_SCHEMA
from bartlebot.wiki_pages import WIKI_HOST, WIKI_PAGE_PARAM, resolve_wiki_page


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


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
    form = _form({WIKI_PAGE_PARAM: "Creature:Luska Charmbeak"})
    assert form.get_params()[WIKI_PAGE_PARAM] == "Creature:Luska Charmbeak"


def test_a_pasted_url_is_stored_as_a_title(qapp):
    # Whichever form the user has to hand, the bot file gets the same thing — and
    # never a host of its own.
    form = _form()
    w = _widget(form)
    w.setText(f"https://{WIKI_HOST}/wiki/Creature:Luska_Charmbeak")
    w.normalise()
    assert form.get_params()[WIKI_PAGE_PARAM] == "Creature:Luska Charmbeak"


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
    form = _form({WIKI_PAGE_PARAM: "Creature:Sylster Glowstorm"})
    assert _widget(form).is_usable()


# ---------------------------------------------------------------------------
# Provenance recorded from the import flow
# ---------------------------------------------------------------------------


def test_set_param_value_fills_the_field(qapp):
    form = _form()
    form.set_param_value(WIKI_PAGE_PARAM, "Creature:Luska Charmbeak")
    assert form.get_params()[WIKI_PAGE_PARAM] == "Creature:Luska Charmbeak"


def test_setting_the_same_page_again_changes_nothing(qapp):
    # Re-importing the same page should not dirty the bot.
    form = _form({WIKI_PAGE_PARAM: "Creature:Luska Charmbeak"})
    changes = []
    form.changed.connect(lambda: changes.append(1))
    form.set_param_value(WIKI_PAGE_PARAM, "Creature:Luska Charmbeak")
    assert changes == []


def test_setting_a_different_page_emits_changed(qapp):
    form = _form({WIKI_PAGE_PARAM: "Creature:Luska Charmbeak"})
    changes = []
    form.changed.connect(lambda: changes.append(1))
    form.set_param_value(WIKI_PAGE_PARAM, "Creature:Sylster Glowstorm")
    assert changes
    assert form.get_params()[WIKI_PAGE_PARAM] == "Creature:Sylster Glowstorm"


def test_the_recorded_page_resolves_back_to_a_url(qapp):
    form = _form()
    form.set_param_value(WIKI_PAGE_PARAM, "Creature:Luska Charmbeak")
    url = resolve_wiki_page(form.get_params())
    assert url == f"https://{WIKI_HOST}/wiki/Creature:Luska%20Charmbeak"


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
    dlg = _dialog(qapp, monkeypatch, f"https://{WIKI_HOST}/wiki/Creature:Luska_Charmbeak")
    assert dlg.selected_page() == "Creature:Luska Charmbeak"


def test_a_rejected_url_never_becomes_a_page(qapp, monkeypatch):
    # _on_fetch validates before recording, so a bad URL leaves no provenance.
    dlg = _dialog(qapp, monkeypatch, "https://evil.co/wiki/Creature:X")
    assert dlg.selected_page() is None
