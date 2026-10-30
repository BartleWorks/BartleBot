"""A deck's archmastery, drawn as the game's own two icons instead of a sentence.

``Archmastery: Fire · always set`` is four words and a separator saying what the game
says with a mark and a medallion, in a sidebar whose deck cards are otherwise all
picture. So where the icons have been extracted this replaces that line; where they
have not, the card keeps the line, which is the same fallback every other art surface
makes (see "Game art is optional" in ``gui/CLAUDE.md``).

TWO ICONS, AND THE SECOND ONE IS A CLAIM
    The first is ``Icon_Archmastery`` -- the rainbow spiral that means *archmastery*,
    and nothing about which school. The second is the school: ``Art_School_<School>``,
    the same ringed medallion the game's own archmastery picker uses, which is what
    makes it read correctly beside the card art tiles rather than as a different app's
    iconography.

    **The medallion is only drawn when the school will actually be set.** A deck can
    carry a school it is never going to apply -- the override resolves through the app
    setting, and Off is the shipped default -- and a Fire medallion on a card whose
    apply leaves archmastery alone is a claim about the wizard that this deck does not
    make. ``Type_Global``, the game's own universal mark, stands in for that case: the
    deck remembers a school, and this apply means nothing by it.

The tooltip carries the sentence the icons replaced, so nothing is only available as a
picture.

WHERE IT SITS
    Inline on the deck card's header row, right of the player pill and left of the
    summary. Not out at the right-hand end: that end is the face verbs and the kebab,
    and a picture you cannot click, docked against a row of icons you can, invites the
    click it will not answer. The text fallback stays on a row of its own below, being
    a sentence rather than two glyphs.
"""

from __future__ import annotations

from bartleby.qt.widgets.labels.art_icon_view import ArtIconView
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QWidget

from bartlebot.game import card_art
from bartlebot.gui.widgets.cards.card_art_tile import art_icon

#: Height of both icons on the deck card. ``Icon_Archmastery`` is a 32px texture and
#: the game ships nothing larger, so this is the ceiling rather than a preference --
#: 20px keeps the row the height of the text line it replaces and stays inside it.
ICON_SIZE = 20


def extracted_icon(name: str) -> QIcon | None:
    """One extracted icon by its key, or None if it is not on this machine."""
    path = card_art.icon_path(name)
    return None if path is None else art_icon(path)


def school_icon_for(school: str | None, will_set: bool) -> QIcon | None:
    """The medallion to draw beside the archmastery mark, or None if it cannot be.

    The universal mark whenever the school will not be set -- see the module docstring
    for why naming the school there would be wrong rather than merely redundant.
    """
    if not school:
        return None
    if not will_set:
        return extracted_icon(card_art.UNIVERSAL_ICON)
    return extracted_icon(card_art.school_icon_name(school))


class ArchmasteryBadge(QWidget):
    """The archmastery mark and the school medallion, side by side.

    Reads no ``THEME`` value, so it needs neither ``ThemedMixin`` nor a
    ``changeEvent``: both icons are the game's own artwork and keep their own colors
    in either theme, exactly as the card art tiles do.
    """

    def __init__(self, size: int = ICON_SIZE, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._has_icons = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._mark = ArtIconView(size, self)
        self._school = ArtIconView(size, self)
        layout.addWidget(self._mark, 0)
        layout.addWidget(self._school, 0)
        # No trailing stretch, and Fixed rather than Preferred: this sits inline on the
        # deck card's header row, between the player pill and the summary, so it has to
        # hug its two icons rather than claim width the summary would otherwise use
        # before eliding.
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def has_icons(self) -> bool:
        """Whether both icons resolved, and so whether this may stand in for the text.

        False for a deck with no school, for an install with no extracted icons, and
        for an icon set missing the one school this deck names -- the caller shows its
        text line in all three cases, so none of them needs telling apart.
        """
        return self._has_icons

    def set_archmastery(self, school: str | None, will_set: bool, tooltip: str = "") -> None:
        """Draw *school*, or the universal mark when *will_set* is false.

        Both icons are set together or neither is: a lone archmastery spiral says only
        that the deck has an opinion, without saying what it is.
        """
        mark = extracted_icon(card_art.ARCHMASTERY_ICON) if school else None
        medallion = school_icon_for(school, will_set)
        self._has_icons = mark is not None and medallion is not None

        if self._has_icons:
            self._mark.set_icon(mark)
            self._school.set_icon(medallion)
        else:
            self._mark.set_icon(None)
            self._school.set_icon(None)
        self.setToolTip(tooltip)
