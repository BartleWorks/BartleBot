"""A game window Windows is scaling is held unembedded, and its tile offers the setting that stops it.

Embedded, a scaled window was bigger than its tile and showed nothing. Left where it is,
it draws correctly, so its slot says why it is not embedded and offers the one setting
that makes every later start of the game unscaled.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from bartleby.qt.game_embed import client_tile
from bartleby.qt.game_embed.client_tile import SCALED_FAILED_TEXT, SCALED_FIXED_TEXT, SCALED_TEXT, ScaledTile
from bartleby.runtime.clients.tile_slots import TileSlot
from bartleby.shared import game_dpi

from bartlebot.gui.game_embed.game_view import GameView

EXE = Path(r"C:\Games\Wizard101\Bin\WizardGraphicalClient.exe")
HWND = 4242


@pytest.fixture
def overrides(monkeypatch):
    """The exes whose override is on, standing in for the registry, and the window's exe."""
    on: set[Path] = set()
    monkeypatch.setattr(game_dpi, "window_executable", lambda hwnd: EXE)
    monkeypatch.setattr(game_dpi, "has_high_dpi_override", lambda exe: exe in on)
    monkeypatch.setattr(game_dpi, "set_high_dpi_override", on.add)
    return on


def _tile(qapp) -> ScaledTile:
    return ScaledTile("Alt", HWND)


def test_the_tile_says_why_and_offers_the_setting(qapp, overrides):
    tile = _tile(qapp)

    assert tile.text == SCALED_TEXT
    assert tile.offers_fix


def test_the_button_turns_the_override_on_and_says_to_restart(qapp, overrides):
    tile = _tile(qapp)

    tile._button.click()

    assert overrides == {EXE}
    assert tile.text == SCALED_FIXED_TEXT
    assert not tile.offers_fix


def test_with_the_override_already_on_it_only_says_to_restart(qapp, overrides):
    overrides.add(EXE)

    tile = _tile(qapp)

    assert tile.text == SCALED_FIXED_TEXT
    assert not tile.offers_fix


def test_with_no_exe_to_name_there_is_nothing_to_offer(qapp, overrides, monkeypatch):
    monkeypatch.setattr(game_dpi, "window_executable", lambda hwnd: None)

    tile = _tile(qapp)

    assert tile.text == SCALED_TEXT
    assert not tile.offers_fix


def test_a_write_that_fails_says_so_and_can_be_tried_again(qapp, overrides, monkeypatch):
    def refuse(exe):
        raise PermissionError("denied")

    monkeypatch.setattr(game_dpi, "set_high_dpi_override", refuse)
    tile = _tile(qapp)

    tile._button.click()

    assert tile.text == SCALED_FAILED_TEXT
    assert tile.offers_fix


def test_building_the_tile_never_shows_a_parentless_widget(qapp, overrides, parentless_shows):
    view = GameView()
    tile = ScaledTile("Alt", HWND, parent=view)
    tile.show()

    assert parentless_shows == []
    view.deleteLater()


# ---------------------------------------------------------------------------
# In the game view
# ---------------------------------------------------------------------------


@pytest.fixture
def game_view(qapp, overrides, monkeypatch):
    """A GameView with its two app-wide hooks disarmed, and every window scaled."""
    monkeypatch.setattr(game_dpi, "window_is_scaled", lambda hwnd: True)
    view = GameView()
    view._focus_timer.stop()
    qapp.removeEventFilter(view)
    yield view
    view.sync_tiles([])
    view.deleteLater()


def _window(slot: int = 0) -> TileSlot:
    return TileSlot(slot=slot, hwnd=HWND, label="Alt", player=None, username="alt")


def test_a_scaled_window_is_held_detached_rather_than_embedded(game_view):
    game_view.sync_tiles([_window()])

    assert isinstance(game_view._widget_at(0), ScaledTile)
    assert game_view._tiles == {}
    assert game_view.has_detached


def test_a_scaled_window_alone_offers_no_reattach(game_view):
    game_view.sync_tiles([_window()])

    assert game_view.overlay_button.isHidden()


def test_reattaching_a_scaled_window_leaves_it_where_it_is(game_view, monkeypatch):
    game_view.sync_tiles([_window()])
    monkeypatch.setattr(client_tile, "EmbeddedWindow", lambda hwnd: pytest.fail("a scaled window was embedded"))

    game_view.reattach_all()

    assert isinstance(game_view._widget_at(0), ScaledTile)


def test_the_slot_empties_when_the_window_goes(game_view):
    game_view.sync_tiles([_window()])

    game_view.sync_tiles([])

    assert game_view._widget_at(0) is None
    assert not game_view.has_detached
