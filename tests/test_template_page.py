"""Where setup steps appear (10.4): the template page, a node's rows, the Setup card and the library row.

Real widget tests (QApplication required). The rules behind them -- what is a fill-in,
what is done -- are in ``tests/test_setup_steps.py``; these check that each surface says
what those rules say, and that the canvas picks the right page.
"""

from __future__ import annotations

from datetime import date

import pytest
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QLineEdit, QMessageBox, QPlainTextEdit

from bartlebot import setup_steps
from bartlebot.bot.schema import (
    BotConfig,
    SetupStep,
    SetupTarget,
    StateModel,
    TemplateMeta,
    TemplateRef,
    TransitionModel,
)
from bartlebot.gui.canvas.bot_canvas import BotMode
from bartlebot.gui.canvas.template_page import TemplatePage, progress_text, target_choices
from bartlebot.gui.sidebar.setup_steps_card import SetupStepsCard, steps_left_text
from bartlebot.subgraph_store import SubgraphLibrary
from bartlebot.template_store import release, save_draft
from tests.test_subgraph_integration import ControllerHarness

TEMPLATE_ID = "tpl-mob"
_HUB = "WizardCity/WC_Hub"

_ZONE = SetupStep(text="Choose the zone the mobs spawn in", targets=[SetupTarget(state="t_1", param="zone")])
_MOB = SetupStep(text="Name the mob to fight", targets=[SetupTarget(state="t_2", param="enemy")])
_DROPS = SetupStep(text="Import the mob's drops", targets=[SetupTarget(field="drops")])
_STEPS = [_ZONE, _MOB, _DROPS]


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    """Answer any message box a test did not stub, so a missed prompt fails fast instead of blocking the run."""
    for name in ("question", "warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.StandardButton.No)


def _copies(steps) -> list[SetupStep]:
    """Copies, since a draft's page edits its steps in place."""
    return [step.model_copy(deep=True) for step in steps]


def _draft(steps=None) -> BotConfig:
    return BotConfig(
        id=TEMPLATE_ID,
        name="Mob Farmer",
        initial="t_1",
        recovery_state="t_1",
        tags=["Mobs"],
        states=[
            StateModel(
                id="t_1",
                node_type="sync_in_zone",
                label="Go to mob spawn zone",
                position={"x": 0.0, "y": 0.0},
                params={"zone": ""},
            ),
            StateModel(
                id="t_2",
                node_type="handle_combat",
                label="Defeat mobs",
                position={"x": 440.0, "y": 0.0},
                params={"enemy": ""},
            ),
        ],
        transitions=[
            TransitionModel(trigger="advance", source="t_1", dest="t_2", when="SUCCESS"),
            TransitionModel(trigger="advance", source="t_2", dest="t_1", when="SUCCESS"),
        ],
        template_meta=TemplateMeta(blurb="Farms one kind of mob.", setup=_copies(_STEPS if steps is None else steps)),
    )


def _bot() -> BotConfig:
    config = _draft()
    config.id = "bot-mob"
    config.template_meta = None
    config.template = TemplateRef(id=TEMPLATE_ID, version=1)
    return config


def _state(config: BotConfig, state_id: str) -> StateModel:
    return next(s for s in config.states if s.id == state_id)


def _click(widget) -> None:
    centre = QPointF(widget.rect().center())
    widget.mouseReleaseEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            centre,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


# ---------------------------------------------------------------------------
# The page, for a bot
# ---------------------------------------------------------------------------


@pytest.fixture
def page(qapp):
    return TemplatePage()


def _show_bot(page: TemplatePage, bot: BotConfig) -> None:
    page.show_template(bot, setup_steps.progress(bot, _STEPS))


def test_a_bot_sees_its_steps_with_a_tick_on_each_one_done(page):
    bot = _bot()
    _state(bot, "t_2").params["enemy"] = "Troll"

    _show_bot(page, bot)

    assert [row.text for row in page.step_rows] == [s.text for s in _STEPS]
    assert [row.done for row in page.step_rows] == [False, True, False]
    assert page.summary == "1 of 3 done"


def test_the_page_says_when_every_step_is_done(page):
    bot = BotConfig.model_validate({**_bot().model_dump(), "drops": ["Amber"]})
    _state(bot, "t_1").params["zone"] = _HUB
    _state(bot, "t_2").params["enemy"] = "Troll"

    _show_bot(page, bot)

    assert all(row.done for row in page.step_rows)
    assert page.summary == "All 3 done. This bot is set up."


def test_progress_text_counts_only_what_is_listed():
    assert progress_text([]) == "Nothing left to set up."


def test_clicking_a_step_goes_to_its_node_and_a_drops_step_to_the_drops_tab(page):
    _show_bot(page, _bot())
    went: list[str] = []
    drops: list[bool] = []
    page.go_to_requested.connect(went.append)
    page.drops_requested.connect(lambda: drops.append(True))

    zone, mob, drop_list = page.step_rows
    _click(mob)
    _click(zone)
    _click(drop_list)

    assert went == ["t_2", "t_1"]
    assert drops == [True]


def test_a_step_goes_to_the_first_target_still_to_do(page):
    both = SetupStep(
        text="Set both", targets=[SetupTarget(state="t_1", param="zone"), SetupTarget(state="t_2", param="enemy")]
    )
    bot = _bot()
    _state(bot, "t_1").params["zone"] = _HUB
    page.show_template(bot, setup_steps.progress(bot, [both]))
    went: list[str] = []
    page.go_to_requested.connect(went.append)

    _click(page.step_rows[0])

    assert went == ["t_2"]


def test_building_the_page_never_shows_a_parentless_widget(qapp, parentless_shows):
    page = TemplatePage()
    _show_bot(page, _bot())
    page.show_template(_draft())
    _show_bot(page, _bot())

    assert parentless_shows == []


# ---------------------------------------------------------------------------
# The page, for a draft
# ---------------------------------------------------------------------------


def test_a_draft_edits_its_blurb_and_step_text_in_place(page):
    draft = _draft()
    page.show_template(draft)
    typed: list[str] = []
    page.edit_starting.connect(typed.append)

    page.findChild(QPlainTextEdit).setPlainText("Farms trolls.")
    edits = [w for w in page.findChildren(QLineEdit) if w.text() == _MOB.text]
    edits[0].setText("Name the troll")

    assert draft.template_meta.blurb == "Farms trolls."
    assert draft.template_meta.setup[1].text == "Name the troll"
    assert typed == ["Edit blurb", "Edit setup step"]
    assert page.step_rows == [], "a draft lists editors, not ticks"


def test_a_draft_adds_reorders_and_deletes_steps_each_as_one_undo_step(page):
    draft = _draft()
    page.show_template(draft)
    steps: list[str] = []
    page.structural_change.connect(steps.append)

    page._add_step()
    page._move_step(0, 1)
    page._delete_step(2)

    setup = draft.template_meta.setup
    assert [s.text for s in setup] == [_MOB.text, _ZONE.text, ""]
    assert steps == ["Add setup step", "Reorder setup steps", "Delete setup step"]


def test_adding_a_target_makes_a_param_a_fill_in_and_removing_it_stops(page):
    draft = _draft(steps=[_ZONE])
    page.show_template(draft)

    page.add_target(0, SetupTarget(state="t_2", param="enemy"))
    assert ("t_2", "enemy") in setup_steps.fill_ins(draft, draft.template_meta.setup)

    page._remove_target(0, 1)
    assert ("t_2", "enemy") not in setup_steps.fill_ins(draft, draft.template_meta.setup)


def test_add_target_offers_node_params_and_the_drop_list():
    choices = dict(target_choices(_draft()))

    assert [t.param for _label, t in choices["Go to mob spawn zone  (t_1)"]][0] == "zone"
    assert any(t.param == "enemy" for _label, t in choices["Defeat mobs  (t_2)"])
    assert [t.field for _label, t in choices["This bot"]] == ["drops"]


# ---------------------------------------------------------------------------
# The canvas: which page, the errors, and the way in from Setup
# ---------------------------------------------------------------------------


@pytest.fixture
def canvas(qapp, tmp_path):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    canvas = BotCanvas()
    canvas.set_subgraph_library(SubgraphLibrary(tmp_path / "library"))
    yield canvas
    canvas.prepare_for_shutdown()


def _open_bot(canvas, bot: BotConfig, mode: BotMode = BotMode.EDIT) -> None:
    canvas.setup(bot, mode=mode)
    canvas.set_base_setup_steps(_STEPS)


def test_properties_shows_a_bots_steps_in_edit_mode_with_nothing_selected(canvas):
    _open_bot(canvas, _bot())

    panel = canvas.param_panel
    assert panel.showing_template
    assert [row.text for row in panel.template_page.step_rows] == [s.text for s in _STEPS]


def test_properties_shows_nothing_in_run_mode_or_for_a_bot_with_no_steps(canvas):
    _open_bot(canvas, _bot(), mode=BotMode.RUN)
    assert not canvas.param_panel.showing_template

    plain = _bot()
    plain.template = None
    canvas.setup(plain, mode=BotMode.EDIT)
    assert not canvas.param_panel.showing_template


def test_a_draft_shows_the_step_editor(canvas):
    canvas.setup(_draft(), mode=BotMode.EDIT)

    assert canvas.param_panel.showing_template
    assert canvas.param_panel.template_page.step_rows == []
    assert canvas.setup_steps_left == 0, "a draft's blanks are not steps left"


def test_a_blank_fill_in_blocks_run_until_it_is_filled(canvas):
    bot = _bot()
    _open_bot(canvas, bot)

    errors = canvas.validation_errors()
    assert f"Go to mob spawn zone: Setup: {_ZONE.text}" in errors
    assert f"Defeat mobs: Setup: {_MOB.text}" in errors

    _state(bot, "t_1").params["zone"] = _HUB
    _state(bot, "t_2").params["enemy"] = "Troll"
    canvas.set_base_setup_steps(_STEPS)

    assert not [e for e in canvas.validation_errors() if "Setup" in e]


def test_the_steps_of_one_bot_do_not_follow_to_the_next(canvas):
    _open_bot(canvas, _bot())
    other = _bot()
    other.id = "another-bot"

    canvas.setup(other, mode=BotMode.EDIT)

    assert not canvas.param_panel.showing_template
    assert not [e for e in canvas.validation_errors() if "Setup" in e]


def test_clicking_a_step_selects_its_node(canvas):
    _open_bot(canvas, _bot())

    _click(canvas.param_panel.template_page.step_rows[1])

    assert canvas.param_panel.shown_state is not None
    assert canvas.param_panel.shown_state.id == "t_2"


def test_a_drops_step_asks_for_the_drops_tab(canvas):
    _open_bot(canvas, _bot())
    tabs: list[str] = []
    canvas.tab_switch.connect(tabs.append)

    _click(canvas.param_panel.template_page.step_rows[2])

    assert tabs == ["drops"]


def test_the_canvas_announces_how_many_steps_are_left(canvas):
    counts: list[int] = []
    canvas.setup_left_changed.connect(counts.append)
    bot = _bot()

    _open_bot(canvas, bot)
    _state(bot, "t_2").params["enemy"] = "Troll"
    canvas.refresh_setup_page()

    assert counts == [3, 2]
    assert [row.done for row in canvas.param_panel.template_page.step_rows] == [False, True, False]


def test_closing_the_bot_leaves_no_steps_behind(canvas):
    counts: list[int] = []
    canvas.setup_left_changed.connect(counts.append)
    _open_bot(canvas, _bot())

    canvas.close_bot()

    assert counts == [3, 0], "or the card heading Setup would outlive the bot it was about"


def test_set_up_goes_to_edit_mode_with_the_steps_showing(canvas):
    _open_bot(canvas, _bot(), mode=BotMode.RUN)
    tabs: list[str] = []
    canvas.tab_switch.connect(tabs.append)

    canvas.begin_setup()

    assert canvas.mode == BotMode.EDIT
    assert canvas.param_panel.showing_template
    assert tabs == ["properties"]


def test_a_fill_in_row_is_marked_required_with_its_step_in_a_bot(canvas):
    _open_bot(canvas, _bot())

    canvas.select_state("t_2")

    form = canvas.param_panel.param_form
    assert "enemy" in [f.key for f in form.required_fields()], "blank blocks Run, so the row says so"
    assert form.widget("enemy").placeholderText() == f"Fill in: {_MOB.text}"
    assert form.fill_hint_text("wiki_page") is None, "not a fill-in"

    canvas.select_state("t_1")
    assert form.fill_hint_text("zone") == f"Fill in: {_ZONE.text}", "a picker has no placeholder, so a line says it"


def test_a_fill_in_row_wears_its_step_number_in_a_draft(canvas):
    canvas.setup(_draft(), mode=BotMode.EDIT)

    canvas.select_state("t_2")

    form = canvas.param_panel.param_form
    assert form.step_chip_text("enemy") == "Setup step 2"
    assert "enemy" not in [f.key for f in form.required_fields()]


# ---------------------------------------------------------------------------
# The controller hands a bot its base version's steps
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    save_draft(_draft(), tmp_path)
    release(TEMPLATE_ID, "First release", root=tmp_path, today=date(2026, 10, 1))
    return tmp_path


@pytest.fixture
def harness(qapp, store, tmp_path):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    library = SubgraphLibrary(tmp_path / "library")
    canvas = BotCanvas()
    canvas.set_subgraph_library(library)
    h = ControllerHarness(canvas, library)
    h._templates_root = store
    yield h
    canvas.prepare_for_shutdown()


def test_opening_a_bot_made_from_a_template_brings_its_steps(harness, tmp_path):
    path = tmp_path / "bots" / "trolls.json"
    _bot().save(path)

    harness._open_bot(str(path), mode=BotMode.EDIT)

    canvas = harness._window.bot_canvas
    assert canvas.setup_steps_left == 3
    assert canvas.param_panel.showing_template
    assert any("Setup" in e for e in canvas.validation_errors())


def test_opening_and_saving_a_bot_with_steps_left_changes_nothing_in_its_file(harness, tmp_path):
    path = tmp_path / "bots" / "trolls.json"
    _bot().save(path)
    before = path.read_bytes()

    harness._open_bot(str(path), mode=BotMode.EDIT)
    assert harness._save_bot()

    assert path.read_bytes() == before, "the steps are read from the template, never written into the bot"


# ---------------------------------------------------------------------------
# The Setup card and the library row
# ---------------------------------------------------------------------------


def test_the_setup_card_is_hidden_until_steps_are_left(qapp):
    card = SetupStepsCard()
    assert card.isHidden()

    card.set_steps_left(2)
    assert not card.isHidden()
    assert card.text == "Not set up yet: 2 steps left"

    card.set_steps_left(1)
    assert card.text == steps_left_text(1) == "Not set up yet: 1 step left"

    card.set_steps_left(0)
    assert card.isHidden()


def test_the_setup_cards_button_asks_for_setup(qapp):
    card = SetupStepsCard()
    asked: list[bool] = []
    card.setup_requested.connect(lambda: asked.append(True))

    card.button.click()

    assert asked == [True]


def test_building_the_setup_card_never_shows_a_parentless_widget(qapp, parentless_shows):
    from PyQt6.QtWidgets import QWidget  # noqa: PLC0415

    from bartlebot.gui.sidebar.left_sidebar import LeftSidebar  # noqa: PLC0415

    sidebar = LeftSidebar(QWidget())
    sidebar.set_setup_steps_left(2)
    sidebar.set_setup_steps_left(0)

    assert parentless_shows == []
    assert sidebar.setup_card.isHidden()


def test_a_library_row_says_how_many_setup_steps_are_left(qapp, store, tmp_path):
    from bartlebot.gui.canvas.bot_canvas import _BotCard, _read_bot_meta  # noqa: PLC0415

    half = tmp_path / "trolls.json"
    bot = _bot()
    _state(bot, "t_2").params["enemy"] = "Troll"
    bot.save(half)
    plain = tmp_path / "plain.json"
    made_from_nothing = _bot()
    made_from_nothing.template = None
    made_from_nothing.save(plain)

    meta = _read_bot_meta(half, store)
    assert meta.setup_left == 2
    assert _BotCard(meta).setup_text == "2 setup steps left"
    assert _BotCard(_read_bot_meta(plain, store)).setup_text is None


# ---------------------------------------------------------------------------
# A template file has the same bytes on every platform
# ---------------------------------------------------------------------------


def test_a_draft_is_saved_with_unix_line_endings_and_a_bot_is_left_to_the_platform(tmp_path):
    """A released version is hashed, so ``templates/**`` is byte-exact in git: a draft must not flip its endings."""
    draft_path = save_draft(_draft(), tmp_path)

    assert b"\r\n" not in draft_path.read_bytes()
    assert b"\r\n" not in (draft_path.parent / "template.json").read_bytes()
