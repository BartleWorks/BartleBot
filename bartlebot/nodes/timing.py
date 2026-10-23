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

NETWORK_LINK_POLL_INTERVAL = 1.0
# Seconds between reads of the HUD's network status icons. Coarser than POLL_INTERVAL: a
# connection degrades over seconds, and each read walks the HUD's children.

NETWORK_LINK_BAD_TIMEOUT = 15.0
# Seconds the "bad" network icon may stay up before the client is recovered. The game can
# climb back out of it, but rarely does; left alone it usually reaches "broken" within
# 5-10 s, so this waits out one plausible recovery and no more.

NETWORK_LINK_BROKEN_POLLS = 3
# Consecutive polls showing the "broken" network icon before the client is recovered. That
# state never recovers on its own, so this only guards against a single misread.

ZONE_SETTLE_GRACE = 5.0
# Seconds wait_for_zone_with_recovery holds off teleport-to-player recovery for a client
# that is not in the destination zone and not (yet) on a loading screen. A client whose
# transition has fired but whose loading screen has not started still reads its old zone
# name, and porting it then undoes the arrival it was about to make on its own — the
# teleport lands after it gets there. Long enough for a slow trigger's loading screen to
# appear, short enough that a client that never triggered is still recovered quickly.

PORT_QUIET_GRACE = 5.0
# Seconds a port to a player may show nothing -- no loading screen, no jump in position, no
# dialog -- before port_clients acts on it, for a client that may already be standing where it
# is going. The first time it is re-issued; the second time the client is presumed already
# there, because a port onto someone you are beside, on the same realm, does nothing visible.
# Sized like ZONE_SETTLE_GRACE: long enough for a cross-realm port's loading screen to appear,
# since re-issuing a port that was about to start is what undoes an arrival (#70).

ZONE_READABLE_TIMEOUT = 10.0
# Seconds read_zones waits for every client's zone to be readable. zone_name() is None while a
# client is between zones or not logged in, and it turns readable a moment after the loading
# screen is gone, so this covers that moment, not a slow load: a caller waits out the loading
# screen itself first. Comparing a None zone proves nothing, so the zone is waited for rather
# than judged.

SIGHTING_SETTLE_TIMEOUT = 3.0
# Seconds an arrival check waits for players who have landed together to see each other in
# their entity lists (#130). The probe saw arrivals 1-2 s before their own loading screen
# ended, so they are usually seen at once; this covers a slow list, not a slow port.

MAX_RETRIES = 3
# Default number of attempts for recoverable operations (teleport-and-check loops).

PROMPT_CLEAR_SETTLE = 0.5
# Seconds a prompt must keep reading absent at reset_xyz before a client approaches the
# object it is there for. Absent is also what a prompt still on its way reads as: a client
# just off a loading screen beside another object (a World Gate's spiral door) raises that
# object's prompt a moment later, and one that approaches before then carries it along and
# presses X on the wrong object (#152). One read is not enough; a short run of them is.

TRIGGER_TIMEOUT = 10.0
# Seconds to wait for a UI event to fire after an input (loading screen start,
# button becoming enabled, dialog appearing).

RIDE_TIMEOUT = 180.0
# Ceiling on waiting out a ride -- a boat, an airship, a dragon -- for a player whose skip
# never landed. Skipping normally ends a ride within seconds of the prompt appearing, and a
# ride left alone ends on its own well inside this, so reaching it means the player is stuck
# rather than slow; what is left is the arrival check's teleport-to-player recovery.

QUICKSELL_COMPLETE_TIMEOUT = 15.0
# Seconds to wait for the backpack close button to reappear after confirming
# a quicksell. Selling a full backpack triggers a server-side transaction that
# can lag significantly; this gives the game time to finish processing before
# treating the operation as failed.

COMPASS_TELEPORT_TIMEOUT = 35.0
# Seconds to wait for a compass-controlled teleport button to become enabled.
# Recall has a 30-second cooldown of its own, and the hub and HOME buttons share
# a second 30-second one; this adds a 5-second safety margin. Marking has none.

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

DIALOG_PAGE_SETTLE = 0.15
# Seconds a dialogue press holds its client's dialogue lock after the press, so the next
# page is on screen before anyone reads the buttons again. The ambient watcher and a node's
# own advance loop both press dialogue; two reads of the page before a side quest offer,
# each followed by a press, would put the second press on the offer's ACCEPT.


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
# that is normally instant can stretch to many seconds.
#
# The poll runs in the background (combat_runner.start_combat_drop_poll), so this
# window never holds up the next node, even on a fight that drops nothing and waits
# all of it out. Nor is it the last line of
# defense: the caller carries its chat baseline between fights, so a message that
# beats even this window is recorded late by the next fight's poll rather than lost.
# That makes this value an attribution-latency knob, not a correctness one.

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

PLAYER_COLOR_REFRESH_INTERVAL = 1.0
# Seconds between the Combat tab re-asking which account is behind each player slot, while
# the tab is on screen. An identity is learned some time after its client is hooked and
# nothing announces it, so the tab polls; the ask is a dict lookup per client and the
# widgets repaint only when the answer changed.

CARD_HIGHLIGHT_DURATION = 1.5
# Seconds a card just added stays outlined in the accent (``CardFrame.highlight``), so
# the user sees where it landed: the Combat tab's add flow ends in two dialogs, and the
# card appears behind them (#272). Long enough to find after the dialog closes, short
# enough not to read as a selection.

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

RELOG_CHAR_SELECT_TIMEOUT = 60.0
# How long the relog node waits for character select to appear after clicking Quit. The
# logout tears down the world and the connection, so this is a server round trip rather
# than a UI animation.

RELOG_IN_GAME_TIMEOUT = 180.0
# How long the relog node waits for a character to finish loading back into the world.
# Matched to the launch path's patience rather than to a healthy client's: this node exists
# for clients that have become slow, and giving up early on one strands the fleet a player
# short.

RELOG_WATCH_INTERVAL = 60.0
# How often a running bot checks whether a relog is due and samples each client's
# GDI count. Deliberately coarse: the timer it serves is measured in hours, and GDI
# climbs over a whole session, so anything faster is memory reads bought for nothing.

RELOG_PARK_TIMEOUT = 300.0
# How long a scheduled relog waits for the FSM to reach a node boundary before giving up
# and trying again on the next tick. Generous on purpose — the longest node in a farming
# bot is a fight, and a slow one on a degraded client is exactly what this feature exists
# to fix, so timing out mid-fight and retrying is cheaper than killing a client in one.

RECOVERY_BATCH_SETTLE = 5.0
# How long the recovery queue waits for the rest of a relog batch before relaunching what
# it has. A relog closes several clients at once, but each window is only noticed missing
# on the next _client_loop poll, so the queue would otherwise start on the first one alone
# and relaunch the fleet a client at a time -- which is the whole cost the batch avoids.

CLIENT_REFRESH_TIMEOUT = 1.0
# Per-widget timeout for a single refresh cycle. If any one client's
# zone/xyz reads take longer than this (e.g. during a zone transition),
# that widget's refresh is cancelled rather than blocking all others.

DECK_IMPORT_TIMEOUT = 120.0
# Ceiling for snapshotting one client's deck. The read is memory under a page
# opened by keypress (deck_io.read_deck), so this only bounds a hung client.

DECK_ITEM_PAGE_SETTLE = 0.3
# Pause after writing the ItemSpells page (deck_io._set_item_page), so the new
# page is rendered before its slots are clicked. Kept from the Prev/Next clicks
# the write replaced and confirmed live after a write; it may be longer than a
# write needs, but it is paid once per item page.

DECK_ITEM_TOGGLE_SETTLE = 0.15
# Pause after toggling one item card slot.

DECK_FUSION_FLYOUT_SETTLE = 0.3
# Pause after clicking a fusion-root spell ("Novus Storm - T01 - Base") before
# reading TieredSpellMPUnlockedList. The variant list is populated by the click,
# so reading immediately can return the previous spell's variants or nothing.

DECK_SPELL_PAGE_SETTLE = 0.1
# Pause after writing the spellbook page (deck_io._set_spell_page), so the new
# page is rendered before a card on it is clicked. Kept from the PageDown clicks
# the write replaced, and confirmed live: a click this long after a write lands
# on the page written.

DECK_FUSION_CLICK_SETTLE = 0.05
# Pause between repeat clicks on one card -- a direct spellbook card or a fusion
# variant -- matching the cadence wizwalker used when adding copies.

DECK_CARD_CONFIRM_TIMEOUT = 2.0
# How long to wait for a clicked item card slot to read back toggled before
# treating the click as lost. The read is one byte, so polling is cheap.

DECK_CARD_CONFIRM_POLL = 0.05
# Gap between reads while confirming an item card toggle went in.

DECK_CARD_STABLE_WINDOW = 2.0
# How long a card's copy count in CardsInDeck must sit still, short of what was
# clicked, before the missing copies are taken as lost and clicked again. The
# deck list can trail the clicks by seconds (#107), and a re-click on a copy
# that was only late puts an extra one in the deck, so a count that is still
# moving is always waited on. Also bounds the wait for a trimmed copy to leave.

DECK_CARD_LAND_TIMEOUT = 8.0
# Ceiling on one round of waiting for clicked copies of a card to appear, however
# much the count keeps creeping. Only reached by a deck that trickles copies in
# for this long; a stalled one is caught sooner by DECK_CARD_STABLE_WINDOW.

DECK_CARD_COUNT_POLL = 0.1
# Gap between reads of a card's copy count while confirming or trimming it. Each
# read names every card in the deck, about four memory reads a card, so this is
# coarser than the item card poll.

DECK_FUSION_CLOSE_SETTLE = 0.5
# Pause after a base's variant clicks are confirmed in CardsInDeck, before the
# tier flyout is closed. The CardsInDeck count that confirms a click can grow
# ahead of the server actually committing the card -- closing the flyout in
# that gap has been observed to drop a card the read had just confirmed, and
# the card reappears if the deck page is closed and reopened, which is the
# game catching up rather than a UI refresh. Confirming is still what decides
# ``placed``; this only buys the server time to agree before the flyout that
# card came from goes away.

LAUNCH_STAGGER_DELAY = 1.0

# How long one launch stage may run before its pending tile starts showing how long it has
# been in it. Below this the number is noise -- every stage takes some seconds and a
# counter on all of them reads as a stopwatch rather than as a warning. Above it the
# distinction the caption cannot otherwise draw is the one that matters: a launch stalled
# at the login screen and a launch doing fine look identical without it.
LAUNCH_STAGE_ELAPSED_HINT = 20.0

# How long a launched window may take to reach the login screen before that account is
# given up on. Generous, because this covers a cold start of the game off a slow disk --
# but bounded, because the wait had no bound at all and a window that never arrives left
# its launch running, and so its tile spinning, for the rest of the session.
LAUNCH_LOGIN_SCREEN_TIMEOUT = 180.0

# How long to wait for a batch's windows to exist at all. Shorter than the login screen
# wait: this covers process spawn, and a window that has not appeared by now is one the
# launcher never started rather than one that is being slow.
LAUNCH_SPAWN_TIMEOUT = 90.0
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

AUTO_SELECT_CHAR_SELECT_TIMEOUT = 60.0
# Ceiling on driving one client from auto-login as far as character select, for when
# automatic character selection is off. Shorter than AUTO_SELECT_TIMEOUT because it stops
# two stages earlier — no Play press and no load-in — and the same reasoning applies:
# reaching it means something is wrong, not that the client is slow.

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
# without the elixir, so the uses are serialized and spaced out. The whole interaction
# is serialized rather than only the clicks: the station's menu closes on every client
# that has not clicked yet as soon as one of them does, so overlapping the menus and
# staggering only the clicks loses the elixir for everyone after the first. Anyone still
# missing it is retried rather than failed, so this only has to cover the common case.

OBJECT_USE_DELAY = 1.0
# Seconds between one player finishing its turn at a shared world object and the next one
# starting on it, in the optional sequential mode of the interaction nodes. Some objects —
# zone teleporters among them — only serve one player at a time, so a second player
# interacting while the first is mid-interaction has its keypress swallowed. The delay
# covers the object becoming free again, which has no readable signal of its own.
#
# Distinct from LEVEL_SCALING_STAGGER_DELAY, which paces the one level-scaling station
# handle_level_scaling uses; these are separate knobs because there is no reason to believe every
# contended object frees up at the same rate.

INTERACT_ANIMATION_DELAY = 0.0
# Extra seconds an interaction's own animation is given, on top of the resolved Safety
# Delay, before its dialogue is treated as finished. Zero by default: most interactions
# have no animation to wait out.
#
# Additive rather than a replacement because the two waits measure different things. A
# cutscene — a milk churn tipping over, a statue rising — takes the same N seconds on
# every connection, while Safety Delay is how much slack the *user's* connection needs
# for the server response that follows. A node that pinned Safety Delay to cover an
# animation took the user's slack away to buy the animation's time, which is the bug
# this exists to retire.

WISP_COLLECT_TIMEOUT = 10.0
# Ceiling on how long collect_wisps may spend moving the group between
# wisps. Guards against a game bug where a client's
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

GROUP_PROFILE_SETTLE_TIMEOUT = 2.0
# Ceiling on waiting for the profile popup's buttons to stop moving before Invite to Group
# is clicked (#280). The popup reads as visible before its ButtonLayout has closed up the
# gaps its hidden buttons leave, so the first positions read can be a slot or two low, and
# a click aimed at them lands on the button below. Two reads a POLL_INTERVAL apart that
# agree end the wait; a settled popup costs one interval. On timeout the click goes ahead.

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
# costs in a regroup.

GROUP_UI_SETTLE = 1.0
# Pause after clicking the social window's Group tab, in the one case its arrival cannot be
# observed: the window was already open with the Group page loaded from earlier, and the
# caller is about to CLICK something on the page (leave_group, teleport_to_group_member).
# Nothing distinguishes the Group tab from the others — tools/probe_group_tab.py swept every
# tab and every window under the social window and came back empty (2026-08-11) — so a
# click aimed at the page could otherwise land on whichever tab is still showing.
#
# Everywhere else this is polled away by GROUP_TAB_LOAD_TIMEOUT below. It used to follow
# every tab click, which made it the fixed floor on every membership survey (#127).

GROUP_TAB_LOAD_TIMEOUT = 3.0
# Ceiling on waiting for the Group page to load after its tab is clicked. The page is blank
# each time the social window opens, fills in only once the Group tab is selected, and is
# cleared again when the window closes (checked in-game, 2026-09-29) — and its first roster
# line is never blank once loaded, holding a member or the invitation placeholder. So the
# load is observable whether or not the client is grouped, and group_manager polls for it.
#
# A ceiling, not a delay: a healthy page loads within a frame or two. It is reached only
# when the page never loads, most likely because a game update moved the roster path, and
# group_manager then logs a warning. Three times the 1.0 s that reliably covered the page
# loading under load when this was a fixed sleep.


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


SHOP_OPEN_TIMEOUT = 5.0
# Seconds to wait for the potion vendor's shop window to open after the buy prompt is
# accepted. Opening the shop is a local UI action with no server round-trip.

SHOP_CLICK_DELAY = 0.25
# Pause after clicking Fill All / Buy, to let the shop window settle before the next click.

SHOP_EXIT_CLICK_INTERVAL = 0.125
# Interval between repeated Exit clicks while the shop window is still showing. Faster
# than POLL_INTERVAL because each pass is a click, not a state read: the window closes on
# whichever click lands first.

COMBAT_TP_RETRY_INTERVAL = 5.0
# How often handle_combat re-teleports to its target while the clients are still not in
# battle. A teleport that missed leaves nothing to poll for, so the action is repeated.

FLEE_AFTER_ROUNDS = 3
# Planning phases in a row a fight must look unwinnable before our players flee it (#111):
# none of them holds a castable damage card, and no damage over time is on the enemies. One
# round is not enough, because a card grayed out for want of pips reads the same as one that
# can never be cast, and round one rarely has the pips for a big hit.

FLEE_VOTE_TIMEOUT = 5.0
# Seconds a player waits, at the start of a planning phase, for the rest of our players in
# its fight to report whether they can still deal damage. A report that has not arrived by
# then counts as "can", so a slow read never causes a flee. Well inside the 30s planning
# timer, which is spent only once the wait is over.

FLEE_CONFIRM_TIMEOUT = 3.0
# Seconds to wait for the flee confirmation to appear after clicking Flee. A miss is retried
# at the next planning phase.

FLEE_TIMEOUT = 90.0
# Seconds a fight has to end after a flee is called for because it ran past its maximum
# duration. The flee lands at the next planning phase, which can sit behind a full round of
# animations. Past this the combat is cancelled, with the players still in the fight.

AMBIENT_COMBAT_MAX_DURATION = 600.0
# The ambient combat watcher's counterpart to handle_combat's max_duration param: a fight
# no node started is fled once it has run this long.

AMBIENT_HELP_HOLD = 20.0
# Longest the ambient combat watcher holds a fighter's first cast while players who can help
# teleport into its fight (#116). Counted from the later of the fighter's first planning phase
# and the most recent join, since the game restarts its 30s planning timer whenever a player
# joins. Under 30s, so the fighter still casts in round one.

AMBIENT_FLEE_LIMIT = 3
AMBIENT_FLEE_WINDOW = 600.0
# The third flee within this many seconds stops the bot, whether handle_combat or the ambient
# watcher fought it. Fight, flee, recovery, fight never trips the same-state or no-match
# limits, because the states keep changing.

DEAD_CLIENT_CONFIRM_TIMEOUT = 45.0
# Seconds the run loop waits, after a node fails with a dead-client error, for proof that a
# client has gone: it drops out of the client list, or the health monitor hands it to crash
# recovery. Past it the error is counted as the node's FAILURE, so a memory read that failed
# on a live client cannot leave the run paused with nothing coming to resume it. Covers the
# monitor's slowest verdict, three 5s status checks on a hung client, plus the close after.

PREEMPT_PARK_TIMEOUT = 5.0
# Seconds the ambient combat watcher waits for a node it cancelled to unwind and the run
# loop to park. A cancel lands at the node's next await, so this is normally a moment; past
# it the fight is fought anyway, since a fighter left alone is worse than a node still running.

COMBAT_AFK_POLL_INTERVAL = 2.0
# How often the idle-popup watcher looks for "You Are Idle" on every client (#203). The popup
# stays up until answered and takes two whole planning phases to appear, so a coarse poll loses
# nothing, and each look is a window-tree walk per client.

RELOG_FIGHT_TIMEOUT = AMBIENT_COMBAT_MAX_DURATION + FLEE_TIMEOUT
# How long the relog node waits for a fight to end before logging out, and again before
# re-forming the group (#203). The ambient combat watcher fights it meanwhile, and flees it at
# AMBIENT_COMBAT_MAX_DURATION, so a fight still going past this is one nothing will end.

POSITION_STABILIZE_TIMEOUT = 5.0
# Seconds to wait for the server-reported position to match a teleport target. The client
# moves first and the server confirms after, so this covers the gap.

HOME_LOADING_TIMEOUT = 15.0
# Seconds to wait for a loading screen to appear after HOME. Longer than TRIGGER_TIMEOUT
# because the dorm teleport is a full zone load requested from a keypress.

WISP_COLLECT_DELAY = 0.1
# Pause between wisp teleports, so a collection registers before the health/mana ratio is
# read again. Matches the Deimos utils.py pattern.


def jitter(base: float, factor: float = 0.2) -> float:
    """Return base ± (factor * base) as a uniform random value.

    Use on deliberate action delays (waits after clicks, teleports, etc.)
    to avoid perfectly regular timing that could fingerprint the bot.
    Do NOT use on polling intervals — those are state-check loops, not
    human-action simulations.
    """
    return base * (1.0 + random.uniform(-factor, factor))


SPEED_REAPPLY_INTERVAL = 0.3
# Seconds between the authoring speed toggle's checks that each client still walks at the
# boosted speed. The game rewrites the multiplier on every zone change and mount, so a
# boost written once is gone after the first door; this is how soon it comes back.
