"""One hue per account, and the one module that decides which.

A launch profile's pills say which accounts it brings; a colour on them only
*associates* those pills with anything if the account cards carry the same colour.
So the mapping has to be shared, and — like ``group_accent`` for tag groups — it is
shared by living in one place rather than by two widgets agreeing.

WHERE A COLOUR COMES FROM

Three steps, in order:

1. An account may **pin** one, as a ``color`` slot in its settings entry. The picker
   in the account editor writes it, and honouring it first is what made that a
   UI change rather than a data migration. Picking *Automatic* removes the key rather
   than writing a value, so the account drops back to steps 2 and 3.
2. Otherwise a slot is **derived** from the username. The digest is ``blake2s``, not
   ``hash()``: Python salts string hashing per process, so ``hash()`` would repaint
   the whole launch screen on every restart.
3. The derived slots are then **de-duplicated across the whole account list**, which
   is what :func:`account_color_keys` is for.

Step 3 is not a refinement — without it the feature does not work. Six accounts
dropped independently into six slots collide the way birthdays do: the expected
number of *distinct* colours is under four, and the first fleet this was tried on
showed three. A palette that repeats is worse than no palette, because the repeat
invites a match that is wrong.

The cost of resolving collisions is that a colour becomes a property of the account
*list* rather than of one username. Two rules keep that from becoming a colour that
moves under the user:

* **Assignment order is the sorted usernames, not the list order.** Accounts can be
  dragged into any order in Settings, and reordering them is a presentational act —
  it must not repaint anything.
* **An uncontested preference is always honoured.** An account is moved only when
  something else wants the same slot, so adding or removing an account can only
  recolour the accounts it actually collides with.

More accounts than slots is the one case that must repeat. Then the least-used slot
wins, so nine accounts are one colour doubled rather than one colour tripled.

Streamer Mode is unaffected: the hue is derived from the username but shows none of
it, and it stays stable while the name on the pill is censored — which is the only
thing that makes two censored pills tellable apart.
"""

from __future__ import annotations

import hashlib
from collections import Counter

from bartlebot.gui.theme import THEME

#: Slots in the palette, numbered from 1 to match ``THEME.account_N``. Eight rather
#: than six so that a fleet larger than most still gets a colour each: the
#: de-duplication below has only as many distinct answers as there are slots.
ACCOUNT_PALETTE_SIZE = 8

#: Alpha of the tint behind a pill, as a QSS percentage. Low enough that the
#: card's own hover colour still reads through it, high enough to be a colour
#: rather than a smudge at 14px.
TINT_ALPHA_PERCENT = 22


def pinned_color_key(account: dict | None) -> int | None:
    """The slot *account* pins, or None if it pins nothing usable.

    A pinned value outside the palette is ignored rather than clamped — a stored 12
    is a bug or a palette that shrank, and deriving is a better answer than pinning
    every such account onto the last slot. ``True`` is rejected with it: it is an
    ``int`` in Python and 1 is a valid slot, so a stray flag would otherwise pin
    every account it touched to the same hue.
    """
    if account is None:
        return None
    pinned = account.get("color")
    if isinstance(pinned, bool) or not isinstance(pinned, int):
        return None
    return pinned if 1 <= pinned <= ACCOUNT_PALETTE_SIZE else None


def account_color_key(username: str, account: dict | None = None) -> int:
    """The slot *username* asks for: its pin, else the one derived from its name.

    This is a *preference*, not an answer — two usernames can prefer the same slot.
    Anything drawing a screenful of accounts wants :func:`account_color_keys`, which
    settles those collisions. This one is for the odd account with no list around it:
    a username a profile still names after the account itself was deleted.
    """
    if (pinned := pinned_color_key(account)) is not None:
        return pinned
    digest = hashlib.blake2s(username.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, "big") % ACCOUNT_PALETTE_SIZE + 1


def account_color_keys(accounts: list[dict]) -> dict[str, int]:
    """A slot per account username, distinct while the palette has slots to spare.

    Three passes, and the order of them is the whole design:

    1. **Pins**, which are claimed first and never moved: an account that says which
       colour it is outranks one that merely derived a preference for the same slot.
    2. **Uncontested preferences** — every account whose derived slot nothing else in
       the list wants. Settling these before any collision is resolved is what makes
       the guarantee in the module docstring true: probing forward for a free slot,
       as pass 3 does, would otherwise let a displaced account take a slot that some
       account further down the list was going to derive, and displace *it* in turn.
    3. **The rest**, in sorted-username order, each probing forward from its
       preference for the least-used slot.

    Sorting rather than taking the list order matters only in pass 3, where two
    accounts want one slot and one of them has to move. Which one is then decided by
    username, so that dragging accounts into a new order in Settings — which changes
    nothing about them — cannot recolour the screen.
    """
    keys: dict[str, int] = {}
    counts = dict.fromkeys(range(1, ACCOUNT_PALETTE_SIZE + 1), 0)

    preferences: dict[str, int] = {}
    for account in accounts:
        username = account.get("username", "")
        if (pinned := pinned_color_key(account)) is None:
            preferences[username] = account_color_key(username)
        else:
            keys[username] = pinned
            counts[pinned] += 1

    wanted_by = Counter(preferences.values())
    contested = [u for u in sorted(preferences) if wanted_by[preferences[u]] > 1 or counts[preferences[u]]]
    for username, slot in preferences.items():
        if username not in contested:
            keys[username] = slot
            counts[slot] += 1

    for username in contested:
        slot = _least_used_from(preferences[username], counts)
        keys[username] = slot
        counts[slot] += 1
    return keys


def _least_used_from(preferred: int, counts: dict[int, int]) -> int:
    """*preferred* if it is free, else the least-used slot, nearest one first.

    Probing forward from the preference rather than picking the globally emptiest
    slot keeps the choice local: an account displaced by a collision lands beside the
    colour it asked for, and which account gets displaced does not depend on what the
    far end of the list happens to hold.
    """
    probe = [(preferred - 1 + offset) % ACCOUNT_PALETTE_SIZE + 1 for offset in range(ACCOUNT_PALETTE_SIZE)]
    return min(probe, key=lambda slot: counts[slot])  # min is stable: ties go to the nearest


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
