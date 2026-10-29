"""BartleBot's client manager: core's, with recovery, relogging and the running bot.

Finding, hooking and naming clients, launches, the slot order and unhooking are core's
(:mod:`bartleby.runtime.clients.client_manager`). What is here is what a running bot
needs of them: the machine handed each new client list, a reorder refused while a node
holds that list, the drop tables kept in player order, and bringing back a client that
disappeared mid-run, whether it crashed or a relog closed it on purpose. Core reports a
lost client on ``clients_removed``; :meth:`ClientManager._recover_removed` answers it.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from bartleby.runtime.clients.client_manager import ClientManager as CoreClientManager
from bartleby.runtime.clients.client_manager import Removal
from bartleby.runtime.clients.credentials import CredentialStore, account_label
from bartleby.runtime.clients.process_resources import gdi_object_count
from bartleby.runtime.clients.window_kill import force_close_client
from bartleby.runtime.launcher import auto_select
from bartleby.runtime.timing import (
    CLIENT_DETECT_INTERVAL,
    RECOVERY_BATCH_SETTLE,
    RELOG_PARK_TIMEOUT,
    RELOG_WATCH_INTERVAL,
)
from PyQt6.QtCore import QObject, pyqtSignal

from bartlebot.bot.machine import PauseHold
from bartlebot.clients import recovery_readiness, relog
from bartlebot.settings_manager import SettingsManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _RecoveryTarget:
    """One slot being relaunched, with everything the launcher needs to bring it back."""

    orig_pos: int
    username: str
    password: str
    display_name: str
    title: str
    resolution: tuple[int, int] | None


def _group_by_resolution(
    targets: list[_RecoveryTarget],
) -> list[tuple[tuple[int, int] | None, list[_RecoveryTarget]]]:
    """Split targets into batches that can share one config.xml patch.

    The game reads its window resolution from a single file at startup, so a batch can
    only hold clients that want the same one. Order within a group is preserved, and so is
    the order the groups themselves are launched in, so a fleet with one resolution — the
    normal case — comes back as one batch in slot order.
    """
    groups: dict[tuple[int, int] | None, list[_RecoveryTarget]] = {}
    for target in targets:
        groups.setdefault(target.resolution, []).append(target)
    return list(groups.items())


class _Signals(QObject):
    """The Qt signals the manager publishes on, in place of core's listeners."""

    clients_changed = pyqtSignal(list)
    all_clients_changed = pyqtSignal(list, list)  # (active_clients, unhooked_clients)
    # list[str | None], positionally aligned with the live client list.
    wizard_names_changed = pyqtSignal(list)
    recovery_error = pyqtSignal(str)


class ClientManager(CoreClientManager):
    """Core's client manager, with recovery, relogging and the running bot.

    Signals:
        clients_changed: Emitted whenever the live client list changes.
        all_clients_changed: ``(active, unhooked)`` whenever either list changes.
        wizard_names_changed: Emitted with the per-client wizard names when they change.
        recovery_error:  Emitted when recovery cannot proceed due to a user-fixable error.

    The first three are core's listeners, replaced by Qt signals so every connection
    behaves as it did when this class was a ``QObject``. They live on a ``QObject`` of
    their own, since core's manager is not one.
    """

    def __init__(
        self,
        handler,
        game_view,
        drops_widget,
        get_recovery_state: Callable[[], str | None],
        parent=None,
    ) -> None:
        super().__init__(handler, SettingsManager.instance(), game_view=game_view)
        self._signals = _Signals(parent)
        self.clients_changed = self._signals.clients_changed
        self.all_clients_changed = self._signals.all_clients_changed
        self.wizard_names_changed = self._signals.wizard_names_changed
        self.recovery_error = self._signals.recovery_error
        self._drops_widget = drops_widget
        self._get_recovery_state = get_recovery_state

        self._recovery_in_progress: bool = False
        self._recovery_task: asyncio.Task | None = None
        # Windows being closed on purpose for a relog. Consumed by _recover_removed so
        # scheduled maintenance is not filed in the log as a fault.
        self._relogging_hwnds: set[int] = set()
        self._relog_watch_task: asyncio.Task | None = None
        self._relog_in_progress: bool = False
        self._machine = None

        # Connected first, so the machine has the new list before anything else hears it,
        # as when the loop handed it over itself.
        self.clients_changed.connect(self._hand_clients_to_machine)
        # The drop tables are kept per player, so they follow the players' order.
        self.clients_reordered.connect(self._drops_widget.reorder_clients)
        self.clients_removed.connect(self._recover_removed)

    def set_machine(self, machine) -> None:
        """Called by AppController when a bot starts (machine != None) or stops (machine == None)."""
        self._machine = machine
        if machine is not None:
            # Fresh run — clear any leftover recovery state from the previous run.
            self._pending_recovery.clear()
            self._recovery_in_progress = False
            self._relogging_hwnds.clear()
            self._relog_in_progress = False
            self._start_relog_watch()
        else:
            self._stop_relog_watch()

    def _hand_clients_to_machine(self, _clients: list) -> None:
        """Give a running bot the client list core just published.

        The live list itself, not the copy some publications carry: a node holds what it
        is handed, and the list core rebuilds is the one it routes to next.
        """
        if self._machine:
            self._machine.set_clients(self._clients)

    def recovery_gaps(self) -> list[tuple[int, str]]:
        """Live clients that could not be recovered, as ``(position, reason)`` pairs.

        Position is 1-based and matches the client list order, so it lines up with the
        Player numbering the sidebar and game tiles show.

        Both halves of the recovery path are checked, because either one missing has the
        same outcome: an account to relaunch, and a password to log it back in. The
        account map is private and the password lookup is CredentialStore's, so this is
        the only place both can be asked at once.
        """
        gaps: list[tuple[int, str]] = []
        for position, client in enumerate(self._clients, start=1):
            gap = self._recovery_gap(client.window_handle, position)
            if gap:
                gaps.append((position, gap))
        return gaps

    def recovery_gap_for(self, hwnd: int) -> str | None:
        """Why this one client could not be recovered, or None when it could.

        The single-client form of ``recovery_gaps``, for a node that is about to hand a
        client to recovery mid-run and needs to know first whether it would come back.
        """
        position = next((i for i, c in enumerate(self._clients, start=1) if c.window_handle == hwnd), 0)
        return self._recovery_gap(hwnd, position)

    def _recovery_gap(self, hwnd: int, position: int) -> str | None:
        username = self._hwnd_account_map.get(hwnd)
        if not username:
            return recovery_readiness.NO_ACCOUNT
        try:
            has_password = bool(CredentialStore.retrieve(username))
        except Exception:
            logger.exception("Could not check the stored password for P%d's account", position)
            return None  # Credential Manager is unhappy; do not claim a gap we cannot verify
        if not has_password:
            display = CredentialStore.display_name(username, self._alias_for(username))
            return recovery_readiness.NO_PASSWORD.format(account=display)
        return None

    async def drive_client_in(self, expected_title: str) -> bool:
        """Advance a just-launched window through to in-game, then tidy up after it.

        This is the callback the launcher is handed (see MainWindow.set_auto_select_driver);
        it lives here because the completion signal is has_client_titled, which only this
        class can answer. Recovery uses it too, so a relaunched client and a freshly
        launched one take exactly the same path in.

        The single place the auto-select setting is read. With it off the loading screens
        are still cleared — they carry no decision — but the window is then left sitting at
        character select for the user to pick from, and False says so: the client is not
        in-game. Callers that need it anyway (recovery) fall back to await_client_in.
        """
        if not SettingsManager.instance().auto_select_character:
            await auto_select.drive_to_character_select(
                expected_title,
                lambda: self.has_client_titled(expected_title),
                lambda: self.pending_client_titled(expected_title),
            )
            return False
        return await auto_select.drive_client_in(
            expected_title,
            lambda: self.has_client_titled(expected_title),
            lambda: self.pending_client_titled(expected_title),
            # Remember Realm only matters once there is a realm to come back to.
            remember_realm=bool(SettingsManager.instance().favorite_realm),
        )

    def _refuse_reorder(self) -> bool:
        """True, and the sidebar redrawn in the order it really is, while a bot is running.

        The Players tab locks its grips for a run, so this is the backstop for a drag that
        was already under way when the run started. ``_clients`` is the very list the
        running node was handed, and a reorder rewrites it in place, so the node would see
        its player indices change under it. The redraw is what puts a dropped card back:
        the column already shows the order the user dragged to, and nothing else would
        rebuild it.
        """
        if self._machine is None:
            return False
        logger.info("Ignoring a player reorder while a bot is running")
        self.all_clients_changed.emit(list(self._clients), list(self._unhooked_clients))
        return True

    def _recover_removed(self, removals: list[Removal]) -> None:
        """Queue recovery for the clients core reports gone, if a bot is running.

        Core has already emptied each one's slot and remembered it as vacated; this holds
        the slot for the client in ``_pending_recovery``, which core's slot code honors,
        and pauses the bot until the queue has brought every one back.
        """
        machine = self._machine
        bot_running = machine is not None and not machine.is_stopped
        for removal in removals:
            hwnd, username, orig_pos = removal.hwnd, removal.username, removal.slot
            # Was this window closed by relog_clients rather than lost? Consumed here, so a
            # client that comes back and later dies for real is reported as a disconnect.
            relogging = hwnd in self._relogging_hwnds
            self._relogging_hwnds.discard(hwnd)
            if not username:
                self._log_unrecoverable_removal(hwnd, removal.title, bot_running)
                continue
            if orig_pos is not None:
                if bot_running:
                    self._pending_recovery[orig_pos] = username
                    # A relog is routed through this same path on purpose, and filing
                    # scheduled maintenance as a fault is the kind of double-meaning that
                    # makes a ten-hour log unreadable. The wording is the only thing
                    # that differs — everything downstream of here is identical.
                    if relogging:
                        logger.info(
                            "Client at slot %d (account: %s) closed for a relog — bringing it back",
                            orig_pos,
                            account_label(username),
                        )
                    else:
                        logger.warning(
                            "Client at slot %d (account: %s) disconnected — queuing recovery",
                            orig_pos,
                            account_label(username),
                        )
            elif bot_running:
                # Mapped to an account, but holding no slot — the other way a removal can
                # skip recovery in silence.
                logger.warning(
                    "Client hwnd=%d (account: %s) disconnected, but holds no slot in the client "
                    "ordering — recovery is being SKIPPED",
                    hwnd,
                    account_label(username),
                )
        if self._pending_recovery:
            # Pause immediately to prevent FAILURE results from accumulating before
            # recovery resumes the machine. Idempotent if already paused.
            if bot_running:
                machine.pause(PauseHold.RECOVERY)
            if not self._recovery_in_progress:
                self._recovery_in_progress = True
                self._recovery_task = asyncio.create_task(self._run_recovery_queue())

    def _log_unrecoverable_removal(self, hwnd: int, title: str | None, bot_running: bool) -> None:
        """Say out loud that a disconnected client has no account, and so cannot be recovered.

        The account map is keyed on the ``[account] Wizard101`` window title, and that
        title is written by launch_profile during login — so only a client BartleBot
        launched carries one. A window started from the game's own launcher and adopted
        here never enters the map, and every recovery decision downstream is keyed on a
        username this client does not have.

        Until this existed the miss was a bare ``continue``: an overnight run lost every
        client, queued nothing, and left five hours of silence in the log with no
        indication that recovery had been skipped rather than attempted.
        """
        described = repr(title) if title else "unreadable"
        if not bot_running:
            logger.debug("Client hwnd=%d (title %s) closed; it has no account mapping", hwnd, described)
            return
        logger.warning(
            "Client hwnd=%d (title %s) disconnected, but no account is mapped to it — "
            "recovery is being SKIPPED and the bot will stay paused. Only clients launched "
            "by BartleBot carry the '[account] Wizard101' title that ties a window to an "
            "account; one started elsewhere and adopted here has no credentials to log back in with.",
            hwnd,
            described,
        )

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    async def _run_recovery_queue(self) -> None:
        """Bring every pending slot back, then resume the FSM once.

        Slots are drained in batches rather than one at a time: a scheduled relog closes
        the whole fleet, and relaunching four clients in turn costs four full launches --
        spawn, two loading screens, character select, load-in -- where one batch costs
        roughly one. See ``launch_accounts``.

        The FSM stays paused for the entire duration. Once every pending recovery has
        completed successfully, the FSM is jumped to recovery_state and resumed exactly
        once.
        """
        try:
            await self._settle_recovery_batch()
            relaunched: set[str] = set()
            logged_out = False
            while True:
                while self._pending_recovery:
                    batch = dict(self._pending_recovery)
                    try:
                        await self._reconnect_clients(batch)
                    except Exception:
                        logger.exception(
                            "Unhandled error recovering slot(s) %s — stopping bot",
                            ", ".join(str(pos) for pos in sorted(batch)),
                        )
                        self._pending_recovery.clear()
                        if self._machine:
                            self._machine.stop()
                        return
                    for orig_pos in batch:
                        self._pending_recovery.pop(orig_pos, None)
                    relaunched.update(batch.values())

                # All pending clients are back in-game — settle any fight, regroup, then jump to
                # recovery state and resume. Grouping happens while the FSM is still paused so no
                # node is competing for the mouse, and before the state jump so the bot resumes
                # with the group already restored. The fight comes first: the pause keeps every
                # combat watcher off, and the survey would open the social window over the cards.
                if self._machine is None or self._machine.is_stopped:
                    # A slot that could not be relaunched stopped the run. Regrouping now would
                    # open the social window on clients the user has just been handed back.
                    return
                # A relaunched player that cannot get back into its fight has the rest logged
                # out, and they come back through the loop above. Once only: the second
                # settle fights whatever it finds, as any other would.
                rejoining = [] if logged_out else self._clients_of(relaunched)
                leaving = await self._machine.settle_fights(rejoining)
                if not leaving or not await self._log_out_for_recovery(leaving):
                    break
                logged_out = True

            if self._machine is None or self._machine.is_stopped:
                # Stopped during the fight: the clients are the user's now (the same reason).
                return
            await self._regroup_after_recovery()

            machine = self._machine
            if machine and not machine.is_stopped:
                recovery_state = self._get_recovery_state()
                if recovery_state:
                    machine.force_state(recovery_state)
                    # Recovery's own hold only: a pause the user asked for meanwhile stands.
                    machine.resume(PauseHold.RECOVERY)
                    logger.info("All clients recovered — FSM resuming at %r", recovery_state)
                else:
                    # Unreachable via a loaded bot: BotConfig requires recovery_state and
                    # validates it against the states list. Only a run with no config at
                    # all lands here, which means something else has already gone wrong.
                    logger.error("Recovery finished with no bot config loaded — stopping.")
                    machine.stop()
        finally:
            self._recovery_in_progress = False

    async def _settle_recovery_batch(self) -> None:
        """Wait for a relog in flight to finish closing, so its clients recover together.

        Only a relog closes several clients on purpose, and it is the one case where the
        batch is worth waiting for: the closes are issued together but each window is only
        noticed missing on the next ``_client_loop`` poll, so starting on the first arrival
        would relaunch the fleet one client at a time. A crash queues whatever it queues
        and is not waited on — there is nothing more coming.

        Bounded, and never the difference between recovering and not: if the relog is still
        closing when the budget runs out, the queue proceeds with what it has and picks up
        the stragglers on its next pass.
        """
        if not self._relog_in_progress:
            return
        loop = asyncio.get_event_loop()
        deadline = loop.time() + RECOVERY_BATCH_SETTLE
        while self._relog_in_progress and loop.time() < deadline:
            await asyncio.sleep(CLIENT_DETECT_INTERVAL)
        # The last process to die is still one poll away from being noticed missing.
        await asyncio.sleep(CLIENT_DETECT_INTERVAL * 2)

    def _clients_of(self, usernames: set[str]) -> list:
        """The live clients logged in to *usernames*' accounts."""
        return [c for c in self._clients if self._hwnd_account_map.get(c.window_handle) in usernames]

    async def _log_out_for_recovery(self, clients: list) -> bool:
        """Log *clients* out, so the whole team starts again beside the player recovery relaunched.

        For the players left fighting a fight a relaunched player could not get back into,
        usually a dungeon that refuses the port. Fighting on a player short loses fights a
        strategy was written for a full team, and logging out inside a dungeon puts a character
        back at its entrance, where the relaunched player already is.

        Esc → Quit first, every player at once (``relog.log_out_and_in``): seconds, and the client
        is never closed, so it comes back in the world with nothing to relaunch. A player that
        never got past Quit is still in the fight, and is closed instead, the way a relog closes
        it: the log files the removal as a relog, and the next pass of the queue relaunches it.
        One that got past Quit but could not finish has been handed to crash recovery by the
        relog already. The queue is waited on until every closed player is in it, so they come
        back as one batch.

        False, logging nobody out, when any of them could not be relaunched should it come to
        that: the fight goes on without the relaunched player rather than the run stopping a
        team short.
        """
        from bartlebot.nodes.utility import relog as relog_node  # noqa: PLC0415

        machine = self._machine
        for client in clients:
            gap = self.recovery_gap_for(client.window_handle)
            if gap:
                logger.warning(
                    "Leaving the fight to the players in it — hwnd=%d could not be brought back after logging out: %s",
                    client.window_handle,
                    gap,
                )
                return False

        machine.suspend_health_checks_for([c.window_handle for c in clients], watch_disconnects=True)
        logger.info("Logging out %d player(s) so the whole team starts again together", len(clients))
        pacing = relog_node.RelogPacing()
        outcomes = await asyncio.gather(
            *[relog_node.log_out_and_in(machine, c, in_battle=True, pacing=pacing) for c in clients],
            return_exceptions=True,
        )
        back = [c for c, outcome in zip(clients, outcomes) if outcome == relog_node.BACK]
        abandoned = [c for c, outcome in zip(clients, outcomes) if outcome == relog_node.ABANDONED]
        stuck = [c for c in clients if c not in back and c not in abandoned]
        # An abandoned player stays flagged until recovery's resume(), as the relog node leaves it.
        machine.resume_health_checks_for([c.window_handle for c in back])
        if back:
            logger.info("%d player(s) logged out of the fight and back in", len(back))

        closed = await self._close_for_recovery(stuck) if stuck else []
        queued = {self._hwnd_account_map[c.window_handle] for c in [*abandoned, *closed]}
        if not back and not queued:
            return False

        # A closed window is only noticed missing on the next _client_loop poll.
        loop = asyncio.get_event_loop()
        deadline = loop.time() + RECOVERY_BATCH_SETTLE
        while not queued <= set(self._pending_recovery.values()) and loop.time() < deadline:
            await asyncio.sleep(CLIENT_DETECT_INTERVAL)
        return True

    async def _close_for_recovery(self, clients: list) -> list:
        """Close the players Esc → Quit could not log out, as a relog closes them. Returns the ones that closed.

        One that will not close is still in the fight, and gets its health checks back.
        """
        logger.warning("%d player(s) could not log out from the game's menu — closing them instead", len(clients))
        hwnds = [c.window_handle for c in clients]
        self._relogging_hwnds.update(hwnds)
        results = await asyncio.gather(
            *[force_close_client(c.window_handle, pid=c.process_id) for c in clients],
            return_exceptions=True,
        )
        survivors = [c for c, ok in zip(clients, results) if ok is not True]
        if survivors:
            logger.error(
                "Client(s) %s would not close; they stay in the fight",
                ", ".join(f"hwnd={c.window_handle}" for c in survivors),
            )
            self._relogging_hwnds.difference_update(c.window_handle for c in survivors)
            self._machine.resume_health_checks_for([c.window_handle for c in survivors])
        return [c for c in clients if c not in survivors]

    async def _regroup_after_recovery(self) -> None:
        """Rebuild the in-game group after a recovery, if the user has that enabled.

        A crashed client is dropped from its group by the game and comes back ungrouped,
        which silently costs the fleet the guaranteed first turn in combat. Grouping is
        done through the friends list, which works across zones, so this runs correctly
        wherever the recovered client happens to have landed.

        Never raises and never stops the bot: running ungrouped is a degraded run, but
        stopping outright turns one crash into a dead overnight session.
        """
        from bartleby.runtime.clients import group_manager  # noqa: PLC0415
        from bartleby.runtime.game import wizard_names  # noqa: PLC0415

        if not SettingsManager.instance().auto_group_players:
            return

        clients = list(self._clients)
        if len(clients) < 2:
            logger.debug("Skipping post-recovery grouping — fewer than two clients connected")
            return

        try:
            # Survey once and hand the result to form_group — see the matching comment in
            # AppController._form_pre_run_group.
            states = await group_manager.read_group_states(clients)
            names = await wizard_names.resolve_names(clients)
            if all(s.grouped for s in states) and group_manager.same_group(states) is not False:
                logger.debug("Skipping post-recovery grouping — all clients are already in one group")
                self._refresh_group_routes(names, states)
                return
            result = await group_manager.form_group(
                clients, names, states=states, safety_delay=SettingsManager.instance().default_safety_delay
            )
            if result.success:
                logger.info("Post-recovery grouping: %s", result.summary())
            else:
                logger.warning(
                    "Post-recovery grouping did not fully succeed (%s) — the bot will resume ungrouped",
                    result.summary(),
                )
            self._refresh_group_routes(names, result.states)
        except Exception:
            logger.warning("Post-recovery grouping failed — the bot will resume ungrouped", exc_info=True)

    def _refresh_group_routes(self, names: list, states: list | None) -> None:
        """Bring the run's port map up to date with the group a recovery regroup left.

        The map is surveyed before the run, and a crash drops a client from its group, so
        without this a route plan can count on a group port that no longer exists — found
        out only when the port fails and is demoted. Only the group routes move: friendships
        survive a crash. A run with no map yet is left alone; navigation surveys when it
        first needs one.
        """
        ports = self._machine.port_map if self._machine is not None else None
        if ports is None or states is None:
            return
        ports.set_groups(names, [state.roster for state in states])

    def _recovery_target(self, orig_pos: int, username: str) -> "_RecoveryTarget | None":
        """Everything needed to relaunch one slot, or None if it cannot be relaunched.

        None means the machine has already been stopped and the user told why: a bot
        mid-run cannot continue a player short, and no stored password is not something
        this can drive its way out of.
        """
        machine = self._machine
        password = CredentialStore.retrieve(username)
        if not password:
            logger.error("No stored password for P%d's account — stopping bot", orig_pos + 1)
            display = CredentialStore.display_name(username, self._alias_for(username))
            self.recovery_error.emit(
                f"No stored password for account '{display}'.\n\n"
                "Open Settings → Accounts and save the password, then restart the bot."
            )
            if machine:
                machine.stop()
            return None

        display_name = CredentialStore.display_name(username, self._alias_for(username))
        return _RecoveryTarget(
            orig_pos=orig_pos,
            username=username,
            password=password,
            display_name=display_name,
            title=f"[{display_name}] Wizard101",
            # The last known resolution for this client, captured while the window was
            # alive. None falls back to the global setting, for a client never seen before.
            resolution=self._client_resolutions.get(username),
        )

    async def _reconnect_clients(self, slots: dict[int, str]) -> None:
        """Relaunch these slots together and put each client back where it was.

        The screens between auto-login and in-game are driven by drive_client_in, the same
        path a normal launch takes — see launcher/auto_select.py for why that has to be
        done blind. Recovery differs only in what failure means: a bot mid-run cannot
        continue a player short, so a drive that gives up stops the machine.

        The clients go out as one batch, and their drives run concurrently. That is the
        launcher's ordinary shape rather than a liberty taken here — a launch profile has
        always advanced its whole team at once, and auto-select posts its keystrokes with
        ``PostMessageW``, so no drive needs focus or competes with another for it.

        One exception: the game reads its resolution from a single config.xml at startup,
        so clients that want different resolutions cannot share a batch. They are grouped
        by resolution and each group is launched in turn. A fleet normally shares one.
        """
        from bartleby.runtime.launcher.launch_profile import launch_accounts  # noqa: PLC0415

        machine = self._machine
        if machine is None:
            return

        targets: list[_RecoveryTarget] = []
        for orig_pos, username in sorted(slots.items()):
            target = self._recovery_target(orig_pos, username)
            if target is None:
                # Nothing is launched when any slot is unrecoverable: the run is over
                # either way, and half a fleet relaunched is worse than none.
                return
            targets.append(target)
        if not targets:
            return

        auto_select_on = SettingsManager.instance().auto_select_character
        if not auto_select_on:
            # The user has opted out of automatic character selection, so recovery waits
            # for them rather than pressing anything — but it still has to wait, since the
            # run cannot resume a player short.
            self._warn_awaiting_manual_login(targets)

        reached: dict[str, bool] = {}

        async def _drive(title: str) -> bool:
            ok = await self.drive_client_in(title)
            reached[title] = ok
            return ok

        for resolution, group in _group_by_resolution(targets):
            logger.info(
                "Relaunching %d player(s) for recovery: %s",
                len(group),
                ", ".join(f"'{t.display_name}' at slot {t.orig_pos}" for t in group),
            )
            try:
                await launch_accounts(
                    [(t.username, t.password, t.display_name) for t in group],
                    resolution=resolution,
                    # Handed the drive either way: with auto-select off it presses nothing
                    # that picks a character, it just clears the loading screens so the
                    # user arrives at a character-select screen rather than a black one.
                    drive=_drive,
                )
            except Exception:
                logger.exception(
                    "Failed to launch account(s) %s — stopping bot",
                    ", ".join(f"'{t.display_name}'" for t in group),
                )
                machine.stop()
                return

        if not auto_select_on:
            # The drive stopped at character select, so nothing above answered whether the
            # client got in — wait every window out at once rather than in turn.
            results = await asyncio.gather(*[self.await_client_in(t.title) for t in targets])
            reached = {t.title: ok for t, ok in zip(targets, results)}

        missing = [t for t in targets if not reached.get(t.title)]
        if missing:
            logger.error(
                "Timed out waiting for reconnected client(s) %s — stopping bot",
                ", ".join(f"'{t.display_name}'" for t in missing),
            )
            machine.stop()
            return

        for target in targets:
            logger.info(
                "Client '%s' recovered and in-game, slot %d restored",
                target.display_name,
                target.orig_pos,
            )
            # Restore the slot so the next _client_loop rebuild puts the client back in
            # order. machine.clients is kept in sync by _client_loop automatically.
            self._client_order[target.orig_pos] = target.username
            client = self.client_titled(target.title)
            if client is not None:
                # Just logged in: a recovery state of Relog has nothing to do for it.
                machine.note_logged_in([client.window_handle])

    def _warn_awaiting_manual_login(self, targets: list["_RecoveryTarget"]) -> None:
        """Say once, for the whole batch, that these windows are waiting to be logged in.

        One message rather than one per client: with auto-select off a fleet relog would
        otherwise raise a dialog per player, each saying the same thing.
        """
        names = ", ".join(f"'{t.display_name}'" for t in targets)
        logger.warning(
            "Auto-select is off — %s %s been relaunched and %s waiting at character select. Log in to resume the run.",
            names,
            "have" if len(targets) > 1 else "has",
            "are" if len(targets) > 1 else "is",
        )
        self.recovery_error.emit(
            f"{names} {'have' if len(targets) > 1 else 'has'} been relaunched and "
            f"{'are' if len(targets) > 1 else 'is'} waiting at character select.\n\n"
            "Log in to resume the run, or turn on Settings → Game → "
            "Automatically select a character to have this done for you."
        )

    # ------------------------------------------------------------------
    # Relogging
    # ------------------------------------------------------------------

    def _start_relog_watch(self) -> None:
        """Begin watching for a due relog. Called at the start of every run."""
        self._stop_relog_watch()
        self._relog_watch_task = asyncio.create_task(self._relog_watch_loop())

    def _stop_relog_watch(self) -> None:
        """Cancel the relog watch. Called when the run ends, and before starting a new one."""
        if self._relog_watch_task is not None:
            self._relog_watch_task.cancel()
            self._relog_watch_task = None

    async def _relog_watch_loop(self) -> None:
        """Relog the fleet on a timer, and any one client running out of GDI objects.

        Both triggers live here rather than in loops of their own because they ask the same
        question — is now a safe moment to take clients away from the FSM — and because
        either kind of relog resets the timer. The policy itself is in ``bartlebot.clients.relog``;
        this supplies only the clock and the readings.

        Never raises: it outlives every node, and an exception escaping here would take the
        timer with it for the rest of the run.
        """
        loop = asyncio.get_event_loop()
        last_relog_at = loop.time()
        while True:
            try:
                await asyncio.sleep(RELOG_WATCH_INTERVAL)
                machine = self._machine
                if machine is None or machine.is_stopped or self._relog_in_progress:
                    continue

                now = loop.time()
                interval = SettingsManager.instance().relog_interval_hours
                if relog.relog_due(now, last_relog_at, interval):
                    logger.info(
                        "Scheduled relog due — %.1f hours since the last one",
                        (now - last_relog_at) / relog.SECONDS_PER_HOUR,
                    )
                    if await self.relog_clients(list(self._clients), reason="scheduled"):
                        last_relog_at = loop.time()
                    continue

                starved = self._clients_near_gdi_quota()
                if starved and await self.relog_clients(starved, reason="GDI quota"):
                    last_relog_at = loop.time()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Relog watch loop error")

    def _clients_near_gdi_quota(self) -> list:
        """Live clients close enough to the 10,000 GDI object cap to need a relog.

        One client has been seen reaching the cap exactly, after which every GDI allocation
        in that process fails. A failed read answers 0, which is never a trigger — a live
        client always holds thousands.
        """
        starved = []
        for client in list(self._clients):
            count = gdi_object_count(client.process_id)
            if relog.gdi_relog_due(count):
                logger.warning(
                    "Client hwnd=%d holds %d GDI objects of a %d quota — relogging it before allocations start failing",
                    client.window_handle,
                    count,
                    relog.GDI_QUOTA,
                )
                starved.append(client)
        return starved

    async def relog_clients(self, targets: list, *, reason: str) -> bool:
        """Close these clients on purpose so recovery brings them back fresh.

        The cycle *is* the crash-recovery path, deliberately: close the process and let
        ``remove_dead_clients()`` -> core's ``_on_clients_removed`` -> ``_recover_removed`` ->
        ``_run_recovery_queue`` do
        everything after that. Nothing here relaunches anything.

        Returns whether the clients were actually closed. False means the cycle was refused
        or abandoned, and the caller should leave its timer alone and try again later.

        The ordering is not negotiable:

        1. **Refuse unless every client could be recovered.** A crash happens whether or not
           recovery can cope with it; a scheduled relog is a healthy run being broken on
           purpose, so it must not start unless it is known to be reversible.
           ``_reconnect_clients`` stops the bot on a login failure — right for a crash, and
           unacceptable as a failure mode this feature introduces.
        2. **Wait for a real node boundary**, not merely for ``is_paused``. See
           ``BotMachine.wait_until_parked``.
        3. **Close, never unhook.** Killing the process sidesteps the fixed sleep in
           ``HookHandler._rewrite_autobot``, which is a hazard on exactly the lagging
           clients this exists for.
        """
        machine = self._machine
        if machine is None or machine.is_stopped or not targets:
            return False
        if self._relog_in_progress or self._recovery_in_progress or self._pending_recovery:
            logger.debug("Skipping %s relog — a recovery or relog is already in flight", reason)
            return False
        if machine.is_paused or machine.is_ambient_combat_active:
            # The bot is held by someone else: the user, or the ambient watcher mid-fight. The
            # relog pauses and resumes on its own account, so starting now would resume a bot
            # it never paused -- running a node beside the ambient fight, or overriding the
            # user's pause (#154). The next tick tries again.
            logger.debug("Deferring %s relog — the bot is paused or fighting", reason)
            return False

        gaps = self.recovery_gaps()
        if gaps:
            for position, gap in gaps:
                logger.warning(
                    "Skipping %s relog — Player %d could not be brought back: %s",
                    reason,
                    position,
                    gap,
                )
            return False

        self._relog_in_progress = True
        try:
            machine.pause(PauseHold.RELOG)
            if not await machine.wait_until_parked(RELOG_PARK_TIMEOUT):
                logger.warning(
                    "Skipping %s relog — the bot did not reach a node boundary within %.0fs",
                    reason,
                    RELOG_PARK_TIMEOUT,
                )
                machine.resume(PauseHold.RELOG)
                return False
            if machine.is_ambient_combat_active:
                # Parked is not idle: the ambient watcher runs alongside the run loop and
                # can be mid-fight on every client. Try again next tick rather than closing
                # a client out of a duel.
                logger.info("Deferring %s relog — ambient combat is still being handled", reason)
                machine.resume(PauseHold.RELOG)
                return False

            hwnds = [c.window_handle for c in targets]
            machine.suspend_health_checks_for(hwnds)
            self._relogging_hwnds.update(hwnds)
            logger.info("Relogging %d player(s) — reason: %s", len(targets), reason)

            # Closed together rather than in turn: force_close_client waits out the
            # process, so a fleet closed one at a time spends that wait per client and
            # spreads the removals across several _client_loop polls -- which is what
            # decides whether they come back as one batch or one at a time.
            results = await asyncio.gather(
                *[force_close_client(c.window_handle, pid=c.process_id) for c in targets],
                return_exceptions=True,
            )
            survivors = [c for c, ok in zip(targets, results) if ok is not True]
            for client, result in zip(targets, results):
                if isinstance(result, BaseException):
                    logger.error("Closing client hwnd=%d for a relog raised", client.window_handle, exc_info=result)
            if len(survivors) < len(targets):
                # Recovery brings the closed ones back and resumes on its own hold. Taken here
                # rather than left to the removal, which the client loop only notices on its
                # next poll, so the bot is never free to run in between.
                machine.pause(PauseHold.RECOVERY)
            # Released either way: with nothing closed there is nothing for recovery to do, and
            # the bot carries on with the fleet it has.
            machine.resume(PauseHold.RELOG)
            if survivors:
                # Some of the fleet is already gone and these will not die. Recovery brings
                # back what it can; saying so is what stops the log reading as though the
                # cycle completed.
                logger.error(
                    "Relog incomplete — client(s) %s would not close, so the fleet is part-way "
                    "through a relog. Recovery will restore the players that did close.",
                    ", ".join(f"hwnd={c.window_handle}" for c in survivors),
                )
                return False
            return True
        finally:
            self._relog_in_progress = False
