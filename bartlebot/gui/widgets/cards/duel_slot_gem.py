"""The gold diamond the game marks each duel position with, as a widget.

WHY THE COMBAT ORDER READS RIGHT TO LEFT
----------------------------------------

A Wizard101 duel seats four wizards around a circle and takes turns clockwise, which
puts a team's own players **right to left**: slot 1 is the rightmost, slot 4 the
leftmost. The game's "Set Sigil Slot Preference" dialog lists them that way, and marks
each with one of four gold diamonds -- Sun, Eye, Star, Moon, first position to last.

This app used to lay the same four positions out left to right under the labels ``1st``
… ``4th``, which is the reading order of every other row in the GUI and the *opposite*
of what the user sees in the duel they are configuring. So the surfaces that show a
combat order are mirrored, and wear the game's own gems beside the ordinal. The ordinal
stays: the gems are the game's vocabulary, not this app's, and nothing on screen teaches
a user who has never opened that dialog that a purple moon means "last".

Two places draw this, and both have to agree or the mirroring is worse than not doing it
at all: :class:`~bartlebot.gui.sidebar.sections.combat_order_widget.CombatOrderWidget`
where the order is set, and
:class:`~bartlebot.gui.widgets.pills.player_pills.PlayerBadgeRow` where it is summarized.

EVERY GEM MAY BE NONE
    The art is extracted per machine (see :mod:`bartlebot.game.card_art`), so most installs
    have no gems at all. That is the ordinary case, not an error: the ordinal text
    carries the row on its own, and only the picture is missing. The *mirroring* is not
    conditional on the art -- it is a fact about the game either way.
"""

from __future__ import annotations

from functools import lru_cache

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QWidget

from bartlebot.game import card_art
from bartlebot.gui.widgets.cards.card_art_tile import ArtIconView, art_icon

#: How big a gem is drawn beside a label. The textures are 64px, so this is a preference
#: rather than a ceiling -- but below about 20px the four symbols stop resolving and the
#: gems read as four colored lozenges, which is still the whole of what they have to say
#: on a row that also carries the ordinal.
GEM_PX = 20


def duel_slot_gem(slot: int) -> QIcon | None:
    """A duel slot's gem at :data:`GEM_PX`, or None -- the ordinary case.

    Cached per :func:`card_art.cache_generation`, as ``tag_picker.tag_icon`` is and for
    the same reason: the combat order is rebuilt on every drag and every refresh, and
    each lookup otherwise stats a file.
    """
    return _gem(slot, card_art.cache_generation())


@lru_cache(maxsize=16)
def _gem(slot: int, _generation: int) -> QIcon | None:
    path = card_art.duel_slot_icon_path(slot)
    return None if path is None else art_icon(path)


def duel_slot_tooltip(slot: int) -> str:
    """What a gem says on hover: the position, its symbol, and which end of the row it is.

    Named out loud because the mirroring is the surprising part. A user who reads the row
    left to right sees P4 first and has no reason to guess that the rightmost chip is the
    one that enters first, and the arrow between badges is the only other thing saying so.
    """
    symbol = card_art.duel_slot_symbol(slot)
    where = {1: " — enters first, rightmost in the duel", card_art.DUEL_SLOTS: " — enters last, leftmost"}.get(slot, "")
    return f"Slot {slot} ({symbol}){where}" if symbol else f"Slot {slot}{where}"


class DuelSlotGem(ArtIconView):
    """One slot's gem, sized to :data:`GEM_PX` and tooltipped with its symbol.

    Reads no ``THEME`` value and so needs no ``changeEvent``: the gem is the game's own
    artwork and keeps its colors in either theme, exactly as ``ArchmasteryBadge`` does.

    Hidden outright when there is no art, rather than left as a :data:`GEM_PX` hole: the
    row it sits on is four columns wide and an empty column would push the ordinals out
    of line with the chips beneath them.
    """

    def __init__(self, slot: int, size: int = GEM_PX, parent: QWidget | None = None) -> None:
        super().__init__(size, parent)
        self._slot = 0
        self.set_slot(slot)

    @property
    def slot(self) -> int:
        """The 1-based duel position this gem stands for."""
        return self._slot

    def set_slot(self, slot: int) -> None:
        """Re-point this gem at another position, keeping the widget."""
        self._slot = slot
        icon = duel_slot_gem(slot)
        self.set_icon(icon)
        self.setToolTip(duel_slot_tooltip(slot))
        self.setVisible(icon is not None)
