"""
Shared timing constants for all BartleBot nodes.

Centralised here so values can be tuned in one place.  Every node that needs
a timeout, interval, or retry count imports from this module instead of
defining its own copy.
"""

import random

POLL_INTERVAL = 0.25
# General polling interval used by all wait loops (poll_until, poll_each_until_seen, manual sleep loops).

RECORDING_POLL_INTERVAL = 0.15
# Poll interval for recording coroutines — faster than POLL_INTERVAL because recording
# tracks the player's live position during interactive walkthroughs.

ZONE_CHANGE_TIMEOUT = 30.0
# Seconds to wait for a zone transition to complete (clients leave the source
# zone and arrive in the destination zone).

LOADING_SCREEN_TIMEOUT = 60.0
# Seconds a client may remain on a loading screen before it is treated as an
# infinite-load crash. Normal Wizard101 loading screens complete in 5–10 s;
# 60 s provides a generous safety margin.

MAX_RETRIES = 3
# Default number of attempts for recoverable operations (teleport-and-check loops).

TRIGGER_TIMEOUT = 10.0
# Seconds to wait for a UI event to fire after an input (loading screen start,
# button becoming enabled, dialog appearing).

QUICKSELL_COMPLETE_TIMEOUT = 15.0
# Seconds to wait for the backpack close button to reappear after confirming
# a quicksell. Selling a full backpack triggers a server-side transaction that
# can lag significantly; this gives the game time to finish processing before
# treating the operation as failed.

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

AFK_INTERVAL = 180.0
# Seconds between anti-AFK inputs (pressing A briefly) while waiting in the
# team-up queue.  Wizard101 idles the character after a few minutes without input.


REALM_HOP_TIMEOUT = 65.0
# Seconds to wait for the realm-hop cooldown to expire.
# Wizard101 imposes a ~60-second timer between realm changes; this adds a 5-second margin.

DIALOG_TIMEOUT = 60.0
# Seconds to wait for an NPC dialog prompt to appear after triggering interaction.
# Dialog appearance can be slow when the game is under load or zone assets are streaming.

DIALOG_FAST_POLL_INTERVAL = 0.05
# Polling interval for the ambient dialog watcher while a dialogue sequence is in progress.
# Once a banner is dismissed, the watcher switches to this faster rate until a full
# Safety Delay window has elapsed with no further dialogue.


CLIENT_REFRESH_INTERVAL_IDLE = 0.1
# Seconds between client sidebar refreshes when no bot is running.

CLIENT_REFRESH_INTERVAL_RUN = 0.5
# Seconds between client sidebar refreshes during an active bot run.
# Reduced from idle rate to lower contention on the asyncio event loop —
# WizWalker memory reads are synchronous (blocking) calls, so fewer GUI
# reads per second means less interference with the running bot's reads.

CLIENT_REFRESH_TIMEOUT = 1.0
# Per-widget timeout for a single refresh cycle. If any one client's
# zone/xyz reads take longer than this (e.g. during a zone transition),
# that widget's refresh is cancelled rather than blocking all others.

DECK_IMPORT_TIMEOUT = 120.0
# Ceiling for snapshotting one client's deck. A full 64-card deck is read by
# hovering each slot, so this is generously above the expected duration.

DECK_ITEM_PAGE_SETTLE = 0.3
# Pause after clicking Prev/NextItemSpells, so the new page is rendered before
# its slots are clicked.

DECK_ITEM_TOGGLE_SETTLE = 0.15
# Pause after toggling one item card slot.

LAUNCH_STAGGER_DELAY = 1.0
# Seconds to wait between starting successive Wizard101 instances, and between
# successive auto-login attempts, in a multi-account launch profile. The
# login server intermittently rejects logins that arrive in too-tight
# succession; this spaces them out enough to avoid that without giving up
# most of the benefit of launching a team in parallel.

DECK_APPLY_TIMEOUT = 180.0
# Ceiling for pushing a saved deck back to a client (clear + re-add every
# card, with spellbook page navigation between them).
#
# Both deck timeouts exist because DeckBuilder drives the game through UI
# clicks and several of its loops terminate on `is_control_grayed()`, which
# is unreliable — without a ceiling a failed read spins forever instead of
# raising. See _read_deck / _write_deck in app_controller.py.


def jitter(base: float, factor: float = 0.2) -> float:
    """Return base ± (factor * base) as a uniform random value.

    Use on deliberate action delays (waits after clicks, teleports, etc.)
    to avoid perfectly regular timing that could fingerprint the bot.
    Do NOT use on polling intervals — those are state-check loops, not
    human-action simulations.
    """
    return base * (1.0 + random.uniform(-factor, factor))
