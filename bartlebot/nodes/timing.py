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


REAGENT_DROP_MESSAGE_TIMEOUT = 3.0
# Seconds to keep watching the chat log for a drop announcement after a reagent
# pickup. The message is a server round-trip, so it lands after the local UI has
# already finished reacting — diffing the chat the instant a reagent prompt
# disappears reads it as it was before the drop. Only paid in full when nothing
# arrives at all; the poll returns as soon as a line shows up.
#
# Kept short because collect_reagent pays this window once per *pickup*, and a
# pickup that dismissed its prompt has almost certainly earned an announcement.
# The combat constants below are deliberately separate and much longer.

REAGENT_DROP_MESSAGE_POLL = 0.1
# How often to re-read the chat log while waiting for a reagent drop announcement.
# Finer than POLL_INTERVAL because this wait sits between two game actions, so every
# poll cycle spent past the message's arrival is added latency per pickup.

COMBAT_DROP_MESSAGE_TIMEOUT = 15.0
# Seconds to keep watching the chat log for a drop announcement after a duel ends.
# Far longer than the reagent window: end-of-fight is when the server grants loot,
# and that is exactly when a long-running session lags hardest, so the round-trip
# that is normally instant can stretch to many seconds (#127).
#
# Paid in full only by a fight that drops nothing at all — poll_new_drops returns
# as soon as a line appears. It is also not the last line of defence: the caller
# carries its chat baseline between fights, so a message that beats even this
# window is recorded late by the next fight's poll rather than lost. That makes
# this value an attribution-latency knob, not a correctness one.

COMBAT_DROP_MESSAGE_POLL = 0.5
# How often to re-read the chat log while waiting for a combat drop announcement.
# Coarser than the reagent poll on purpose: each read is a synchronous
# ReadProcessMemory of the whole chat log on the event loop thread, and these
# reads land during the end-of-fight lag spike they are waiting out. Since the
# poll returns on the first hit, a coarser interval costs latency, never a drop.

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

CLIENT_DETECT_INTERVAL = 1.0
# Seconds between ClientManager detection cycles: notice new game windows, rebuild the
# slot ordering, re-read wizard names. Hook activation no longer happens inline here, so
# this cadence is now the whole cost of a cycle.

CLIENT_DETECT_INTERVAL_IDLE = 3.0
# Detection cadence while no client is hooked yet. Slower than CLIENT_DETECT_INTERVAL
# because there is nothing to keep in sync — every window present is either absent or
# still loading in, and neither transition is worth a per-second poll.

CLIENT_DETECT_ERROR_BACKOFF = 3.0
# Pause after an unexpected detection-cycle error, so a persistent fault (a client dying
# mid-read, say) does not spin the loop at full speed while it logs.

CLIENT_CLOSE_GRACE = 6.0
# How long a client gets to exit on its own after WM_CLOSE before it is terminated.
# A healthy client is gone well inside a second; the headroom is for one that is
# mid-crash and putting up an error popup, which we dismiss and re-check each poll.
# Recovery cannot start until the process is actually gone, so this is dead time on
# every crash — long enough to let a clean exit happen, short enough not to matter.

CLIENT_CLOSE_POLL = 0.25
# How often to re-check for process exit, and to sweep for error popups, while
# waiting out CLIENT_CLOSE_GRACE.

CLIENT_TERMINATE_TIMEOUT = 2.0
# How long to wait for the process to disappear after TerminateProcess. The call is
# asynchronous — it returns before the kernel has torn the process down — and
# remove_dead_clients() only reaps the client once the handle reports exited.

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

DECK_SPELL_PAGE_SETTLE = 0.1
# Pause after clicking PageDown in the spellbook, so the new page is rendered
# before a card on it is clicked. Matches the cadence wizwalker's own page
# sweep uses.

DECK_FUSION_CLICK_SETTLE = 0.05
# Pause between repeat clicks on one fusion variant, matching the cadence
# wizwalker uses when adding copies of an ordinary card.

DECK_CARD_CONFIRM_TIMEOUT = 2.0
# How long to wait for clicked copies of one card to appear in CardsInDeck
# before treating the clicks as lost. Generous: the cost of being wrong is a
# duplicate copy, and the deck read is two memory reads so polling is cheap.

DECK_CARD_CONFIRM_POLL = 0.05
# Gap between deck-count reads while confirming a card went in.

LAUNCH_STAGGER_DELAY = 1.0
# Seconds to wait between starting successive Wizard101 instances, and between
# successive auto-login attempts, in a multi-account launch profile. The
# login server intermittently rejects logins that arrive in too-tight
# succession; this spaces them out enough to avoid that without giving up
# most of the benefit of launching a team in parallel.

AUTO_SELECT_FIRST_SCREEN_DELAY = 3.0
# Seconds to wait after auto-login is typed before the first keypress. The loading screen
# that follows login ignores input for roughly this long, so anything sent earlier is
# simply dropped — this is a floor, not a guess at when the screen ends.

AUTO_SELECT_KEY_INTERVAL = 0.5
# Seconds between keypresses while advancing a launching client through the loading
# screens and character select. There are three gates to clear and no readable signal for
# where the client is between them, so the interval is the worst-case cost of each gate:
# recovery's original 5.0 s could waste 15 s per launch.

AUTO_SELECT_TIMEOUT = 120.0
# Ceiling on driving one client from auto-login to in-game. Generous against the observed
# worst case (~3 s + loading + ~5 s character select + ~15 s load-in); hitting it means
# something is wrong — a login queue, a bad password, a server outage — not that the
# client is slow.

AUTO_SELECT_TREE_TIMEOUT = 20.0
# How long to wait for a launching client's UI tree to become readable before giving up on
# reading it and driving the client blind instead. Measured at 1.8 s from attach
# (tools/probe_char_select.py), so this is a wide margin: reaching it means something is
# wrong with the hook, not that the client is slow.

AUTO_SELECT_MAX_ENTER = 8
# How many times to press Enter on a confirmed character-select screen before giving up on
# it. The screen is live for about two seconds before the Play button finishes resolving
# (its label is still an unresolved lang key until then), so the first press or two can be
# swallowed — but a screen that eats eight is not going to take a ninth.

AUTO_SELECT_CLEANUP_INTERVAL = 2.0
# Pause between ESC presses while closing whatever the Enter spam opened. The chat box and
# the settings window both animate, and a second ESC sent mid-animation is swallowed.

LEVEL_SCALING_STAGGER_DELAY = 1.0
# Seconds between one client finishing with the level-scaling station and the next one
# starting on it. Several clients using it at once intermittently leaves some of them
# without the elixir, so the uses are serialised and spaced out. The whole interaction
# is serialised rather than only the clicks: the station's menu closes on every client
# that has not clicked yet as soon as one of them does, so overlapping the menus and
# staggering only the clicks loses the elixir for everyone after the first. Anyone still
# missing it is retried rather than failed, so this only has to cover the common case.

SEQUENTIAL_INTERACT_STAGGER = 1.0
# Seconds between one client finishing its turn at a shared world object and the next one
# starting on it, in the optional sequential mode of the interaction nodes. Some objects —
# zone teleporters among them — only serve one player at a time, so a second client
# interacting while the first is mid-interaction has its keypress swallowed. The gap covers
# the object becoming free again, which has no readable signal of its own.
#
# Distinct from LEVEL_SCALING_STAGGER_DELAY, which paces the one station apply_level_scaling
# is hardcoded against; these are separate knobs because there is no reason to believe every
# contended object frees up at the same rate.

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


ARCHMASTERY_MENU_TIMEOUT = 5.0
# Ceiling on the archmastery school menu opening or closing after its gauge is
# clicked. Nothing in the window tree announces the menu, so this is bounded by
# the icons finishing their move; generous because elapsing it in full means the
# click did not register, which is retried rather than reported.

ARCHMASTERY_MENU_POLL = 0.05
# How often to re-read the school icons' rectangles while waiting. One memory
# read per icon per tick, seven icons, entirely local.

ARCHMASTERY_MENU_SETTLE = 0.15
# How long the icons must hold still before the menu counts as open (or closed).
# The move is animated, so "the rectangles changed" only means it has started;
# clicking then would aim at a position the icon is still leaving.
#
# This is a ceiling, not a cadence: shortening it does not make a test faster,
# it makes the test click mid-animation. Compress ARCHMASTERY_MENU_POLL instead.

ARCHMASTERY_CONFIRM_TIMEOUT = 3.0
# Ceiling on the school reading back as the one that was clicked. The write is
# the game's own, so this covers a UI round trip rather than a server one.

ARCHMASTERY_CONFIRM_POLL = 0.05
# How often to re-read archmastery_school() while confirming. Two memory reads.

ARCHMASTERY_ATTEMPTS = 3
# How many times the open-click-confirm sequence is repeated before giving up.
# Bounded for the same reason CARD_CLICK_ATTEMPTS is: a click the game declined
# and a click that was swallowed look identical from here.

GROUP_INVITE_TIMEOUT = 15.0
# Seconds to wait for the group invitation modal to appear on the invited client after
# the inviter clicks "Add to Group". The invite is relayed by the server, so this covers
# a full round-trip rather than just a local UI update.

GROUP_VERIFY_TIMEOUT = 15.0
# Seconds to wait for the social window to open by itself on an invited client after the
# invitation is accepted, which is the game's own signal that the join went through. The
# join is a server round-trip, so this covers more than a local UI update.

GROUP_PROFILE_TIMEOUT = 6.0
# Ceiling on waiting for a friend's profile popup to appear after their row is clicked in
# the friends list. Polled rather than slept through: the popup is drawn locally with no
# server round-trip, so it is normally up within a frame or two and the wait ends there.

GROUP_JOIN_HINT_TIMEOUT = 3.0
# Ceiling on waiting for the game to open the social window by itself on an invited client
# after it accepts. That auto-open is only a hint that the accept was processed — the
# authoritative check is the Leave Group button — so this is short, and it is spent from
# the same GROUP_VERIFY_TIMEOUT budget as that check rather than added to it. Its real job
# is to keep our own HUD-button click from racing the game's open and toggling the window
# straight back shut.

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

WINDOW_CLOSE_CONFIRM_INTERVAL = 0.1
# How often the "is it gone yet?" check runs *within* one WINDOW_CLOSE_RETRY_INTERVAL.
# The retry interval is the floor on how fast we may click again, not on how fast we may
# notice the click worked, and the common case is that it worked immediately. Sleeping the
# whole interval before looking put a 0.4 s floor under every window close in the app;
# group formation closes several windows per invite, so that was among the largest fixed
# costs in a regroup (#114).

GROUP_UI_SETTLE = 1.0
# Pause after switching to the social window's Group tab, so the page is populated before
# it is read. Matches the sleep WizWalker uses between its own friends-list clicks.
#
# One remaining use, and it is the one place with nothing to wait FOR: NO element
# distinguishes the Group tab from the others. That is not an unexplored shortcut —
# tools/probe_group_tab.py swept every tab and every window under the social window and
# came back empty (2026-08-11, and see paths.py). A membership *survey* therefore cannot
# conclude early, because either answer is plausible and neither has a signal.
#
# Confirming a JOIN is not that case and no longer pays this: only "grouped" is being
# waited for there, so group_manager._confirm_joined polls the Leave Group button instead
# and returns the moment it appears (#114). Same for the two sleeps that used to sit in
# _invite_and_accept — the friend profile popup and the "already in a group" refusal are
# both polled now, the latter raced against the invitation prompt so its absence costs
# nothing.
#
# What is left should not be shortened: 1.0 s is what reliably covers the page populating
# under load, and a survey that misreads membership sends spurious invites. It is
# concurrent across clients (see group_manager.read_group_flags), so a fleet pays it once
# rather than once per client, which is where the win came from instead.


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
