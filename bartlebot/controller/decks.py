"""Decks: reading a player's deck out of the game, and building one in it.

From the Combat tab's cards and the combat dialog's Deck tab (one slot, or all of them),
as a job of its own, one at a time; and before a run, every player's deck from the
active Bot Combat Strategy, as part of the pre-run setup. The game's side is
``combat/deck_io``. What is stored is the active strategy's ``player_decks``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from bartleby.runtime.timing import DECK_APPLY_TIMEOUT, DECK_IMPORT_TIMEOUT
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMessageBox

from bartlebot.combat.deck_io import DeckApplyReport, apply_deck, read_deck
from bartlebot.settings_manager import SettingsManager

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from bartleby.shared.strategy_models import DeckData

from collections.abc import Callable

logger = logging.getLogger(__name__)

#: Player slots with a deck card on the Combat tab; a group is at most four wizards, so
#: Import all reads no client past the fourth.
_DECK_SLOTS = 4

#: Why a deck import or apply failed, when the log has the traceback and the user does not.
_DECK_JOB_FAILED = "failed — see the log for details"

#: Said under every deck failure: a job that dies part-way leaves the game's window as it was.
_DECK_WINDOW_NOTE = "The game's deck window may be left open or partially edited — check the client before retrying."


class Decks:
    """Deck import and apply. Takes the window, the client manager and the open document."""

    def __init__(self, window, client_manager, documents) -> None:
        self._window = window
        self._client_manager = client_manager
        self._documents = documents
        # Held so the event loop's weak reference is not all that keeps it alive: the
        # one deck import or apply allowed to run -- see _start_deck_job.
        self._deck_task: asyncio.Task | None = None

    def on_import_deck_requested(self, slot: int) -> None:
        if self._slot_has_client(slot):
            self._start_deck_job(self._import_deck_for_slot(slot))

    def on_apply_deck_requested(self, slot: int) -> None:
        if self._slot_has_client(slot):
            self._start_deck_job(self._apply_deck_for_slot(slot))

    def on_import_all_decks_requested(self) -> None:
        if self._slot_has_client(1):
            self._start_deck_job(self._import_all_decks())

    def on_apply_all_decks_requested(self) -> None:
        if self._slot_has_client(1):
            self._start_deck_job(self._apply_all_decks())

    def import_deck_for_dialog(self, slot: int, done: Callable[[DeckData | str], None]) -> bool:
        """Read a slot's deck for the combat dialog's Deck tab, which stores it in its own preset.

        Unlike Import on a deck card this touches no Bot Combat Strategy: the answer goes to
        *done* and the dialog's Save decides whether it lasts. False if nothing started.
        """
        if self._deck_task is not None or not self._slot_has_client(slot):
            return False

        async def job() -> None:
            result = await self._read_slot_deck(slot, self._client_manager.clients[slot - 1])
            if isinstance(result, str):
                self._warn_deck_failed("import", {slot: result})
            self._answer_dialog(done, result)

        self._start_deck_job(job())
        return True

    def apply_deck_for_dialog(self, slot: int, deck: DeckData, done: Callable[[str | None], None]) -> bool:
        """Put *deck*, a library preset's, on a slot's client. False if nothing started."""
        if self._deck_task is not None or not self._slot_has_client(slot):
            return False

        async def job() -> None:
            result = await self._apply_slot_deck(slot, self._client_manager.clients[slot - 1], deck)
            if isinstance(result, str):
                self._warn_deck_failed("apply", {slot: result})
            else:
                self._report_deck_apply(slot, result)
            self._answer_dialog(done, result if isinstance(result, str) else None)

        self._start_deck_job(job())
        return True

    @staticmethod
    def _answer_dialog(done: Callable[[Any], None], result: object) -> None:
        """Hand a deck job's result to the dialog that asked, which may have closed meanwhile."""
        try:
            done(result)
        except RuntimeError:
            logger.info("The dialog that asked for a deck job closed before it finished")
        except Exception:
            logger.exception("A deck job's answer raised")

    def _slot_has_client(self, slot: int) -> bool:
        """Whether *slot* has a hooked client, telling the user when it does not.

        Slot 1 is what the whole-group verbs ask about: it is missing exactly when no
        client is hooked at all.
        """
        clients = self._client_manager.clients
        if not clients:
            QMessageBox.warning(self._window, "No clients", "No Wizard101 clients are connected.")
            return False
        if slot - 1 >= len(clients):
            QMessageBox.warning(self._window, "No client", f"No client connected for slot {slot}.")
            return False
        return True

    def _start_deck_job(self, job: Coroutine[object, object, None]) -> None:
        """Run a deck import or apply, holding every deck verb until it finishes.

        One job at a time. Two on one client drive its deck window at once, and two
        imports each load, set and save the strategy, so the later save drops the
        earlier one's deck. The tab disables its verbs while a job runs, but a card's
        right-click menu still reaches Import and Apply, so the refusal lives here too.
        """
        if self._deck_task is not None:
            job.close()
            logger.info("A deck import or apply is already running — ignoring the new request")
            return
        self._deck_task = asyncio.create_task(job)
        self._deck_task.add_done_callback(self._on_deck_job_done)
        self._window.left_sidebar.combat_tab.set_deck_busy(True)

    def _on_deck_job_done(self, task: asyncio.Task) -> None:
        self._deck_task = None
        if not task.cancelled() and task.exception() is not None:
            logger.error("Deck job raised", exc_info=task.exception())
        self._window.left_sidebar.combat_tab.set_deck_busy(False)

    def _active_deck_strategy_id(self, action: str) -> str | None:
        """The Bot Combat Strategy a deck import or apply works on, or None to skip it."""
        if self._documents.loaded_config is None:
            return None
        bcs_id = SettingsManager.instance().get_active_strategy(self._documents.loaded_config.id)
        if not bcs_id:
            logger.warning("No active Bot Combat Strategy — deck %s skipped", action)
            return None
        return bcs_id

    async def _import_deck_for_slot(self, slot: int) -> None:
        bcs_id = self._active_deck_strategy_id("import")
        clients = self._client_manager.clients
        if bcs_id is None or slot - 1 >= len(clients):
            return
        result = await self._read_slot_deck(slot, clients[slot - 1])
        if isinstance(result, str):
            self._warn_deck_failed("import", {slot: result})
            return
        self._store_imported_decks(bcs_id, {slot: result})

    async def _import_all_decks(self) -> None:
        """Read every hooked client's deck, then store them all with one save.

        The reads run concurrently, for the reason ``apply_pre_run_decks`` gives: every
        input WizWalker sends is addressed to one client's own window. The save does not:
        see ``_store_imported_decks``. A slot that fails is reported, and the others are
        kept.
        """
        bcs_id = self._active_deck_strategy_id("import")
        if bcs_id is None:
            return
        clients = list(self._client_manager.clients)[:_DECK_SLOTS]
        slots = list(range(1, len(clients) + 1))
        results = await asyncio.gather(*(self._read_slot_deck(slot, client) for slot, client in zip(slots, clients)))
        decks = {slot: result for slot, result in zip(slots, results) if not isinstance(result, str)}
        failures = {slot: result for slot, result in zip(slots, results) if isinstance(result, str)}
        if decks:
            self._store_imported_decks(bcs_id, decks)
        if failures:
            self._warn_deck_failed("import", failures)

    async def _read_slot_deck(self, slot: int, client) -> DeckData | str:
        """*client*'s current deck, or the reason it could not be read. Never raises.

        The read half of an import, shared by one slot and by all of them, so Import all
        can read concurrently and still save once.
        """
        from bartleby.shared.strategy_models import DeckData  # noqa: PLC0415

        logger.info("Importing deck for slot %d", slot)
        try:
            preset_dict = await asyncio.wait_for(read_deck(client), timeout=DECK_IMPORT_TIMEOUT)
            deck_data = DeckData(**preset_dict)
        except TimeoutError:
            logger.error("Deck import for slot %d timed out after %.0fs", slot, DECK_IMPORT_TIMEOUT)
            return "timed out"
        except Exception:
            logger.exception("Failed to import deck for slot %d", slot)
            return _DECK_JOB_FAILED
        logger.info("Deck imported for slot %d: %d cards", slot, deck_data.total_cards())
        return deck_data

    def _store_imported_decks(self, bcs_id: str, decks: dict[int, DeckData]) -> None:
        """Write *decks* into the strategy with one load and one save, then redraw.

        Once for every slot, never once per slot: a load-set-save per slot, run
        concurrently, keeps only the slot saved last and silently drops the others.
        Loaded after the reads rather than before them, so the save lands on the file as
        it stands now.
        """
        from bartlebot.combat.strategy_store import load_bot_strategy, save_bot_strategy  # noqa: PLC0415

        try:
            bcs = load_bot_strategy(bcs_id)
        except Exception:
            logger.exception("Failed to load BotCombatStrategy %s for deck import", bcs_id)
            return
        bcs.player_decks.update(decks)
        try:
            save_bot_strategy(bcs)
        except Exception:
            logger.exception("Failed to save BotCombatStrategy after deck import")
        self._window.left_sidebar.combat_tab.update_deck_slots(decks)
        self._documents.on_config_changed()

    def _saved_decks(self) -> dict[int, DeckData] | None:
        """The active strategy's non-empty decks by slot, or None if there is no strategy."""
        from bartlebot.combat.strategy_store import load_bot_strategy  # noqa: PLC0415

        bcs_id = self._active_deck_strategy_id("apply")
        if bcs_id is None:
            return None
        try:
            bcs = load_bot_strategy(bcs_id)
        except Exception:
            logger.exception("Failed to load BotCombatStrategy %s for deck apply", bcs_id)
            return None
        return {slot: deck_data for slot, deck_data in bcs.player_decks.items() if not deck_data.is_empty()}

    async def _apply_deck_for_slot(self, slot: int) -> None:
        decks = self._saved_decks()
        clients = self._client_manager.clients
        if decks is None or slot - 1 >= len(clients):
            return
        deck_data = decks.get(slot)
        if deck_data is None:
            logger.warning("No deck saved for slot %d — nothing to apply", slot)
            return
        result = await self._apply_slot_deck(slot, clients[slot - 1], deck_data)
        if isinstance(result, str):
            self._warn_deck_failed("apply", {slot: result})
        else:
            self._report_deck_apply(slot, result)

    async def _apply_all_decks(self) -> None:
        """Apply each saved deck to its hooked client, concurrently, and report once."""
        decks = self._saved_decks()
        if decks is None:
            return
        clients = self._client_manager.clients
        pending = {slot: deck_data for slot, deck_data in sorted(decks.items()) if slot - 1 < len(clients)}
        if not pending:
            logger.warning("No saved deck has a connected client — nothing to apply")
            return
        results = await asyncio.gather(
            *(self._apply_slot_deck(slot, clients[slot - 1], deck_data) for slot, deck_data in pending.items())
        )
        self._report_apply_all(dict(zip(pending, results)))

    async def _apply_slot_deck(self, slot: int, client, deck_data: DeckData) -> DeckApplyReport | str:
        """Apply one slot's deck: the report, or the reason it failed. Never raises."""
        logger.info("Applying deck for slot %d (%d cards)", slot, deck_data.total_cards())
        try:
            report = await apply_deck(client, deck_data, slot=slot)
        except TimeoutError:
            logger.error("Deck apply for slot %d timed out after %.0fs", slot, DECK_APPLY_TIMEOUT)
            return "timed out"
        except Exception:
            logger.exception("Failed to apply deck for slot %d", slot)
            return _DECK_JOB_FAILED
        logger.info("Deck applied for slot %d", slot)
        return report

    def _report_deck_apply(self, slot: int, report: DeckApplyReport) -> None:
        """Tell the user when the deck that went on is not the deck that was saved.

        Saved decks name specific spells, so a deck built on another wizard
        routinely asks for cards this one does not have — a different school's
        hitter cannot be substituted at all.  Staying silent about that produced
        a half-built deck that looked like a success.

        Nothing is shown for a clean apply, which is the common case when the
        deck and the wizard are the same one it was saved from.  A *successful*
        archmastery change counts as clean: opting in is what asked for it, and
        a dialog on every apply would train the user to dismiss these.  Failing
        to make one does not — it was asked for and silently did nothing.
        """
        if report.is_clean:
            return

        lines = self._deck_apply_lines(report)
        if not report.applied:
            title = f"Deck for Player {slot} not applied"
            body = "None of the saved deck's cards are in this wizard's spellbook, so the current deck was left alone."
            self._show_deck_notice(title, f"{body}\n\n" + "\n".join(lines), warn=True)
            return

        self._show_deck_notice(
            f"Deck applied for Player {slot}, with changes",
            "\n".join(lines),
            warn=bool(report.skipped or report.archmastery_failed),
        )

    def _report_apply_all(self, outcomes: dict[int, DeckApplyReport | str]) -> None:
        """One notice for Apply all, however many slots need one.

        Each slot says what ``_report_deck_apply`` or ``_warn_deck_failed`` would have
        said for it alone; four dialogs in a row was the alternative. Silent when every
        deck went on clean, as a single apply is.
        """
        failures = {slot: outcome for slot, outcome in outcomes.items() if isinstance(outcome, str)}
        sections: list[str] = []
        if failures:
            sections.append(self._deck_failure_lines("apply", failures))
        warn = bool(failures)
        all_applied = not failures
        for slot, report in outcomes.items():
            if isinstance(report, str) or report.is_clean:
                continue
            if report.applied:
                heading = f"Player {slot}: applied, with changes"
                warn = warn or bool(report.skipped or report.archmastery_failed)
            else:
                heading = f"Player {slot}: not applied — none of the saved deck's cards are in this wizard's spellbook"
                warn = True
                all_applied = False
            body = [f"    {line}" if line else "" for line in self._deck_apply_lines(report)]
            sections.append("\n".join([heading, *body]))
        if not sections:
            return
        if failures:
            sections.append(_DECK_WINDOW_NOTE)
        title = "Decks applied, with changes" if all_applied else "Some decks were not applied"
        self._show_deck_notice(title, "\n\n".join(sections), warn=warn)

    def _deck_apply_lines(self, report: DeckApplyReport) -> list[str]:
        """What went on differently from the saved deck, in groups split by blank lines."""
        lines: list[str] = []
        if report.archmastery_failed:
            lines.append(
                f"Could not set the archmastery school to {report.archmastery_failed}. The school was left as it was."
            )
        if report.substitutions:
            if lines:
                lines.append("")
            lines.append("Used a lower tier than the deck asked for:")
            lines += [f"    {used}  (deck wanted {req})" for req, used in sorted(report.substitutions.items())]
        if report.short:
            if lines:
                lines.append("")
            lines.append("Fewer copies went in than the deck asked for:")
            lines += [f"    {name}  ({got} of {wanted})" for name, (got, wanted) in sorted(report.short.items())]
        if report.over:
            if lines:
                lines.append("")
            lines.append("More copies went in than the deck asked for, and could not be taken back out:")
            lines += [
                f"    {name}  ({held}, asked for {wanted})" for name, (held, wanted) in sorted(report.over.items())
            ]
        if report.skipped:
            if lines:
                lines.append("")
            lines.append("Not in this wizard's spellbook, so left out:")
            lines += [f"    {self._describe_skipped(name, report)}" for name in report.skipped]
            lines.append("")
            lines.append("Swap in this wizard's own cards and re-import the deck to fix this.")
        return lines

    @staticmethod
    def _describe_skipped(name: str, report: DeckApplyReport) -> str:
        """One line for a skipped card: what it was, and what could stand in.

        The kind and the alternatives are only known for blades, traps, wards,
        charms and auras — the interchangeable ones.  A missing attack spell
        gets its name and nothing else, which is the honest answer: a Fire
        wizard has no stand-in for a Storm hitter.
        """
        from bartlebot.combat.card_kinds import describe_kind  # noqa: PLC0415

        kind = describe_kind(report.kinds.get(name))
        line = f"{name} — {kind}" if kind else name
        options = report.alternatives.get(name)
        if options:
            line += f"; you have {', '.join(options)}"
        return line

    def _warn_deck_failed(self, action: str, failures: dict[int, str]) -> None:
        """Surface deck import/apply failures, one line per slot, instead of leaving them log-only."""
        self._show_deck_notice(
            f"Deck {action} failed",
            f"{self._deck_failure_lines(action, failures)}\n\n{_DECK_WINDOW_NOTE}",
            warn=True,
        )

    @staticmethod
    def _deck_failure_lines(action: str, failures: dict[int, str]) -> str:
        return "\n".join(f"Deck {action} for Player {slot} {reason}." for slot, reason in sorted(failures.items()))

    def _show_deck_notice(self, title: str, body: str, *, warn: bool) -> None:
        """Show a deck dialog *after* the calling task has finished.

        Both callers run inside an ``asyncio.Task``, and a blocking QMessageBox
        there is the Python 3.13 re-entrancy trap described in CLAUDE.md: the
        dialog re-enters Qt's event loop while the task's frame is still on the
        Python stack, and any task woken meanwhile is left permanently stuck.
        That is what froze the client sidebar for the duration of a bot run.

        Deferring through the Qt event loop means the dialog opens once the task
        has returned and no task is mid-execution, so blocking is safe again.
        """
        box = QMessageBox.warning if warn else QMessageBox.information
        QTimer.singleShot(0, lambda: box(self._window, title, body))

    # ------------------------------------------------------------------
    # Before a run
    # ------------------------------------------------------------------

    async def apply_pre_run_decks(self) -> None:
        """Apply saved deck presets to each connected client before the FSM starts."""
        from bartlebot.combat.strategy_store import load_bot_strategy  # noqa: PLC0415

        if self._documents.loaded_config is None:
            return
        active_bcs_id = SettingsManager.instance().get_active_strategy(self._documents.loaded_config.id)
        if not active_bcs_id:
            return
        try:
            bcs = load_bot_strategy(active_bcs_id)
        except Exception:
            logger.warning("Could not load BotCombatStrategy %s — skipping deck apply", active_bcs_id)
            return
        if not bcs.player_decks:
            return
        clients = self._client_manager.clients
        pending = []
        for slot, deck_data in bcs.player_decks.items():
            if deck_data.is_empty():
                continue
            client_idx = slot - 1  # slots are 1-based
            if client_idx >= len(clients):
                logger.warning("Deck saved for slot %d but only %d client(s) connected — skipping", slot, len(clients))
                continue
            logger.info("Applying saved deck for slot %d (%d cards)", slot, deck_data.total_cards())
            pending.append(self._apply_one_pre_run_deck(clients[client_idx], slot, deck_data))
        if not pending:
            return
        # Concurrently, because applying four decks in sequence is four times the
        # wait for no reason: every input WizWalker sends is addressed to a single
        # window handle (send_key -> timed_send_key(self.window_handle, ...),
        # MouseHandler -> SendMessageW(self.client.window_handle, ...)), the
        # mouseless hook writes into each client's own memory, and nothing anywhere
        # touches the foreground window. So the clients cannot interfere.
        #
        # The win is smaller than N: memory reads are synchronous ReadProcessMemory
        # on the loop thread. Measured at 1.88x for four clients, from before
        # wizwalker sent each window's messages from a thread of its own, when the
        # waits for the games' answers held the loop too.
        #
        # Each task swallows its own failures, so no return_exceptions is needed and
        # one bad client cannot cancel the others' applies half-finished.
        await asyncio.gather(*pending)

    async def _apply_one_pre_run_deck(self, client, slot: int, deck_data: DeckData) -> None:
        """Apply one slot's deck, absorbing any failure.

        Never raises: these run concurrently under a single gather, and a deck that
        will not apply is a degraded run, not a cancelled one.
        """
        try:
            await apply_deck(client, deck_data, slot=slot)
        except TimeoutError:
            # Log only. This runs inside the _start_bot task, and a blocking
            # QMessageBox from an asyncio task deadlocks the qasync loop under
            # Python 3.13 (see CLAUDE.md).
            logger.error(
                "Deck apply for slot %d timed out after %.0fs — run will continue with whatever was set",
                slot,
                DECK_APPLY_TIMEOUT,
            )
        except Exception:
            logger.warning(
                "Failed to fully apply deck for slot %d — run will continue with whatever was set",
                slot,
                exc_info=True,
            )
