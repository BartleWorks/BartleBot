"""The shipped default duel strategies, and the DSL a fight runs when no file can be read.

Every product by this vendor fights, and the defaults are shipped content they share
(``docs/COMBAT_DEFAULTS.md``), so the ids and the last-resort DSL live here and import
nothing product-specific.

The three duels are Official items in ``strategies/duel/``, found by id:

- :data:`SHIPPED_SOLO_DUEL_ID`: one player, who runs Default.
- :data:`SHIPPED_TEAM_DUEL_ID`: P1 runs Default, P2 to P4 run Default Support, which
  supports while a boss is up and hits otherwise.
- :data:`SHIPPED_EVERYONE_ATTACKS_DUEL_ID`: every player runs Default.
"""

from __future__ import annotations

_SEPARATOR = " | "

#: Card choice priority, highest first: buffs and debuffs before damage, falling back
#: gracefully when a preferred card is not in hand. Generic to any school.
_PRIORITY = [
    "any<trap & inc_damage>[potent] @ enemy",
    "any<trap & inc_damage & aoe>[potent]",
    "any<blade & out_damage>[sharp] @ self",
    "any<blade & out_damage & aoe>[sharp]",
    "any<global>",
    "any<aura & out_damage>",
    "any<shadow>",
    "any<damage & aoe>[any<mod_damage>]",
    "any<damage>[any<mod_damage>] @ enemy",
]

#: The generic single-wizard DSL. The shipped Default player strategy carries the same
#: text, and it is what a fight runs when the shipped files cannot be read at all.
LAST_RESORT_DSL = _SEPARATOR.join(_PRIORITY)

#: Default Solo Duel: one player.
SHIPPED_SOLO_DUEL_ID = "530aa34e-1567-4492-b507-12529a41f0db"
#: Default Team Duel: P1 hits, P2 to P4 support while a boss is up and hit otherwise.
SHIPPED_TEAM_DUEL_ID = "9594ae6a-2cdc-4990-9ee7-3c43bb715121"
#: Default Team Duel (Everyone Attacks): every player runs Default.
SHIPPED_EVERYONE_ATTACKS_DUEL_ID = "f51b5e89-ba2b-40da-81ef-301e26ca5dd5"
#: The Default player strategy, which the three duels above link.
SHIPPED_DEFAULT_PLAYER_ID = "a6ddb3e8-5a97-42de-a8d6-174a8353a589"
#: The Default Support player strategy.
SHIPPED_DEFAULT_SUPPORT_ID = "d2496f8b-85c5-44f5-98da-13fb0c7ddea5"
