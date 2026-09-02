"""Bar B — the header above the graph, and Bar A above the game windows.

The split these test is the point of #80: the bar over the graph owns the *bot*,
the bar over the game windows owns the *clients*.  Name and run status used to be
in the second one, where they disappeared with the game view.

``BotHeader`` also absorbed what the Bot tab used to hold, so the metadata
behaviour that lived in ``test_tag_picker`` — pills in Run mode, chips in Edit —
is still exercised there against this class.
"""

from __future__ import annotations

import sys

import pytest
from PyQt6.QtGui import QResizeEvent
from PyQt6.QtWidgets import QApplication, QWidget

from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.gui.canvas.bot_header import BotHeader, _ElidedLabel
from bartlebot.gui.game_embed.game_view_panel import client_summary
from bartlebot.gui.widgets.tag_pill import TagPill


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


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


def test_an_empty_canvas_says_so_and_shows_no_byline(header):
    """The library panel is what fills the canvas then; a byline for nothing is noise."""
    assert header.name_text == "No bot loaded"
    assert not header._byline.isVisibleTo(header)


def test_loading_a_bot_fills_both_lines(header):
    header.set_config(_config(tags=["Gear", "Boss"]))
    header.set_file("jade_oni.json")

    assert header.name_text == "Jade Oni"
    assert header.version_text == "v2.1"
    assert header.file_text == "· jade_oni.json"
    assert header.byline_text == "by Somebody"
    assert header._tags_field.tags() == ["Gear", "Boss"]


def test_a_bot_with_no_version_gets_no_pill(header):
    """An empty pill is a grey smudge that reads as a missing value rather than none."""
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
    """The dot's colour is resolved by the canvas, so a restyle must not clear it."""
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

    assert grid.getItemPosition(grid.indexOf(header._byline))[:2] == (1, 0)


def test_the_two_button_clusters_are_level_with_each_other(header):
    """The status used to sit under the transport, which left the view tools as the
    only cluster with an empty row beneath them -- so they centred in row 1 and read
    as sitting high.  Neither column stacks anything now, so both span the metadata's
    two rows and land on the bar's own centre."""
    grid = header.layout()

    run = grid.getItemPosition(grid.indexOf(grid.itemAtPosition(0, 1).widget()))
    tools = grid.getItemPosition(grid.indexOf(grid.itemAtPosition(0, 2).widget()))

    assert run[:1] == tools[:1] == (0,)  # same row
    assert run[2:] == tools[2:] == (2, 1)  # both two rows deep


def test_the_clock_and_the_status_reserve_the_same_width(header):
    """Equal slots either side are what put the transport in the middle of its own
    column, so that centring the column centres the buttons."""
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
    # And centred *within its own column*, which is what the equal slots buy: where
    # that column then sits is the side columns' business, not this one's.
    column = transport.parentWidget()
    assert abs(transport.geometry().center().x() - column.rect().center().x()) <= 1
    header.close()


def test_a_long_caption_shortens_rather_than_shoving_the_run_button_off_centre(qapp):
    """The filename and the byline both use this; between them they are what would
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


def test_the_tags_share_the_byline_rather_than_taking_a_row_of_their_own(header, qapp):
    """Line 2 is `by Author  <pills>`; two pills stacking is the bug that made the
    bar taller than it needed to be."""
    header.set_config(_config(tags=["Gear", "Boss"]))
    header.resize(1400, 60)
    header.layout().activate()

    pills = header._tags_field._pill_host.findChildren(TagPill)
    assert len(pills) == 2
    assert pills[0].y() == pills[1].y()
