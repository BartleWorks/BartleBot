"""The Players tab as widgets: the drag, the buckets, the stripe, the copy.

``tests/test_player_card_text.py`` has everything decidable from a username and an index.
What needs a ``QApplication`` is the part that is genuinely widget behavior, and it is
where the sharp edges of this issue are:

- ``ClientManager.clients`` is hooked-only and contiguous, so a drag must not be able to
  land an active card among the unhooked ones. That is enforced by *which container holds
  what*, and by an unhooked card having no grip to press.
- the stripe has to arrive **late** — the account identity is learned inside the client
  loop and nothing signals it — so a card colored once in its constructor is the bug.
- a rebuild must not reset live state, because with a drag it is one gesture away.
- the copy affordance has to copy the value the row *displays*, and say that it did.

The drag itself is driven exactly as ``test_card_column_reorder.py`` drives it: real
mouse events on the grip, against real geometry, with the settle waited out.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

from bartlebot.gui.account_accent import account_accent, account_color_keys
from bartlebot.gui.game_embed.card_columns import CARD_SETTLE_MS
from bartlebot.gui.sidebar.player_card import PLACEHOLDER, XYZ_PLACEHOLDER, CopyValue, PlayerCard
from bartlebot.gui.sidebar.sections.client_section import ClientSection
from bartlebot.settings_manager import SettingsManager

ACCOUNTS = [
    {"username": "one@example.com", "alias": "One"},
    {"username": "two@example.com", "alias": "Two"},
    {"username": "three@example.com", "alias": "Three"},
]


class _FakeClient:
    """A client that exists only to be keyed by its window handle and laid out."""

    process_id = 1234
    title = "Wizard101"

    def __init__(self, handle: int) -> None:
        self.window_handle = handle


@pytest.fixture
def section(qapp):
    """A shown ``ClientSection`` and the host holding it alive.

    Shown and pumped because the drag is computed from real geometry: an unshown card has
    no height, so every insertion point resolves to the same slot.
    """

    def build(active: int = 3, unhooked: int = 0, height: int = 700):
        widget = ClientSection()
        host = QWidget()
        QVBoxLayout(host).addWidget(widget)
        host.resize(320, height)
        host.show()
        widget.set_all_clients(
            [_FakeClient(h) for h in range(1, active + 1)],
            [_FakeClient(100 + h) for h in range(unhooked)],
        )
        for _ in range(3):
            qapp.processEvents()
        return host, widget

    return build


# ---------------------------------------------------------------------------
# Driving a drag, lifted from test_card_column_reorder.py
# ---------------------------------------------------------------------------


def _event(kind, card, offset: QPoint, buttons):
    local = getattr(card, "frame", card).geometry().topLeft() + QPoint(5, 5) + offset
    return QMouseEvent(
        kind,
        local.toPointF(),
        card.mapToGlobal(local).toPointF(),
        Qt.MouseButton.LeftButton,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )


def _send(target, card, kind, dy: int, buttons) -> None:
    QApplication.sendEvent(target, _event(kind, card, QPoint(0, dy), buttons))


def _drag(qapp, card, dy: int, *, target=None) -> None:
    """Press on *card*'s grip, move it *dy* down, release, and wait for it to land."""
    target = card.grip if target is None else target
    _send(target, card, QEvent.Type.MouseButtonPress, 0, Qt.MouseButton.LeftButton)
    _send(target, card, QEvent.Type.MouseMove, dy, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    _send(target, card, QEvent.Type.MouseButtonRelease, dy, Qt.MouseButton.NoButton)
    QTest.qWait(CARD_SETTLE_MS * 2)


def _row_height(column) -> int:
    return column.cards[0].height() + column.scroll_area.widget().layout().spacing()


# ---------------------------------------------------------------------------
# The drag reaches reorder_clients
# ---------------------------------------------------------------------------


def test_a_drag_emits_the_permutation_the_client_list_is_reordered_by(qapp, section):
    """A permutation, not a swap. ``ClientManager.reorder_clients`` is what takes one."""
    host, clients = section()
    seen: list[list] = []
    clients.reordered.connect(seen.append)

    _drag(qapp, clients.column.cards[0], _row_height(clients.column))

    assert seen == [[1, 0, 2]]
    assert host


def test_the_permutation_reaches_reorder_clients(qapp, section):
    """The wiring ``AppController`` makes, asserted against the real method's contract.

    ``reorder_clients`` refuses a permutation that does not describe its list, so a drag
    that reported the wrong shape would be silently dropped rather than misordering
    anything — which is exactly why this is worth asserting at the seam.
    """
    from bartlebot.gui.game_embed.card_columns import apply_order

    host, clients = section()
    order: list[list] = []
    clients.reordered.connect(order.append)

    _drag(qapp, clients.column.cards[0], _row_height(clients.column))

    assert apply_order(["a", "b", "c"], order[0]) == ["b", "a", "c"]
    assert host


def test_the_cards_are_renumbered_rather_than_rebuilt_after_a_reorder(qapp, section):
    """The order arriving back is a re-key, not a rebuild — see ``PlayerCard.set_index``."""
    host, clients = section()
    first, second = clients.column.cards[0], clients.column.cards[1]

    clients.set_all_clients([second.client, first.client], [])

    assert clients.cards == [second, first], "both cards survived the rebuild"
    assert (second.index, first.index) == (0, 1)
    assert host


# ---------------------------------------------------------------------------
# Hooked and unhooked are two containers
# ---------------------------------------------------------------------------


def test_only_the_hooked_cards_are_in_the_column(qapp, section):
    """The column clamps a drop to the card list it holds, so it must hold only these.

    A single column holding both kinds would let a drag land an active card in the
    unhooked run, and ``ClientManager.clients`` cannot express a hole.
    """
    host, clients = section(active=2, unhooked=2)

    assert len(clients.column.cards) == 2
    assert all(not c.is_unhooked for c in clients.column.cards)
    assert host


def test_an_unhooked_card_has_no_grip_and_cannot_be_dragged(qapp, section):
    host, clients = section(active=2, unhooked=1)
    unhooked = [c for c in clients.cards if c.is_unhooked][0]
    seen: list[list] = []
    clients.reordered.connect(seen.append)

    assert unhooked.grip is None

    _drag(qapp, unhooked, -200, target=unhooked)

    assert seen == []
    assert host


def test_the_unhooked_section_appears_only_when_there_is_something_in_it(qapp, section):
    host, clients = section(active=2, unhooked=0)
    assert not clients.unhooked_section.isVisible()

    clients.set_all_clients([_FakeClient(1)], [_FakeClient(9)])
    qapp.processEvents()
    assert clients.unhooked_section.isVisible()

    clients.set_all_clients([_FakeClient(1)], [])
    qapp.processEvents()
    assert not clients.unhooked_section.isVisible()
    assert host


def test_a_card_that_changes_bucket_is_rebuilt(qapp, section):
    """Whether a card has a grip is fixed at construction, so a bucket change is a new card."""
    host, clients = section(active=1)
    before = clients.cards[0]

    clients.set_all_clients([], [before.client])

    after = clients.cards[0]
    assert after is not before
    assert after.is_unhooked and after.grip is None
    assert host


def test_a_hooked_card_carries_its_player_number_and_an_unhooked_one_does_not(qapp, section):
    """An unhooked client is not Player anything until it is hooked back in."""
    host, clients = section(active=1, unhooked=1)
    hooked, unhooked = clients.cards

    assert hooked.pill_text == "P1"
    assert unhooked.pill_text is None
    assert host


# ---------------------------------------------------------------------------
# The stripe
# ---------------------------------------------------------------------------


def test_the_stripe_appears_when_the_account_is_known(qapp, section):
    host, clients = section(active=1)
    SettingsManager.instance().accounts = ACCOUNTS
    clients.set_account_source(lambda client: "two@example.com")

    key = account_color_keys(ACCOUNTS)["two@example.com"]
    assert clients.cards[0].color_key == key
    assert account_accent(key) in clients.cards[0].styleSheet()
    assert host


def test_there_is_no_stripe_while_the_identity_is_unknown(qapp, section):
    """A window BartleBot did not launch gets no color, not a default one."""
    host, clients = section(active=1)
    SettingsManager.instance().accounts = ACCOUNTS
    clients.set_account_source(lambda client: None)

    assert clients.cards[0].color_key is None
    assert "border-left" not in clients.cards[0].styleSheet()
    assert host


def test_a_stripe_that_arrives_late_still_lands(qapp, section):
    """The one that matters. The identity is learned inside the client loop and signals
    nothing, so a card that resolved its color in its constructor never recovers."""
    host, clients = section(active=1)
    SettingsManager.instance().accounts = ACCOUNTS
    known: dict[int, str] = {}
    clients.set_account_source(lambda client: known.get(client.window_handle))

    assert clients.cards[0].color_key is None, "nothing is known yet"

    known[1] = "one@example.com"
    clients.refresh_account_colors()  # what every refresh tick does

    assert clients.cards[0].color_key == account_color_keys(ACCOUNTS)["one@example.com"]
    assert host


def test_the_player_pill_wears_the_stripe_s_color_and_keeps_its_text(qapp, section):
    """The same pill the Combat tab's deck card for this slot wears (#40)."""
    host, clients = section(active=1)
    SettingsManager.instance().accounts = ACCOUNTS
    known: dict[int, str] = {}
    clients.set_account_source(lambda client: known.get(client.window_handle))
    card = clients.cards[0]

    assert card._pill.color_key is None, "gray until the account is known"

    known[1] = "two@example.com"
    clients.refresh_account_colors()

    key = account_color_keys(ACCOUNTS)["two@example.com"]
    assert card._pill.color_key == key == card.color_key
    assert account_accent(key) in card._pill.styleSheet()
    assert card.pill_text == "P1"
    assert host


def test_an_unhooked_card_keeps_its_stripe(qapp, section):
    """Grayed, but the stripe names an account rather than a state, so it survives."""
    host, clients = section(active=0, unhooked=1)
    SettingsManager.instance().accounts = ACCOUNTS
    clients.set_account_source(lambda client: "three@example.com")

    card = clients.cards[0]
    assert account_accent(card.color_key) in card.styleSheet()
    assert host


# ---------------------------------------------------------------------------
# The copy affordance
# ---------------------------------------------------------------------------


@pytest.fixture
def clipboard(monkeypatch):
    """Capture what would go to the system clipboard, rather than writing to it."""
    written: list[str] = []
    monkeypatch.setattr("bartlebot.gui.sidebar.player_card.pyperclip.copy", written.append)
    return written


def _click(widget) -> None:
    widget.mouseReleaseEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPoint(5, 5).toPointF(),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def test_clicking_a_value_copies_what_the_row_displays(qapp, clipboard):
    row = CopyValue("Click to copy this zone name")
    row.set_value("WizardCity/WC_Streets/Interior")

    _click(row)

    assert clipboard == ["WizardCity/WC_Streets/Interior"]


def test_a_row_whose_copy_form_differs_copies_the_copy_form(qapp, clipboard):
    """XYZ reads at one decimal and pastes as JSON at two — see ``xyz_clipboard_text``."""
    row = CopyValue("Click to copy this position as JSON")
    row.set_value("X=1.0  Y=2.0  Z=3.0", '{"x": 1.0, "y": 2.0, "z": 3.0}')

    _click(row)

    assert clipboard == ['{"x": 1.0, "y": 2.0, "z": 3.0}']


def test_a_copy_confirms_itself(qapp, clipboard):
    """There was no feedback at all before: you pressed ``Copy`` and nothing changed."""
    row = CopyValue("Click to copy")
    row.set_value("Colossus Boulevard")
    assert not row.confirming

    _click(row)

    assert row.confirming
    assert clipboard == ["Colossus Boulevard"]


def test_the_confirmation_goes_away_on_its_own(qapp):
    from bartlebot.gui.sidebar import player_card

    row = CopyValue("Click to copy")
    row.set_value("Colossus Boulevard")
    row.copy()

    QTest.qWait(player_card.COPY_CONFIRM_MS + 200)

    assert not row.confirming


def test_a_placeholder_row_copies_nothing_and_confirms_nothing(qapp, clipboard):
    """An em-dash pasted into a bot param is a silently wrong value."""
    row = CopyValue("Click to copy this zone name")

    assert row.display_text == PLACEHOLDER
    assert not row.copyable
    assert row.copy() is False

    _click(row)

    assert clipboard == []
    assert not row.confirming


def test_a_card_starts_with_every_row_uncopyable(qapp):
    """Nothing has been read yet, so there is nothing on the card worth pasting anywhere."""
    card = PlayerCard(_FakeClient(1), 0)

    assert card.rows["XYZ"].display_text == XYZ_PLACEHOLDER
    assert not any(row.copyable for row in card.rows.values())


# ---------------------------------------------------------------------------
# Live state across a rebuild
# ---------------------------------------------------------------------------


def test_a_rebuild_keeps_the_freecam_toggle(qapp, section):
    """Reset on every reorder before the Players tab card rework, when a reorder was a rare pair of buttons.

    A drag makes it one gesture away and visible, so the cards are re-keyed by hwnd
    instead of rebuilt.
    """
    host, clients = section(active=2)
    card = clients.column.cards[0]
    card._set_freecam(True)

    clients.set_all_clients([c.client for c in clients.column.cards], [])

    assert clients.cards[0] is card
    assert card.in_freecam
    assert host


def test_a_rebuild_keeps_the_rows_that_were_already_read(qapp, section):
    host, clients = section(active=1)
    card = clients.cards[0]
    card.rows["Zone"].set_value("Colossus Boulevard")

    clients.set_all_clients([card.client], [])

    assert clients.cards[0].rows["Zone"].display_text == "Colossus Boulevard"
    assert host


# ---------------------------------------------------------------------------
# The verbs
# ---------------------------------------------------------------------------


def test_the_three_tools_are_on_the_face_and_in_the_menu(qapp, section):
    """This surface exists to run them, so none of them is hidden behind a right-click."""
    host, clients = section(active=1)
    card = clients.cards[0]

    assert [v.label for v in card._verbs()] == ["Entities", "UI Tree", "Unhook"]
    assert all(v.on_face for v in card._verbs())
    assert len(card.action_buttons) == 3
    assert all(b.isVisible() for b in card.action_buttons), "chrome is pinned open here"
    assert host


def test_an_unhooked_card_offers_rehook_where_a_hooked_one_offers_unhook(qapp, section):
    host, clients = section(active=1, unhooked=1)
    hooked, unhooked = clients.cards

    assert hooked.action_button("Unhook") is not None
    assert hooked.action_button("Rehook") is None
    assert unhooked.action_button("Rehook") is not None
    assert unhooked.action_button("Unhook") is None
    assert host


def test_the_unhook_verb_carries_the_index_it_currently_has(qapp, section):
    """It has to move with the pill after a reorder, not merely beside it."""
    host, clients = section(active=2)
    first = clients.column.cards[0]
    seen: list[int] = []
    clients.unhook_requested.connect(seen.append)

    clients.set_all_clients([clients.column.cards[1].client, first.client], [])
    first.action_button("Unhook").click()

    assert seen == [1]
    assert host


def test_a_run_locks_unhook_and_leaves_rehook_alone(qapp, section):
    """Unhooking mid-run pulls a client out from under the FSM; rehooking brings one back."""
    host, clients = section(active=1, unhooked=1)
    hooked, unhooked = clients.cards

    clients.set_bot_running(True)
    assert not hooked.action_button("Unhook").isEnabled()
    assert unhooked.action_button("Rehook").isEnabled()

    clients.set_bot_running(False)
    assert hooked.action_button("Unhook").isEnabled()
    assert host


def test_a_run_locks_the_drag_grip(qapp, section):
    """Reordering mid-run moves a running node's players under it (#15)."""
    host, clients = section(active=2)
    seen: list[list] = []
    clients.reordered.connect(seen.append)

    clients.set_bot_running(True)
    card = clients.column.cards[0]
    assert not card.grip.isEnabled()
    assert "bot is running" in card.grip.toolTip()
    _drag(qapp, card, _row_height(clients.column))
    assert seen == [], "a locked grip must not start a drag"

    clients.set_bot_running(False)
    assert card.grip.isEnabled()
    assert "bot is running" not in card.grip.toolTip()
    _drag(qapp, clients.column.cards[0], _row_height(clients.column))
    assert seen == [[1, 0]]
    assert host


def test_a_run_locks_both_freecam_buttons(qapp, section):
    """Detaching the camera or teleporting moves a client the FSM is driving."""
    host, clients = section(active=1)
    card = clients.cards[0]
    toggle, tp = card.freecam_buttons

    clients.set_bot_running(True)
    assert not toggle.isEnabled()
    assert not tp.isEnabled()

    card._set_freecam(True)
    assert not tp.isEnabled(), "entering freecam must not re-enable TP during a run"

    clients.set_bot_running(False)
    assert toggle.isEnabled()
    assert tp.isEnabled(), "TP comes back with the camera still detached"

    card._set_freecam(False)
    assert toggle.isEnabled()
    assert not tp.isEnabled()
    assert host


def test_exit_freecam_brings_players_back_without_teleporting(qapp, section):
    """Run start: the camera returns to the player, and the player does not move."""
    host, clients = section(active=2)
    in_cam, normal = clients.column.cards
    for card in (in_cam, normal):
        card.client.camera_elastic = AsyncMock()
        card.client.teleport = AsyncMock()
    in_cam._set_freecam(True)

    assert asyncio.run(clients.exit_freecam()) == 1

    assert not in_cam.in_freecam
    in_cam.client.camera_elastic.assert_awaited_once()
    in_cam.client.teleport.assert_not_awaited()
    normal.client.camera_elastic.assert_not_awaited()
    assert host


def test_a_client_that_cannot_be_reached_stays_in_freecam(qapp, section):
    """Nothing says the camera came back, so the card must not claim it did."""
    host, clients = section(active=1)
    card = clients.cards[0]
    card.client.camera_elastic = AsyncMock(side_effect=RuntimeError("disconnected"))
    card._set_freecam(True)

    assert asyncio.run(clients.exit_freecam()) == 0
    assert card.in_freecam
    assert host


def test_a_card_that_arrives_mid_run_is_locked_too(qapp, section):
    """A client hooked during a run gets a fresh card, and it must not arrive draggable."""
    host, clients = section(active=1)

    clients.set_bot_running(True)
    clients.set_all_clients([clients.cards[0].client, _FakeClient(9)], [])

    assert [card.grip.isEnabled() for card in clients.column.cards] == [False, False]
    assert host


def test_the_body_of_a_player_card_is_not_a_button(qapp, section):
    """MEMBER mode, not MANAGE: there is no "open this player" for a body click to mean."""
    host, clients = section(active=1)
    card = clients.cards[0]
    activated: list[int] = []
    card.activated.connect(lambda: activated.append(1))

    _send(card, card, QEvent.Type.MouseButtonPress, 0, Qt.MouseButton.LeftButton)
    _send(card, card, QEvent.Type.MouseButtonRelease, 0, Qt.MouseButton.NoButton)

    assert activated == []
    assert not card.has_body_verb
    assert card.pins_chrome and not card.refuses_when_unfit
    assert host


# ---------------------------------------------------------------------------
# The tab itself
# ---------------------------------------------------------------------------


def test_the_players_tab_has_exactly_one_scroll_area(qapp):
    """Two nested ones give two competing scrollbars — the Drops tab already avoids this."""
    from PyQt6.QtWidgets import QScrollArea

    from bartlebot.gui.sidebar.right_sidebar import RightSidebar

    sidebar = RightSidebar()
    sidebar.clients.set_all_clients([_FakeClient(1)], [])

    assert len(sidebar.widget(0).findChildren(QScrollArea)) == 1


def test_the_column_draws_no_heading_of_its_own(qapp, section):
    """The tab label already says "Players"; a bold heading under it says the word twice."""
    from bartlebot.gui.widgets.themed_label import ThemedTextLabel

    host, clients = section(active=1)

    headings = clients.column.findChildren(ThemedTextLabel, options=Qt.FindChildOption.FindDirectChildrenOnly)
    assert [w.text() for w in headings] == []
    assert clients.column.add_card is None, "there is nothing to add to a list of clients"
    assert host
