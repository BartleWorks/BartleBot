"""
Shared timing constants for all BartleBot nodes.

Centralised here so values can be tuned in one place.  Every node that needs
a timeout, interval, or retry count imports from this module instead of
defining its own copy.
"""

import random

POLL_INTERVAL = 0.25
# General polling interval used by all wait loops (poll_until, poll_each_until_seen, manual sleep loops).

ZONE_CHANGE_TIMEOUT = 30.0
# Seconds to wait for a zone transition to complete (clients leave the source
# zone and arrive in the destination zone).

MAX_RETRIES = 3
# Default number of attempts for recoverable operations (teleport-and-check loops).

TRIGGER_TIMEOUT = 10.0
# Seconds to wait for a UI event to fire after an input (loading screen start,
# button becoming enabled, dialog appearing).

COMPASS_TELEPORT_TIMEOUT = 35.0
# Seconds to wait for a compass-controlled teleport button to become enabled.
# The game imposes a 30-second cooldown on mark/recall/dorm/hub teleports;
# this adds a 5-second safety margin.

ENTER_COMBAT_TIMEOUT = 30.0
# Seconds to wait for the combat-state flag to become active after teleporting
# to a mob.  Kept separate from ZONE_CHANGE_TIMEOUT — entering combat is a
# distinct game mechanic from a zone transition.

TARGET_ACQUIRE_RETRIES = 3
# Attempts to find a target entity before handle_combat returns FAILURE.
# Enemy entities may not be loaded immediately after a teleport.

TARGET_ACQUIRE_DELAY = 3.0
# Seconds to wait between target-acquire retries.

TEAMUP_QUEUE_TIMEOUT = 3600.0
# Maximum seconds to wait for a team-up queue match to complete and zone-change.
# Team-up matchmaking can take well over 10 minutes in low-population instances,
# so a 1-hour ceiling is used.

TEAMUP_AFK_INTERVAL = 180.0
# Seconds between anti-AFK inputs (pressing A briefly) while waiting in the
# team-up queue.  Wizard101 idles the character after a few minutes without input.


REAGENT_COLLECT_DELAY = 0.5
# Seconds to wait after pressing X at a reagent spawn before moving to the next
# location. Gives the game time to register the collection animation and item pickup.


REALM_HOP_TIMEOUT = 65.0
# Seconds to wait for the realm-hop cooldown to expire.
# Wizard101 imposes a ~60-second timer between realm changes; this adds a 5-second margin.


def jitter(base: float, factor: float = 0.2) -> float:
    """Return base ± (factor * base) as a uniform random value.

    Use on deliberate action delays (waits after clicks, teleports, etc.)
    to avoid perfectly regular timing that could fingerprint the bot.
    Do NOT use on polling intervals — those are state-check loops, not
    human-action simulations.
    """
    return base * (1.0 + random.uniform(-factor, factor))
