"""Which panel holds what, after the layout reorganisation (#80).

The left sidebar is now two tabs whose *identity* depends on the mode — Setup and
Combat while running, Options and Properties while editing — which is §3.5 of the
declarations/overrides plan: the two surfaces are separated by context, never by a
toggle inside one panel.  So the thing worth pinning down is that no tab is
reachable from the wrong mode, and that nothing is lost across the swap.

Log and Possible Drops moved to the right sidebar, which is what the second half
covers.
"""

from __future__ import annotations

import sys

import pytest
from PyQt6.QtWidgets import QApplication, QWidget

from bartlebot.bot.schema import BotConfig, DropModel, StateModel
from bartlebot.gui.sidebar.left_sidebar import LeftSidebar
from bartlebot.gui.sidebar.right_sidebar import RightSidebar


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def _config(**overrides) -> BotConfig:
    data = {
        "id": "test-bot",
        "name": "Test Bot",
        "initial": "a",
        "recovery_state": "a",
        "states": [StateModel(id="a", node_type="mark_location", params={})],
        "transitions": [],
    }
    data.update(overrides)
    return BotConfig(**data)


def _tabs(widget) -> list[str]:
    return [widget.tabText(i) for i in range(widget.count())]


@pytest.fixture
def sidebar(qapp) -> LeftSidebar:
    return LeftSidebar(QWidget())


# ---------------------------------------------------------------------------
# The left sidebar's two tab sets
# ---------------------------------------------------------------------------


def test_run_mode_offers_setup_and_combat(sidebar):
    assert _tabs(sidebar) == ["Setup", "Combat"]


def test_edit_mode_offers_options_and_properties(sidebar):
    sidebar.set_editable(True)

    assert _tabs(sidebar) == ["Options", "Properties"]


def test_each_mode_lands_on_its_first_tab(sidebar):
    """Setup being the Run-mode default is the whole pre-run configuration story:
    adjacency to the Run button rather than a gate in front of it."""
    sidebar.setCurrentIndex(1)
    sidebar.set_editable(True)

    assert sidebar.currentIndex() == 0


def test_switching_back_and_forth_keeps_the_same_widgets(sidebar):
    param_panel = sidebar._param_panel
    setup_page = sidebar._setup_page

    sidebar.set_editable(True)
    sidebar.set_editable(False)
    sidebar.set_editable(True)

    assert sidebar.widget(1) is param_panel
    assert sidebar.indexOf(setup_page) == -1


def test_a_tab_the_mode_does_not_offer_is_parked_rather_than_orphaned(sidebar):
    """``removeTab`` leaves a widget parentless, which makes it a top-level window."""
    sidebar.set_editable(True)

    assert sidebar._setup_page.parent() is not None
    assert not sidebar._setup_page.isVisibleTo(sidebar)


def test_switching_to_a_tab_the_mode_does_not_have_is_a_no_op(sidebar):
    """The canvas asks for Properties whenever a node is selected; in Run mode the
    scene is not interactive, but the request must not land on Combat by index."""
    sidebar.switch_to_properties()

    assert sidebar.currentIndex() == 0
    assert _tabs(sidebar)[0] == "Setup"


def test_switching_to_combat_finds_it_by_widget_not_by_index(sidebar):
    sidebar.switch_to_combat()

    assert sidebar.widget(sidebar.currentIndex()) is sidebar.combat_tab


# ---------------------------------------------------------------------------
# The description heads both option surfaces
# ---------------------------------------------------------------------------


def test_the_full_description_heads_both_surfaces(sidebar):
    """Elided in a header it would hide the multi-line ``REQUIREMENTS:`` blocks the
    gauntlet bots carry, which is exactly the text a user has to read."""
    text = "REQUIREMENTS:\nChange the client resolution to 800x600 before running."
    sidebar.set_config(_config(description=text))

    assert sidebar.setup_description.text() == text
    assert sidebar.options_description.text() == text


def test_a_bot_with_no_description_says_so_rather_than_showing_a_gap(sidebar):
    sidebar.set_config(_config())

    assert sidebar.setup_description.text() == "This bot has no description."


def test_closing_the_bot_hides_the_description(sidebar):
    sidebar.set_config(_config(description="Farms Jade Oni."))
    sidebar.set_config(None)

    assert not sidebar.setup_description.isVisibleTo(sidebar._setup_page)


# ---------------------------------------------------------------------------
# Only the Options copy is writable (#180)
# ---------------------------------------------------------------------------


def test_the_options_copy_is_the_only_editor(sidebar):
    """Phase 5 of #145 left the description rendered twice and editable nowhere.
    The writable copy belongs on the declarations surface, not the values one."""
    assert sidebar.options_description.is_editable
    assert not sidebar.setup_description.is_editable


def test_both_copies_collapse(sidebar):
    """A long REQUIREMENTS block is only affordable above a surface the user came
    to the tab to use if they can fold it away."""
    sidebar.set_config(_config(description="Farms Jade Oni."))

    sidebar.setup_description.set_expanded(False)

    assert not sidebar.setup_description.expanded
    assert not sidebar.setup_description.body.isVisibleTo(sidebar.setup_description)


def test_typing_a_description_writes_it_to_the_bot(sidebar):
    config = _config()
    sidebar.set_config(config)

    sidebar.options_description.set_text("Farms Jade Oni.")

    assert config.description == "Farms Jade Oni."


def test_clearing_the_description_stores_none_rather_than_an_empty_string(sidebar):
    config = _config(description="Farms Jade Oni.")
    sidebar.set_config(config)

    sidebar.options_description.set_text("")

    assert config.description is None


def test_editing_the_description_dirties_the_bot(sidebar):
    """An override lives in settings.json and must not dirty the file; the
    description is authorial, so it must."""
    sidebar.set_config(_config())
    seen = []
    sidebar.config_changed.connect(lambda: seen.append(True))

    sidebar.options_description.set_text("Farms Jade Oni.")

    assert seen


def test_the_read_only_copy_follows_the_editor(sidebar):
    """A mode switch re-binds neither copy, so the Setup label would otherwise go
    on showing whatever the bot said when it was opened."""
    sidebar.set_config(_config())

    sidebar.options_description.set_text("Farms Jade Oni.")

    assert sidebar.setup_description.text() == "Farms Jade Oni."


def test_loading_a_bot_does_not_dirty_it(sidebar):
    """``setPlainText`` emits ``textChanged`` like any other edit, so an unguarded
    refresh marks a freshly opened bot as modified."""
    seen = []
    sidebar.config_changed.connect(lambda: seen.append(True))

    sidebar.set_config(_config(description="Farms Jade Oni."))

    assert not seen


def test_an_edit_elsewhere_does_not_move_the_caret(sidebar):
    """Every keystroke comes back round as ``refresh_options``; re-setting the text
    it just wrote would put the cursor back at the start on every character."""
    config = _config()
    sidebar.set_config(config)
    sidebar.options_description.set_text("Farms Jade Oni.")
    editor = sidebar.options_description.editor
    editor.moveCursor(editor.textCursor().MoveOperation.End)
    before = editor.textCursor().position()

    sidebar.refresh_options(config)

    assert editor.textCursor().position() == before


# ---------------------------------------------------------------------------
# The right sidebar
# ---------------------------------------------------------------------------


def test_the_right_sidebar_holds_players_drops_and_log(qapp):
    assert _tabs(RightSidebar()) == ["Players", "Drops", "Log"]


def test_players_is_the_default_tab(qapp):
    assert RightSidebar().currentIndex() == 0


def test_possible_drops_rides_with_the_live_drop_tables(qapp):
    """Same subject at two different times: what the run could yield, and what it has."""
    panel = RightSidebar()
    panel.set_config(_config(drops=[DropModel(name="Amber", category="Reagents")]))

    drops_page = panel.widget(1)
    assert panel.possible_drops.isVisibleTo(drops_page)
    assert panel.drops.isVisibleTo(drops_page)


def test_editing_the_drop_list_dirties_the_bot(qapp):
    """It is the one authorial thing left in this panel, so it needs a route out."""
    panel = RightSidebar()
    panel.set_config(_config())
    dirty: list[int] = []
    panel.config_changed.connect(lambda: dirty.append(1))

    panel.possible_drops.drops_changed.emit()

    assert dirty


def test_leaving_the_players_tab_lets_the_client_poll_slow_down(qapp):
    panel = RightSidebar()

    panel.switch_to_log()

    assert not panel.clients.players_visible
