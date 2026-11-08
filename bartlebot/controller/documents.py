"""The open document, and every flow that opens, saves, closes or recovers one.

A document is a bot, a template draft, or a subgraph definition opened on top of
either (``gui/CLAUDE.md``, "A definition is a document of its own"). ``Documents``
holds the one on top -- its config, its file, whether it is an Official bot, its
unsaved flag and the snapshot that flag is compared against -- and the stack of those
set aside under it. It does New Bot, Open, Close, Save, Save As and Make my own copy,
the unsaved-edits question, the definition stack and the library verbs that must
not touch an open definition, the autosave and crash recovery, and keeps the window's
Save, Close and unsaved marks in step.

What it needs is handed in: the window, the session's subgraph library, the template
roots, the hooked clients (for whether Run may start) and whether a run is going (an
autosave never writes a running bot). After anything that changes which template is
behind the open document it emits ``template_context_changed``, which the template
flows answer.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QDialog, QFileDialog, QMessageBox

from bartlebot.bot.schema import BotConfig
from bartlebot.content import bot_delete, bot_fork, bot_roots, new_bot
from bartlebot.content.bot_tags import normalize_tags, suggested_tags, wants_rewards_prompt
from bartlebot.content.store_roots import Origin, dev_mode, pill_text
from bartlebot.content.subgraphs.subgraph_signature import signature_changes
from bartlebot.content.subgraphs.subgraph_signature import warning_text as signature_warning_text
from bartlebot.content.subgraphs.subgraph_store import SubgraphLibrary, bots_linking, count_subgraph_uses
from bartlebot.content.subgraphs.subgraphs import (
    ComposeReport,
    compose_bot,
    config_to_definition,
    definition_change,
    definition_to_config,
)
from bartlebot.content.templates import template_store
from bartlebot.controller.template_roots import TemplateRoots
from bartlebot.gui.canvas.bot_canvas import BotMode, CanvasDocument
from bartlebot.gui.dialogs.make_copy_dialog import TITLE as MAKE_COPY_TITLE
from bartlebot.gui.dialogs.make_copy_dialog import MakeCopyDialog
from bartlebot.gui.dialogs.new_bot_dialog import NewBotDialog
from bartlebot.gui.dialogs.rewards_tag_dialog import RewardsChoice, RewardsTagDialog
from bartlebot.settings_manager import SettingsManager

logger = logging.getLogger(__name__)

#: An autosave of a definition document is named for the definition, not a file.
_DEFINITION_AUTOSAVE_PREFIX = "subgraph_"

#: Nor is a template draft's: every draft is a ``draft.json``, so its stem says nothing.
_TEMPLATE_AUTOSAVE_PREFIX = "template_"


@dataclass
class _DocumentFrame:
    """A document set aside while a definition is open on top of it.

    Everything the controller holds for the open document -- the canvas's own state,
    the file it came from, whether it has unsaved changes -- so going back puts it
    exactly as it was.
    """

    doc: CanvasDocument
    path: str | None
    unsaved: bool
    clean: dict | None = None


def file_snapshot(config: BotConfig | None) -> dict | None:
    """What ``config`` would write to disk: the baseline an autosave is compared against.

    Taken wherever a document is opened or saved. The dirty flag alone cannot say
    whether anything changed -- leaving Edit mode, or an edit typed and reverted,
    raises it too (#122) -- so an autosave that would only restate this is skipped.
    """
    return config.file_dump() if config is not None else None


def matches_clean(config: BotConfig | None, snapshot: dict | None) -> bool:
    """Whether ``config`` is still what was last opened or saved. False when there is no snapshot."""
    return snapshot is not None and file_snapshot(config) == snapshot


def _same_bot_content(autosave_path: Path, original_path: Path) -> bool:
    """Whether a recovery file holds the same bot as its original. False when either will not load.

    Both go through ``BotConfig`` rather than being compared as text, so a file an
    older version wrote -- different formatting, a default spelled out -- still matches.
    """
    try:
        return BotConfig.load(autosave_path).file_dump() == BotConfig.load(original_path).file_dump()
    except Exception:
        logger.debug("Could not compare %s with %s", autosave_path, original_path, exc_info=True)
        return False


class Documents(QObject):
    """The open document, the ones set aside under it, and every flow on them."""

    #: Which template is behind the open document may have changed: a bot was opened,
    #: saved as a copy or recovered, or one came back from under a definition.
    template_context_changed = pyqtSignal()

    def __init__(
        self,
        window,
        library: SubgraphLibrary,
        roots: TemplateRoots,
        *,
        clients: Callable[[], list],
        is_running: Callable[[], bool],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._window = window
        # One view of the subgraph library for the session, reloaded on every open and
        # before every run so a definition edited on disk reaches the bot that links it.
        self._subgraph_library = library
        self._roots = roots
        self._clients = clients
        self._is_running = is_running
        self._loaded_config: BotConfig | None = None
        self._last_save_path: str | None = None
        # Whether the open bot is an Official one, opened from the install directory: it
        # is never edited or saved in place, and Edit mode offers Make my own copy (#149).
        self._official_open: bool = False

        # Documents under the open one, bottom first: a bot, then each definition
        # opened from it. Empty while a bot, or a definition opened on its own, is on top.
        self._doc_stack: list[_DocumentFrame] = []

        # Auto-save state, behind has_unsaved_changes and clean_snapshot: setting
        # either refreshes the unsaved marks.
        self._unsaved: bool = False
        # The open document as last opened or saved; None when there is no such state
        # to return to (a recovered file). See file_snapshot.
        self._clean: dict | None = None
        # The id of the open bot whose author chose Save Without at the Rewards prompt:
        # asked once per open, and remembered nowhere else. See rewards_tag_dialog.
        self._rewards_declined_for: str | None = None
        self._auto_save_timer = QTimer(self)
        self._auto_save_timer.setSingleShot(False)
        self._auto_save_timer.timeout.connect(self.do_autosave)

    # ------------------------------------------------------------------
    # The open document
    # ------------------------------------------------------------------

    @property
    def loaded_config(self) -> BotConfig | None:
        """The document on top, or None when nothing is open."""
        return self._loaded_config

    @property
    def last_save_path(self) -> str | None:
        """The file the document on top was opened from or last saved to; None for a definition or a new one."""
        return self._last_save_path

    @property
    def official_open(self) -> bool:
        """Whether the bot open is an Official one, read-only. See ``read_only_open`` for the document on top."""
        return self._official_open

    @property
    def doc_stack(self) -> list[_DocumentFrame]:
        """The documents set aside under the open one, bottom first. A copy."""
        return list(self._doc_stack)

    @property
    def subgraph_library(self) -> SubgraphLibrary:
        """The session's view of the two subgraph roots. Tests swap in a ``tmp_path`` one."""
        return self._subgraph_library

    @subgraph_library.setter
    def subgraph_library(self, library: SubgraphLibrary) -> None:
        self._subgraph_library = library

    @property
    def has_unsaved_changes(self) -> bool:
        """The dirty flag. Raised by an edit that changes nothing too (#122): compare
        against ``clean_snapshot`` before treating it as a change."""
        return self._unsaved

    @has_unsaved_changes.setter
    def has_unsaved_changes(self, unsaved: bool) -> None:
        self._unsaved = unsaved
        self._show_unsaved()

    @property
    def clean_snapshot(self) -> dict | None:
        return self._clean

    @clean_snapshot.setter
    def clean_snapshot(self, snapshot: dict | None) -> None:
        self._clean = snapshot
        self._show_unsaved()

    def _show_unsaved(self) -> None:
        """Mark the window while the open document differs from its file (#267).

        Properties rather than calls at each site, since the flag and the snapshot are
        set from some two dozen places. Only a raised flag is compared, so a save or an open
        costs nothing here, and an edit undone back to the file clears the mark.
        """
        self._window.set_unsaved(self._unsaved and not matches_clean(self._loaded_config, self._clean))

    def mark_unsaved(self) -> None:
        """Raise the unsaved flag and enable Save -- except on an Official bot, which is never saved (#149)."""
        if self.read_only_open():
            return
        self.has_unsaved_changes = True
        self._window.set_save_enabled(True)

    def enable_save_actions(self) -> None:
        """Enable Save and Save As for the open document. On an Official bot only Save As,
        which makes a copy: there is nothing of its own to save."""
        if self.read_only_open():
            self._window.set_save_enabled(False, save_as=True)
        else:
            self._window.set_save_enabled(True)

    def read_only_open(self) -> bool:
        """Whether the document on top is an Official bot. A definition opened over one is not."""
        config = self._loaded_config
        return self._official_open and config is not None and not config.is_definition

    def start_enabled(self) -> bool:
        return bool(self._clients()) and self._loaded_config is not None and not self._loaded_config.is_definition

    def on_metadata_changed(self) -> None:
        """Mark the bot dirty for an edit that cannot have changed its option set.

        The description is the case this exists for.  ``on_config_changed`` ends in
        ``refresh_options``, which recomposes both option surfaces and rebuilds every
        row when the composed list moved -- around 55ms on a 67-state bot, and it was
        being paid once per *character* typed into the description box, which is what
        made that field unusable.  Nothing composes an option from prose, so there is
        nothing here to rebuild: the guard in ``refresh_options`` would now
        skip it either way, and this still saves the recompose and the description's
        own round trip.
        """
        self.mark_unsaved()

    def on_config_changed(self) -> None:
        canvas_config = self._window.bot_canvas.config
        if canvas_config is not None:
            # First, so the flag below is raised for the document now on top.
            self._loaded_config = canvas_config
        self.mark_unsaved()
        if canvas_config is not None and canvas_config.is_definition:
            # No sidebars to follow and no options to compose: a definition has neither.
            self._window.bot_canvas.refresh_initial(canvas_config.initial)
            self._window.bot_canvas.refresh_edge_flags()
            self._window.set_bot_name(canvas_config.name, "subgraph")
            return
        if canvas_config is not None:
            # Undo/redo replaces the canvas's config object; the sidebar has to
            # follow it or its edits go nowhere.  No-op the rest of the time.
            self._window.left_sidebar.sync_config(canvas_config, self._window.bot_canvas.mode == BotMode.EDIT)
            self._window.right_sidebar.sync_config(canvas_config)
        if self._loaded_config is not None:
            self._window.bot_canvas.refresh_initial(self._loaded_config.initial)
            self._window.bot_canvas.refresh_recovery(self._loaded_config.recovery_state)
            # An edge is drawn dashed while it is conditional, and deleting an option
            # strips its conditions off every transition that guarded on it -- from a
            # surface with no way to reach the canvas.  Cheap: each edge is only
            # repainted when its own flag actually moved.
            self._window.bot_canvas.refresh_edge_flags()
            self._window.left_sidebar.refresh_options(self._loaded_config)
            self._window.set_bot_name(
                self._loaded_config.name, self._roots.file_label(self._loaded_config, self._last_save_path)
            )

    # ------------------------------------------------------------------
    # Subgraphs
    # ------------------------------------------------------------------

    def compose(self, config: BotConfig) -> tuple[BotConfig, ComposeReport]:
        """Recompose every linked region of ``config`` from the library, reloaded first.

        The result is what the app holds in memory; the file the config came from is
        never written here (see docs/SUBGRAPHS.md). ``compose_bot`` raising is a bug
        in composition, not in the library, and a bug there must not make a bot
        unopenable: it is logged and the snapshot in the file is used as-is.
        """
        self._subgraph_library.reload()
        try:
            return compose_bot(config, self._subgraph_library)
        except Exception:
            logger.exception("Composing subgraphs failed; using the bot file's snapshot")
            return config, ComposeReport()

    def recompose_before_run(self) -> bool:
        """Refresh linked regions from the library before a run. Returns whether to proceed.

        Silent by design: one log line per region that changed and the banner entry
        the canvas draws from the report, never a dialog -- see docs/SUBGRAPHS.md.
        The one refusal is a cycle, which has no snapshot worth running. A changed
        region swaps the in-memory config, exactly as an undo does, and marks the bot
        unsaved so the refreshed snapshot reaches disk on the next save.
        """
        if self._loaded_config is None:
            return True
        before = self._loaded_config
        composed, report = self.compose(before)
        canvas = self._window.bot_canvas
        if composed is not before and composed.model_dump() != before.model_dump():
            for region_id, label in _changed_linked_regions(before, composed):
                status = report.regions.get(region_id)
                version = status.library_version if status is not None else None
                logger.info("Subgraph region '%s' updated to library version %s", label, version)
            canvas.replace_config(composed, "Update subgraphs")
            self._loaded_config = composed
            # An Official bot runs the refreshed regions without anything to save (#149).
            self.mark_unsaved()
        canvas.set_compose_report(report)
        labels = {r.id: (r.label or r.id) for r in composed.regions}
        cycles = [(labels.get(rid, rid), s.reasons) for rid, s in report.regions.items() if s.kind == "cycle"]
        if not cycles:
            return True
        lines = "\n".join(f"\u2022 {label}: {'; '.join(reasons)}" for label, reasons in cycles)
        logger.critical("Refusing to run: %d subgraph region(s) contain a cycle", len(cycles))
        QMessageBox.critical(
            self._window,
            "Subgraph Cycle",
            "This bot cannot be run. A linked subgraph contains itself:\n\n"
            f"{lines}\n\n"
            "Fix the definition in the subgraph library, or detach the region.",
        )
        return False

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def new_bot(self) -> bool:
        """New Bot: ask for a starting point and the bot's details, then save the bot and open it for editing.

        Saved before it is opened, so the file exists from the start: a plain Save can
        never land on a template, and a bot abandoned half set up stays in the library.
        False when nothing was created.
        """
        if not self.close_definitions():
            return False
        # Written to the user's own folder, offering every world folder either root has.
        bots_dir = bot_roots.user_bots_dir()
        dialog = NewBotDialog(
            new_bot.starting_points(self._roots.root, self._roots.user_root),
            bots_dir,
            self._window,
            initial_folder=self._new_bot_folder(),
            folder_sources=(bot_roots.shipped_bots_dir(),),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        if not self.confirm_unsaved("New Bot"):
            return False
        # Composed before it is written, so the file holds what opening it would make
        # of it and the first Save after that changes nothing.
        config, _report = self.compose(new_bot.create_bot(dialog.start, dialog.details()))
        path = dialog.target_path()
        try:
            config.save(path)
        except Exception as e:
            QMessageBox.critical(self._window, "Failed to create bot", str(e))
            return False
        self.open_bot(str(path), mode=BotMode.EDIT)
        return True

    def _new_bot_folder(self) -> str | None:
        """The library folder New Bot opens on: the one being looked at, else the open bot's."""
        looking_at = self._window.bot_canvas.library_folder()
        if looking_at is not None:
            return looking_at
        return world_folder(self._last_save_path)

    def open_bot(self, path: str | None = None, *, mode: BotMode = BotMode.RUN) -> None:
        if not self.close_definitions():
            return
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self._window, "Open Bot File", "", "Bot Files (*.json)")
        if not path:
            return
        if not self.confirm_unsaved("Open Bot"):
            return
        try:
            config = BotConfig.load(path)
        except Exception as e:
            QMessageBox.critical(self._window, "Failed to load bot", str(e))
            return
        if config.is_template and not self._roots.may_edit_template(config, path, self._window):
            return
        config, report = self.compose(config)
        old_save_path = self._last_save_path
        self.delete_autosave(old_save_path)
        self.has_unsaved_changes = False
        self._loaded_config = config
        self._last_save_path = path
        self._rewards_declined_for = None
        # An Official bot is read-only and only ever opens in Run mode (#149).
        self._official_open = not config.is_template and bot_roots.is_shipped(path)
        if self._official_open:
            mode = BotMode.RUN
        self._window.set_bot_name(config.name, self._roots.file_label(config, path))
        self._window.bot_canvas.set_read_only(self._official_open)
        self._window.bot_canvas.setup(config, mode=mode)
        # After setup and compose, which the open does not count as edits either.
        self.clean_snapshot = file_snapshot(config)
        self._window.bot_canvas.set_compose_report(report)
        self.template_context_changed.emit()
        self._window.left_sidebar.set_config(config)
        self._window.right_sidebar.set_config(config)
        self.enable_save_actions()
        self._window.set_close_enabled(True)
        self._window.set_start_enabled(self.start_enabled())
        if not config.is_template:
            # A draft is reached through File › Templates…, never as a recent bot.
            self._window.update_recent_menu(SettingsManager.instance().add_recent_bot_file(path))
        self._window.bot_canvas.refresh_placeholder()

    def close_bot(self) -> None:
        if self._loaded_config is None:
            return
        if self._loaded_config.is_definition:
            # Close is the document on top: a definition goes back to what it was opened from.
            self.definition_back()
            return
        if not self.confirm_unsaved("Close Bot"):
            return
        self.delete_autosave(stem=self.autosave_stem())
        self._loaded_config = None
        self._last_save_path = None
        self._official_open = False
        self.has_unsaved_changes = False
        self.clean_snapshot = None
        self._rewards_declined_for = None
        self._window.bot_canvas.close_bot()
        self._window.left_sidebar.set_config(None)
        self._window.right_sidebar.set_config(None)
        self._window.set_bot_name(None, None)
        self._window.set_save_enabled(False)
        self._window.set_close_enabled(False)
        self._window.set_start_enabled(False)

    def confirm_unsaved(self, title: str) -> bool:
        """Ask about the open bot's unsaved edits before something replaces it. False to stay put.

        Save saves, and stays put if the save is cancelled or fails. Discard drops the edits
        and their recovery file, so the next launch does not offer them back. Open, New Bot
        and Quit used to replace an edited bot without asking, deleting its recovery file on
        the way (#154). The flag is raised by edits that change nothing, too: ask only when
        one did. A definition on top is closed first, by ``close_definitions``.
        """
        config = self._loaded_config
        if config is None or not self.has_unsaved_changes or matches_clean(config, self.clean_snapshot):
            return True
        reply = QMessageBox.question(
            self._window,
            title,
            f"Save your changes to '{config.name}' first?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if reply == QMessageBox.StandardButton.Save:
            return self.save_bot() and not self.has_unsaved_changes
        if reply != QMessageBox.StandardButton.Discard:
            return False
        self.delete_autosave(stem=self.autosave_stem())
        self.has_unsaved_changes = False
        return True

    def save_and_done(self) -> None:
        """Done: back to Run mode, but only once the save has gone through.

        A save the author cancels -- at the Rewards prompt, or in the file dialog --
        leaves them where they were, editing. A bot still as it was opened or last saved
        is not written again: Done is Edit mode's one way back to Run mode (#267), and leaving
        after only looking would otherwise rewrite the file and ask about Rewards.
        """
        if matches_clean(self._loaded_config, self.clean_snapshot) or self.save_bot():
            self._window.bot_canvas.exit_edit_mode(saved=True)

    def _confirm_rewards_tags(self) -> bool:
        """Ask for a Rewards tag if the bot has none. False when the author cancels the save.

        Asked once per open: Save Without is remembered against the bot's id until it is
        closed or reopened. A template draft is never asked.
        """
        config = self._loaded_config
        if (
            config is None
            or config.id == self._rewards_declined_for
            or not wants_rewards_prompt(config.tags, config.is_template)
        ):
            return True
        dialog = RewardsTagDialog(suggested_tags(config.states), self._window)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        if dialog.choice is RewardsChoice.TAGGED:
            config.tags = normalize_tags([*config.tags, *dialog.selected_tags()])
            self._window.bot_canvas.refresh_tags()
        else:
            self._rewards_declined_for = config.id
        return True

    def save_bot(self) -> bool:
        """Save the open document. False when nothing was written: cancelled, refused or failed."""
        if self._loaded_config is None:
            return False
        if self._loaded_config.is_definition:
            return self.save_definition()
        if self.read_only_open():
            # An Official bot is never changed, so there is nothing to write: Save As
            # makes a plain copy, and Edit mode offers Make my own copy (#149).
            return False
        if self._last_save_path is None:
            return self.save_bot_as()
        if not self._loaded_config.is_template and not bot_roots.may_write(self._last_save_path):
            # An Official bot, or one recovered from a crash in it: the install directory is
            # replaced by every update, so the save goes to a copy in the user's own folder.
            return self.save_bot_as()
        if self._loaded_config.is_template:
            refused = template_store.draft_name_refused(self._loaded_config, **self._roots.kwargs())
            if refused:
                QMessageBox.information(self._window, "Save Template", refused)
                return False
        if not self._confirm_rewards_tags():
            return False
        try:
            if not self._loaded_config.is_template:
                # A template keeps no history: a released version is hashed by its bytes.
                bot_roots.record_save(self._loaded_config, self._last_save_path)
            self._loaded_config.save(self._last_save_path)
            self.has_unsaved_changes = False
            self.clean_snapshot = file_snapshot(self._loaded_config)
            if self._loaded_config.is_template:
                self.delete_autosave(stem=self.autosave_stem())
            else:
                self.delete_autosave()
        except Exception as e:
            QMessageBox.critical(self._window, "Failed to save bot", str(e))
            return False
        return True

    def save_bot_as(self) -> bool:
        """Save the open bot to a file the user names. False when nothing was written."""
        if self._loaded_config is None:
            return False
        if self._loaded_config.is_definition:
            # A definition lives in the library under its id, not in a file the user names.
            return self.save_definition()
        if self._loaded_config.is_template:
            # A copy would be a second draft of one template, or a bot carrying
            # template_meta; a bot is started from a template with New Bot instead.
            QMessageBox.information(
                self._window,
                "Save Bot As",
                "A template draft is only saved in place. Release it to make a version New Bot offers.",
            )
            return False
        path, _ = QFileDialog.getSaveFileName(
            self._window, "Save Bot As", str(_save_as_start(self._last_save_path)), "Bot Files (*.json)"
        )
        if not path:
            return False
        if not path.endswith(".json"):
            path += ".json"
        if not bot_roots.may_write(path):
            QMessageBox.information(
                self._window,
                "Save Bot As",
                "That folder belongs to BartleBot's installation, which every update replaces, "
                "so a bot saved there would be lost. Choose a folder of your own.",
            )
            return False
        # After the file dialog, not before: cancelling the dialog should ask nothing.
        if not self._confirm_rewards_tags():
            return False
        old_save_path = self._last_save_path
        # A fork gets its own identity. The copy would otherwise share the original's
        # id, and with it every value the user has stored against that bot -- and the
        # remembered combat strategy, which is a fork's to choose again.
        # Its history goes too: what the original was copied from is not the copy's own past.
        old_id = self._loaded_config.id
        old_history = self._loaded_config.history
        old_sent = self._loaded_config.sent_sha256
        self._loaded_config.id = str(uuid4())
        self._loaded_config.history = []
        self._loaded_config.sent_sha256 = None
        try:
            self._loaded_config.save(path)
        except Exception as e:
            # Nothing was written, so the file on disk is still the original and the
            # loaded bot is still that bot. Keeping the minted id here would orphan
            # every value the user has stored against it for the rest of the session.
            self._loaded_config.id = old_id
            self._loaded_config.history = old_history
            self._loaded_config.sent_sha256 = old_sent
            QMessageBox.critical(self._window, "Failed to save bot", str(e))
            return False
        if self._rewards_declined_for == old_id:
            # Still the same open document, under its new identity.
            self._rewards_declined_for = self._loaded_config.id
        self._last_save_path = path
        self.has_unsaved_changes = False
        self.clean_snapshot = file_snapshot(self._loaded_config)
        self.delete_autosave(old_save_path)
        # The copy is the user's own: editable, and saved in place from now on.
        self._official_open = False
        self._window.bot_canvas.set_read_only(False)
        self._window.set_save_enabled(True)
        self._window.set_bot_name(self._loaded_config.name, Path(path).name)
        self._window.update_recent_menu(SettingsManager.instance().add_recent_bot_file(path))
        self._window.bot_canvas.refresh_placeholder()
        # A new id resolves against a different set of stored values -- none, for a
        # fresh fork -- so every surface displaying one has to re-resolve. Without
        # this, Option Values goes on showing the original's overrides against a bot that
        # does not have them, and only a reopen tells the truth.
        self._window.left_sidebar.refresh_options(self._loaded_config)
        # The combat half of the same re-resolve: the active strategy and the BCS
        # list are both keyed on the id, and a fork inherits neither.
        self._window.left_sidebar.rebind_identity()
        # And the template half: the canvas holds the base version's setup steps and the
        # update waiting against the bot's id, so under the new one it had neither, and
        # a copy of a half set up bot lost its Setup errors until it was reopened.
        self.template_context_changed.emit()
        return True

    def make_own_copy(self) -> bool:
        """Make my own copy of the open Official bot, and open the copy in Edit mode (#149).

        Asked when Edit mode is asked for on an Official bot. The copy is a fork
        (:mod:`bartlebot.content.bot_fork`): a new id recording the Official bot's, in the user's
        own folder, written at once as New Bot writes a new bot. Unlike Save As, it takes
        every option value, Bot Combat Strategy and the active pick with it, so it runs
        exactly as the Official bot did. False when cancelled or nothing was written.
        """
        config, path = self._loaded_config, self._last_save_path
        if config is None or path is None or not self.read_only_open():
            return False
        dialog = MakeCopyDialog(config.name, self._window)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        try:
            # The file as it ships, not the composed copy in memory: its hash is the record.
            official = BotConfig.load(path)
            fork, _report = self.compose(bot_fork.make_fork(official, dialog.name))
            target = bot_fork.fork_path(path, bot_roots.user_bots_dir(), world_folder(path))
            target.parent.mkdir(parents=True, exist_ok=True)
            fork.save(target)
        except Exception as e:
            logger.exception("Could not make a copy of %s", path)
            QMessageBox.critical(self._window, MAKE_COPY_TITLE, f"Could not make your copy:\n\n{e}")
            return False
        logger.info("Made a copy of the Official bot '%s' at %s", official.name, target)
        try:
            bot_fork.carry_over(official.id, fork.id, SettingsManager.instance())
        except Exception:
            # The copy exists and opens; what did not come with it is the user's to set again.
            logger.exception("Could not carry the option values and combat setup over to the copy")
            QMessageBox.warning(
                self._window,
                MAKE_COPY_TITLE,
                "Your copy was made, but not all of its option values and Bot Combat Strategies "
                "could be copied from the Official bot. See the log for details.",
            )
        self.open_bot(str(target), mode=BotMode.EDIT)
        return True

    # ------------------------------------------------------------------
    # Definition documents
    # ------------------------------------------------------------------

    @property
    def document_depth(self) -> int:
        """How many documents sit under the open one."""
        return len(self._doc_stack)

    def open_definition(self, subgraph_id: str) -> bool:
        """Open a subgraph definition as a document, on top of whatever is open.

        The document underneath is set aside whole -- canvas, undo history, file,
        unsaved flag -- and comes back when this one closes. A definition already open
        further down is refused, since editing it twice at once has no good answer.
        """
        self._subgraph_library.reload()
        defn = self._subgraph_library.get(subgraph_id)
        if defn is None:
            QMessageBox.warning(self._window, "Open Definition", "That subgraph is not in the library.")
            return False
        if subgraph_id in self.open_definition_ids():
            QMessageBox.information(
                self._window, "Open Definition", f"'{defn.name}' is already open: go back to it to edit it."
            )
            return False
        self._push_current()
        self._show_definition(definition_to_config(defn), unsaved=False)
        logger.info("Opened subgraph '%s' v%d as a document", defn.name, defn.version)
        return True

    def _push_current(self) -> None:
        """Set the open document aside on the stack, if there is one."""
        if self._loaded_config is None:
            return
        # The one underneath stops autosaving while it is covered, so its latest
        # edits reach a recovery file now rather than being held only in memory.
        self.do_autosave()
        doc = self._window.bot_canvas.take_document()
        if doc is not None:
            self._doc_stack.append(
                _DocumentFrame(doc, self._last_save_path, self.has_unsaved_changes, self.clean_snapshot)
            )

    def _show_definition(self, config: BotConfig, *, unsaved: bool) -> None:
        canvas = self._window.bot_canvas
        config, report = self.compose(config)
        self._loaded_config = config
        self._last_save_path = None
        self.has_unsaved_changes = unsaved
        canvas.setup(config, mode=BotMode.EDIT)
        # A recovered definition has no saved state here to fall back to.
        self.clean_snapshot = None if unsaved else file_snapshot(config)
        canvas.set_compose_report(report)
        self._refresh_definition_context()
        # The sidebars describe a bot: its options, combat, drops. A definition has none.
        self._window.left_sidebar.set_config(None)
        self._window.right_sidebar.set_config(None)
        self._window.set_bot_name(config.name, "subgraph")
        self._window.set_save_enabled(True)
        self._window.set_close_enabled(True)
        self._window.set_start_enabled(False)

    def _refresh_definition_context(self) -> None:
        """Hand the canvas the crumb's path -- every document under this one -- and what its header says."""
        config = self._loaded_config
        if config is None or config.definition is None:
            return
        path = [frame.doc.config.name for frame in self._doc_stack]
        origin = self._subgraph_library.origin(config.definition.id)
        used_by = self._bots_linking(config.definition.id)
        # "· Official updated" on a modified Official one whose Official one has changed
        # since, beside the Revert the header offers it in Edit mode (#169).
        updated = self._subgraph_library.official_updated(config.definition.id)
        self._window.bot_canvas.set_definition_context(
            path,
            origin=f"Source: {pill_text(origin, official_updated=updated)}",
            used_by=used_by,
            can_revert=origin == Origin.SHIPPED_OVERRIDE,
            can_rename=origin == Origin.USER,
        )

    def _bots_linking(self, subgraph_id: str) -> int:
        """How many bot files in the library link ``subgraph_id``."""
        return count_subgraph_uses(*bot_roots.library_dirs()).get(subgraph_id, 0)

    def open_definition_ids(self) -> set[str]:
        """The ids of every definition open as a document, on top or set aside under it."""
        configs = [f.doc.config for f in self._doc_stack]
        if self._loaded_config is not None:
            configs.append(self._loaded_config)
        return {c.definition.id for c in configs if c.definition is not None}

    def delete_subgraph(self, subgraph_id: str) -> bool:
        """Delete a definition from the library, from its card there. False when refused or cancelled.

        Deleting removes the user's file only (:mod:`bartlebot.content.store_roots`), so a
        modified Official one is reverted rather than deleted, and an unedited one has
        nothing to delete. What linked the definition is never broken by it: a bot keeps
        the snapshot it was saved with and runs that, flagged ``missing``, as does a bot
        linking another definition that nested it (docs/SUBGRAPHS.md). It is still
        asked first, naming them, because from then on none of them gets an update.
        Refused while the definition is open, since the document would save it back.
        """
        library = self._subgraph_library
        library.reload()
        defn = library.get(subgraph_id)
        origin = library.origin(subgraph_id)
        if defn is None or origin not in (Origin.USER, Origin.SHIPPED_OVERRIDE):
            return False
        revert = origin is Origin.SHIPPED_OVERRIDE
        title = "Revert to Official" if revert else "Delete Subgraph"
        verb = "revert" if revert else "delete"
        if subgraph_id in self.open_definition_ids():
            QMessageBox.information(self._window, title, f"'{defn.name}' is open. Close it first, then {verb} it.")
            return False
        if revert:
            # The id stays, so whatever links it follows the Official one from its next compose.
            text = (
                f"Revert '{defn.name}' to the Official version?\n\n"
                "Your changes to it are lost, and every bot linking it uses the Official version "
                "from now on. This can't be undone."
            )
        else:
            text = f"Delete '{defn.name}' from the library?"
            users = []
            if n := self._bots_linking(subgraph_id):
                users.append(f"{n} bot{'s' if n != 1 else ''}")
            users.extend(f"the subgraph '{d.name}'" for d in library.nesting(subgraph_id))
            if users:
                listed = users[0] if len(users) == 1 else f"{', '.join(users[:-1])} and {users[-1]}"
                text += (
                    f"\n\nIt is used by {listed}. Each keeps running the copy it was last saved with, "
                    "flagged as a missing subgraph, and gets no more changes to it."
                )
            text += "\n\nThis can't be undone."
        reply = QMessageBox.question(
            self._window,
            title,
            text,
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return False
        try:
            library.delete(subgraph_id)
        except OSError as exc:
            QMessageBox.critical(self._window, title, f"Could not {verb} '{defn.name}':\n\n{exc}")
            return False
        # A recovery file holds edits to a definition that is no longer there to take them.
        self.delete_autosave(stem=f"{_DEFINITION_AUTOSAVE_PREFIX}{subgraph_id}")
        if revert:
            logger.info("Reverted subgraph '%s' to the Official version", defn.name)
        else:
            logger.info("Deleted subgraph '%s' from the library", defn.name)
        canvas = self._window.bot_canvas
        canvas.refresh_subgraph_listing()
        if self._loaded_config is not None:
            # The open document's banner says what its links compose to now. Its config
            # follows at the next open or run, as for any library change.
            _, report = self.compose(self._loaded_config)
            canvas.set_compose_report(report)
        return True

    def delete_bot(self, path: str) -> bool:
        """Delete a bot from its card in the library. False when refused, cancelled or it failed.

        The user's own bots only: an Official one ships in the install directory and would
        be back with the next launch. Nothing links a bot, so no other bot is affected,
        a fork included (:mod:`bartlebot.content.bot_delete`); the confirmation names the
        Bot Combat Strategies that go with it, as they are the user's work. Refused while
        the bot is open, since the document would save it back.
        """
        title = "Delete Bot"
        if bot_roots.is_shipped(path):
            return False
        try:
            plan = bot_delete.plan_deletion(path)
        except Exception as e:
            QMessageBox.critical(self._window, title, f"The bot could not be read:\n\n{e}")
            return False
        if self._last_save_path is not None and _same_file(self._last_save_path, path):
            QMessageBox.information(self._window, title, f"'{plan.name}' is open. Close it first, then delete it.")
            return False
        text = f"Delete the bot '{plan.name}'?"
        if n := len(plan.strategy_ids):
            text += f"\n\nIts {n} Bot Combat Strateg{'ies' if n != 1 else 'y'} will be deleted with it."
        text += "\n\nThis can't be undone."
        reply = QMessageBox.question(
            self._window,
            title,
            text,
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return False
        settings = SettingsManager.instance()
        try:
            bot_delete.delete_bot(plan, settings)
        except OSError as exc:
            QMessageBox.critical(self._window, title, f"Could not delete '{plan.name}':\n\n{exc}")
            return False
        # A recovery file holds edits to a bot that is no longer there to take them. Autosaves
        # are named by file stem, so one the open document is writing under is left alone.
        if self._loaded_config is None or self.autosave_stem() != Path(path).stem:
            self.delete_autosave(path)
        self._window.update_recent_menu(settings.recent_bot_files)
        self._window.bot_canvas.refresh_subgraph_listing()
        return True

    def rename_subgraph(self, subgraph_id: str) -> bool:
        """Rename a definition, and edit its description, from its card in the library. False when refused or cancelled.

        Written at the version it has: a bot links the id, and an instance keeps the
        label it was given when it was placed, so nothing that links it changes and
        none of them is offered an update (:func:`~bartlebot.content.subgraphs.subgraphs.definition_change`).
        The user's own definitions only. An Official one's name is the shipped file's,
        and renaming it would make a copy that stops its updates arriving -- in dev
        mode every definition is the user's, so that is where an Official one is renamed.
        Refused while the definition is open, whose header renames it instead.
        """
        library = self._subgraph_library
        library.reload()
        defn = library.get(subgraph_id)
        if defn is None or library.origin(subgraph_id) is not Origin.USER:
            return False
        title = "Rename Subgraph"
        if subgraph_id in self.open_definition_ids():
            QMessageBox.information(
                self._window, title, f"'{defn.name}' is open. Rename it there, in its header in Edit mode."
            )
            return False
        from bartlebot.gui.canvas.subgraph_widgets import SubgraphMetaDialog  # noqa: PLC0415

        dlg = SubgraphMetaDialog(
            self._window, name=defn.name, description=defn.description or "", title=title, commit_text="Rename"
        )
        if not dlg.exec():
            return False
        if (dlg.name, dlg.description) == (defn.name, defn.description):
            return False
        renamed = defn.model_copy(update={"name": dlg.name, "description": dlg.description})
        try:
            library.save(renamed)
        except OSError as exc:
            QMessageBox.critical(self._window, title, f"Could not rename '{defn.name}':\n\n{exc}")
            return False
        if renamed.name != defn.name:
            logger.info("Renamed subgraph '%s' to '%s'", defn.name, renamed.name)
        else:
            logger.info("Changed the description of subgraph '%s'", defn.name)
        self._window.bot_canvas.refresh_subgraph_listing()
        return True

    def on_definition_save_requested(self, then_back: bool) -> None:
        if self.save_definition() and then_back:
            self.definition_back()

    def save_definition(self) -> bool:
        """Write the open definition to the library as its next version. False when refused or cancelled.

        Only a change to what runs makes a new version (#158): a definition identical
        to the library's copy is not written at all, and one that differs only in
        layout or in name and description (:func:`~bartlebot.content.subgraphs.subgraphs.definition_change`)
        is written at the version it has. Refused while
        the document has errors: a definition that cannot compose must not reach the
        bots that link it. A shipped definition is saved as the user's
        own copy, which shadows it until reverted -- asked first, since from then on
        the shipped one's updates stop arriving.
        """
        config = self._loaded_config
        if config is None or config.definition is None:
            return False
        if self._refused_for_errors(config, "Save to Library"):
            return False
        library = self._subgraph_library
        library.reload()
        sid = config.definition.id
        try:
            defn = config_to_definition(config)
        except ValueError as exc:
            QMessageBox.critical(self._window, "Save to Library", str(exc))
            return False
        stored = library.get(sid)
        change = definition_change(stored, defn) if stored is not None else "content"
        if change == "none":
            # Nothing to write: a new version would send every linking bot an update
            # that changes nothing, and an Official one would gain a copy that shadows it.
            config.definition.version = stored.version
            self._mark_definition_saved(config)
            logger.info("Subgraph '%s' is unchanged; kept version %d", config.name, stored.version)
            return True
        if library.origin(sid) == Origin.SHIPPED and not dev_mode():
            reply = QMessageBox.question(
                self._window,
                "Save to Library",
                f"'{config.name}' is Official. Saving makes a modified copy of it, which every bot "
                "linking it uses from now on, in place of the Official one's updates.\n\n"
                "Save the modified copy?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if reply != QMessageBox.StandardButton.Save:
                return False
        if stored is not None and not self._confirm_signature_change(stored, defn):
            return False
        if change in ("layout", "meta"):
            version = stored.version
        else:
            version = max(config.definition.version, stored.version if stored is not None else 0) + 1
        defn = defn.model_copy(update={"version": version})
        try:
            library.save(defn)
        except Exception as exc:
            QMessageBox.critical(self._window, "Save to Library", f"Could not save '{config.name}':\n\n{exc}")
            return False
        config.definition.version = version
        self._mark_definition_saved(config)
        logger.info("Saved subgraph '%s' version %d to the library", defn.name, version)
        return True

    def _confirm_signature_change(self, stored, defn) -> bool:
        """Warn before a save that changes what a linking bot depends on (#169). False when cancelled.

        Asked only when the save removes or renames an input or a port, or adds an input
        with no default (:mod:`bartlebot.content.subgraphs.subgraph_signature`), and only when a bot or
        another definition links this one: with nothing linking it, nothing can break.
        A warning, never a refusal: a bot that cannot take the change is held on its
        snapshot.
        """
        changes = signature_changes(stored, defn)
        if not changes:
            return True
        users = bots_linking(defn.id, *bot_roots.library_dirs())
        users += [f"the subgraph '{d.name}'" for d in self._subgraph_library.nesting(defn.id)]
        if not users:
            return True
        reply = QMessageBox.warning(
            self._window,
            "Save to Library",
            signature_warning_text(defn.name, changes, users),
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if reply != QMessageBox.StandardButton.Save:
            return False
        logger.info("Saving subgraph '%s' with %d signature change(s) used by %d", defn.name, len(changes), len(users))
        return True

    def _mark_definition_saved(self, config: BotConfig) -> None:
        self.has_unsaved_changes = False
        self.clean_snapshot = file_snapshot(config)
        self.delete_autosave(stem=self.autosave_stem())
        self._refresh_definition_context()

    def _refused_for_errors(self, config: BotConfig, title: str) -> bool:
        """Say what blocks saving the open definition, if anything does. True when it is refused."""
        errors = self._window.bot_canvas.validation_errors()
        if not errors:
            return False
        listed = "\n".join(f"\u2022 {e}" for e in errors[:12])
        more = f"\n\u2026and {len(errors) - 12} more" if len(errors) > 12 else ""
        self._window.bot_canvas.param_panel.show_required_errors()
        QMessageBox.critical(self._window, title, f"Fix these before saving '{config.name}':\n\n{listed}{more}")
        return True

    def save_definition_as(
        self, name: str | None = None, description: str | None = None, categories: list[str] | None = None
    ) -> bool:
        """Save the open definition as a new one, and go on editing the new one. False when refused or cancelled.

        Save As, as for a file: the copy gets its own id at version 1, and the
        document becomes the copy. The bots linking the original -- the one this was
        opened from included -- keep linking it, since the copy is a new thing nothing
        uses yet. Asks for a name, description and categories, starting from the
        original's, unless a name is given; ``categories`` None keeps the original's.
        """
        config = self._loaded_config
        if config is None or config.definition is None:
            return False
        if self._refused_for_errors(config, "Save as New Subgraph"):
            return False
        if name is None:
            from bartlebot.gui.canvas.subgraph_widgets import SubgraphMetaDialog  # noqa: PLC0415

            dlg = SubgraphMetaDialog(
                self._window,
                name=f"{config.name} (copy)",
                description=config.description or "",
                categories=list(config.definition.tags),
                title="Save as New Subgraph",
            )
            if not dlg.exec():
                return False
            name, description, categories = dlg.name, dlg.description, dlg.categories
        try:
            defn = config_to_definition(config, version=1)
        except ValueError as exc:
            QMessageBox.critical(self._window, "Save as New Subgraph", str(exc))
            return False
        update = {"id": str(uuid4()), "name": name, "description": description}
        if categories is not None:
            update["tags"] = list(categories)
        defn = defn.model_copy(update=update)
        try:
            self._subgraph_library.save(defn)
        except Exception as exc:
            QMessageBox.critical(self._window, "Save as New Subgraph", f"Could not save '{name}':\n\n{exc}")
            return False
        # The unsaved edits went into the copy; the original's recovery file has nothing left to recover.
        self.delete_autosave(stem=self.autosave_stem())
        self._reshow_definition(defn)
        logger.info("Saved subgraph '%s' as a new subgraph", defn.name)
        return True

    def revert_definition(self) -> bool:
        """Discard the user's copy of an Official definition, and show the Official one. False when cancelled.

        Deleting the user copy is the revert (:mod:`bartlebot.content.store_roots`). Every bot
        linking it follows the Official one from its next compose, and unsaved edits
        in the document go with the copy, so it asks first.
        """
        config = self._loaded_config
        if config is None or config.definition is None:
            return False
        sid = config.definition.id
        library = self._subgraph_library
        if library.origin(sid) != Origin.SHIPPED_OVERRIDE:
            return False
        reply = QMessageBox.question(
            self._window,
            "Revert to Official",
            f"Revert '{config.name}' to the Official version?\n\n"
            "Your changes to it are lost, and every bot linking it uses the Official version "
            "from now on. This can't be undone.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return False
        library.delete(sid)
        shipped = library.get(sid)
        if shipped is None:
            QMessageBox.critical(
                self._window, "Revert to Official", f"The Official '{config.name}' could not be loaded."
            )
            return False
        self.delete_autosave(stem=self.autosave_stem())
        self._reshow_definition(shipped)
        logger.info("Reverted subgraph '%s' to the Official version %d", shipped.name, shipped.version)
        return True

    def _reshow_definition(self, defn) -> None:
        """Put ``defn`` in place of the open definition, saved, keeping the camera.

        A fresh document rather than an edit to this one: its undo history belongs to
        a definition with a different id or content, and undoing into it would save
        over the wrong file.
        """
        canvas = self._window.bot_canvas
        camera = canvas.view.camera_state()
        self._show_definition(definition_to_config(defn), unsaved=False)
        canvas.view.restore_camera(camera)

    def back_to_document(self, depth: int) -> bool:
        """Close definitions until ``depth`` documents sit under the open one, asking at each. False if one was cancelled."""
        while len(self._doc_stack) > depth and self._loaded_config is not None and self._loaded_config.is_definition:
            if not self.definition_back():
                return False
        return True

    def definition_back(self) -> bool:
        """Close the open definition, asking about unsaved changes first. False when the user cancels."""
        config = self._loaded_config
        if config is None or config.definition is None:
            return True
        if self.has_unsaved_changes and not matches_clean(config, self.clean_snapshot):
            reply = QMessageBox.question(
                self._window,
                "Close Definition",
                f"Save your changes to '{config.name}' to the library?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if reply == QMessageBox.StandardButton.Cancel:
                return False
            if reply == QMessageBox.StandardButton.Save and not self.save_definition():
                return False
        self._leave_definition()
        return True

    def _leave_definition(self) -> None:
        """Drop the open definition and put back the document underneath, recomposed from the library."""
        stem = self.autosave_stem()
        self.delete_autosave(stem=stem)
        canvas = self._window.bot_canvas
        if not self._doc_stack:
            self._loaded_config = None
            self._last_save_path = None
            self._official_open = False
            self.has_unsaved_changes = False
            self.clean_snapshot = None
            canvas.close_bot()
            self._window.set_bot_name(None, None)
            self._window.set_save_enabled(False)
            self._window.set_close_enabled(False)
            self._window.set_start_enabled(False)
            return
        frame = self._doc_stack.pop()
        canvas.restore_document(frame.doc)
        config = frame.doc.config
        self._loaded_config = config
        self._last_save_path = frame.path
        self.has_unsaved_changes = frame.unsaved
        self.clean_snapshot = frame.clean
        # What was just edited is linked from here, most likely: take it now, as a
        # run would, so the call node shows the new ports straight away.
        composed, report = self.compose(config)
        if composed is not config and composed.model_dump() != config.model_dump():
            canvas.replace_config(composed, "Update subgraphs")
            self._loaded_config = composed
            self.has_unsaved_changes = True
        canvas.set_compose_report(report)
        if self.read_only_open():
            # An Official bot has nothing to save: what composed differently is shown, not kept.
            self.has_unsaved_changes = False
        loaded = self._loaded_config
        if loaded.is_definition:
            self._refresh_definition_context()
            self._window.set_bot_name(loaded.name, "subgraph")
            self._window.set_start_enabled(False)
        else:
            self._window.left_sidebar.set_config(loaded)
            self._window.right_sidebar.set_config(loaded)
            self._window.left_sidebar.set_editable(canvas.mode == BotMode.EDIT)
            self._window.right_sidebar.set_editable(canvas.mode == BotMode.EDIT)
            self._window.set_bot_name(loaded.name, self._roots.file_label(loaded, self._last_save_path))
            self.template_context_changed.emit()
            self._window.set_start_enabled(self.start_enabled())
        self.enable_save_actions()
        self._window.set_close_enabled(True)

    def close_definitions(self) -> bool:
        """Close every definition document open, top down. False if the user cancelled one."""
        while self._loaded_config is not None and self._loaded_config.is_definition:
            if not self.definition_back():
                return False
        return True

    # ------------------------------------------------------------------
    # Auto-save / crash recovery
    # ------------------------------------------------------------------

    def reconfigure_autosave_timer(self) -> None:
        """Read current settings and start/stop/reconfigure the auto-save timer."""
        sm = SettingsManager.instance()
        enabled = sm.get_general("auto_save_enabled", default=True)
        interval_sec = sm.get_general("auto_save_interval_seconds", default=300)
        interval_ms = max(30, interval_sec) * 1000
        self._auto_save_timer.stop()
        if enabled:
            self._auto_save_timer.setInterval(interval_ms)
            self._auto_save_timer.start()

    def autosave_stem(self) -> str:
        """The recovery file's stem: the bot file's, or for a definition document the definition's id."""
        config = self._loaded_config
        if config is not None and config.definition is not None:
            return f"{_DEFINITION_AUTOSAVE_PREFIX}{config.definition.id}"
        if config is not None and config.is_template:
            return f"{_TEMPLATE_AUTOSAVE_PREFIX}{config.id}"
        return Path(self._last_save_path).stem if self._last_save_path else "_unsaved"

    def autosave_path(self) -> Path:
        """Derive the recovery file path for the currently loaded config."""
        return SettingsManager.autosave_dir() / f"{self.autosave_stem()}.autosave.json"

    def do_autosave(self) -> None:
        """Timer callback — write a recovery file if there are unsaved changes."""
        if not self.has_unsaved_changes or self._loaded_config is None:
            return
        # The config is read-only while the bot is running, nothing new to save.
        if self._is_running():
            return
        if matches_clean(self._loaded_config, self.clean_snapshot):
            # Nothing to recover: no edit was made, or every one was undone. A copy
            # identical to the file would only be offered back at the next launch (#122).
            self.has_unsaved_changes = False
            self.delete_autosave(stem=self.autosave_stem())
            return
        try:
            self._loaded_config.save(self.autosave_path())
            logger.debug("Auto-saved recovery file: %s", self.autosave_path())
            if self._last_save_path and not self._loaded_config.is_definition:
                SettingsManager.instance().set_autosave_origin(self.autosave_stem(), self._last_save_path)
        except Exception:
            logger.exception("Auto-save failed")

    def delete_autosave(self, save_path: str | None = None, *, stem: str | None = None) -> None:
        """Remove the recovery file after a successful manual save."""
        if stem is None:
            stem = Path(save_path).stem if save_path else "_unsaved"
        p = SettingsManager.autosave_dir() / f"{stem}.autosave.json"
        try:
            if p.exists():
                p.unlink()
                logger.debug("Deleted auto-save file: %s", p)
        except OSError:
            logger.warning("Could not delete auto-save file %s", p)
        SettingsManager.instance().remove_autosave_origin(stem)

    def check_crash_recovery(self) -> None:
        """Scan the autosave directory at startup and offer to restore newer files."""
        candidates = []
        for autosave_path in sorted(SettingsManager.autosave_dir().glob("*.autosave.json")):
            stem = autosave_path.name.replace(".autosave.json", "")
            if stem == "_unsaved" or stem.startswith(_DEFINITION_AUTOSAVE_PREFIX):
                # A definition's recovery file has no bot file to compare against; it
                # only exists while that definition had unsaved changes.
                original_path: Path | None = None
                is_candidate = True
            else:
                original_path = self._find_original_for_stem(stem)
                if original_path and original_path.exists():
                    is_candidate = autosave_path.stat().st_mtime > original_path.stat().st_mtime
                    if is_candidate and _same_bot_content(autosave_path, original_path):
                        # Newer, but restating the file: nothing to offer (#122).
                        self.delete_autosave(stem=stem)
                        continue
                else:
                    is_candidate = True
            if is_candidate:
                candidates.append(
                    {
                        "autosave_path": autosave_path,
                        "original_path": original_path,
                        "modified": datetime.fromtimestamp(autosave_path.stat().st_mtime),
                    }
                )
        if candidates:
            self._offer_recovery(candidates)

    def _find_original_for_stem(self, stem: str) -> Path | None:
        """Resolve the original bot file for an auto-save stem.

        Prefers the origin recorded at auto-save time (set_autosave_origin), which
        stores the exact path the auto-save was derived from. Falls back to a
        stem match against recent_bot_files for auto-saves written before that
        mapping existed.
        """
        origin = SettingsManager.instance().get_autosave_origin(stem)
        if origin:
            return Path(origin)
        for recent_path in SettingsManager.instance().recent_bot_files:
            p = Path(recent_path)
            if p.stem == stem:
                return p
        return None

    def _offer_recovery(self, candidates: list[dict]) -> None:
        """Show the RecoveryDialog and act on the user's choices."""
        from bartlebot.gui.dialogs.recovery_dialog import RecoveryDialog  # noqa: PLC0415

        dlg = RecoveryDialog(candidates, parent=self._window)
        if dlg.exec():
            chosen = dlg.selected_candidates()
        else:
            chosen = []

        chosen_paths = {c["autosave_path"] for c in chosen}

        # Restore chosen candidates (last one loaded becomes the active config).
        for c in chosen:
            autosave_path: Path = c["autosave_path"]
            original_path: Path | None = c.get("original_path")
            try:
                config = BotConfig.load(autosave_path)
            except Exception as e:
                QMessageBox.warning(
                    self._window,
                    "Recovery failed",
                    f"Could not load recovery file:\n{autosave_path}\n\n{e}",
                )
                chosen_paths.discard(autosave_path)
                continue
            if config.is_definition:
                self._push_current()
                self._show_definition(config, unsaved=True)
                continue
            config, report = self.compose(config)
            self._loaded_config = config
            self._last_save_path = str(original_path) if original_path else None
            # Unsaved edits to recover, so never read-only: a Save of an Official bot's
            # becomes Save As, into the user's own folder.
            self._official_open = False
            self._window.bot_canvas.set_read_only(False)
            display_name = self._roots.file_label(config, original_path) if original_path else "Untitled (recovered)"
            self._window.set_bot_name(config.name, display_name)
            self._window.bot_canvas.setup(config, mode=BotMode.EDIT)
            self._window.bot_canvas.set_compose_report(report)
            self.template_context_changed.emit()
            self._window.left_sidebar.set_config(config)
            self._window.right_sidebar.set_config(config)
            self._window.set_save_enabled(True)
            self._window.set_close_enabled(True)
            self._window.set_start_enabled(self.start_enabled())
            # Mark dirty so the user is reminded to save and autosave continues.
            self.has_unsaved_changes = True
            self.clean_snapshot = None

        # Delete autosave files that were not chosen (discarded by the user).
        for c in candidates:
            if c["autosave_path"] not in chosen_paths:
                try:
                    c["autosave_path"].unlink()
                except OSError:
                    pass


def _same_file(a: str | Path, b: str | Path) -> bool:
    """Whether two paths name one file, however each was spelled."""
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return Path(a) == Path(b)


def world_folder(path: str | Path | None) -> str | None:
    """The library folder ``path`` is in, under either root, or None for one outside the library."""
    if path is None:
        return None
    parent = Path(path).resolve().parent
    roots = {d.resolve() for d in bot_roots.library_dirs()}
    return parent.name if parent.parent in roots else None


def _save_as_start(last_save_path: str | Path | None) -> Path:
    """Where Save As opens: the user's own folder, in the open bot's world folder, under its file name.

    The user's folder rather than the open file's, which may be an Official bot's in the
    install directory, where nothing may be written (#168). The folder is not created here:
    opening a dialog writes nothing, and the save makes it if the user keeps the path.
    """
    folder = bot_roots.user_bots_dir()
    world = world_folder(last_save_path)
    if world is not None:
        folder = folder / world
    return folder / Path(last_save_path).name if last_save_path else folder


def _changed_linked_regions(before: BotConfig, after: BotConfig) -> list[tuple[str, str]]:
    """``(region id, label)`` for each linked region whose members or version differ between the two."""

    def snapshot(config: BotConfig, region_id: str) -> tuple | None:
        region = next((r for r in config.regions if r.id == region_id), None)
        if region is None or region.source is None:
            return None
        members = set(region.members)
        states = tuple(s.model_dump() for s in config.states if s.id in members)
        internal = tuple(t.model_dump() for t in config.transitions if t.source in members and t.dest in members)
        return region.source.version, states, internal

    changed: list[tuple[str, str]] = []
    for region in after.regions:
        if region.source is None:
            continue
        if snapshot(before, region.id) != snapshot(after, region.id):
            changed.append((region.id, region.label or region.id))
    return changed
