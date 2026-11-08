"""A run, from Run to the end: the checks and dialogs before it, the pre-run setup, and the machine's callbacks.

The run sequence refuses what can never start (a restricted zone, players who must
merge in a Separate bot, a variable error, a player in a fight or with an action that
cannot be pressed), then asks what the user may wave through (the safety warnings,
the combat setup, recovery gaps), then builds the ``BotMachine`` and runs the pre-run
setup -- out of freecam, decks, the group, the ports survey -- before the FSM. While
it runs, the machine's callbacks drive the window, and a crashed client or a
restricted zone is closed or detached here. Pause, Resume and Stop come from Bar B.
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from bartleby.qt.app.logging_setup import add_gui_callback, remove_gui_callback, session_log_dir
from bartleby.runtime import run_trace
from bartleby.runtime.clients import group_manager
from bartleby.runtime.clients.window_kill import force_close_client
from bartleby.runtime.game import wizard_names
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

from bartlebot.bot import variables
from bartlebot.bot.machine import BotMachine
from bartlebot.clients import recovery_readiness, run_probe
from bartlebot.combat.combat_resolution import CombatResolver, Level, describe_run
from bartlebot.combat.combat_warnings import CombatCheck, check_combat
from bartlebot.content.store_roots import dev_mode
from bartlebot.game import restricted_zones
from bartlebot.nodes.registry import NODE_REGISTRY, NODE_SPECS
from bartlebot.options import option_values, player_realms
from bartlebot.settings_manager import SettingsManager

if TYPE_CHECKING:
    pass

from collections.abc import Callable

logger = logging.getLogger(__name__)


def start_run_trace(slot_of) -> Path | None:
    """Begin the run's trace, in developer mode only, under ``logs/trace/``. Returns its file.

    Core's ``run_trace`` writes wherever it is told; whether a run is traced at all is
    BartleBot's call, and it is only while ``BARTLEBOT_DEV`` is set (``store_roots.dev_mode``).
    ``tools/run_trace_report.py`` summarizes the newest one.
    """
    if not dev_mode():
        return None
    return run_trace.start(slot_of, session_log_dir() / "trace")


class Run:
    """The bot run: what starts it, what it drives, what stops it. Takes what it needs: see ``__init__``."""

    def __init__(
        self,
        window,
        client_manager,
        documents,
        decks,
        authoring,
        flee_marks,
        *,
        on_run_starting: Callable[[], None],
        on_run_ended: Callable[[], None],
    ) -> None:
        self._window = window
        self._client_manager = client_manager
        self._documents = documents
        self._decks = decks
        # The authoring utilities step aside for a run: speed back to the game's, no fighting.
        self._authoring = authoring
        # Every bot's fights fled on a default, for as long as the app is open: the Combat
        # tab marks them (Stage 7 of epic #201). See _on_machine_fight_fled.
        self._flee_marks = flee_marks
        # The controller's own part at either end: the hotkeys off for the run, and back by focus after.
        self._on_run_starting = on_run_starting
        self._on_run_ended = on_run_ended
        self._current_machine: BotMachine | None = None

        # Long-lived asyncio task — held here to prevent GC (event loop keeps weak refs only).
        self._bot_task: asyncio.Task | None = None
        # The run-start read of who already has an elixir. See on_run_requested.
        self._run_probe: asyncio.Task | None = None
        # The pre-run setup of the run in progress, so Stop can cut it short.
        self._pre_run: asyncio.Task | None = None

        # Same reason: in-flight client close sequences, one per crashed client.
        self._close_tasks: set[asyncio.Task] = set()

    @property
    def is_running(self) -> bool:
        """Whether a machine is built and not yet torn down: from the pre-run setup to the end of the run."""
        return self._current_machine is not None

    @property
    def busy(self) -> bool:
        """Whether a bot is starting or running: the update popup waits for it to stop."""
        if self._current_machine is not None:
            return True
        return any(task is not None and not task.done() for task in (self._bot_task, self._run_probe))

    def on_run_requested(self) -> None:
        if not self._client_manager.clients or self._documents.loaded_config is None:
            return
        if not self._documents.recompose_before_run():
            return
        if not self._check_restricted_zones():
            return
        if not self._check_player_realms():
            return
        if not self._check_variables():
            return
        from bartlebot.gui.dialogs.safety_warning_dialog import (  # noqa: PLC0415
            level_scaling_players,
            uses_friends_list,
        )

        clients = self._client_manager.clients
        players = level_scaling_players(self._documents.loaded_config, len(clients))
        friends_list = uses_friends_list(self._documents.loaded_config, len(clients))
        # Who is in a fight, and whether a level scaling player already has an elixir, are
        # memory reads, which are async, and what they feed is a blocking dialog, which must
        # not open inside a task (see "qasync + Python 3.13" in bartlebot/gui/CLAUDE.md). So
        # the reads run as a task and the rest of the sequence runs from its done callback,
        # which the loop calls outside any task.
        if self._run_probe is not None and not self._run_probe.done():
            return  # a second press while the first is still reading
        self._run_probe = asyncio.create_task(run_probe.probe_before_run(clients, players, friends_list))
        self._run_probe.add_done_callback(self._on_run_probe_done)

    def _on_run_probe_done(self, probe: asyncio.Task) -> None:
        """Carry on with the run request once the reads are in. A failed read stops nothing."""
        if probe.cancelled():
            return
        if probe.exception() is not None:
            logger.warning("Could not read the players before the run", exc_info=probe.exception())
            self._finish_run_request((), ())
            return
        result: run_probe.RunProbe = probe.result()
        if result.fighting:
            self._refuse_while_fighting(result.fighting)
            return
        if result.key_problems:
            self._refuse_unpressable_keys(result.key_problems)
            return
        self._finish_run_request(result.elixir_players, result.quick_teleport_players)

    def _refuse_while_fighting(self, fighting: list[int]) -> None:
        """Refuse to start while a player is in a fight (#203).

        Tells rather than asks. Everything before the first node needs the players out of
        battle: decks cannot be changed mid-fight, and forming the group opens the social
        window over the cards, which is how players come to sit out rounds until the game
        asks "You Are Idle". Ending the fight first costs the user seconds.
        """
        players = ", ".join(f"P{n}" for n in fighting)
        logger.warning("Refusing to run: %s in a fight", players)
        QMessageBox.warning(self._window, "Players in a Fight", run_probe.refusal_text(fighting))

    def _refuse_unpressable_keys(self, problems: tuple[str, ...]) -> None:
        """Refuse to start while a player has an action BartleBot cannot press.

        Tells rather than asks, as the fight refusal does: a node pressing that action would
        fail partway through the run, or type into the chat line, and only the player can
        change a binding, in the game (``bartleby-core/docs/KEY_BINDINGS.md``).
        """
        for problem in problems:
            logger.warning("Refusing to run: %s", problem)
        QMessageBox.warning(self._window, "Run Bot", run_probe.key_refusal_text(problems))

    def _finish_run_request(self, elixir_players, quick_teleport_players) -> None:
        """The rest of the run sequence: the dialogs that ask, then the run itself."""
        if not self._client_manager.clients or self._documents.loaded_config is None:
            return
        from bartlebot.gui.dialogs.safety_warning_dialog import (  # noqa: PLC0415
            SafetyWarningDialog,
            collect_safety_warnings,
        )

        safety_warnings = collect_safety_warnings(
            self._documents.loaded_config, self._client_manager.clients, elixir_players, quick_teleport_players
        )
        if safety_warnings:
            dlg = SafetyWarningDialog(safety_warnings, parent=self._window)
            if not dlg.exec():
                return

        if not self._confirm_combat():
            return

        if not self._confirm_recovery_gaps():
            return

        self._bot_task = asyncio.create_task(self._start_bot())

    def _check_restricted_zones(self) -> bool:
        """Refuse to start a bot that names a restricted zone. Returns whether to proceed.

        First check in the run sequence, and the only one with no way past it. The
        other pre-run dialogs ask; this one tells. Running the safety and combat
        warnings first would make the user answer questions about a run that was
        never going to start.

        Catching it here rather than letting the in-run watcher handle it means the
        client never travels: there is no arena to be seen standing in and no hooks
        to tear out.
        """
        if self._documents.loaded_config is None:
            return True
        found = restricted_zones.check_bot_config(self._documents.loaded_config)
        if not found:
            return True
        logger.critical("Refusing to run: bot names %d restricted zone(s)", len(found))
        QMessageBox.critical(
            self._window,
            "Restricted Zone",
            "This bot cannot be run. It sends a client to a zone BartleBot does not "
            "automate:\n\n"
            f"{restricted_zones.describe_found(found)}\n\n"
            f"{restricted_zones.PVE_ONLY_TEXT}",
        )
        return False

    def _check_player_realms(self) -> bool:
        """Refuse to run a Separate bot holding a node that merges players. Returns whether to proceed.

        Like the restricted-zone check it tells rather than asks: the run would contradict
        itself, porting everyone onto one realm in a bot that says they keep to their own.
        The Options and Option Values rows already refuse the choice, so this is what a hand-edited
        file or an override from before the node was added reaches.
        """
        if self._documents.loaded_config is None:
            return True
        value = option_values.option_value(self._documents.loaded_config, player_realms.OPTION, player_realms.TOGETHER)
        reason = player_realms.run_refusal(self._documents.loaded_config, value)
        if reason is None:
            return True
        logger.warning("Refusing to run: %s is Separate in a bot with a merging node", player_realms.OPTION)
        QMessageBox.critical(self._window, player_realms.OPTION, reason)
        return False

    def _check_variables(self) -> bool:
        """Refuse to run a bot whose variables have errors. Returns whether to proceed.

        Tells rather than asks, as the two checks before it do: a condition on an undeclared
        variable, or a write its type refuses, is a graph that cannot run as drawn
        (``bartlebot.bot.variables``). Warnings are the author's and never stop a run.
        """
        if self._documents.loaded_config is None:
            return True
        reason = variables.run_refusal(self._documents.loaded_config)
        if reason is None:
            return True
        logger.warning("Refusing to run: the bot's variables have errors")
        QMessageBox.critical(self._window, "Variables", reason)
        return False

    def _confirm_recovery_gaps(self) -> bool:
        """Warn about clients that could not be recovered, and return whether to proceed.

        Runs here in the Qt slot rather than inside _start_bot for the reason in
        CLAUDE.md: exec() from within an asyncio task wedges the tasks that wake while
        the dialog is open. No task exists yet at this point.

        Silent when every client is recoverable, which is the normal case — a dialog
        that appears every run stops being read.
        """
        gaps = self._client_manager.recovery_gaps()
        if not gaps:
            return True

        clients = self._client_manager.clients
        lines = [
            recovery_readiness.client_label(position, wizard_names.cached_name(clients[position - 1])) + f": {reason}"
            for position, reason in gaps
        ]
        for line in lines:
            logger.warning("Client cannot be recovered — %s", line)

        result = QMessageBox.warning(
            self._window,
            "Clients Cannot Be Recovered",
            recovery_readiness.warning_text(lines, len(clients)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        return result == QMessageBox.StandardButton.Yes

    def _confirm_combat(self) -> bool:
        """Check the combat setup, and return whether to proceed.

        Only a fault, a choice the user made that cannot work, opens a dialog, and its
        default is Go back. A notice is a fallback in use, and the run starts. Only the
        fights the pick's default covers, and each duel leaving a slot to chance, are
        logged here: the fallbacks are in the line ``_log_combat_sources`` writes before
        the first node, and are never logged twice
        (``docs/COMBAT_CONFIG.md``, "Before a run: faults and notices").
        """
        from bartlebot.gui.dialogs import combat_faults_dialog  # noqa: PLC0415

        check = self._check_combat()
        for notice in check.unassigned:
            logger.info("Combat: %s", notice)
        for notice in check.slot_order:
            logger.warning("Combat: %s", notice)
        if not check.faults:
            return True
        for fault in check.faults:
            logger.warning("Combat strategy problem: %s", fault)
        if combat_faults_dialog.confirm_run(self._window, check.faults):
            logger.info("Running anyway with %d combat strategy problem(s)", len(check.faults))
            return True
        return False

    def _check_combat(self) -> CombatCheck:
        """The combat setup's faults and notices for the bot about to run.

        Parsing every DSL the run could reach takes a noticeable moment on a large
        library, so this runs under a wait cursor.
        """
        if self._documents.loaded_config is None:
            return CombatCheck()

        active_bcs_id = SettingsManager.instance().get_active_strategy(self._documents.loaded_config.id)
        num_clients = len(self._client_manager.clients)

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            return check_combat(active_bcs_id, num_clients, self._documents.loaded_config)
        finally:
            QApplication.restoreOverrideCursor()

    def _log_combat_sources(self) -> None:
        """Log one line naming where the run's fights get their duel strategies, before the first node."""
        if self._documents.loaded_config is None:
            return
        try:
            pick = SettingsManager.instance().get_active_strategy(self._documents.loaded_config.id)
            resolver = CombatResolver(self._documents.loaded_config, len(self._client_manager.clients), pick)
            logger.info(describe_run(resolver))
        except Exception:
            logger.warning("Could not name the run's combat strategies", exc_info=True)

    async def _start_bot(self) -> None:
        """Build the machine, run the pre-run setup, then the run. Stop works throughout."""
        if not self._client_manager.clients or self._documents.loaded_config is None:
            return
        machine = BotMachine(
            clients=self._client_manager.clients,
            config=self._documents.loaded_config.to_machine_config(node_specs=NODE_SPECS),
            node_registry=NODE_REGISTRY,
            bot_config=self._documents.loaded_config,
            on_state_change=self._on_machine_state_change,
            on_transition=self._on_machine_transition,
            on_drop=self._on_machine_drop,
            on_client_disconnected=self._on_machine_client_disconnected,
            on_restricted_zone=self._on_machine_restricted_zone,
            on_explicit_stop=self._on_machine_explicit_stop,
            on_stopped=self._on_machine_stopped,
            recovery_gap=self._client_manager.recovery_gap_for,
            on_variable_change=self._on_machine_variable_change,
            on_fight_fled=partial(self._on_machine_fight_fled, self._documents.loaded_config.id),
        )
        self._current_machine = machine
        self._client_manager.set_machine(machine)
        self._window.set_start_enabled(False)
        self._window.action_import.setEnabled(False)
        self._window.right_sidebar.drops.setup_clients(len(self._client_manager.clients))
        self._window.right_sidebar.drops.clear_drops()
        log_panel = self._window.right_sidebar.log_panel
        if SettingsManager.instance().clear_log_on_run:
            log_panel.clear()
        log_cb = log_panel.append_entry
        add_gui_callback(log_cb)
        self._window.bot_canvas.set_running()
        self._window.right_sidebar.clients.set_bot_running(True)
        self._window.left_sidebar.set_bot_running(True)
        self._on_run_starting()
        start_run_trace(self._player_number)
        try:
            # A run walks at the game's own speed and fights through its own nodes.
            await self._authoring.suspend_for_run()
            # Inside the try so the finally still tears down if the user stops during
            # a slow apply, and after add_gui_callback so failures reach this run's
            # log panel rather than the previous run's. Its own task, so Stop can cancel it.
            self._pre_run = asyncio.ensure_future(run_trace.traced(self._run_pre_run_setup(), "pre_run"))
            try:
                await self._pre_run
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise  # this task was cancelled, not just the setup
                logger.info("Stopped before the run began; the rest of the pre-run setup was skipped")
                return
            finally:
                self._pre_run = None
            if machine.is_stopped:
                return  # a stop that landed just as the setup finished
            self._log_combat_sources()
            # Here rather than at the top: importing wakepy costs startup a tenth of a second (#154).
            from wakepy import ActivationError, keep  # noqa: PLC0415

            try:
                with keep.running(on_fail="error"):
                    await machine.run()
            except ActivationError:
                logger.warning(
                    "wakepy could not acquire a sleep-prevention wake lock; bot will run without sleep guard"
                )
                await machine.run()
        finally:
            run_trace.stop()
            self._window.bot_canvas.set_idle()
            self._window.right_sidebar.clients.set_bot_running(False)
            self._window.left_sidebar.set_bot_running(False)
            self._authoring.resume_after_run()
            self._on_run_ended()
            remove_gui_callback(log_cb)
            self._client_manager.set_machine(None)
            self._current_machine = None
            self._window.set_start_enabled(self._documents.start_enabled())
            self._window.action_import.setEnabled(True)

    async def _run_pre_run_setup(self) -> None:
        """Prepare every connected client before the FSM starts.

        Each step must catch its own failures and log rather than raise: a setup problem
        should degrade the run, not cancel it. None of them may show a blocking dialog
        either — this runs inside the _start_bot asyncio task, where a modal .exec()
        deadlocks the qasync loop under Python 3.13 (see CLAUDE.md).
        """
        with run_trace.span("pre_run.freecam"):
            await self._exit_pre_run_freecam()
        with run_trace.span("pre_run.decks"):
            await self._decks.apply_pre_run_decks()
        with run_trace.span("pre_run.group"):
            states = await self._form_pre_run_group()
        with run_trace.span("pre_run.port_survey"):
            await self._survey_pre_run_ports(states)

    def _player_number(self, client) -> int | None:
        """*client*'s player number, P1 first, or None for one not hooked. For the run trace."""
        clients = self._client_manager.clients
        return clients.index(client) + 1 if client in clients else None

    async def _exit_pre_run_freecam(self) -> None:
        """Put every player's camera back on them before anything drives a client.

        First, because every later step and every node assumes the camera follows the
        player, and because the run locks the Exit Freecam button -- a camera left detached
        now would stay detached until the run ended. Exit only, never TP to Camera: the
        player is where the user left them, and a bot may well depend on that. Never raises.
        """
        try:
            exited = await self._window.right_sidebar.clients.exit_freecam()
        except Exception:
            logger.warning("Could not take players out of freecam before the run", exc_info=True)
            return
        if exited:
            logger.info("Exited freecam for %d player(s) before the run", exited)

    async def _survey_pre_run_ports(self, states: list | None) -> None:
        """Read who among the clients can port to whom, for zone navigation. See bartlebot.clients.port_map.

        Runs whether or not auto-grouping is on: a friends list is one text read per client,
        and a route planned around a port that cannot happen costs far more. Never raises —
        a node that finds no map surveys for itself.
        """
        from bartlebot.clients import port_map  # noqa: PLC0415

        clients = self._client_manager.clients
        machine = self._current_machine
        if machine is None or len(clients) < 2:
            return
        try:
            machine.port_map = await port_map.survey(clients, states=states)
        except Exception:
            logger.warning("Port survey failed — zone navigation will survey when it needs one", exc_info=True)

    async def _form_pre_run_group(self) -> list | None:
        """Put all clients into one in-game group, if the user has that enabled.

        Group membership is lost whenever a client logs off or crashes, and it is what
        guarantees the first turn in combat, so it is re-established at the start of every
        run rather than assumed to have survived from the last one.

        Returns every client's group state as grouping left it, so the port survey can reuse
        them rather than opening every social window a second time: what the survey read when
        nothing needed changing, and GroupResult.states when form_group changed something.
        None when grouping is off or failed outright, and the survey then reads for itself.
        """
        if not SettingsManager.instance().auto_group_players:
            return None

        clients = self._client_manager.clients
        if len(clients) < 2:
            logger.debug("Skipping pre-run grouping — fewer than two clients connected")
            return None

        try:
            # Survey once and hand the result to form_group. Each survey opens and closes
            # the social window on every client, so letting form_group re-read would
            # double the slowest and most failure-prone part of this.
            with run_trace.span("group.read_states"):
                states = await group_manager.read_group_states(clients)
            if all(s.grouped for s in states) and group_manager.same_group(states) is not False:
                logger.debug("Skipping pre-run grouping — all clients are already in one group")
                return states
            with run_trace.span("group.resolve_names"):
                names = await wizard_names.resolve_names(clients)
            with run_trace.span("group.form"):
                result = await group_manager.form_group(
                    clients, names, states=states, safety_delay=SettingsManager.instance().default_safety_delay
                )
            if result.success:
                logger.info("Pre-run grouping: %s", result.summary())
            else:
                logger.warning(
                    "Pre-run grouping did not fully succeed (%s) — the run will continue ungrouped",
                    result.summary(),
                )
            return result.states
        except Exception:
            logger.warning("Pre-run grouping failed — the run will continue ungrouped", exc_info=True)
            return None

    def pause_bot(self) -> None:
        if self._current_machine:
            self._current_machine.pause()

    def resume_bot(self) -> None:
        if self._current_machine:
            self._current_machine.resume()

    def stop_bot(self) -> None:
        # Before the first node, Stop cuts the pre-run setup short rather than waiting out
        # every deck and the group: a user who stops this early has spotted something to fix.
        if self._pre_run is not None and not self._pre_run.done():
            self._pre_run.cancel()
        if self._current_machine:
            self._current_machine.stop()

    # ------------------------------------------------------------------
    # Machine callbacks
    # ------------------------------------------------------------------

    def _on_machine_state_change(self, state_id: str) -> None:
        self._window.set_fsm_state(state_id)
        machine = self._current_machine
        if machine is not None:
            self._window.left_sidebar.variable_values.on_node_started(machine.node_type_of(state_id))

    def _on_machine_variable_change(self, name: str, old, new) -> None:
        self._window.left_sidebar.variable_values.on_variable_change(name, old, new)

    def _on_machine_transition(self, prev: str, result: str, next_: str) -> None:
        self._window.bot_canvas.on_transition(prev, result, next_)

    def _on_machine_drop(self, item: str, qty: int, client_idx: int) -> None:
        self._window.right_sidebar.drops.add_drop(item, qty, client_idx)

    def _on_machine_fight_fled(self, bot_id: str, state_id: str | None, level: Level) -> None:
        """Count a fight fled on a default, and have the Combat tab mark it (Stage 7 of epic #201).

        *bot_id* is the running bot's, bound when the machine is built, so a mark lands on
        the bot that fled even if another is open by the time the tab draws it.
        """
        if self._flee_marks.record(bot_id, state_id, level):
            self._window.left_sidebar.combat_tab.refresh_source()

    def _on_machine_client_disconnected(self, hwnd: int) -> None:
        """Called by the health monitor when a client becomes unresponsive.

        Force-closes the game window so that remove_dead_clients() detects the exit
        and triggers the reconnect procedure via _recover_removed. That reaping is
        keyed on the *process* exiting, not the window, so the close has to be seen
        through: a crashing client parks on an error popup and never exits on WM_CLOSE
        alone, which used to leave recovery waiting forever.
        """
        logger.warning("Client hwnd=%d flagged unresponsive — closing it", hwnd)
        task = asyncio.create_task(self._close_dead_client(hwnd))
        self._close_tasks.add(task)
        task.add_done_callback(self._close_tasks.discard)

    def _on_machine_explicit_stop(self, reason: str, state_label: str, result: str) -> None:
        """Called when a node returns a value its author marked as a deliberate stop.

        The machine has already stopped itself. What is left is telling the user why,
        which is the whole point of the feature -- the case it was built for is
        check_zone finding the wrong world, where a run that merely ends is
        indistinguishable from one that crashed.

        Deferred through the event loop for the Python 3.13 re-entrancy trap in
        CLAUDE.md, exactly as the restricted-zone explanation is.
        """
        body = f"The bot stopped at {state_label}, which returned {result}."
        if reason:
            body += "\n\n" + reason
        QTimer.singleShot(0, lambda: QMessageBox.information(self._window, "Bot Stopped", body))

    def _on_machine_stopped(self, reason: str) -> None:
        """Called when the machine stops itself for a reason the user should see (the flee limit).

        Deferred through the event loop for the same re-entrancy reason as
        _on_machine_explicit_stop.
        """
        QTimer.singleShot(0, lambda: QMessageBox.information(self._window, "Bot Stopped", reason))

    def _on_machine_restricted_zone(self, description: str) -> None:
        """Called by the health monitor when a client reaches a restricted zone.

        The machine has already stopped itself by the time this runs. What is left
        is to detach from every client, so nothing of this tool remains hooked into
        a process sitting in a PvP zone -- a stopped FSM on its own leaves the memory
        hooks in place, which is the state the guard exists to avoid.
        """
        logger.critical("Restricted zone reached (%s) — unhooking all clients", description)
        task = asyncio.create_task(self._detach_from_restricted_zone(description))
        self._close_tasks.add(task)
        task.add_done_callback(self._close_tasks.discard)

    async def _detach_from_restricted_zone(self, description: str) -> None:
        """Unhook every client, then tell the user why the run ended.

        The dialog is deferred past this task for the Python 3.13 re-entrancy trap in
        CLAUDE.md, and is scheduled in a finally: an unhook that throws still has to
        produce the explanation, or the bot simply stops with no reason given.
        """
        try:
            await self._client_manager.unhook_all()
        except Exception:
            logger.exception("Failed to unhook clients after restricted-zone stop")
        finally:
            QTimer.singleShot(
                0,
                lambda: QMessageBox.critical(
                    self._window,
                    "Restricted Zone",
                    f"The bot was stopped: a client entered {description}.\n\n"
                    "BartleBot is for PvE. PvP zones and Beastmoon are off limits.\n\n"
                    "All clients have been unhooked. Move them out of the zone and "
                    "reconnect to continue.",
                ),
            )

    async def _close_dead_client(self, hwnd: int) -> None:
        """Run the close sequence for a flagged client and report a failure to the user."""
        try:
            closed = await force_close_client(hwnd)
        except Exception:
            logger.exception("Failed to close client hwnd=%d", hwnd)
            return
        if closed:
            return
        # Deferred rather than emitted here: recovery_error opens a modal QMessageBox,
        # and exec() from inside an asyncio task is the re-entrancy trap in CLAUDE.md.
        # A zero-delay timer runs it from Qt's loop with no task frame on the stack.
        QTimer.singleShot(
            0,
            lambda: self._client_manager.recovery_error.emit(
                "A crashed client could not be closed — it is likely showing a Windows error "
                "popup that has to be dismissed by hand.\n\n"
                "Close it manually and the bot will recover from there."
            ),
        )
