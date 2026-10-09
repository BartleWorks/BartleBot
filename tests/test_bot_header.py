"""Bar B — the header above the graph, and Bar A above the game windows.

The split these test is the point of the runtime-config split: the bar over the graph owns the *bot*,
the bar over the game windows owns the *clients*.  Name and run status used to be
in the second one, where they disappeared with the game view.

``BotHeader`` also absorbed what the Bot tab used to hold, so the metadata
behavior that lived in ``test_tag_picker`` — pills in Run mode, chips in Edit —
is still exercised there against this class.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QResizeEvent
from PyQt6.QtWidgets import QApplication, QWidget

from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.gui.canvas.bot_header import BotHeader, _ElidedLabel
from bartlebot.gui.game_embed.game_view_panel import client_summary
from bartlebot.gui.widgets.pills.tag_pill import SuggestedTagPill, TagPill


def _config(**overrides) -> BotConfig:
    data = {
        "id": "test-bot",
        "name": "Jade Oni",
        "version": "2.1",
        "authors": "Somebody",
        "initial": "a",
        "recovery_state": "a",
        "states": [StateModel(id="a", node_type="mark_location", params={})],
        "transitions": [],
    }
    data.update(overrides)
    return BotConfig(**data)


def _squeeze(label: _ElidedLabel, width: int) -> None:
    """Narrow *label* and deliver the resize.

    A hidden widget banks its resize until it is shown, and these never are — so the
    event that drives the eliding has to be sent by hand.
    """
    old = label.size()
    label.resize(width, 16)
    QApplication.sendEvent(label, QResizeEvent(label.size(), old))


@pytest.fixture
def header(qapp) -> BotHeader:
    """A header with three throwaway clusters standing in for the toolbar buttons."""
    return BotHeader(QWidget(), QWidget())


# ---------------------------------------------------------------------------
# What the header says
# ---------------------------------------------------------------------------


def test_an_empty_canvas_says_so_and_shows_no_tag_row(header):
    """The library panel is what fills the canvas then; a tag row for nothing is noise."""
    assert header.name_text == "No bot loaded"
    assert not header._tag_row.isVisibleTo(header)


def test_loading_a_bot_fills_both_lines(header):
    header.set_config(_config(tags=["Gear", "Boss"]))
    header.set_file("jade_oni.json")

    assert header.name_text == "Jade Oni"
    assert header.version_text == "v2.1"
    assert header.file_text == "· jade_oni.json"
    assert header.byline_text == "by Somebody"
    assert header._tags_field.tags() == ["Gear", "Boss"]


def test_a_bot_with_no_version_gets_no_pill(header):
    """An empty pill is a gray smudge that reads as a missing value rather than none."""
    header.set_config(_config(version=""))

    assert not header._version_pill.isVisibleTo(header)


def test_loading_a_bot_does_not_mark_it_dirty(header):
    dirty: list[int] = []
    header.metadata_changed.connect(lambda: dirty.append(1))

    header.set_config(_config(tags=["Gear"]))

    assert not dirty


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------


def test_the_fields_are_labels_in_run_mode_and_editors_in_edit_mode(header):
    config = _config()
    header.set_config(config)

    header.set_editable(False)
    assert header._name_lbl.isVisibleTo(header)
    assert not header._name_edit.isVisibleTo(header)

    header.set_editable(True)
    assert not header._name_lbl.isVisibleTo(header)
    assert header._name_edit.isVisibleTo(header)


def test_typing_a_name_writes_it_back_and_reports_the_edit(header):
    config = _config()
    header.set_config(config)
    header.set_editable(True)
    dirty: list[int] = []
    header.metadata_changed.connect(lambda: dirty.append(1))

    header._name_edit.setText("Jade Oni Redux")

    assert config.name == "Jade Oni Redux"
    assert header.name_text == "Jade Oni Redux"
    assert dirty


def test_typing_a_version_moves_the_pill_with_it(header):
    config = _config()
    header.set_config(config)
    header.set_editable(True)

    header._version_edit.setText("3.0")

    assert config.version == "3.0"
    assert header.version_text == "v3.0"


def test_clearing_the_author_clears_the_byline(header):
    """``authors`` is optional, and an empty string in the file is not the same as
    absent — ``exclude_none`` is what drops it."""
    config = _config()
    header.set_config(config)
    header.set_editable(True)

    header._authors_edit.setText("")

    assert config.authors is None
    assert header.byline_text == ""


# ---------------------------------------------------------------------------
# Tags the graph suggests (#19)
# ---------------------------------------------------------------------------


def _reagent_bot(**overrides) -> BotConfig:
    """A reagent route with no combat: its graph suggests Reagents and Collecting."""
    return _config(states=[StateModel(id="a", node_type="collect_reagent", params={})], **overrides)


def test_suggestions_show_in_edit_mode_only(header):
    """A reminder for the author. In Run mode nobody is in a position to act on it."""
    header.set_config(_reagent_bot())

    header.set_editable(False)
    assert header._tags_field.shown_suggestions() == []
    header.set_editable(True)
    assert header._tags_field.shown_suggestions() == ["Reagents", "Collecting"]


def test_a_tag_the_bot_already_carries_is_not_suggested(header):
    header.set_config(_reagent_bot(tags=["Reagents"]))
    header.set_editable(True)

    assert header._tags_field.shown_suggestions() == ["Collecting"]


def test_clicking_a_suggestion_makes_it_an_ordinary_tag(header):
    config = _reagent_bot(tags=["Boss"])
    header.set_config(config)
    header.set_editable(True)
    dirty: list[int] = []
    header.metadata_changed.connect(lambda: dirty.append(1))

    ghost = next(g for g in header._tags_field.findChildren(SuggestedTagPill) if g.tag == "Reagents")
    ghost.click()

    assert config.tags == ["Reagents", "Boss"]
    assert header._tags_field.shown_suggestions() == ["Collecting"]
    assert dirty


def test_a_graph_edit_is_picked_up_on_refresh(header):
    """The canvas calls ``refresh_tag_suggestions`` after every graph or param edit."""
    config = _config(states=[StateModel(id="a", node_type="enter_dungeon", params={})])
    header.set_config(config)
    header.set_editable(True)
    assert header._tags_field.shown_suggestions() == []

    config.states[0].params["offers_team_up"] = True
    header.refresh_tag_suggestions()

    assert header._tags_field.shown_suggestions() == ["Team-Up"]


def test_refresh_tags_reads_tags_set_outside_the_header(header):
    """The save-time Rewards prompt writes the config directly, then asks the header to re-read it."""
    config = _config()
    header.set_config(config)

    config.tags = ["Pets"]
    header.refresh_tags()

    assert header._tags_field.tags() == ["Pets"]


def test_a_mode_switch_does_not_bring_back_an_empty_version_pill(header):
    header.set_config(_config(version=""))

    header.set_editable(True)
    header.set_editable(False)

    assert not header._version_pill.isVisibleTo(header)


# ---------------------------------------------------------------------------
# Run status
# ---------------------------------------------------------------------------


def test_the_clock_zeroes_rather_than_disappearing_when_the_run_stops(header):
    """A clock that came and went would shove the status word sideways at the one
    moment the user is watching it."""
    header.set_status("#00ff00", "Running", "00:01:23")
    assert (header.status_text, header.elapsed_text) == ("Running", "00:01:23")

    header.set_status("#888888", "Idle", "")
    assert (header.status_text, header.elapsed_text) == ("Idle", "00:00:00")
    assert header._elapsed_lbl.isVisibleTo(header)


def test_a_theme_switch_keeps_the_status_it_was_last_told(header, qapp):
    """The dot's color is resolved by the canvas, so a restyle must not clear it."""
    header.set_status("#00ff00", "Running", "00:00:07")

    header.refresh_theme_styles()

    assert (header.status_text, header.elapsed_text) == ("Running", "00:00:07")


# ---------------------------------------------------------------------------
# Bar A — the clients
# ---------------------------------------------------------------------------


def test_the_client_summary_counts_and_names_the_profile():
    assert client_summary(4, "Four Wizards") == "4 clients connected · Four Wizards"


def test_one_client_is_singular():
    assert client_summary(1, None) == "1 client connected"


def test_no_clients_does_not_name_a_profile():
    """ "0 clients · Four Wizards" reads as a claim that the profile is running."""
    assert client_summary(0, "Four Wizards") == "No clients connected"


# ---------------------------------------------------------------------------
# The three columns
# ---------------------------------------------------------------------------


def test_the_run_controls_sit_between_the_metadata_and_the_view_tools(header):
    """Grouped by subject: the bot on the left, its controls in the middle, the
    tools that act on the *view* out with the mode toggle on the right."""
    grid = header.layout()

    assert grid.columnStretch(0) == grid.columnStretch(2) == 1
    assert grid.columnStretch(1) == 0


def test_both_rows_of_the_metadata_are_in_the_metadata_column(header):
    grid = header.layout()

    assert grid.getItemPosition(grid.indexOf(header._tag_row))[:2] == (1, 0)


def test_the_author_shares_line_1_and_the_tags_get_line_2_to_themselves(header):
    """Line 2 is the only thing in the bar that grows with the bot, so it gets the
    width to itself.

    Measured against the 87 shipped bots at 1600px, 83 of them wrapped their tag row
    in Edit mode with the author sharing it and 13 without; at 1400 it is 86 against
    35.  The author is a fixed 180px field that never grows, so line 1 -- where
    everything else either has a fixed width or elides -- is where it costs nothing.
    """
    title = header._name_slot.parentWidget()

    assert header._authors_slot.parentWidget() is title
    assert header._tags_field.parentWidget() is header._tag_row


def test_the_two_button_clusters_are_level_with_each_other(header):
    """The status used to sit under the transport, which left the view tools as the
    only cluster with an empty row beneath them -- so they centered in row 1 and read
    as sitting high.  Neither column stacks anything now, so both span the metadata's
    two rows and land on the bar's own center."""
    grid = header.layout()

    run = grid.getItemPosition(grid.indexOf(grid.itemAtPosition(0, 1).widget()))
    tools = grid.getItemPosition(grid.indexOf(grid.itemAtPosition(0, 2).widget()))

    assert run[:1] == tools[:1] == (0,)  # same row
    assert run[2:] == tools[2:] == (2, 1)  # both two rows deep


def test_the_clock_and_the_status_reserve_the_same_width(header):
    """Equal slots either side are what put the transport in the middle of its own
    column, so that centring the column centers the buttons."""
    assert header._elapsed_lbl.minimumWidth() == header._status_side.minimumWidth() > 0


def test_a_longer_status_word_does_not_move_the_run_buttons(qapp):
    """``Idle`` -> ``Running`` on a free-width label would shuffle play and stop
    sideways at the one moment the user is watching them -- the drift the equal-stretch
    side columns exist to keep out of the bar, re-introduced in its middle."""
    transport = QWidget()
    transport.setFixedSize(64, 32)
    header = BotHeader(transport, QWidget())
    header.resize(900, 48)
    # Shown, because an unshown header never applies the geometry its layout computed
    # and every child reports the same default rectangle -- which the assertion below
    # would read as "nothing moved".
    header.show()

    header.set_status("#888888", "Idle", "")
    header.layout().activate()
    idle_x = transport.mapTo(header, transport.rect().topLeft()).x()

    header.set_status("#00ff00", "Running", "00:01:23")
    header.layout().activate()

    assert transport.mapTo(header, transport.rect().topLeft()).x() == idle_x
    # And centered *within its own column*, which is what the equal slots buy: where
    # that column then sits is the side columns' business, not this one's.
    column = transport.parentWidget()
    assert abs(transport.geometry().center().x() - column.rect().center().x()) <= 1
    header.close()


def test_a_long_caption_shortens_rather_than_shoving_the_run_button_off_center(qapp):
    """The filename and the author both use this; between them they are what would
    otherwise set the width of the whole left column."""
    label = _ElidedLabel("a_very_long_bot_filename_that_will_not_fit_in_the_bar.json")
    wide = label.sizeHint().width()

    _squeeze(label, wide // 4)

    assert label.full_text().endswith(".json")  # what it holds is untouched
    assert label.text() != label.full_text()  # what it draws is elided
    assert label.toolTip() == label.full_text()  # and the whole of it is still reachable


def test_an_elided_label_grows_back_when_the_room_returns(qapp):
    """QLabel sizes itself from ``text()``, which here is already shortened -- so a
    label that had elided once would keep asking for the shorter width forever."""
    label = _ElidedLabel("a_long_caption_that_will_be_squeezed.json")
    wide = label.sizeHint().width()

    _squeeze(label, wide // 4)

    assert label.sizeHint().width() == wide


def test_the_tags_stay_on_one_line_rather_than_stacking(header, qapp):
    """Two pills stacking is the bug that made the bar taller than it needed to be."""
    header.set_config(_config(tags=["Gear", "Boss"]))
    header.resize(1400, 60)
    header.layout().activate()

    pills = header._tags_field._pill_host.findChildren(TagPill)
    assert len(pills) == 2
    assert pills[0].y() == pills[1].y()


def _tall_header(px: int) -> BotHeader:
    """A header whose spanning clusters force the metadata rows taller than their text."""
    from PyQt6.QtWidgets import QHBoxLayout, QPushButton

    def cluster() -> QWidget:
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        btn = QPushButton()
        btn.setFixedSize(px, px)
        row.addWidget(btn)
        return w

    return BotHeader(cluster(), cluster())


def test_the_tag_row_centers_in_its_row_however_tall_the_row_gets(qapp):
    """``FlowLayout`` lays its rows from the top of whatever space it is handed.

    A box layout stretches a child to the full row by default, so once the spanning
    button clusters made the grid row taller than the tag row itself, the tag pills
    sat against the row's ceiling with all the slack underneath.
    """
    header = _tall_header(70)
    header.set_config(_config(tags=["Gear", "Boss"]))
    header.resize(1400, header.sizeHint().height())
    header.show()
    header.layout().activate()

    row = header.layout().cellRect(1, 0)
    tags = header._tags_field
    top = tags.mapTo(header, tags.rect().topLeft()).y()

    assert tags.height() == tags.sizeHint().height(), "stretched, not centered"
    assert abs((top + tags.height() / 2) - (row.y() + row.height() / 2)) <= 1


def test_the_filename_and_author_sit_on_the_bot_name_s_baseline(qapp):
    """Centring matches the boxes, and a font box is mostly ascent -- so the smaller
    of two texts centered in one row carries its baseline a pixel above the other's.
    The eye compares baselines, so that reads as a filename floating off the line.

    The author is the same size as the filename, so those two out of line with *each
    other* is the more jarring of the two: nothing about them says they should sit
    differently.  It read a pixel high because it is centered inside a ``_Swappable``
    rather than in the row, which the original shift missed."""
    from PyQt6.QtGui import QFontMetrics

    header = _tall_header(32)
    header.set_config(_config())
    header.set_file("jade_oni.json")
    header.resize(1400, header.sizeHint().height())
    header.show()
    header.layout().activate()

    def baseline(widget: QWidget) -> float:
        metrics = QFontMetrics(widget.font())
        pad = widget.contentsMargins().top()
        centered = (widget.height() - pad - metrics.height()) // 2 if pad == 0 else 0
        return widget.mapTo(header, widget.rect().topLeft()).y() + pad + centered + metrics.ascent()

    assert baseline(header._file_lbl) == baseline(header._name_lbl)
    assert baseline(header._authors_lbl) == baseline(header._name_lbl)


# ---------------------------------------------------------------------------
# The bar's height does not move with the mode  (#83)
#
# The toolbar flickered on every Run/Edit switch.  Measured, the bar passed through
# four heights per round trip and did not always come back: an untagged bot went
# 38 -> 53 -> 56 and *stayed* 56, eighteen pixels too tall, until an unrelated
# resize put it right.  Two causes, both fixed by making the height constant rather
# than by re-measuring after the fact.
#
# The bar's height across a mode switch is measured in ``test_canvas_toolbar_layout``:
# it only goes wrong through the canvas, whose layout is what reads the stale hint.
# ---------------------------------------------------------------------------


def test_the_tag_field_reserves_the_height_of_its_editable_form(header):
    """The "Add…" buttons are taller than the pills they replace.

    Left unreserved, that difference made the whole bar a pixel taller in Edit mode —
    and because the canvas reads the bar's height one event-loop pass stale, that pixel
    stuck on the way back out.
    """
    header.set_config(_config(tags=[]))
    tags = header._tags_field

    header.set_editable(False)
    run = tags.sizeHint().height()
    header.set_editable(True)
    edit = tags.sizeHint().height()

    assert run == edit
    assert tags.minimumHeight() >= tags._buttons.sizeHint().height()


def test_typing_a_version_does_not_bring_the_pill_up_beside_the_field(header):
    """The pill and its editor are two states of one slot, never both at once.

    ``_apply_version`` showed the pill whenever the version was non-empty, which in
    Edit mode meant the read-only pill appeared next to the field being typed into —
    and grew the bar by its height while it was there.
    """
    header.set_config(_config(version=""))
    header.set_editable(True)

    header._version_edit.setText("3.0")

    assert header._version_edit.isVisibleTo(header)
    assert not header._version_pill.isVisibleTo(header)


def test_a_slot_is_only_as_wide_as_the_field_it_is_showing(header):
    """The stack must not claim the width of the editor it is not showing.

    A ``QStackedLayout`` reports the widest of *every* page, showing or not — unlike a
    box layout, which skips a hidden child.  Left alone, Run mode sized each slot to its
    editor: the name's 240px minimum and the author field's fixed 180 pushed the version
    pill and the tag pills right by that much, and stretched the version pill across a
    slot several times its own text.  Height comes from both pages; width must not.
    """
    header.set_config(_config())
    header.set_editable(False)

    assert header._version_slot.sizeHint().width() == header._version_pill.sizeHint().width()
    assert header._authors_slot.sizeHint().width() == header._authors_lbl.sizeHint().width()

    header.set_editable(True)

    # Each page's own explicit bounds still apply: the version field is a fixed 60px and
    # the name field has a 240px minimum, and reporting a page's width upward must not
    # drop either the way an unclamped hint did.
    assert header._version_slot.sizeHint().width() == 60
    assert header._name_slot.sizeHint().width() >= header._name_edit.minimumWidth()


def test_a_slot_is_as_tall_as_its_taller_half_in_both_modes(header):
    """The other half of the same split, and the reason the stack is there at all."""
    header.set_config(_config())

    header.set_editable(False)
    run = header._authors_slot.sizeHint().height()
    header.set_editable(True)
    edit = header._authors_slot.sizeHint().height()

    assert run == edit
    assert run >= header._authors_edit.sizeHint().height()


def test_the_tag_pills_center_in_their_row_in_run_mode(header):
    """The floor that keeps the bar's height constant must not leave the pills high.

    The tag field reserves the height of its editable form in both modes, so in Run mode
    the pills are shorter than the rect they are handed — and a flow lays its rows from
    the top of that rect unless told otherwise.  The spare pixels all landed below the
    pills, putting them above the center of the row they are given.
    """
    header.set_config(_config(tags=["Gear"]))
    header.set_editable(False)
    header.resize(1200, header.sizeHint().height())
    header.show()
    header.layout().activate()

    field = header._tags_field
    pills = field._pill_host.findChildren(TagPill)
    assert pills, "no pills to measure"
    pill = pills[0]

    pill_center = pill.mapTo(field, pill.rect().center()).y()
    assert abs(pill_center - field.rect().center().y()) <= 1


def test_a_typical_bot_keeps_its_tags_on_one_line_in_edit_mode(header):
    """The width the author used to take off this row, stated as the thing it buys.

    Four tags is the 94th percentile of the 87 shipped bots, and the widest four-tag
    row among them (``the_great_spyre``) is the case measured here.  With the author
    sharing this row it wrapped at 1600px and above; the guard is Edit mode because
    that is where the three "Add..." buttons join the flow and the row is widest.

    A single line means ``height() == _floor()``: the floor is one row of the editable
    form, and wrapping is the only thing that takes the field past it.

    Off-screen, because a shown top-level window is clamped to its screen: CI's Windows
    runner has a 1024x768 display, so a plain ``resize(1920, ...)`` there laid the row
    out at about 1000px, where it does wrap, and the test failed on every push while
    passing on any dev machine with a wider monitor.
    """
    header.set_config(_config(tags=["Gear", "Reagents", "Spellements", "Dungeon"]))
    header.set_editable(True)
    header.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    header.resize(1920, header.sizeHint().height())
    header.show()
    header.layout().activate()

    field = header._tags_field
    assert field.height() == field._floor(), "the tag row wrapped at 1920px with four tags"


def test_the_version_pill_centers_against_the_bot_name(header):
    """A slot taller than the field it is showing must center it, not hang it from the top.

    The slot is as tall as the *editor* it is not showing — that is what keeps the bar's
    height off the mode — so in Run mode the 13px pill sits in a 22px cell.
    ``QStackedLayout`` has no alignment of its own and parks a short page at the top,
    which left the pill four and a half pixels above the name beside it.
    """
    header.set_config(_config())
    header.set_editable(False)
    header.resize(1200, header.sizeHint().height())
    header.show()
    header.layout().activate()

    title = header._name_slot.parentWidget()
    pill = header._version_pill
    pill_center = pill.mapTo(title, pill.rect().center()).y()
    name_center = header._name_lbl.mapTo(title, header._name_lbl.rect().center()).y()

    # Both halves, because each fails a different way.  Stretched to fill the cell, the
    # pill lands near the right center but reads as a tall gray smudge rather than a
    # pill; kept to its own height but hung from the top, it reads as misaligned.
    assert pill.height() == pill.sizeHint().height(), "the pill was stretched to fill its slot"
    assert abs(pill_center - name_center) <= 1, "the pill did not center against the name"
