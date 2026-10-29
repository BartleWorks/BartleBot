"""GameView's pending tiles: built, laid out, captioned and torn down.

The launch guard and the tiles it asks for are core's client manager, tested in
bartleby-core's ``tests/runtime/clients/test_launch_pending.py`` against the reconciler.
What needs a QApplication and BartleBot's ``GameView`` is here, until the game view moves
to core (Stage 9 of epic #167): the three dicts of tiles are consulted together in four
places, and a pending tile missing from any one of them is a tile drawn on top of another.
"""

from bartleby.runtime.clients.tile_slots import TileSlot


def _pending(slot: int, username: str) -> TileSlot:
    return TileSlot(slot=slot, hwnd=None, label=username.title(), player=None, username=username)


class TestGameViewPendingTiles:
    def test_a_pending_tile_is_built_and_shows_instead_of_the_launch_screen(self, qapp) -> None:
        from bartlebot.gui.game_embed.game_view import GameView

        view = GameView()
        view.resize(800, 600)
        view.sync_tiles([_pending(0, "alpha"), _pending(1, "beta")])

        assert view.client_count == 2
        assert view.pending_slots == {0: "alpha", 1: "beta"}
        # Sharing the grid means having a share of it: a tile left out of the layout sits
        # at its default geometry, on top of the tile in slot 0.
        assert view.tile_geometry(0) != view.tile_geometry(1)
        view.deleteLater()

    def test_a_tile_takes_its_account_color_on_sync_and_none_without_an_account(self, qapp) -> None:
        from bartleby.qt.theming.account_accent import account_color_keys

        from bartlebot.gui.game_embed.game_view import GameView
        from bartlebot.settings_manager import SettingsManager

        accounts = [{"username": "alpha"}, {"username": "beta"}]
        SettingsManager.instance().accounts = accounts
        view = GameView()
        view.resize(800, 600)
        anonymous = TileSlot(slot=1, hwnd=None, label="?", player=None, username=None)
        view.sync_tiles([_pending(0, "beta"), anonymous])

        assert view._widget_at(0).color_key == account_color_keys(accounts)["beta"]
        assert view._widget_at(1).color_key is None
        view.deleteLater()

    def test_a_launch_that_ends_with_no_window_leaves_nothing_behind(self, qapp) -> None:
        from bartlebot.gui.game_embed.game_view import GameView

        view = GameView()
        view.resize(800, 600)
        view.sync_tiles([_pending(0, "alpha")])
        view.sync_tiles([])

        assert view.pending_slots == {}
        assert view.client_count == 0
        assert view.tile_geometry(0) is None
        assert view.minimumSize().width() == 0
        view.deleteLater()


class TestPendingTileCaption:
    def test_a_stage_change_redraws_a_tile_that_did_not_move(self, qapp) -> None:
        """The reconciler reports nothing here -- the only thing that changed is the words."""
        from bartlebot.gui.game_embed.game_view import GameView

        view = GameView()
        view.resize(800, 600)
        view.sync_tiles([_pending(0, "alpha")])
        view.sync_tiles([TileSlot(slot=0, hwnd=None, label="Alpha", player=None, username="alpha", status="Loading")])

        assert view.pending_status(0) == "Loading…"
        view.deleteLater()


class TestFailedTileRendering:
    def test_a_failed_tile_asks_the_view_to_dismiss_its_account(self, qapp) -> None:
        from bartlebot.gui.game_embed.game_view import GameView

        view = GameView()
        view.resize(800, 600)
        asked: list[str] = []
        view.dismiss_launch_requested.connect(asked.append)
        view.sync_tiles(
            [
                TileSlot(
                    slot=0,
                    hwnd=None,
                    label="Alpha",
                    player=None,
                    username="alpha",
                    status="Launch failed",
                    failed=True,
                )
            ]
        )

        assert view.pending_failed(0) is True
        view._pending_tiles[0].dismiss_requested.emit()
        assert asked == ["alpha"]
        view.deleteLater()
