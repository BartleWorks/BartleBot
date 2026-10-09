"""Where setup steps appear (10.4): the Setup tab, a node's rows, Bar B's chip and the library row.

Real widget tests (QApplication required). The rules behind them -- what is a fill-in,
what is done -- are in ``tests/test_setup_steps.py``; these check that each surface says
what those rules say, that the tab comes and goes when it should, and that a draft's
edits go through the canvas's undo history.
"""

from __future__ import annotations

from datetime import date

import pytest
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QMessageBox

from bartlebot import setup_steps, template_diff
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
from bartlebot.gui.sidebar.right_sidebar import RightSidebar
from bartlebot.gui.sidebar.setup_panel import (
    ADD_STEP_TEXT,
    APPLY_TEXT,
    KEEP_MINE_TEXT,
    NOTHING_TO_DO_TEXT,
    REMOVE_STEP_VERB,
    USE_TEMPLATE_TEXT,
    ChangeCard,
    DraftStepCard,
    SetupPanel,
    StepCard,
    change_summary,
    fights_of,
    progress_text,
    release_heading,
    review_title,
    step_summary,
    target_choices,
)
from bartlebot.gui.widgets.card_frame import CardMode
from bartlebot.subgraph_store import SubgraphLibrary
from bartlebot.template_store import release, save_draft
from tests.drag_helpers import menu_move
from tests.test_subgraph_integration import ControllerHarness

TEMPLATE_ID = "tpl-mob"
_HUB = "WizardCity/WC_Hub"
_NOWHERE = "Nowhere/NW_Zone"

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
    """Copies, since a draft's tab edits its steps in place."""
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
    """A press and a release in place, which is what activates a card."""
    centre = QPointF(widget.rect().center())
    for kind, buttons in (
        (QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
        (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton),
    ):
        event = QMouseEvent(kind, centre, Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier)
        (widget.mousePressEvent if kind == QEvent.Type.MouseButtonPress else widget.mouseReleaseEvent)(event)


# ---------------------------------------------------------------------------
# What the cards say
# ---------------------------------------------------------------------------


def test_progress_text_counts_the_steps_listed():
    bot = _bot()
    _state(bot, "t_2").params["enemy"] = "Troll"

    assert progress_text(setup_steps.progress(bot, _STEPS)) == "1 of 3 done"
    assert progress_text([]) == "Nothing left to set up."


def test_a_step_says_how_far_along_it_is():
    both = SetupStep(
        text="Set both", targets=[SetupTarget(state="t_1", param="zone"), SetupTarget(state="t_2", param="enemy")]
    )
    bot = _bot()

    def summary(step: SetupStep) -> str:
        [status] = setup_steps.progress(bot, [step])
        return step_summary(bot, status)

    assert summary(_MOB) == "Not filled in yet"
    assert summary(both) == "0 of 2 filled in"
    assert summary(_DROPS) == "No drops yet"

    _state(bot, "t_2").params["enemy"] = "Troll"
    assert summary(_MOB) == "Done"
    assert summary(both) == "1 of 2 filled in"

    _state(bot, "t_1").params["zone"] = _NOWHERE
    assert summary(_ZONE) == "Go to mob spawn zone · Zone is not a destination on the zone map"


def test_a_target_is_called_by_its_fields_own_label():
    draft = _draft()

    assert setup_steps.describe_target(draft, _MOB.targets[0]) == "Defeat mobs · Enemy ID"
    assert setup_steps.describe_target(draft, _DROPS.targets[0]) == "Possible drops"


def test_add_target_offers_node_params_and_the_drop_list():
    choices = dict(target_choices(_draft()))

    assert [t.param for _label, t in choices["Go to mob spawn zone"]][0] == "zone"
    assert any(t.param == "enemy" for _label, t in choices["Defeat mobs"])
    assert [t.field for _label, t in choices["This bot"]] == ["drops"]


def test_the_drops_step_lists_the_bots_fights():
    bot = _bot()
    _state(bot, "t_2").params["wiki_page"] = "Creature:Troll"

    [fight] = fights_of(bot)

    assert (fight.state_id, fight.label, fight.page) == ("t_2", "Defeat mobs", "Creature:Troll")


# ---------------------------------------------------------------------------
# The tab, for a bot
# ---------------------------------------------------------------------------


@pytest.fixture
def panel(qapp):
    return SetupPanel()


def _show_bot(panel: SetupPanel, bot: BotConfig) -> None:
    panel.show_document(bot, setup_steps.progress(bot, _STEPS))


def test_a_bot_sees_a_card_per_step_with_a_tick_on_each_one_done(panel):
    bot = _bot()
    _state(bot, "t_2").params["enemy"] = "Troll"

    _show_bot(panel, bot)

    assert all(isinstance(card, StepCard) for card in panel.cards)
    assert [card.text for card in panel.cards] == [s.text for s in _STEPS]
    assert [card.done for card in panel.cards] == [False, True, False]
    assert [card.summary for card in panel.cards] == ["Not filled in yet", "Done", "No drops yet"]
    assert panel.summary == "1 of 3 done"
    assert panel.add_card is None and panel.blurb_edit is None


def test_a_bots_card_has_no_grip_and_is_itself_the_verb(panel):
    _show_bot(panel, _bot())

    card = panel.cards[0]
    assert card.mode is CardMode.MANAGE
    assert card.grip is None, "the user does not order the steps"
    assert card.action_buttons == []


def test_clicking_a_card_goes_to_its_node(panel):
    _show_bot(panel, _bot())
    went: list[str] = []
    panel.go_to_requested.connect(went.append)

    zone, mob, _drops = panel.cards
    _click(mob)
    _click(zone)

    assert went == ["t_2", "t_1"]


def test_a_card_goes_to_the_first_target_still_to_do(panel):
    both = SetupStep(
        text="Set both", targets=[SetupTarget(state="t_1", param="zone"), SetupTarget(state="t_2", param="enemy")]
    )
    bot = _bot()
    _state(bot, "t_1").params["zone"] = _HUB
    panel.show_document(bot, setup_steps.progress(bot, [both]))
    went: list[str] = []
    panel.go_to_requested.connect(went.append)

    _click(panel.cards[0])

    assert went == ["t_2"]


def test_the_drops_card_has_an_import_per_fight_and_opens_the_drops_tab(panel):
    _show_bot(panel, _bot())
    imports: list[str] = []
    drops: list[bool] = []
    panel.import_requested.connect(imports.append)
    panel.drops_requested.connect(lambda: drops.append(True))

    card = panel.cards[2]
    assert list(card.import_buttons) == ["t_2"]
    card.import_buttons["t_2"].click()
    _click(card)

    assert imports == ["t_2"]
    assert drops == [True]
    assert panel.cards[0].import_buttons == {}, "only the drops step lists fights"


def test_a_refresh_that_changes_nothing_keeps_the_cards(panel):
    bot = _bot()
    _show_bot(panel, bot)
    before = panel.cards

    _show_bot(panel, bot)
    assert panel.cards == before

    _state(bot, "t_2").params["enemy"] = "Troll"
    _show_bot(panel, bot)
    assert panel.cards != before and panel.cards[1].done


def test_the_tab_is_empty_for_a_document_with_no_steps(panel):
    _show_bot(panel, _bot())

    panel.show_document(None)

    assert panel.cards == [] and panel.summary == ""


def test_building_the_tab_never_shows_a_parentless_widget(qapp, parentless_shows):
    panel = SetupPanel()
    _show_bot(panel, _bot())
    panel.show_document(_draft())
    _show_bot(panel, _bot())

    assert parentless_shows == []


# ---------------------------------------------------------------------------
# The tab, for a draft
# ---------------------------------------------------------------------------


def test_a_draft_gets_a_card_per_step_with_a_grip_a_cross_and_an_add_card(panel):
    panel.show_document(_draft())

    assert all(isinstance(card, DraftStepCard) for card in panel.cards)
    card = panel.cards[1]
    assert card.mode is CardMode.MEMBER, "its body holds a text field, so it is not a button"
    assert card.grip is not None
    assert card.action_button(REMOVE_STEP_VERB) is not None
    assert card.text_edit.text() == _MOB.text
    assert card.target_texts == ["Defeat mobs · Enemy ID"]
    assert panel.add_card is not None and panel.add_card.text == ADD_STEP_TEXT


def test_a_draft_edits_its_blurb_and_step_text_without_rebuilding_the_cards(panel):
    draft = _draft()
    panel.show_document(draft)
    typed: list[str] = []
    panel.edit_starting.connect(typed.append)
    before = panel.cards

    panel.blurb_edit.setPlainText("Farms trolls.")
    panel.cards[1].text_edit.setText("Name the troll")
    panel.show_document(draft)

    assert draft.template_meta.blurb == "Farms trolls."
    assert draft.template_meta.setup[1].text == "Name the troll"
    assert typed == ["Edit blurb", "Edit setup step"]
    assert panel.cards == before, "or the field would be destroyed under the hand typing in it"


def test_the_add_card_and_the_cross_add_and_remove_steps_each_as_one_undo_step(panel):
    draft = _draft()
    panel.show_document(draft)
    steps: list[str] = []
    panel.structural_change.connect(steps.append)

    panel.add_step()
    panel.cards[0].action_button(REMOVE_STEP_VERB).click()

    assert [s.text for s in draft.template_meta.setup] == [_MOB.text, _DROPS.text, ""]
    assert steps == ["Add setup step", "Remove setup step"]
    assert len(panel.cards) == 3


def test_a_drag_reorders_the_steps(panel):
    draft = _draft()
    panel.show_document(draft)
    steps: list[str] = []
    panel.structural_change.connect(steps.append)

    panel.reorder_steps([2, 0, 1])
    panel.reorder_steps([0, 1, 2])

    assert [s.text for s in draft.template_meta.setup] == [_DROPS.text, _ZONE.text, _MOB.text]
    assert steps == ["Reorder setup steps"], "a drop back in place is no edit"
    assert [card.text_edit.text() for card in panel.cards] == [_DROPS.text, _ZONE.text, _MOB.text]


def test_move_down_in_a_steps_menu_reorders_the_steps(panel):
    draft = _draft()
    panel.show_document(draft)
    steps: list[str] = []
    panel.structural_change.connect(steps.append)

    assert menu_move(panel.cards[0], "Move down")

    assert [s.text for s in draft.template_meta.setup] == [_MOB.text, _ZONE.text, _DROPS.text]
    assert steps == ["Reorder setup steps"]


def test_adding_a_target_makes_a_param_a_fill_in_and_removing_it_stops(panel):
    draft = _draft(steps=[_ZONE])
    panel.show_document(draft)

    panel.add_target(0, SetupTarget(state="t_2", param="enemy"))
    assert ("t_2", "enemy") in setup_steps.fill_ins(draft, draft.template_meta.setup)
    assert panel.cards[0].target_texts[1] == "Defeat mobs · Enemy ID"

    panel.remove_target(0, 1)
    assert ("t_2", "enemy") not in setup_steps.fill_ins(draft, draft.template_meta.setup)


def test_a_target_the_draft_no_longer_has_is_marked(panel):
    gone = SetupStep(text="Name it", targets=[SetupTarget(state="t_9", param="enemy")])
    panel.show_document(_draft(steps=[gone]))

    assert panel.cards[0].target_texts == ["t_9 · enemy (not in the draft)"]


# ---------------------------------------------------------------------------
# The canvas: errors, the way to a node, and a draft's undo history
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

    assert not canvas.has_setup_steps
    assert not [e for e in canvas.validation_errors() if "Setup" in e]


def test_properties_shows_nothing_with_nothing_selected(canvas):
    """The steps are in the right sidebar, so Properties has no page of its own for them."""
    _open_bot(canvas, _bot())
    assert canvas.param_panel.shown_state is None

    canvas.setup(_draft(), mode=BotMode.EDIT)
    assert canvas.param_panel.shown_state is None
    assert canvas.setup_steps_left == 0, "a draft's blanks are not steps left"


def test_going_to_a_step_selects_its_node_and_switches_to_edit_mode(canvas):
    _open_bot(canvas, _bot(), mode=BotMode.RUN)
    tabs: list[str] = []
    canvas.tab_switch.connect(tabs.append)

    canvas.show_setup_target("t_2")

    assert canvas.mode == BotMode.EDIT, "a fill-in is a value in the bot file"
    assert canvas.param_panel.shown_state.id == "t_2"
    assert tabs == ["properties"]


def test_the_canvas_says_when_the_steps_may_have_moved(canvas):
    counts: list[int] = []
    moved: list[bool] = []
    canvas.setup_left_changed.connect(counts.append)
    canvas.setup_changed.connect(lambda: moved.append(True))
    bot = _bot()

    _open_bot(canvas, bot)
    _state(bot, "t_2").params["enemy"] = "Troll"
    canvas.refresh_setup()
    canvas.close_bot()

    assert counts == [3, 2, 0], "the last, or the tab would outlive the bot it was about"
    assert len(moved) >= 3


def test_a_fill_in_row_is_marked_required_with_its_step_in_a_bot(canvas):
    _open_bot(canvas, _bot())

    canvas.select_state("t_2")

    form = canvas.param_panel.param_form
    assert "enemy" in [f.key for f in form.required_fields()], "blank blocks Run, so the row says so"
    assert form.widget("enemy").placeholderText() == f"Fill in: {_MOB.text}"
    assert form.fill_hint_text("wiki_page") is None, "not a fill-in"
    assert form.step_button("wiki_page") is None, "a bot's rows offer nothing about steps"

    canvas.select_state("t_1")
    assert form.fill_hint_text("zone") == f"Fill in: {_ZONE.text}", "a picker has no placeholder, so a line says it"


def test_a_drafts_rows_wear_their_step_or_offer_to_join_one(canvas):
    canvas.setup(_draft(), mode=BotMode.EDIT)

    canvas.select_state("t_2")

    form = canvas.param_panel.param_form
    assert form.step_chip_text("enemy") == "Setup step 2"
    assert form.step_button("enemy") is None
    assert form.step_button("wiki_page") is not None
    assert "enemy" not in [f.key for f in form.required_fields()]


def test_a_rows_button_adds_the_param_to_a_step_and_its_chip_takes_it_out(canvas):
    draft = _draft()
    canvas.setup(draft, mode=BotMode.EDIT)
    canvas.select_state("t_2")
    form = canvas.param_panel.param_form

    canvas.add_setup_target("t_2", "wiki_page", 1)

    setup = canvas.config.template_meta.setup
    assert [t.param for t in setup[1].targets] == ["enemy", "wiki_page"]
    assert form.step_chip_text("wiki_page") == "Setup step 2", "the page that is up follows"

    form.step_chip("wiki_page").click()

    assert [t.param for t in canvas.config.template_meta.setup[1].targets] == ["enemy"]
    assert form.step_chip_text("wiki_page") is None


def test_a_param_can_start_a_step_of_its_own(canvas):
    canvas.setup(_draft(), mode=BotMode.EDIT)

    canvas.add_setup_target("t_2", "wiki_page", 3)
    canvas.add_setup_target("t_2", "wiki_page", 0)

    setup = canvas.config.template_meta.setup
    assert len(setup) == 4 and setup[3].text == ""
    assert [t.param for t in setup[3].targets] == ["wiki_page"]
    assert [t.param for t in setup[0].targets] == ["zone"], "one step per fill-in"


def test_a_step_edit_from_the_tab_is_one_undo_step(canvas, panel):
    canvas.setup(_draft(), mode=BotMode.EDIT)
    panel.edit_starting.connect(canvas.begin_setup_edit)
    panel.structural_change.connect(canvas.begin_setup_change)
    panel.changed.connect(canvas.setup_edited)
    panel.show_document(canvas.config)

    panel.remove_step(0)
    assert len(canvas.config.template_meta.setup) == 2

    canvas.undo()
    assert [s.text for s in canvas.config.template_meta.setup] == [s.text for s in _STEPS]


def test_removing_a_step_turns_its_blank_into_a_plain_error(canvas, panel):
    """What the step was hiding: a required blank nothing asks the user to fill."""
    canvas.setup(_draft(), mode=BotMode.EDIT)
    panel.structural_change.connect(canvas.begin_setup_change)
    panel.changed.connect(canvas.setup_edited)
    panel.show_document(canvas.config)
    assert not canvas.validation_errors()

    panel.remove_step(0)

    assert "Go to mob spawn zone: Required param 'zone' is empty" in canvas.validation_errors()


# ---------------------------------------------------------------------------
# The right sidebar's tab, and Bar B's chip
# ---------------------------------------------------------------------------


def _tabs(sidebar: RightSidebar) -> list[str]:
    return [sidebar.tabText(i) for i in range(sidebar.count())]


def test_the_setup_tab_is_added_last_and_taken_away(qapp, parentless_shows):
    sidebar = RightSidebar()
    assert _tabs(sidebar) == ["Players", "Drops", "Log"]

    sidebar.set_setup_tab_shown(True)
    sidebar.switch_to_setup()
    assert _tabs(sidebar) == ["Players", "Drops", "Log", "Setup"]
    assert sidebar.currentWidget() is sidebar.setup_panel

    sidebar.set_setup_tab_shown(False)
    assert _tabs(sidebar) == ["Players", "Drops", "Log"]
    assert sidebar.currentIndex() == 0
    assert sidebar.setup_panel.parent() is not None, "a parentless page is a top-level window"
    assert parentless_shows == []


def test_bar_b_names_the_template_a_bot_was_made_from(canvas):
    _open_bot(canvas, _bot())
    asked: list[bool] = []
    canvas.setup_tab_requested.connect(lambda: asked.append(True))
    chip = canvas.header.template_chip
    assert chip.isHidden()

    canvas.set_template_origin("Mob Farmer", 1)
    assert not chip.isHidden() and chip.text() == "Mob Farmer v1"

    _click(chip)
    assert asked == [True]

    plain = _bot()
    plain.id, plain.template = "plain", None
    canvas.setup(plain, mode=BotMode.EDIT)
    assert chip.isHidden(), "the next bot does not inherit it"


def test_the_chip_does_not_change_the_bars_height(canvas):
    _open_bot(canvas, _bot())
    before = canvas.header.sizeHint().height()

    canvas.set_template_origin("Mob Farmer", 1)

    assert canvas.header.sizeHint().height() == before


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


def test_opening_a_bot_made_from_a_template_brings_its_steps_and_names_it(harness, tmp_path):
    path = tmp_path / "bots" / "trolls.json"
    _bot().save(path)

    harness._open_bot(str(path), mode=BotMode.EDIT)

    canvas = harness._window.bot_canvas
    assert canvas.has_setup_steps and canvas.setup_steps_left == 3
    assert any("Setup" in e for e in canvas.validation_errors())
    assert canvas.header.template_chip.text() == "Mob Farmer v1"


def test_opening_and_saving_a_bot_with_steps_left_changes_nothing_in_its_file(harness, tmp_path):
    path = tmp_path / "bots" / "trolls.json"
    _bot().save(path)
    before = path.read_bytes()

    harness._open_bot(str(path), mode=BotMode.EDIT)
    assert harness._save_bot()

    assert path.read_bytes() == before, "the steps are read from the template, never written into the bot"


# ---------------------------------------------------------------------------
# The library row, and a template file's bytes
# ---------------------------------------------------------------------------


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


def test_a_draft_is_saved_with_unix_line_endings(tmp_path):
    """A released version is hashed, so ``templates/**`` is byte-exact in git: a draft must not flip its endings."""
    draft_path = save_draft(_draft(), tmp_path)

    assert b"\r\n" not in draft_path.read_bytes()
    assert b"\r\n" not in (draft_path.parent / "template.json").read_bytes()


# ---------------------------------------------------------------------------
# When the tab is there: the main window's policy
# ---------------------------------------------------------------------------


@pytest.fixture
def window(qapp):
    from bartlebot.gui.main_window import MainWindow  # noqa: PLC0415

    window = MainWindow()
    yield window
    window.bot_canvas.prepare_for_shutdown()


def _done_bot() -> BotConfig:
    bot = BotConfig.model_validate({**_bot().model_dump(), "drops": ["Amber"]})
    _state(bot, "t_1").params["zone"] = _HUB
    _state(bot, "t_2").params["enemy"] = "Troll"
    return bot


def test_a_bot_with_steps_left_opens_on_the_setup_tab(window):
    sidebar = window.right_sidebar
    assert not sidebar.setup_tab_shown

    _open_bot(window.bot_canvas, _bot(), mode=BotMode.RUN)

    assert sidebar.setup_tab_shown, "in Run mode too: the tab is how the steps are found"
    assert sidebar.currentWidget() is sidebar.setup_panel
    assert [card.text for card in sidebar.setup_panel.cards] == [s.text for s in _STEPS]


def test_clicking_a_card_in_run_mode_goes_to_the_node_and_keeps_the_list(window):
    canvas, sidebar = window.bot_canvas, window.right_sidebar
    _open_bot(canvas, _bot(), mode=BotMode.RUN)

    _click(sidebar.setup_panel.cards[1])

    assert canvas.mode == BotMode.EDIT
    assert canvas.param_panel.shown_state.id == "t_2"
    assert sidebar.currentWidget() is sidebar.setup_panel, "the list stays beside the node's page"


def test_the_tab_stays_while_the_last_step_is_done_and_is_gone_next_time(window):
    canvas, sidebar = window.bot_canvas, window.right_sidebar
    bot = _bot()
    _open_bot(canvas, bot)

    _state(bot, "t_1").params["zone"] = _HUB
    _state(bot, "t_2").params["enemy"] = "Troll"
    bot.drops = _done_bot().drops
    canvas.refresh_setup()

    assert sidebar.setup_tab_shown, "or 'All 3 done' would vanish with the last tick"
    assert sidebar.setup_panel.summary == "All 3 done. This bot is set up."

    done = _done_bot()
    done.id = "bot-done"
    _open_bot(canvas, done)
    assert not sidebar.setup_tab_shown


def test_the_template_chip_brings_the_tab_back_for_a_finished_bot(window):
    canvas, sidebar = window.bot_canvas, window.right_sidebar
    _open_bot(canvas, _done_bot())
    canvas.set_template_origin("Mob Farmer", 1)
    assert not sidebar.setup_tab_shown

    _click(canvas.header.template_chip)

    assert sidebar.setup_tab_shown and sidebar.currentWidget() is sidebar.setup_panel
    assert all(card.done for card in sidebar.setup_panel.cards)


def test_a_bot_made_from_nothing_never_gets_the_tab(window):
    plain = _bot()
    plain.template = None

    window.bot_canvas.setup(plain, mode=BotMode.EDIT)

    assert not window.right_sidebar.setup_tab_shown


def test_a_draft_gets_the_tab_as_its_step_editor_without_being_taken_to_it(window):
    canvas, sidebar = window.bot_canvas, window.right_sidebar

    canvas.setup(_draft(), mode=BotMode.EDIT)

    assert sidebar.setup_tab_shown
    assert sidebar.currentWidget() is not sidebar.setup_panel
    assert all(isinstance(card, DraftStepCard) for card in sidebar.setup_panel.cards)


def test_a_draft_edit_in_the_tab_reaches_the_canvas_and_undoes(window):
    canvas, panel = window.bot_canvas, window.right_sidebar.setup_panel
    canvas.setup(_draft(), mode=BotMode.EDIT)

    panel.remove_step(1)
    assert [s.text for s in canvas.config.template_meta.setup] == [_ZONE.text, _DROPS.text]

    canvas.undo()
    assert [card.text_edit.text() for card in panel.cards] == [s.text for s in _STEPS], "the tab follows an undo"


def test_closing_the_bot_takes_the_tab_away(window):
    canvas = window.bot_canvas
    _open_bot(canvas, _bot())

    canvas.close_bot()

    assert not window.right_sidebar.setup_tab_shown
    assert window.right_sidebar.setup_panel.cards == []


def test_a_fights_import_fills_the_drop_list_and_records_the_page(window, monkeypatch):
    from bartlebot.bot.schema import DropModel  # noqa: PLC0415
    from bartlebot.gui import wiki_import_dialog  # noqa: PLC0415

    class _Imported:
        def __init__(self, parent=None) -> None:
            pass

        def exec(self) -> bool:
            return True

        def selected_entries(self):
            return [DropModel(name="Amber", category="Reagents")]

        def selected_page(self) -> str:
            return "Creature:Troll"

    monkeypatch.setattr(wiki_import_dialog, "WikiImportDialog", _Imported)
    canvas, sidebar = window.bot_canvas, window.right_sidebar
    bot = _bot()
    _open_bot(canvas, bot, mode=BotMode.RUN)
    sidebar.set_config(bot)

    sidebar.setup_panel.cards[2].import_buttons["t_2"].click()

    assert canvas.mode == BotMode.EDIT, "the drop list is the bot file's"
    assert [d.name for d in canvas.config.drops] == ["Amber"]
    assert _state(canvas.config, "t_2").params["wiki_page"] == "Creature:Troll"
    assert sidebar.setup_panel.cards[2].done, "the step ticks without leaving the tab"


# ---------------------------------------------------------------------------
# A template update (10.6): the review in the tab, Apply through undo, Finish
# ---------------------------------------------------------------------------
#
# The diff itself is in tests/test_template_diff.py. These check that the tab draws what
# it says, and that each verb reaches the bot through the canvas.

_NOTE = "Collect wisps after the fight"


def _v2_draft() -> BotConfig:
    """Mob Farmer's next version: a wisp node after the fight, and a cap on the fight's length."""
    draft = _draft()
    _state(draft, "t_2").params["max_duration"] = 300
    draft.states.append(
        StateModel(
            id="t_3", node_type="collect_wisps", label="Collect wisps", position={"x": 880.0, "y": 0.0}, params={}
        )
    )
    draft.transitions = [
        TransitionModel(trigger="advance", source="t_1", dest="t_2", when="SUCCESS"),
        TransitionModel(trigger="advance", source="t_2", dest="t_3", when="SUCCESS"),
        TransitionModel(trigger="advance", source="t_3", dest="t_1", when="SUCCESS"),
    ]
    return draft


def _update(theirs: BotConfig | None = None) -> template_diff.TemplateUpdate:
    return template_diff.TemplateUpdate(
        TEMPLATE_ID, "Mob Farmer", 1, _draft(), [template_diff.Release(2, _NOTE, theirs or _v2_draft())]
    )


def _review(bot: BotConfig, theirs: BotConfig | None = None) -> template_diff.Review:
    return template_diff.review(_update(theirs), bot)


def _show_review(panel: SetupPanel, bot: BotConfig, theirs: BotConfig | None = None) -> None:
    panel.show_document(bot, setup_steps.progress(bot, _STEPS), None, _review(bot, theirs))


def _change_card(panel: SetupPanel, text: str) -> ChangeCard:
    return next(card for card in panel.change_cards if text in card.title)


def _open_behind(canvas, bot: BotConfig, mode: BotMode = BotMode.EDIT) -> None:
    """Open *bot* with the update the controller would have read for it."""
    _open_bot(canvas, bot, mode)
    canvas.set_template_update(_update())


def test_the_review_says_what_moved_and_heads_each_release_with_its_note():
    review = _review(_bot())

    assert review_title(review) == "Mob Farmer has moved on (v1 \u2192 v2)"
    assert release_heading(2) == "v2"


def test_a_long_release_note_wraps_rather_than_widening_the_tab(panel):
    """A heading's label never wraps, so a note in one set the tab's minimum width past its viewport."""
    from PyQt6.QtWidgets import QLabel  # noqa: PLC0415

    long_note = "Expose boss duel for combat strategies and update blurb and help text " * 3
    update = template_diff.TemplateUpdate(
        TEMPLATE_ID, "Mob Farmer", 1, _draft(), [template_diff.Release(2, long_note, _v2_draft())]
    )
    panel.show_document(_bot(), setup_steps.progress(_bot(), _STEPS), None, template_diff.review(update, _bot()))

    (note,) = [label for label in panel.findChildren(QLabel) if label.text() == long_note]
    assert note.wordWrap()
    assert panel.scroll_area.widget().minimumSizeHint().width() < 300


def test_a_change_card_says_what_would_change_or_why_it_cannot():
    bot = _bot()
    _state(bot, "t_2").params["max_duration"] = 60
    conflict = next(c for c in _review(bot).changes if "Max" in c.title)
    offered = next(c for c in _review(_bot()).changes if "Max" in c.title)

    assert change_summary(offered) == offered.detail
    assert change_summary(conflict).startswith("You changed this too. ")
    assert change_summary(conflict, kept=True).startswith("Keeping yours. ")

    theirs = _draft()
    theirs.transitions.append(TransitionModel(trigger="advance", source="t_2", dest="t_1", when="FAILURE"))
    gone = _bot()
    gone.states = [s for s in gone.states if s.id != "t_1"]
    gone.transitions, gone.initial, gone.recovery_state = [], "t_2", "t_2"
    blocked = next(c for c in _review(gone, theirs).changes if c.status is template_diff.Status.BLOCKED)
    assert change_summary(blocked).startswith("Can't apply: This bot has no ")


def test_the_tab_draws_a_card_per_change_above_the_bots_steps(panel):
    _show_review(panel, _bot())

    assert [card.title for card in panel.change_cards] == [
        "Add 'Collect wisps'",
        "Defeat mobs \u00b7 Max Duration (s)",
    ]
    assert all(list(card.buttons) == [APPLY_TEXT] for card in panel.change_cards)
    assert [card.text for card in panel.cards] == [s.text for s in _STEPS], "the steps are still there, below"
    assert panel.finish_button is not None and panel.later_button is not None


def test_a_conflict_offers_both_sides_and_keeping_mine_changes_nothing_in_the_bot(panel):
    bot = _bot()
    _state(bot, "t_2").params["max_duration"] = 60
    before = bot.model_dump()
    asked: list[str] = []
    panel.apply_requested.connect(asked.append)
    _show_review(panel, bot)
    card = _change_card(panel, "Max Duration")
    assert list(card.buttons) == [USE_TEMPLATE_TEXT, KEEP_MINE_TEXT]

    card.buttons[KEEP_MINE_TEXT].click()

    kept = _change_card(panel, "Max Duration")
    assert kept.summary.startswith("Keeping yours.")
    assert list(kept.buttons) == [USE_TEMPLATE_TEXT], "the template's side can still be taken"
    assert asked == [] and bot.model_dump() == before

    kept.buttons[USE_TEMPLATE_TEXT].click()
    assert asked == [next(c.id for c in _review(bot).changes if "Max" in c.title)]


def test_a_change_that_cannot_be_applied_has_no_button(panel):
    theirs = _draft()
    theirs.transitions.append(TransitionModel(trigger="advance", source="t_2", dest="t_1", when="FAILURE"))
    bot = _bot()
    bot.states = [s for s in bot.states if s.id != "t_1"]
    bot.transitions, bot.initial, bot.recovery_state = [], "t_2", "t_2"

    _show_review(panel, bot, theirs)

    (card,) = panel.change_cards
    assert card.buttons == {} and card.summary.startswith("Can't apply")


def test_a_release_with_nothing_for_this_bot_says_so(panel):
    from PyQt6.QtWidgets import QLabel  # noqa: PLC0415

    _show_review(panel, _bot(), _draft())

    assert panel.change_cards == []
    assert any(label.text() == NOTHING_TO_DO_TEXT for label in panel.findChildren(QLabel))
    assert panel.finish_button is not None, "finishing is how the newer steps are taken"


def test_the_reviews_verbs_ask_and_clicking_a_card_goes_to_its_node(panel):
    applied, went, ended = [], [], []
    panel.apply_requested.connect(applied.append)
    panel.go_to_requested.connect(went.append)
    panel.finish_requested.connect(lambda: ended.append("finish"))
    panel.later_requested.connect(lambda: ended.append("later"))
    _show_review(panel, _bot())

    _change_card(panel, "Add 'Collect wisps'").buttons[APPLY_TEXT].click()
    _click(_change_card(panel, "Max Duration"))
    panel.finish_button.click()
    panel.later_button.click()

    assert applied == [next(c.id for c in _review(_bot()).changes if c.added)]
    assert went == ["t_2"]
    assert ended == ["finish", "later"]


def test_building_the_review_never_shows_a_parentless_widget(qapp, parentless_shows):
    panel = SetupPanel()
    bot = _bot()
    _state(bot, "t_2").params["max_duration"] = 60
    _show_review(panel, bot)
    panel.keep_change(next(c.id for c in _review(bot).changes if "Max" in c.title))

    assert parentless_shows == []


def test_applying_a_change_is_one_undo_step(canvas):
    _open_behind(canvas, _bot())
    added = next(c for c in canvas.template_review.changes if c.added)

    assert canvas.apply_template_change(added.id)

    assert _state(canvas.config, "t_3").node_type == "collect_wisps"
    assert not any(c.added for c in canvas.template_review.changes), "applied, so no longer offered"
    canvas.undo()
    assert all(s.id != "t_3" for s in canvas.config.states)
    assert any(c.added for c in canvas.template_review.changes)


def test_applying_from_run_mode_switches_to_edit_mode(canvas):
    _open_behind(canvas, _bot(), mode=BotMode.RUN)
    change = next(c for c in canvas.template_review.changes if "Max" in c.title)

    assert canvas.apply_template_change(change.id)

    assert canvas.mode == BotMode.EDIT
    assert _state(canvas.config, "t_2").params["max_duration"] == 300


def test_an_added_node_moves_to_a_free_cell_when_its_spot_is_taken(canvas):
    bot = _bot()
    bot.states.append(
        StateModel(id="state_1", node_type="sleep", position={"x": 880.0, "y": 0.0}, params={"duration": 1})
    )
    _open_behind(canvas, bot)

    canvas.apply_template_change(next(c for c in canvas.template_review.changes if c.added).id)

    assert _state(canvas.config, "t_3").position != _state(canvas.config, "state_1").position


def test_a_bot_with_no_update_has_no_review(canvas):
    _open_bot(canvas, _bot())
    assert canvas.template_review is None
    assert not canvas.apply_template_change("anything")
    assert not canvas.finish_template_review()

    canvas.set_template_update(_update())
    other = _bot()
    other.id = "bot-other"
    _open_bot(canvas, other)
    assert canvas.template_review is None, "an update is for the bot it was read for"


def test_finishing_advances_the_base_as_one_undo_step_and_says_the_template_moved(canvas):
    _open_behind(canvas, _bot())
    moved: list[int] = []
    canvas.template_ref_changed.connect(lambda: moved.append(canvas.config.template.version))

    assert canvas.finish_template_review()

    assert canvas.config.template.version == 2
    assert canvas.template_review is None, "skipped changes are not offered again"
    canvas.undo()
    assert canvas.config.template.version == 1
    assert moved == [2, 1]


def test_removing_a_fight_with_a_strategy_asks_first(canvas, monkeypatch):
    from bartlebot.combat.profile_models import BotCombatStrategy, FightRef, StateAssignment  # noqa: PLC0415
    from bartlebot.gui.canvas import bot_canvas as canvas_module  # noqa: PLC0415
    from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415

    theirs = _draft([_ZONE, _DROPS])
    theirs.states = [s for s in theirs.states if s.id != "t_2"]
    theirs.transitions = [TransitionModel(trigger="advance", source="t_1", dest="t_1", when="SUCCESS")]
    bot = _bot()
    _state(bot, "t_2").params.update(enemy="Troll", use_default_strategy=False)
    profile = BotCombatStrategy(
        name="Solo",
        bot_id=bot.id,
        state_assignments=[StateAssignment(fights=[FightRef(state="t_2")], duel_combat_strategy_id="dcs-1")],
    )
    # Not the active profile, and asked about all the same: it loses the fight too.
    monkeypatch.setattr(canvas_module, "list_bot_strategies", lambda bot_id: [profile] if bot_id == bot.id else [])
    assert SettingsManager.instance().get_active_strategy(bot.id) is None
    _open_bot(canvas, bot)
    canvas.set_template_update(_update(theirs))
    removal = next(c for c in canvas.template_review.changes if c.removed)
    asked: list[str] = []

    def answer(reply):
        def question(_parent, _title, text, *_a, **_k):
            asked.append(text)
            return reply

        return question

    monkeypatch.setattr(QMessageBox, "question", answer(QMessageBox.StandardButton.No))
    assert not canvas.apply_template_change(removal.id)
    assert any(s.id == "t_2" for s in canvas.config.states)
    assert "'Defeat mobs'" in asked[0] and "combat profile 'Solo'" in asked[0]

    monkeypatch.setattr(QMessageBox, "question", answer(QMessageBox.StandardButton.Yes))
    assert canvas.apply_template_change(removal.id)
    assert all(s.id != "t_2" for s in canvas.config.states)


def test_a_bot_behind_its_template_opens_on_the_review(window):
    canvas, sidebar = window.bot_canvas, window.right_sidebar
    done = _done_bot()

    _open_behind(canvas, done, mode=BotMode.RUN)

    assert sidebar.setup_tab_shown and sidebar.currentWidget() is sidebar.setup_panel
    assert len(sidebar.setup_panel.change_cards) == 2


def test_apply_in_the_tab_reaches_the_bot_and_the_card_goes(window):
    canvas, panel = window.bot_canvas, window.right_sidebar.setup_panel
    _open_behind(canvas, _done_bot())

    _change_card(panel, "Add 'Collect wisps'").buttons[APPLY_TEXT].click()

    assert any(s.id == "t_3" for s in canvas.config.states)
    assert [card.title for card in panel.change_cards] == ["Defeat mobs \u00b7 Max Duration (s)"]


def test_later_puts_the_review_away_and_the_chip_brings_it_back(window):
    canvas, sidebar = window.bot_canvas, window.right_sidebar
    _open_behind(canvas, _done_bot())
    canvas.set_template_origin("Mob Farmer", 1, 2)
    assert canvas.header.template_chip.text() == "Mob Farmer v1 \u2192 v2"

    sidebar.setup_panel.later_button.click()

    assert not sidebar.setup_tab_shown
    assert canvas.config.template.version == 1, "Later leaves the base where it is"

    _click(canvas.header.template_chip)

    assert sidebar.setup_tab_shown and len(sidebar.setup_panel.change_cards) == 2


def test_finish_in_the_tab_ends_the_review(window):
    canvas, sidebar = window.bot_canvas, window.right_sidebar
    _open_behind(canvas, _done_bot())

    sidebar.setup_panel.finish_button.click()

    assert canvas.config.template.version == 2
    assert sidebar.setup_panel.change_cards == []


def test_opening_a_bot_behind_reads_its_update_and_finishing_brings_the_newer_steps(harness, store, tmp_path):
    v2 = _v2_draft()
    v2.template_meta.setup.append(
        SetupStep(text="Cap the wisp wait", targets=[SetupTarget(state="t_3", param="timeout")])
    )
    save_draft(v2, store)
    release(TEMPLATE_ID, _NOTE, root=store, today=date(2026, 10, 2))
    path = tmp_path / "bots" / "trolls.json"
    _bot().save(path)
    before = path.read_bytes()
    canvas = harness._window.bot_canvas
    canvas.template_ref_changed.connect(harness._refresh_template_context)

    harness._open_bot(str(path), mode=BotMode.EDIT)

    assert canvas.header.template_chip.text() == "Mob Farmer v1 \u2192 v2"
    assert [c.title for c in canvas.template_review.changes] == [
        "Add 'Collect wisps'",
        "Defeat mobs \u00b7 Max Duration (s)",
    ]
    assert harness._save_bot() and path.read_bytes() == before, "reading an update writes nothing"

    for change in list(canvas.template_review.changes):
        assert canvas.apply_template_change(change.id)
    assert canvas.finish_template_review()

    assert canvas.header.template_chip.text() == "Mob Farmer v2"
    assert canvas.template_review is None
    assert canvas.setup_steps_left == 4, "the newer version's step came with the new base"
    assert harness._save_bot()
    assert BotConfig.load(path).template == TemplateRef(id=TEMPLATE_ID, version=2)


def test_a_library_row_says_when_the_template_has_moved_on(qapp, store, tmp_path):
    from bartlebot.gui.canvas.bot_canvas import UPDATE_AVAILABLE_TEXT, _BotCard, _read_bot_meta  # noqa: PLC0415

    path = tmp_path / "trolls.json"
    _bot().save(path)
    assert _BotCard(_read_bot_meta(path, store)).update_text is None

    save_draft(_v2_draft(), store)
    release(TEMPLATE_ID, _NOTE, root=store, today=date(2026, 10, 2))

    meta = _read_bot_meta(path, store)
    assert meta.update_available
    assert _BotCard(meta).update_text == UPDATE_AVAILABLE_TEXT


# ---------------------------------------------------------------------------
# Fixes from the stage 10 review
# ---------------------------------------------------------------------------


def test_save_as_keeps_the_copys_setup_steps_and_its_review(harness, store, tmp_path, monkeypatch):
    """A copy has a new id, and the steps and the update were held against the old one.

    So the copy of a half set up bot lost its Setup errors and could be run with a blank
    enemy, until it was reopened.
    """
    from PyQt6.QtWidgets import QFileDialog  # noqa: PLC0415

    save_draft(_v2_draft(), store)
    release(TEMPLATE_ID, _NOTE, root=store, today=date(2026, 10, 2))
    path = tmp_path / "bots" / "trolls.json"
    _bot().save(path)
    harness._open_bot(str(path), mode=BotMode.EDIT)
    canvas = harness._window.bot_canvas
    assert canvas.setup_steps_left == 3 and canvas.template_review is not None
    copy = tmp_path / "bots" / "copy.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(copy), "")))
    monkeypatch.setattr(type(harness), "_confirm_rewards_tags", lambda self: True, raising=False)

    assert harness._save_bot_as()

    assert canvas.config.id != "bot-mob"
    assert canvas.has_setup_steps and canvas.setup_steps_left == 3
    assert any("Setup" in e for e in canvas.validation_errors())
    assert canvas.template_review is not None
    assert canvas.header.template_chip.text() == "Mob Farmer v1 \u2192 v2"


def test_an_undo_across_finish_gives_the_controller_the_older_base_back(harness, store, tmp_path):
    """The canvas said the base had moved before the controller held the config it moved in."""
    save_draft(_v2_draft(), store)
    release(TEMPLATE_ID, _NOTE, root=store, today=date(2026, 10, 2))
    path = tmp_path / "bots" / "trolls.json"
    _bot().save(path)
    canvas = harness._window.bot_canvas
    canvas.template_ref_changed.connect(harness._refresh_template_context)
    harness._open_bot(str(path), mode=BotMode.EDIT)

    assert canvas.finish_template_review()
    assert canvas.header.template_chip.text() == "Mob Farmer v2" and canvas.template_review is None

    canvas.undo()

    assert canvas.header.template_chip.text() == "Mob Farmer v1 \u2192 v2"
    assert canvas.template_review is not None


def test_the_review_is_worked_out_again_only_when_the_bot_has_changed(canvas, monkeypatch):
    worked_out: list[int] = []
    real = template_diff.review

    def counting(*args, **kwargs):
        worked_out.append(1)
        return real(*args, **kwargs)

    _open_behind(canvas, _bot())
    monkeypatch.setattr(template_diff, "review", counting)

    first = canvas.template_review
    assert canvas.template_review is first and len(worked_out) == 1

    _state(canvas.config, "t_2").params["max_duration"] = 60
    assert canvas.template_review is not first and len(worked_out) == 2
    assert _change_text(canvas.template_review, "Max Duration").status is template_diff.Status.CONFLICT

    canvas.set_template_update(_update())
    assert canvas.template_review is not None and len(worked_out) == 3, "a new update is a new review"


def _change_text(review: template_diff.Review, text: str) -> template_diff.Change:
    return next(c for c in review.changes if text in c.title)


def test_a_fight_in_two_profiles_names_both(canvas, monkeypatch):
    from bartlebot.combat.profile_models import BotCombatStrategy, FightRef, StateAssignment  # noqa: PLC0415
    from bartlebot.gui.canvas import bot_canvas as canvas_module  # noqa: PLC0415

    theirs = _draft([_ZONE, _DROPS])
    theirs.states = [s for s in theirs.states if s.id != "t_2"]
    theirs.transitions = [TransitionModel(trigger="advance", source="t_1", dest="t_1", when="SUCCESS")]
    bot = _bot()
    _state(bot, "t_2").params.update(enemy="Troll", use_default_strategy=False)

    def profile(name: str, linked: bool = True) -> BotCombatStrategy:
        assignment = StateAssignment(
            fights=[FightRef(state="t_2")], duel_combat_strategy_id="dcs-1" if linked else None
        )
        return BotCombatStrategy(name=name, bot_id=bot.id, state_assignments=[assignment])

    profiles = [profile("Solo"), profile("Nothing linked", linked=False), profile("Team")]
    monkeypatch.setattr(canvas_module, "list_bot_strategies", lambda _bot_id: profiles)
    _open_bot(canvas, bot)
    canvas.set_template_update(_update(theirs))
    removal = next(c for c in canvas.template_review.changes if c.removed)
    asked: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "question", lambda _p, _t, text, *a, **k: asked.append(text) or QMessageBox.StandardButton.No
    )

    assert not canvas.apply_template_change(removal.id)

    assert "combat profiles 'Solo', 'Team'" in asked[0] and "Nothing linked" not in asked[0]


def test_a_visit_to_a_subgraph_definition_does_not_bring_back_a_review_put_away(window):
    """The definition is another config with another id, so coming back looked like a bot just opened."""
    from bartlebot.subgraphs import definition_to_config  # noqa: PLC0415

    canvas, sidebar = window.bot_canvas, window.right_sidebar
    bot = _done_bot()
    _open_behind(canvas, bot)
    sidebar.setup_panel.later_button.click()
    assert not sidebar.setup_tab_shown
    sidebar.switch_to_log()
    library = SubgraphLibrary()
    definition = definition_to_config(library.get(library.ids()[0]))

    canvas.setup(definition, mode=BotMode.EDIT)
    assert not sidebar.setup_tab_shown, "a definition has no Setup tab of its own"
    _open_behind(canvas, bot)

    assert not sidebar.setup_tab_shown, "Later lasts until the bot is next opened, not until the next visit"
    assert sidebar.currentWidget() is not sidebar.setup_panel


def test_coming_back_from_a_definition_does_not_pull_the_sidebar_to_setup(window):
    from bartlebot.subgraphs import definition_to_config  # noqa: PLC0415

    canvas, sidebar = window.bot_canvas, window.right_sidebar
    bot = _bot()
    _open_bot(canvas, bot)
    assert sidebar.currentWidget() is sidebar.setup_panel
    sidebar.switch_to_log()
    library = SubgraphLibrary()

    canvas.setup(definition_to_config(library.get(library.ids()[0])), mode=BotMode.EDIT)
    _open_bot(canvas, bot)

    assert sidebar.setup_tab_shown, "the steps are still to do"
    assert sidebar.currentWidget() is not sidebar.setup_panel, "but the user was reading the log"


def test_a_library_row_counts_a_files_steps_once_until_the_file_changes(qapp, store, tmp_path, monkeypatch):
    """Counting validates the whole file, and the library reads every bot each time it is listed."""
    import os  # noqa: PLC0415

    from bartlebot.gui.canvas.bot_canvas import _read_bot_meta  # noqa: PLC0415

    counted: list[int] = []
    real = setup_steps.left_in_file

    def counting(*args, **kwargs):
        counted.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(setup_steps, "left_in_file", counting)
    path = tmp_path / "trolls.json"
    bot = _bot()
    bot.save(path)

    assert _read_bot_meta(path, store).setup_left == 3
    assert _read_bot_meta(path, store).setup_left == 3
    assert len(counted) == 1

    _state(bot, "t_2").params["enemy"] = "Troll"
    bot.save(path)
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000))

    assert _read_bot_meta(path, store).setup_left == 2
    assert len(counted) == 2
