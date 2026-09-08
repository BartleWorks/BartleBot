"""A palette slot as an actual colour, resolved against the live theme.

The half of the account-colour feature that cannot leave ``gui/``: it reads ``THEME``,
so it is presentation, and a live-play tool colouring the same accounts has a theme of
its own. Which slot an account is in — the pins, the derivation, the de-duplication
that makes a screenful of accounts distinct — is arithmetic over the account list with
no colour in it, and lives in :mod:`bartlebot.shared.account_slots`.

Both halves are re-exported here, so a widget that needs a slot *and* its colour has
one import. ``account_slots`` is where they are defined.
"""

from __future__ import annotations

from bartlebot.gui.theme import THEME
from bartlebot.shared.account_slots import (
    ACCOUNT_PALETTE_SIZE,
    account_color_key,
    account_color_keys,
    pinned_color_key,
)

__all__ = [
    "ACCOUNT_PALETTE_SIZE",
    "TINT_ALPHA_PERCENT",
    "account_accent",
    "account_color_key",
    "account_color_keys",
    "account_tint",
    "pinned_color_key",
]

#: Alpha of the tint behind a pill, as a QSS percentage. Low enough that the
#: card's own hover colour still reads through it, high enough to be a colour
#: rather than a smudge at 14px.
TINT_ALPHA_PERCENT = 22


def account_accent(key: int) -> str:
    """Resolve a palette slot to its colour against the live theme."""
    return getattr(THEME, f"account_{key}")


def account_tint(key: int, percent: int = TINT_ALPHA_PERCENT) -> str:
    """A slot's colour as a QSS ``rgba(...)`` fill at *percent* alpha.

    Alpha rather than a colour blended against the surface: a launch card changes
    its background on hover, and a pre-blended fill would show the un-hovered card
    as a rectangle inside the hovered one.
    """
    colour = account_accent(key).lstrip("#")
    r, g, b = (int(colour[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {percent}%)"
