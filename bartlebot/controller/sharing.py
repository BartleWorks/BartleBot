"""Share files, both ways: Export from a menu or a card, and Import from a menu, a drop or a launch.

``docs/SHARING.md`` has the format and what travels. ``Sharing`` is the controller's
half: what an export reads from and whether the open document is saved first, and for
an import, the refusals (a run going, an open document it would replace), what to close
first, opening or selecting what came in, and a share file handed over by a later
launch of the app. The building and writing are ``content/share_export`` and
``share_import``; the dialogs are ``gui/dialogs/export_dialog`` and ``import_dialog``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from bartleby.qt.app.single_instance import LaunchQueue, SingleInstance, raise_window
from PyQt6.QtWidgets import QApplication, QMessageBox

from bartlebot.bot.schema import BotConfig
from bartlebot.combat import strategy_store
from bartlebot.content import bot_roots, share_export, share_import
from bartlebot.controller.documents import matches_clean, world_folder
from bartlebot.gui.combat_strategies.dialog import DialogMode
from bartlebot.gui.dialogs.export_dialog import run_export
from bartlebot.gui.dialogs.import_dialog import TITLE as IMPORT_TITLE
from bartlebot.gui.dialogs.import_dialog import ask_for_file, confirm_import
from bartlebot.gui.share_drop import is_share_file
from bartlebot.settings_manager import SettingsManager

if TYPE_CHECKING:
    from collections.abc import Callable


from collections.abc import Callable

logger = logging.getLogger(__name__)

#: Every export's title, and what it says on a template, which is shared after 1.0.0 (#165).
EXPORT_TITLE = "Export"

TEMPLATE_EXPORT_TEXT = "Templates can't be shared yet. Export a bot made from one instead."


def _share_files(args: list[str]) -> list[str]:
    """The arguments that name a share file that is there, in order."""
    return [arg for arg in args if is_share_file(arg) and Path(arg).is_file()]


class Sharing:
    """Export and Import, over the open document. Takes what it needs: see ``__init__``."""

    def __init__(self, window, documents, app, *, is_running: Callable[[], bool]) -> None:
        self._window = window
        self._documents = documents
        # The app's own arguments: a share file named at launch, as a double-click names one.
        self._app = app
        self._is_running = is_running
        self._launches: LaunchQueue | None = None

    # ------------------------------------------------------------------
    # Export (#170)
    # ------------------------------------------------------------------

    def _export_sources(self) -> share_export.Sources:
        """Where an export reads from: the session's subgraph library, reloaded, and the real stores."""
        self._documents.subgraph_library.reload()
        return share_export.Sources(subgraph_library=self._documents.subgraph_library)

    def export_open_document(self) -> Path | None:
        """File › Export…: the open bot, or the open subgraph definition, as saved. The path written, or None."""
        config = self._documents.loaded_config
        if config is None:
            return None
        if config.is_template:
            QMessageBox.information(self._window, EXPORT_TITLE, TEMPLATE_EXPORT_TEXT)
            return None
        if not self._saved_for_export():
            return None
        config = self._documents.loaded_config
        if config.is_definition:
            return self.export_subgraph(config.definition.id)
        if self._documents.last_save_path is None:
            return None
        return self.export_bot(self._documents.last_save_path)

    def _saved_for_export(self) -> bool:
        """Whether the open document is saved, offering to save it first. A share file carries what is saved."""
        if not self._documents.has_unsaved_changes or matches_clean(
            self._documents.loaded_config, self._documents.clean_snapshot
        ):
            return True
        reply = QMessageBox.question(
            self._window,
            EXPORT_TITLE,
            "Export sends what is saved. Save your changes first?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
        )
        return reply == QMessageBox.StandardButton.Save and self._documents.save_bot()

    def _active_bot_strategy(self, bot_id: str):
        """The Bot Combat Strategy a bot's export offers: the user's active pick for it, or None.

        The pick chooses what is offered and never travels itself.
        """
        active = SettingsManager.instance().get_active_strategy(bot_id)
        if active is None:
            return None
        try:
            bcs = strategy_store.load_bot_strategy(active)
        except Exception:
            logger.warning("Could not read the active Bot Combat Strategy %s to export it", active, exc_info=True)
            return None
        return bcs if bcs.bot_id == bot_id else None

    def export_bot(self, path: str) -> Path | None:
        """Export… on a bot: the file at ``path``, its subgraphs, and on request its Bot Combat Strategy."""
        try:
            bot = BotConfig.load(path)
        except Exception as e:
            QMessageBox.critical(self._window, EXPORT_TITLE, f"The bot could not be read:\n\n{e}")
            return None
        if bot.is_template:
            QMessageBox.information(self._window, EXPORT_TITLE, TEMPLATE_EXPORT_TEXT)
            return None
        sources = self._export_sources()
        official = bot_roots.is_shipped(path)
        combat = self._active_bot_strategy(bot.id)

        folder = world_folder(path)

        def build(choices):
            return share_export.export_bot(
                bot,
                sources,
                official=official,
                combat=combat if choices.include_combat else None,
                include_decks=choices.include_decks,
                folder=folder,
            )

        return run_export(
            self._window, build, combat_name=combat.name if combat else None, offers_decks=combat is not None
        )

    def export_subgraph(self, subgraph_id: str) -> Path | None:
        """Export… on a subgraph: the definition as saved, and the subgraphs it nests."""
        sources = self._export_sources()
        defn = self._documents.subgraph_library.get(subgraph_id)
        if defn is None:
            QMessageBox.information(self._window, EXPORT_TITLE, "That subgraph could not be read from the library.")
            return None
        return run_export(self._window, lambda _choices: share_export.export_subgraph(defn, sources))

    def export_bot_strategy(self, bcs) -> Path | None:
        """Export… on a Bot Combat Strategy: as saved, with the strategies it links, its bot, and on request its decks."""
        try:
            # The Combat tab writes each change as it is made, so the file is current.
            bcs = strategy_store.load_bot_strategy(bcs.id)
        except FileNotFoundError:
            pass
        except Exception:
            logger.warning("Could not reread the Bot Combat Strategy %s to export it", bcs.id, exc_info=True)
        sources = self._export_sources()
        return run_export(
            self._window,
            lambda choices: share_export.export_bot_strategy(bcs, sources, include_decks=choices.include_decks),
            offers_decks=True,
        )

    # ------------------------------------------------------------------
    # Import (#171)
    # ------------------------------------------------------------------

    def import_from_arguments(self) -> None:
        """Import each share file named on the command line: how a double-clicked file arrives (#27)."""
        for path in _share_files(self._app.arguments()[1:]):
            self.import_share_file(path)

    def listen_for_launches(self, instance: SingleInstance) -> None:
        """Take what later launches hand over: a double-click while the app runs arrives here (#27).

        The window comes forward at once. The files wait for any modal dialog to close, so
        an import's summary never opens over setup or over another import's.
        """
        self._launches = LaunchQueue(lambda: QApplication.activeModalWidget() is not None)
        self._launches.ready.connect(self._import_handed_over)
        instance.arguments_received.connect(self._on_later_launch)

    def _on_later_launch(self, args: list[str]) -> None:
        raise_window(self._window)
        self._launches.put(args)

    def _import_handed_over(self, args: list[str]) -> None:
        """Each share file a later launch named, imported here as one named at this launch would be."""
        paths = _share_files(args)
        if not paths or self._import_blocked_by_run():
            return
        for path in paths:
            self.import_share_file(path)

    def import_dropped(self, paths: list[str]) -> None:
        """Files dropped on the window, imported one after another."""
        if self._import_blocked_by_run():
            return
        for path in paths:
            self.import_share_file(path)

    def _import_blocked_by_run(self) -> bool:
        """Say to stop the bot first, and True, while one runs: an import would write what the run is using (#181)."""
        if not self._is_running():
            return False
        logger.warning("Not importing: a bot is running")
        QMessageBox.warning(self._window, IMPORT_TITLE, "Stop the running bot before importing. Nothing was imported.")
        return True

    def import_share_file(self, path: str | None = None) -> share_import.ImportResult | None:
        """File › Import…, a dropped file, or one named at launch: the summary, then the write. What was written.

        Refused files say why and write nothing. Changing a choice in the summary
        re-matches the file, so what Import writes is what the summary showed last.
        """
        if self._import_blocked_by_run():
            return None
        if path is None:
            path = ask_for_file(self._window)
            if path is None:
                return None
        sources = self._export_sources()
        try:
            share = share_import.read(path)
            incoming = share_import.check(share, sources)
        except share_import.Refused as e:
            logger.warning("Not importing %s: %s", path, e)
            QMessageBox.warning(self._window, IMPORT_TITLE, str(e))
            return None
        try:
            plan = confirm_import(
                self._window, lambda choices: share_import.plan_import(share, sources, choices, incoming=incoming)
            )
        except Exception as e:
            logger.exception("Could not match %s against the library", path)
            QMessageBox.critical(self._window, IMPORT_TITLE, f"Could not read what this file would change:\n\n{e}")
            return None
        if plan is None:
            return None
        reopen = self._clear_for_import(plan)
        if reopen is False:
            return None
        result: share_import.ImportResult | None = None
        try:
            result = share_import.apply_import(plan, sources)
        except Exception as e:
            logger.exception("The import of %s stopped part way", path)
            QMessageBox.critical(
                self._window,
                IMPORT_TITLE,
                f"The import stopped part way, and nothing after this was written:\n\n{e}",
            )
        self._after_import(plan, result, reopen if isinstance(reopen, str) else None)
        return result

    def _open_document_ids(self) -> tuple[str | None, set[str]]:
        """``(the open bot's file, the ids of every open definition)``, the bot under any definitions."""
        configs = [frame.doc.config for frame in self._documents.doc_stack]
        if self._documents.loaded_config is not None:
            configs.append(self._documents.loaded_config)
        definitions = {c.definition.id for c in configs if c.is_definition}
        bot_path = self._documents.doc_stack[0].path if self._documents.doc_stack else self._documents.last_save_path
        if (
            self._documents.loaded_config is not None
            and not self._documents.doc_stack
            and self._documents.loaded_config.is_definition
        ):
            bot_path = None
        return bot_path, definitions

    def _clear_for_import(self, plan: share_import.ImportPlan) -> str | bool:
        """Make way for an import that writes over an open document. False to stop; the bot file to reopen; or True.

        A definition being written over is closed first, asking about unsaved changes as
        going back always does. The open bot is reopened afterward, unless it has unsaved
        changes, which the import would leave out of step with its file: then nothing is written.
        """
        over = [r for r in plan.rows if r.action in (share_import.Action.UPDATE, share_import.Action.REPLACE)]
        if not over:
            return True
        bot_path, definitions = self._open_document_ids()
        if (
            any(r.kind == share_export.SUBGRAPH and r.id in definitions for r in over)
            and not self._documents.close_definitions()
        ):
            return False
        bot_row = next(
            (
                r
                for r in over
                if r.kind == share_export.BOT
                and bot_path is not None
                and r.holding is not None
                and r.holding.path is not None
                and Path(r.holding.path).resolve() == Path(bot_path).resolve()
            ),
            None,
        )
        if bot_row is None:
            return True
        if not self._documents.close_definitions():
            return False
        if self._documents.has_unsaved_changes and not matches_clean(
            self._documents.loaded_config, self._documents.clean_snapshot
        ):
            QMessageBox.information(
                self._window,
                IMPORT_TITLE,
                f"'{bot_row.name}' is open with unsaved changes, and this import would replace it. "
                "Save or close it, then import again.",
            )
            return False
        return str(bot_path)

    def _after_import(
        self, plan: share_import.ImportPlan, result: share_import.ImportResult | None, reopen: str | None
    ) -> None:
        """Bring every surface up to date with what was written, then show the main item where it went."""
        self._documents.subgraph_library.reload()
        self._window.bot_canvas.refresh_subgraph_listing()
        if result is None:
            return
        share_import.assign_active_picks(result, SettingsManager.instance())
        if reopen is not None:
            self._documents.open_bot(reopen)
        elif self._documents.loaded_config is not None and not self._documents.loaded_config.is_definition:
            # A Bot Combat Strategy for the open bot: its list, and maybe its pick, changed.
            if any(bot_id == self._documents.loaded_config.id for _bcs, bot_id in result.strategies_added):
                self._window.left_sidebar.rebind_identity()
        self._select_imported(plan, result)

    def _select_imported(self, plan: share_import.ImportPlan, result: share_import.ImportResult) -> None:
        """Afterward the library selects the main item: a bot or subgraph in the bot library, a strategy in its own."""
        main = plan.main
        kind = plan.share.main.kind
        target = main.target_id if main is not None else plan.share.main.id
        if kind == share_export.BOT_COMBAT_STRATEGY:
            # A Bot Combat Strategy is shown with its bot, unless that bot is the one open.
            if main is None or (
                self._documents.loaded_config is not None and self._documents.loaded_config.id == main.item.bot_id
            ):
                return
            kind, target = share_export.BOT, main.item.bot_id
        if kind == share_export.BOT:
            path = result.bot_paths.get(target)
            if path is None:
                found = self._export_sources().bot_file(target)
                path = found.path if found is not None else None
            open_bot = (
                self._documents.last_save_path if not self._documents.doc_stack else self._documents.doc_stack[0].path
            )
            if path is not None and not (open_bot and Path(open_bot).resolve() == Path(path).resolve()):
                # The open bot is already in front of the user: reopened, never revealed.
                self._window.bot_canvas.reveal_in_library(path=path)
        elif kind == share_export.SUBGRAPH:
            self._window.bot_canvas.reveal_in_library(subgraph_id=target)
        else:
            strategy_kind = {
                share_export.DUEL_STRATEGY: strategy_store.StrategyKind.DUEL,
                share_export.PLAYER_STRATEGY: strategy_store.StrategyKind.PLAYER,
                share_export.DECK: strategy_store.StrategyKind.DECK,
            }[kind]
            self._window.left_sidebar.combat_tab.open_combat_dialog(
                strategy_kind, DialogMode.LIBRARY, current_id=target
            )
