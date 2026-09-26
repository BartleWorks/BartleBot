"""Which panel holds what, after the layout reorganisation.

The left sidebar is now two tabs whose *identity* depends on the mode — Setup and
Combat while running, Options and Properties while editing — which is §3.5 of the
declarations/overrides plan: the two surfaces are separated by context, never by a
toggle inside one panel.  So the thing worth pinning down is that no tab is
reachable from the wrong mode, and that nothing is lost across the swap.

Log and Possible Drops moved to the right sidebar, which is what the second half
covers.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QWidget

from bartlebot.bot.schema import BotConfig, BotOptionModel, DropModel, StateModel
from bartlebot.gui.sidebar.left_sidebar import LeftSidebar
from bartlebot.gui.sidebar.right_sidebar import RightSidebar


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
# Only the Options copy is writable
# ---------------------------------------------------------------------------


def test_the_options_copy_is_the_only_editor(sidebar):
    """Phase 5 of the bot-options split left the description rendered twice and editable nowhere.
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
    sidebar.metadata_changed.connect(lambda: seen.append(True))

    sidebar.options_description.set_text("Farms Jade Oni.")

    assert seen


def test_typing_a_description_does_not_rebuild_the_option_surfaces(sidebar):
    """``config_changed``'s handler ends in ``refresh_options``, which recomposes
    and rebuilds every row on both option surfaces — around 80ms on a large bot.

    Routing the description through it paid that per *character* typed, which made
    the field unusable at roughly 6fps and degrading.  Nothing composes an option
    from prose, so a description edit goes out as ``metadata_changed`` instead.
    """
    sidebar.set_config(_config())
    rebuilds = []
    sidebar.config_changed.connect(lambda: rebuilds.append(True))

    sidebar.options_description.set_text("Farms Jade Oni.")

    assert rebuilds == []


def test_a_short_description_does_not_reserve_room_for_a_long_one(sidebar):
    """The editor is as tall as its text, floored at two lines.

    A fixed three-line box was the median of the shipped library, so it was both
    too small for half of it and too large for the rest.
    """
    editor = sidebar.options_description.editor
    assert editor is not None
    sidebar.set_config(_config(description="Endlessly farms Foulgaze in Olde Town."))

    assert editor.preferred_height() == editor._line_height(2)  # noqa: SLF001


def test_a_requirements_block_makes_the_editor_grow(sidebar):
    """The four bots whose descriptions say "change the client resolution to..."
    are the reason the description is here at all rather than in the header."""
    editor = sidebar.options_description.editor
    assert editor is not None
    short = _config(description="Endlessly farms the Spiral Cup Gauntlet.")
    sidebar.set_config(short)
    small = editor.preferred_height()

    sidebar.set_config(
        _config(
            description=(
                "Endlessly farms the Spiral Cup Gauntlet.\n\n"
                "REQUIREMENTS: Set Friend Name in Setup to the friend who owns the "
                "gauntlet. Your players enter by porting to them rather than by "
                "walking in, so that friend must be standing on the entrance sigil."
            )
        )
    )

    assert editor.preferred_height() > small


def test_the_editor_stops_growing_before_it_takes_the_tab(sidebar):
    """Three gauntlets run to 20 lines at the default sidebar width.  They scroll
    here; the wrapping label above Setup is where a long block is read in full."""
    editor = sidebar.options_description.editor
    assert editor is not None
    sidebar.set_config(_config(description="\n".join(f"line {i}" for i in range(40))))

    assert editor.preferred_height() == editor._line_height(8)  # noqa: SLF001


def test_the_read_only_copy_follows_the_editor(sidebar):
    """A mode switch re-binds neither copy, so the Setup label would otherwise go
    on showing whatever the bot said when it was opened."""
    sidebar.set_config(_config())

    sidebar.options_description.set_text("Farms Jade Oni.")

    assert sidebar.setup_description.text() == "Farms Jade Oni."


# ---------------------------------------------------------------------------
# An empty description stands in with the one the graph implies
# ---------------------------------------------------------------------------

_NIGHTSHADE = "Defeats Lord Nightshade in Nightshade's Tower, Haunted Cave, Wizard City."


def _boss_config(**overrides) -> BotConfig:
    """A bot whose graph implies :data:`_NIGHTSHADE`. See tests/test_bot_summary.py."""
    states = [
        StateModel(
            id="a",
            node_type="enter_dungeon",
            params={
                "from_zone": "WizardCity/WC_Streets/WC_HauntedCave",
                "dungeon_zone": "WizardCity/WC_Streets/Interiors/WC_HauntedCave_T1",
            },
        ),
        StateModel(id="b", node_type="handle_combat", params={"enemy": "Wraith-WCBoss-L05"}),
    ]
    return _config(states=states, **overrides)


def test_an_empty_description_shows_the_generated_one_above_setup(sidebar):
    sidebar.set_config(_boss_config())

    assert sidebar.setup_description.text() == _NIGHTSHADE


def test_the_generated_description_is_offered_under_the_empty_editor(sidebar):
    sidebar.set_editable(True)
    sidebar.set_config(_boss_config())

    assert sidebar.options_description.editor.toPlainText() == ""
    assert sidebar.options_description.suggestion_shown


def test_use_this_writes_the_generated_description_into_the_bot(sidebar):
    config = _boss_config()
    sidebar.set_editable(True)
    sidebar.set_config(config)
    seen = []
    sidebar.metadata_changed.connect(lambda: seen.append(True))

    sidebar.options_description._use_btn.click()  # noqa: SLF001

    assert config.description == _NIGHTSHADE
    assert seen
    assert not sidebar.options_description.suggestion_shown


def test_the_generated_description_is_never_written_unasked(sidebar):
    config = _boss_config()
    seen = []
    sidebar.metadata_changed.connect(lambda: seen.append(True))

    sidebar.set_config(config)

    assert config.description is None
    assert not seen


def test_typing_withdraws_the_offer_and_clearing_brings_it_back(sidebar):
    sidebar.set_editable(True)
    sidebar.set_config(_boss_config())

    sidebar.options_description.set_text("Farms Lord Nightshade.")
    assert not sidebar.options_description.suggestion_shown
    assert sidebar.setup_description.text() == "Farms Lord Nightshade."

    sidebar.options_description.set_text("")
    assert sidebar.options_description.suggestion_shown
    assert sidebar.setup_description.text() == _NIGHTSHADE


def test_an_authored_description_is_shown_instead_of_the_generated_one(sidebar):
    sidebar.set_editable(True)
    sidebar.set_config(_boss_config(description="Farms Lord Nightshade."))

    assert sidebar.setup_description.text() == "Farms Lord Nightshade."
    assert not sidebar.options_description.suggestion_shown


def test_a_graph_edit_regenerates_the_description(sidebar):
    config = _boss_config()
    sidebar.set_config(config)

    config.states[1].params["enemy"] = ""
    sidebar.refresh_options(config)

    assert sidebar.setup_description.text() == "Completes Nightshade's Tower, Wizard City."


def test_a_graph_implying_nothing_still_says_it_has_no_description(sidebar):
    sidebar.set_editable(True)
    sidebar.set_config(_config())

    assert sidebar.setup_description.text() == "This bot has no description."
    assert not sidebar.options_description.suggestion_shown


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
# Not rebuilding what did not change
# ---------------------------------------------------------------------------


def test_a_graph_edit_that_changes_no_option_rebuilds_nothing(sidebar):
    """``refresh_options`` runs on every config change, and building the rows costs
    ~55ms on a 67-state bot.  Most edits touch no node carrying a ``BotOptionSpec``,
    so the composed list is the one already on screen; composing it to find that out
    costs 0.05ms."""
    config = _config(options=[BotOptionModel(name="repeat", type="bool", default=True)])
    sidebar.set_config(config)
    before = sidebar.setup.option_widgets
    declarations = sidebar.options.option_widgets
    assert before and declarations  # otherwise the assertions below hold vacuously

    config.states.append(StateModel(id="b", node_type="mark_location", params={}))
    sidebar.refresh_options(config)

    # The same widget objects, not merely the same names: a rebuild replaces them.
    assert sidebar.setup.option_widgets == before
    assert sidebar.options.option_widgets == declarations


def test_an_edit_that_adds_a_required_option_does_rebuild(sidebar):
    """The reason the call exists: a node can bring a derived option with it."""
    config = _config()
    sidebar.set_config(config)

    config.states.append(StateModel(id="b", node_type="interact", params={}))
    sidebar.refresh_options(config)

    assert "Safety Delay" in sidebar.setup.option_widgets


def test_a_replacement_config_rebinds_even_when_it_says_the_same_thing(sidebar):
    """Undo/redo hands the canvas a brand-new ``BotConfig`` restored from a
    ``model_dump``, so its contents usually match the one before it.  The sections
    write through the object they hold: one left bound to the replaced config edits
    something nothing saves."""
    sidebar.set_config(_config(options=[BotOptionModel(name="repeat", type="bool", default=True)]))
    before = sidebar.setup.option_widgets

    replacement = _config(options=[BotOptionModel(name="repeat", type="bool", default=True)])
    sidebar.refresh_options(replacement)

    assert sidebar.setup.option_widgets != before
    assert sidebar.config is replacement


def test_save_as_re_resolves_the_rows_against_the_new_id(sidebar):
    """A fork inherits none of the original's stored values, so Setup must not go on
    showing them.  Same object, new id -- the one case identity cannot see."""
    config = _config(options=[BotOptionModel(name="repeat", type="bool", default=True)])
    sidebar.set_config(config)
    before = sidebar.setup.option_widgets

    config.id = "forked-bot"
    sidebar.refresh_options(config)

    assert sidebar.setup.option_widgets != before


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


# ---------------------------------------------------------------------------
# Folding a description out grows the section downwards
# ---------------------------------------------------------------------------
#
# A SidebarSection whose content is height-for-width -- which an option row's
# word-wrapped description makes it -- asks the layout for a cell taller than its
# own sizeHint.  Capped at that sizeHint by a Maximum size policy it could not
# fill the cell, and QWidgetItem centres what cannot fill: the section, and its
# body inside it, slid *down* as they grew.


def _setup_geometry(sidebar: LeftSidebar):
    """``(y of the Setup section, its height, y of its body)`` inside the scroll content."""
    qapp = QApplication.instance()
    for _ in range(5):
        qapp.processEvents()
    section = sidebar.setup
    content = sidebar.widget(0).widget()
    top = section.mapTo(content, section.rect().topLeft()).y()
    body_top = section.body.mapTo(content, section.body.rect().topLeft()).y()
    return top, section.height(), body_top


def _wordy_config() -> BotConfig:
    # Long enough, and enough of them, that unfolding overflows the viewport: the
    # scrollbar that appears narrows the content, and the section's height-for-width
    # then exceeds the width-agnostic sizeHint that used to cap it.
    prose = (
        "A description long enough to wrap onto several lines at a sidebar width, which is "
        "what makes the section height-for-width in the first place, and long enough again "
        "that the height it needs depends on the width it is given. "
    ) * 2
    return _config(
        options=[BotOptionModel(name=f"Option {i}", type="bool", default=False, description=prose) for i in range(12)]
    )


def test_showing_descriptions_grows_the_setup_section_downwards(sidebar):
    sidebar.resize(400, 600)
    sidebar.set_config(_wordy_config())
    sidebar.setup.set_descriptions_visible(False)
    sidebar.show()
    try:
        folded_top, folded_height, folded_body_top = _setup_geometry(sidebar)

        sidebar.setup.set_descriptions_visible(True)
        shown_top, shown_height, shown_body_top = _setup_geometry(sidebar)

        assert shown_height > folded_height, "the descriptions did not actually unfold"
        assert shown_top == folded_top
        assert shown_body_top == folded_body_top
    finally:
        sidebar.setup.set_descriptions_visible(False)
        sidebar.hide()
