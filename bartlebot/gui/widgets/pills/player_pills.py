"""``PlayerPillBar``: which player slots an assignment covers, as toggleable pills.

WHY PILLS AND NOT CHECKBOXES
----------------------------

The row this replaces was four ``QCheckBox``es plus an ``All`` and a ``None`` button —
seven controls, about 210px, in front of the one thing the row is actually about (what
these players should *do*). Four pills say the same thing in a third of the width, and
they say it in the shape the rest of the app already uses to name a thing that belongs
to somebody: ``AccountPill`` on a launch profile, ``TagPill`` on a bot card.

``All`` and ``None`` go with them. They existed because ticking four boxes is four
gestures; with the slots down to four small targets in a row, and exclusivity between
assignments enforced anyway, the two buttons were saving one click at the cost of being
the widest thing in the row.

WHY THE PILL STILL READS "P1"
-----------------------------

The color is the account's; the *text* is not. A ``DuelConfig`` stores player slots,
not accounts — it is authored offline, shared between bots, and outlives whichever
account happens to be in slot 1 tonight. A pill reading ``Farmer`` would be a claim the
stored data does not make, and the same card would read differently depending on whether
anything was hooked when it was opened.

So the slot is the label, always, and the account is the *color* — which is exactly the
job color is doing everywhere else in this app (see gui/theming/account_accent.py): a match
target, not an identifier. Hook four clients and the pills pick up the same four hues as
the player cards and the launch profile; hook nothing and they are simply uncolored.
The account's name is in the tooltip, which is where a claim that can go stale belongs.
"""

from __future__ import annotations

from collections.abc import Sequence

from bartleby.qt.art_signal import follow_art
from bartleby.qt.theming.account_accent import account_accent, account_tint
from bartleby.qt.theming.theme import THEME
from bartleby.qt.theming.themed import set_style_sheet
from bartleby.qt.widgets.labels.themed_label import ThemedTextLabel
from bartleby.qt.widgets.pills.player_badge import PlayerBadge
from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from bartlebot.gui.widgets.cards.duel_slot_gem import GEM_PX, DuelSlotGem

#: The pill's metrics. Deliberately ``AccountPill``'s, so a slot pill and an account pill
#: on the same screen are one shape at two sizes of claim rather than two shapes.
_PILL_HEIGHT = 18
_PILL_MIN_WIDTH = 30


class PlayerSlotPill(QLabel):
    """One ``P{n}`` pill: on or off, and colored by whoever is in that slot.

    Three states, and each is one the user has to be able to tell apart at 9px:

    * **on** — outlined and tinted, in the slot's account color when there is one and in
      the theme accent when there is not. This assignment covers that player.
    * **off** — a hairline in ``border`` over nothing, with muted text. Still legible as
      a slot that exists and could be turned on, which a hidden pill would not be.
    * **unavailable** — hidden outright, for a slot past the duel's ``max_players``.
      That is a *hard* limit, unlike the advisory "not currently connected" graying
      ``PlayerListWidget`` does: a two-player duel has no third player at all.

    Holds the palette slot rather than a color, like ``AccountPill``, so a theme switch
    re-resolves instead of repainting a stale hex.
    """

    toggled = pyqtSignal()

    def __init__(self, player: int, parent: QWidget | None = None) -> None:
        super().__init__(f"P{player}", parent)
        self._player = player
        self._on = False
        self._color_key: int | None = None
        self._editable = True
        self._applying_style = False
        self._account_label = ""
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(_PILL_HEIGHT)
        self.setMinimumWidth(_PILL_MIN_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._restyle()

    @property
    def player(self) -> int:
        """The 1-based slot this pill stands for."""
        return self._player

    @property
    def is_on(self) -> bool:
        return self._on

    @property
    def is_editable(self) -> bool:
        """Whether a click would toggle this pill. Off while a bot is running."""
        return self._editable

    def set_on(self, on: bool) -> None:
        """Set the state without emitting — how an external enforcement pass applies."""
        if on == self._on:
            return
        self._on = on
        self._restyle()

    def set_color_key(self, key: int | None) -> None:
        """Give this slot an account's palette slot, or ``None`` for nobody known."""
        if key == self._color_key:
            return
        self._color_key = key
        self._restyle()

    def set_account_label(self, label: str) -> None:
        """Name the account in this slot, for the tooltip. Empty when nobody is known."""
        self._account_label = label
        self._restyle()

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self._restyle()

    def _restyle(self) -> None:
        # Guard as in TagPill._restyle: setStyleSheet delivers StyleChange straight back
        # to changeEvent, and the recursion kills the app during construction.
        if self._applying_style:
            return
        self._applying_style = True
        try:
            if self._on:
                accent = account_accent(self._color_key) if self._color_key is not None else THEME.accent
                fill = account_tint(self._color_key) if self._color_key is not None else "transparent"
                text = THEME.text
            else:
                accent = THEME.border
                fill = "transparent"
                text = THEME.text_muted
            set_style_sheet(
                self,
                f"background: {fill}; color: {text}; font-size: {THEME.font_micro_px}px;"
                f" font-weight: bold; border-radius: {_PILL_HEIGHT // 2}px;"
                f" border: 1px solid {accent}; padding: 0px 6px;",
            )
            self.setCursor(Qt.CursorShape.PointingHandCursor if self._editable else Qt.CursorShape.ArrowCursor)
            self.setToolTip(self._tooltip())
        finally:
            self._applying_style = False

    def _tooltip(self) -> str:
        who = f" — {self._account_label}" if self._account_label else ""
        if not self._editable:
            return f"Player {self._player}{who} — locked while the bot is running"
        verb = "excluded from" if self._on else "included in"
        return f"Player {self._player}{who}. Click to have this player {verb} this assignment."

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.MouseButton.LeftButton and self._editable:
            self._on = not self._on
            self._restyle()
            # Accepted rather than passed on: this pill sits on a card whose own press
            # handling starts a reorder gesture, and a toggle that also arms a drag is a
            # pill you cannot click without the card twitching.
            event.accept()
            self.toggled.emit()
            return
        super().mousePressEvent(event)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._restyle()


class PlayerPillBar(QWidget):
    """The row of ``P1``…``P4`` pills, and the same value API ``PlayerListWidget`` has.

    ``get_value``/``set_value``/``set_max_players`` are spelled exactly as they are there,
    so a surface can swap one for the other without its own code changing — which is what
    lets the param forms keep their checkboxes (a form is a form) while the strategy cards
    take pills.
    """

    changed = pyqtSignal()

    def __init__(self, max_players: int = 4, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._max_players = max_players
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self._pills: list[PlayerSlotPill] = []
        for player in range(1, 5):
            pill = PlayerSlotPill(player, self)
            pill.toggled.connect(self.changed)
            row.addWidget(pill)
            self._pills.append(pill)
        self.set_max_players(max_players)

    @property
    def pills(self) -> list[PlayerSlotPill]:
        """Every pill, slot order, hidden ones included."""
        return list(self._pills)

    def get_value(self) -> list[int]:
        return [p.player for p in self._pills if p.player <= self._max_players and p.is_on]

    def set_value(self, value: list[int]) -> None:
        for pill in self._pills:
            pill.set_on(pill.player <= self._max_players and pill.player in value)

    def set_max_players(self, n: int) -> None:
        """Show only the first *n* slots; turn off and hide the rest — a hard limit."""
        self._max_players = n
        for pill in self._pills:
            if pill.player > n:
                pill.set_on(False)
            pill.setVisible(pill.player <= n)

    def set_color_keys(self, keys: list[int | None]) -> None:
        """Color each slot by the account behind it, ``None`` where nobody is known.

        Shorter lists are fine and are the normal case: two clients hooked colors P1 and
        P2 and leaves P3/P4 uncolored, which is the truth about what is running.
        """
        for index, pill in enumerate(self._pills):
            pill.set_color_key(keys[index] if index < len(keys) else None)

    def set_account_labels(self, labels: list[str]) -> None:
        """Name the account in each slot, for the pills' tooltips. See ``set_color_keys``."""
        for index, pill in enumerate(self._pills):
            pill.set_account_label(labels[index] if index < len(labels) else "")

    def set_editable(self, editable: bool) -> None:
        for pill in self._pills:
            pill.set_editable(editable)


# ---------------------------------------------------------------------------
# Read-only: naming a slot rather than toggling it
# ---------------------------------------------------------------------------

#: How many badges a row builds. A Wizard101 team is four wizards.
_MAX_SLOTS = 4


class PlayerBadgeRow(QWidget):
    """A run of :class:`PlayerBadge`\\ s, optionally joined by a separator such as ``→``.

    Built once to four badges and hidden down to what is shown, rather than rebuilt: the
    Combat tab redraws its cards on every pick, and destroying children mid-refresh is how
    that tab used to delete a widget a click was still travelling to.

    Colors are held per *slot*, not per position, so a combat order of ``P1 ← P3`` puts
    slot 3's account color on the right-hand badge -- the same hue P3 wears everywhere else.

    ``duel_order`` makes the row a *combat order* rather than a set of players: it is drawn
    right to left, and each position wears the game's own gem, exactly as
    :class:`~bartlebot.gui.sidebar.sections.combat_order_widget.CombatOrderWidget` draws the
    same value where it is edited. Off for the assignment lines, which name a set of players
    and have no order to mirror. See :mod:`bartlebot.gui.widgets.cards.duel_slot_gem`.
    """

    def __init__(self, separator: str = "", parent: QWidget | None = None, *, duel_order: bool = False) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)
        self._duel_order = duel_order
        self._badges: list[PlayerBadge] = []
        self._gems: list[DuelSlotGem] = []
        self._separators: list[ThemedTextLabel] = []
        self._players: list[int] = []
        #: What :meth:`set_players` was last handed, first to last.
        self._given: list[int] = []
        self._color_keys: list[int | None] = []
        self._account_labels: list[str] = []
        for index in range(_MAX_SLOTS):
            if index and separator:
                sep = ThemedTextLabel(separator, color="text_muted", parent=self)
                row.addWidget(sep, 0, Qt.AlignmentFlag.AlignVCenter)
                self._separators.append(sep)
            if duel_order:
                # The gem leads its badge, so "which position" is read before "who".
                gem = DuelSlotGem(index + 1, GEM_PX, self)
                row.addWidget(gem, 0, Qt.AlignmentFlag.AlignVCenter)
                self._gems.append(gem)
            badge = PlayerBadge(index + 1, self)
            row.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
            self._badges.append(badge)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.set_players([])
        if duel_order:
            # Which gems show is this row's to say, by how many players it holds.
            follow_art(self, lambda: self.set_players(self._given))

    @property
    def players(self) -> list[int]:
        """The slots currently shown, in display order."""
        return list(self._players)

    @property
    def badges(self) -> list[PlayerBadge]:
        """The shown badges, in display order."""
        return self._badges[: len(self._players)]

    def set_players(self, players: Sequence[int]) -> None:
        """Show *players*, always given first-to-last however the row draws them.

        A ``duel_order`` row reverses on the way onto the screen and nowhere else, so a
        caller hands over the same list it would for any other row and never has to know
        which way this one runs. :attr:`players` reports what is on screen, which for a
        mirrored row is the reverse of what came in.
        """
        self._given = list(players)
        self._players = list(players)[:_MAX_SLOTS]
        if self._duel_order:
            self._players.reverse()
        for index, badge in enumerate(self._badges):
            if index < len(self._players):
                badge.set_player(self._players[index])
            badge.setVisible(index < len(self._players))
        for index, gem in enumerate(self._gems):
            # The i-th badge from the left holds the (len - i)-th player to enter, and the
            # gem names that *position* -- not the player in it, and not the badge's index.
            if index < len(self._players):
                gem.set_slot(len(self._players) - index)
            else:
                gem.setVisible(False)
        for index, sep in enumerate(self._separators, start=1):
            sep.setVisible(index < len(self._players))
        self._apply_colors()

    def set_player_colors(self, keys: Sequence[int | None], labels: Sequence[str] = ()) -> None:
        """A palette slot and an account name per player slot, index 0 being P1.

        Shorter lists are the normal case -- two clients hooked says nothing about P3.
        """
        self._color_keys = list(keys)
        self._account_labels = list(labels)
        self._apply_colors()

    def _apply_colors(self) -> None:
        for badge in self.badges:
            index = badge.player - 1
            badge.set_color_key(self._color_keys[index] if 0 <= index < len(self._color_keys) else None)
            badge.set_account_label(self._account_labels[index] if 0 <= index < len(self._account_labels) else "")
