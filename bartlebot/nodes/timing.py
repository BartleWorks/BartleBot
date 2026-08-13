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


DROP_MESSAGE_TIMEOUT = 3.0
# Seconds to keep watching the chat log for a drop announcement after an action
# that should have produced one. The message is a server round-trip, so it lands
# after the local UI has already finished reacting — diffing the chat the instant
# a reagent prompt disappears reads it as it was before the drop. Only paid in
# full when nothing arrives at all; the poll returns as soon as a line shows up.

DROP_MESSAGE_POLL = 0.1
# How often to re-read the chat log while waiting for a drop announcement. Finer
# than POLL_INTERVAL because this wait sits between two game actions, so every
# poll cycle spent past the message's arrival is added latency per pickup.

CLIENT_REFRESH_INTERVAL_IDLE = 0.1
# Seconds between client sidebar refreshes when no bot is running.

CLIENT_REFRESH_INTERVAL_RUN = 0.5
# Seconds between client sidebar refreshes during an active bot run.
# Reduced from idle rate to lower contention on the asyncio event loop —
# WizWalker memory reads are synchronous (blocking) calls, so fewer GUI
# reads per second means less interference with the running bot's reads.

CLIENT_REFRESH_INTERVAL_HIDDEN = 2.0
# Seconds between client sidebar refreshes while the Players tab is not the
# current tab. Deliberately slower than the run interval, and visibility is
# checked before bot state: hidden-and-running is the highest-contention,
# lowest-value combination, since nothing is on screen to update. Polling does
# not stop entirely because AppController's zone-dropdown getter reads each
# widget's cached zone, and a frozen cache would offer zones nobody is in.

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

DECK_FUSION_FLYOUT_SETTLE = 0.3
# Pause after clicking a fusion-root spell ("Novus Storm - T01 - Base") before
# reading TieredSpellMPUnlockedList. The variant list is populated by the click,
# so reading immediately can return the previous spell's variants or nothing.

DECK_FUSION_CLICK_SETTLE = 0.05
# Pause between repeat clicks on one fusion variant, matching the cadence
# wizwalker uses when adding copies of an ordinary card.

LAUNCH_STAGGER_DELAY = 1.0
# Seconds to wait between starting successive Wizard101 instances, and between
# successive auto-login attempts, in a multi-account launch profile. The
# login server intermittently rejects logins that arrive in too-tight
# succession; this spaces them out enough to avoid that without giving up
# most of the benefit of launching a team in parallel.

WISP_COLLECT_TIMEOUT = 10.0
# Ceiling on how long a single client may spend collecting wisps for one
# resource (health or mana). Guards against a game bug where a client's
# displayed max HP can read far above actual, making calc_health_ratio()
# never clear the threshold and the collection loop spin indefinitely.

DECK_PAGE_OPEN_TIMEOUT = 5.0
# Ceiling on waiting for the deck page to appear after pressing P. Measured at
# 190-270 ms across four clients, so this is a generous margin that only matters
# when the keypress was swallowed entirely.

DECK_PAGE_POLL = 0.02
# How often to re-check for the deck window while waiting for it to open. Much
# finer than POLL_INTERVAL because the wait itself is only ~200 ms — polling at a
# quarter second would more than double it.

DECK_SETTLE_TIMEOUT = 15.0
# Ceiling on waiting for a rewritten deck to read back as expected before the
# deck page is closed. Cards are added by clicking, and the list the game shows
# lags the clicks — closing the page too early left cards out of the deck even
# though every click landed. Generous because this only elapses in full when the
# deck is never going to match, which is already being reported as a warning.

DECK_SETTLE_POLL = 0.1
# How often to re-read the deck while waiting for it to settle. Each poll is two
# memory reads, so this is fine-grained enough to close promptly without adding
# meaningful load.

DECK_APPLY_TIMEOUT = 180.0
# Ceiling for pushing a saved deck back to a client (clear + re-add every
# card, with spellbook page navigation between them).
#
# Both deck timeouts exist because DeckBuilder drives the game through UI
# clicks and several of its loops terminate on `is_control_grayed()`, which
# is unreliable — without a ceiling a failed read spins forever instead of
# raising. See _read_deck / _write_deck in app_controller.py.


GROUP_INVITE_TIMEOUT = 15.0
# Seconds to wait for the group invitation modal to appear on the invited client after
# the inviter clicks "Add to Group". The invite is relayed by the server, so this covers
# a full round-trip rather than just a local UI update.

GROUP_VERIFY_TIMEOUT = 15.0
# Seconds to wait for the social window to open by itself on an invited client after the
# invitation is accepted, which is the game's own signal that the join went through. The
# join is a server round-trip, so this covers more than a local UI update.

GROUP_ADD_RETRIES = 2
# Attempts to add one client to the group before giving up on that client. A failed add
# usually means a missed click or a dropped invite, both of which a retry fixes; anything
# that survives two attempts is a real problem (not friended, not online) that retrying
# will not solve.

WINDOW_CLOSE_TIMEOUT = 6.0
# Ceiling on repeatedly clicking a window's close button until the window is gone.
# A close click can be swallowed when another window sits on top of the button, or when
# the game is mid-animation, so one click is not reliable — but a window that will not
# close after several seconds is stuck, not slow.

WINDOW_CLOSE_RETRY_INTERVAL = 0.4
# Pause between close attempts. Long enough for the game to process the previous click
# and repaint before the next one, short enough that a normal close still feels instant.

GROUP_UI_SETTLE = 1.0
# Pause after clicking a friends-list or profile button, so the resulting window is
# rendered before it is read or clicked. Matches the sleep WizWalker uses between its own
# friends-list clicks.
#
# Used where there is nothing specific to wait FOR: before checking whether the "already in
# a group" popup appeared, where absence is the expected outcome so no amount of polling
# can conclude early — and after switching to the social window's Group tab, where NO
# element distinguishes that tab from the others. That second one is not an unexplored
# shortcut: tools/probe_group_tab.py swept every tab and every window under the social
# window and came back empty (2026-08-11, and see paths.py).
#
# So this is the largest fixed cost in a group membership survey and it cannot be polled
# away. Nor should it be shortened — 1.0 s is what reliably covers the page populating
# under load, and a survey that misreads membership sends spurious invites. It is
# concurrent across clients (see group_manager.read_group_flags), so it costs one second
# for the whole fleet rather than one per client, which is where the win came from instead.


HOUSING_EQUIP_TIMEOUT = 5.0
# Ceiling on waiting for a castle's "equipped" fist icon to reflect an Equip_Item click.
# Equipping is a server round-trip, but the icon is the game's own confirmation, so this is
# polled rather than slept through. Generous because the only cost of a high ceiling is the
# time taken by a click that was never going to land.

HOUSING_OPEN_RETRIES = 3
# Attempts at pressing U to open the housing page. The key is swallowed when another
# spellbook page owns the window, which one ESC clears — so a second attempt almost always
# succeeds and a third is pure margin.

HOUSING_PAGE_MAX = 10
# Ceiling on how many pages of the castle list are searched. At 8 rows per page this covers
# 80 castles, far past any real inventory; it exists so that a paging control that stops
# advancing cannot spin forever.


CHARACTER_PAGE_SETTLE = 0.5
# Pause after pressing C, so the character page is rendered before its title is read.
# Unlike GROUP_UI_SETTLE this is not the floor on the operation: it is followed by a poll
# for the title window (wizard_names._read_title_scroll), so a slow render is recovered
# rather than misread. It only needs to cover the common case — the page is a local UI
# panel with no server round-trip, so a frame or two is enough.
#
# Concurrent across clients (see wizard_names.resolve_names), so a fleet pays it once
# rather than once per client.


def jitter(base: float, factor: float = 0.2) -> float:
    """Return base ± (factor * base) as a uniform random value.

    Use on deliberate action delays (waits after clicks, teleports, etc.)
    to avoid perfectly regular timing that could fingerprint the bot.
    Do NOT use on polling intervals — those are state-check loops, not
    human-action simulations.
    """
    return base * (1.0 + random.uniform(-factor, factor))
