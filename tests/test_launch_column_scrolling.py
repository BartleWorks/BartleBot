"""The Quick Launch columns scroll only when there is something to scroll to (#191).

Both columns are built by ``_GamePlaceholder._make_scroll_area`` and filled by
``_rebuild_list``, and the invariant they have to hold is one sentence: a vertical
scroll bar appears exactly when the cards draw taller than the viewport.

It used to fail as soon as a card was greyed out -- which is to say whenever the game
resolution did not fit the space the launch screen had -- because ``set_unavailable``
turns word wrap on for the refusal line, and a wrapping ``QLabel``'s sizeHint height is
computed at a width unrelated to the column's. The cards were vertically ``Fixed``, so
that phantom height became their minimum, and ``setWidgetResizable`` will not let the
container be shorter than the sum of those minima. Two greyed cards drawing 208px asked
for a 468px container inside a 278px viewport: a scroll bar over 190px of nothing.

The tests measure what the cards actually draw rather than pinning any pixel count, so
they keep meaning if a font or a margin changes.
"""

import pytest
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from bartlebot.gui.game_embed.game_view import _GamePlaceholder
from bartlebot.gui.game_embed.launch_cards import AccountCard, AddCard, ProfileCard

ACCOUNT = {"username": "someone@example.com", "alias": "Main"}
PROFILE = {"name": "Farm Team", "accounts": ["someone@example.com"], "resolution": [1280, 720]}
ROSTER = [{"gid": 1001, "name": "Luke SkullHunter"}]

# The shape of a real refusal from screen_fit: long enough to wrap several times in a
# column this narrow, which is the whole mechanism.
REASON = (
    "4 clients at 1280x720 needs 2600x1500 of game area; this screen offers 1900x900. The largest that fits is 800x600."
)


def _cards(kind: str, count: int, reason: str | None) -> list[QWidget]:
    cards = []
    for i in range(count):
        if kind == "account":
            card = AccountCard(dict(ACCOUNT, alias=f"Main {i}"), ROSTER)
        else:
            card = ProfileCard(dict(PROFILE, name=f"Team {i}"), [ACCOUNT])
        card.set_unavailable(reason)
        cards.append(card)
    return cards


def _column(qapp, cards: list[QWidget], height: int, width: int = 300):
    """One Quick Launch column, laid out and shown at a fixed size.

    Shown because an unshown widget never applies the geometry its layout computed, and
    the scroll bar's visibility is exactly that geometry.

    Returns the host as well as the scroll area: the host is the only strong reference to
    the tree, and a caller that drops it takes every widget under it with it.
    """
    area = _GamePlaceholder._make_scroll_area()
    # A real column, Add card and all: it is the last row of every one of them, so the
    # invariant these tests hold has to hold with it in there.
    _GamePlaceholder._rebuild_list(area, cards, AddCard("Add account"), "An account is one login.")
    host = QWidget()
    QVBoxLayout(host).addWidget(area)
    host.resize(width, height)
    host.show()
    # Three passes: the show, the resize it provokes, and the re-fit of any card whose
    # own resizeEvent re-measures its text against the width it just got.
    for _ in range(3):
        qapp.processEvents()
    return host, area


def _drawn_height(area) -> int:
    """What the column occupies on screen, spacing included -- not what it asked for.

    Read off the layout rather than off the cards handed in, so the Add card and the
    empty column's hint are counted too: they are part of what the viewport has to hold.
    """
    layout = area.widget().layout()
    widgets = [layout.itemAt(i).widget() for i in range(layout.count())]
    widgets = [w for w in widgets if w is not None]
    return sum(w.height() for w in widgets) + layout.spacing() * max(0, len(widgets) - 1)


@pytest.mark.parametrize("kind", ["account", "profile"])
# Tall enough that three greyed cards and the Add card below them still fit -- this test
# is about a column with room to spare, and each height has to actually be one.
@pytest.mark.parametrize("count", [1, 2, 3])
@pytest.mark.parametrize("column_height", [400, 500, 900])
def test_a_column_with_room_to_spare_shows_no_scroll_bar(qapp, kind, count, column_height):
    """The bug itself: greyed cards that fit still raised a bar."""
    cards = _cards(kind, count, REASON)
    _host, area = _column(qapp, cards, column_height)

    drawn = _drawn_height(area)
    assert drawn <= area.viewport().height(), "test needs a column its rows fit inside"
    assert not area.verticalScrollBar().isVisible(), (
        f"{count} greyed {kind} cards draw {drawn}px in a {area.viewport().height()}px "
        f"viewport, yet the column asked for {area.widget().height()}px"
    )


@pytest.mark.parametrize("kind", ["account", "profile"])
@pytest.mark.parametrize("reason", [None, REASON], ids=["launchable", "greyed"])
def test_a_column_that_overflows_still_scrolls(qapp, kind, reason):
    """The half a policy flag would have broken: a real overflow must stay reachable."""
    cards = _cards(kind, 20, reason)
    _host, area = _column(qapp, cards, 300)

    drawn = _drawn_height(area)
    assert drawn > area.viewport().height(), "test needs more cards than the column holds"
    bar = area.verticalScrollBar()
    assert bar.isVisible()
    # Scrolling to the bottom must reach the last card rather than stopping short of it.
    assert bar.maximum() + area.viewport().height() >= drawn


@pytest.mark.parametrize("kind", ["account", "profile"])
@pytest.mark.parametrize("reason", [None, REASON], ids=["launchable", "greyed"])
@pytest.mark.parametrize("count", [0, 1, 2, 5, 20])
@pytest.mark.parametrize("column_height", [200, 900])
def test_the_bar_appears_exactly_when_the_cards_overflow(qapp, kind, reason, count, column_height):
    """The invariant, over the three cases #191 asks the fix to hold for: no cards, one
    card, and enough cards to genuinely need scrolling."""
    cards = _cards(kind, count, reason)
    _host, area = _column(qapp, cards, column_height)

    overflows = _drawn_height(area) > area.viewport().height()
    assert area.verticalScrollBar().isVisible() is overflows


@pytest.mark.parametrize("width", [160, 200, 240, 400])
def test_a_narrow_column_wraps_the_refusal_without_inventing_scroll(qapp, width):
    """Narrower means more wrapped lines, which is what used to inflate the phantom.

    The card has to grow to hold them -- the refusal is the thing the greyed card is for
    -- but the column must still be exactly as tall as what the cards draw.

    Profile cards, because they are the ones that still draw a refusal: an account card's
    reason is its whole column's and is shown once above it, so an account card carries no
    wrapping line to inflate anything.
    """
    cards = _cards("profile", 2, REASON)
    _host, area = _column(qapp, cards, 900, width=width)

    drawn = _drawn_height(area)
    assert drawn <= area.viewport().height()
    assert not area.verticalScrollBar().isVisible()


def test_an_account_card_that_can_launch_reserves_no_refusal_line(qapp):
    """The quiet line is hidden while empty, not merely blank.

    An empty QLabel still contributes a line to the card's minimum height while drawing
    nothing, and that difference is scroll range over blank space once a column holds
    enough cards -- the same defect as #191, a dozen pixels at a time.
    """
    launchable = _cards("account", 1, None)
    _host, _area = _column(qapp, launchable, 900)
    card = launchable[0]

    assert not card._quiet_lbl.isVisible()
    assert card.height() == card.minimumSizeHint().height()
