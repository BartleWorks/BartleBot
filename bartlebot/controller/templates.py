"""File › Templates… and a template draft's own verbs: the controller's template flows.

``Templates`` is the gallery, the user's own templates (new, duplicate, rename, delete),
Release on a draft's crumb, and what the canvas is told of the template behind the open
document. A draft is otherwise a document like any other, opened, saved and closed
through ``Documents``. ``docs/TEMPLATES.md`` has the model.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QDialog, QInputDialog, QMessageBox

from bartlebot.bot.schema import BotConfig
from bartlebot.content.store_roots import dev_mode
from bartlebot.content.templates import setup_steps, template_diff, template_gallery, template_store
from bartlebot.controller.documents import file_snapshot, matches_clean
from bartlebot.controller.template_roots import TemplateRoots
from bartlebot.gui.canvas.bot_canvas import BotMode
from bartlebot.gui.dialogs.templates_dialog import TemplatesDialog, TemplatesVerb

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)


class Templates:
    """File › Templates… and a template draft's verbs: the gallery, the user's own templates, and Release.

    A draft is a document like any other, opened, saved and closed through ``Documents``;
    what is here is what only a template has. It also tells the canvas which template is
    behind the open document (``refresh_template_context``), answering
    ``Documents.template_context_changed``. See ``docs/TEMPLATES.md``.
    """

    def __init__(self, window, documents, roots: TemplateRoots) -> None:
        self._window = window
        self._documents = documents
        self._roots = roots

    def _template_gallery(self) -> list[template_gallery.GalleryEntry]:
        return template_gallery.gallery(self._roots.user_bots_dirs(), dev=dev_mode(), **self._roots.kwargs())

    def open_templates_dialog(self) -> bool:
        """File › Templates…: every template, and the verbs that make templates of your own (#164).

        Edit, New and Duplicate end the dialog and open a draft for editing; Rename and
        Delete happen while it stays open. True when a draft was opened.
        """
        if not self._documents.close_definitions():
            return False
        dialog = TemplatesDialog(
            self._template_gallery, self._window, rename=self._rename_template, delete=self._delete_template
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.request is None:
            return False
        request = dialog.request
        if request.verb is TemplatesVerb.NEW:
            return self._new_template()
        if request.verb is TemplatesVerb.DUPLICATE:
            return self._duplicate_template(request.entry.template)
        return self._edit_template(request.entry.template)

    def _edit_template(self, template: template_store.Template) -> bool:
        """Open ``template``'s draft as the top-level document, in Edit mode. False when it did not open."""
        open_now = self._open_template()
        if open_now is None or open_now.id != template.id:
            self._documents.open_bot(str(template.draft_path), mode=BotMode.EDIT)
        return self._documents.loaded_config is not None and self._documents.loaded_config.id == template.id

    def _ask_template_name(self, title: str, label: str, text: str = "", template_id: str | None = None) -> str | None:
        """Ask for a template's name until it is one no other template has. None when cancelled or blank.

        A taken name asks again, with what was typed and a line saying whose name it is.
        ``template_id`` is the template being renamed, whose own name is never taken.
        """
        prompt = label
        while True:
            name, ok = QInputDialog.getText(self._window, title, prompt, text=text)
            name = name.strip()
            if not ok or not name:
                return None
            taken = template_store.name_clash(name, template_id, **self._roots.kwargs())
            if taken is None:
                return name
            prompt = f"There is already a template called '{taken}'.\n\n{label}"
            text = name

    def _new_template(self) -> bool:
        """New: ask for a name, start a blank template of the user's own, and open its draft."""
        name = self._ask_template_name("New Template", "What is the template called?")
        if name is None:
            return False
        try:
            template = template_store.new_template(name, **self._roots.kwargs())
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self._window, "New Template", f"Could not create the template:\n\n{exc}")
            return False
        return self._edit_template(template)

    def _duplicate_template(self, source: template_store.Template) -> bool:
        """Duplicate: fork ``source`` into a template of the user's own, and open its draft."""
        taken = [t.name for t in template_store.list_templates(**self._roots.kwargs())]
        name = self._ask_template_name(
            "Duplicate Template",
            f"What is your copy of {source.name} called?",
            text=template_gallery.copy_name(source.name, taken),
        )
        if name is None:
            return False
        try:
            fork = template_store.duplicate_template(source, name, **self._roots.kwargs())
        except (OSError, LookupError, ValueError) as exc:
            QMessageBox.critical(self._window, "Duplicate Template", f"Could not copy {source.name}:\n\n{exc}")
            return False
        return self._edit_template(fork)

    def _rename_template(self, entry: template_gallery.GalleryEntry) -> bool:
        """Rename the user's own template. True when it was renamed.

        An open draft of it takes the name too, so its next save keeps it rather than
        writing the old one back.
        """
        template = entry.template
        current = template_store.renamed_in_draft(template) or template.name
        name = self._ask_template_name("Rename Template", "Template name:", text=current, template_id=template.id)
        if name is None or name == current:
            return False
        try:
            template_store.rename_template(template.id, name, **self._roots.kwargs())
        except (OSError, PermissionError, ValueError) as exc:
            QMessageBox.warning(self._window, "Rename Template", f"Could not rename {template.name}:\n\n{exc}")
            return False
        open_draft = self._open_template()
        if open_draft is not None and open_draft.id == template.id:
            config = self._documents.loaded_config
            config.name = name
            if not self._documents.has_unsaved_changes:
                self._documents.clean_snapshot = file_snapshot(config)
            self._window.set_bot_name(config.name, self._roots.file_label(config, self._documents.last_save_path))
            self._window.bot_canvas.refresh_name()
        if template_store.rename_waits_for_release(template, **self._roots.kwargs()):
            QMessageBox.information(
                self._window,
                "Rename Template",
                f"The draft is now called {name}. {template.name} has been released with the app, so its "
                "card keeps that name until you release the draft.",
            )
        return True

    def _delete_template(self, entry: template_gallery.GalleryEntry) -> bool:
        """Delete the user's own template, after naming the bots made from it. True when it was deleted.

        The bots keep running, without setup steps or update reviews. An open draft of it
        is closed first, unsaved changes and all: they belong to what is being deleted.
        """
        template = entry.template
        refused = template_store.deletion_refused(template, **self._roots.kwargs())
        if refused:
            QMessageBox.information(self._window, "Delete Template", refused)
            return False
        bots = template_store.bots_based_on(template.id, *self._roots.user_bots_dirs())
        if bots:
            listed = "\n".join(f"• {b}" for b in bots[:12])
            more = f"\n…and {len(bots) - 12} more" if len(bots) > 12 else ""
            made = (
                f"These bots were made from it:\n\n{listed}{more}\n\n"
                "They keep running, but without its setup steps or update reviews."
            )
        else:
            made = "None of your bots were made from it."
        reply = QMessageBox.warning(
            self._window,
            "Delete Template",
            f"Delete {template.name}, its draft and every version released from it?\n\n{made}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return False
        open_draft = self._open_template()
        if open_draft is not None and open_draft.id == template.id:
            self._documents.has_unsaved_changes = False
            self._documents.close_bot()
            if self._documents.loaded_config is not None:
                return False
        try:
            template_store.delete_template(template.id, **self._roots.kwargs())
        except (OSError, PermissionError) as exc:
            QMessageBox.critical(self._window, "Delete Template", f"Could not delete {template.name}:\n\n{exc}")
            return False
        # A bot open now may have been made from it: its steps and review go with it.
        self.refresh_template_context()
        return True

    def _open_template(self) -> template_store.Template | None:
        """The template whose draft is the open document, or None."""
        config = self._documents.loaded_config
        if config is None or not config.is_template or self._documents.last_save_path is None:
            return None
        template = template_store.find_template(config.id, self._roots.root, self._roots.user_root)
        if template is None or Path(self._documents.last_save_path).resolve() != template.draft_path.resolve():
            return None
        return template

    def refresh_template_context(self) -> None:
        """Tell the canvas what a template is behind the open document.

        For a draft, what its crumb says and which ids it must not mint. For a bot made
        from a template, the setup steps of its base version, which it does not carry,
        and the update waiting for it when the template has released since.
        """
        config = self._documents.loaded_config
        if config is None:
            return
        if not config.is_template:
            if not config.is_definition:
                canvas = self._window.bot_canvas
                canvas.set_base_setup_steps(setup_steps.steps_for(config, self._roots.root, self._roots.user_root))
                origin = (
                    template_store.find_template(config.template.id, self._roots.root, self._roots.user_root)
                    if config.template is not None
                    else None
                )
                canvas.set_template_origin(
                    origin.name if origin is not None else None,
                    config.template.version if origin else None,
                    origin.latest_version if origin else None,
                )
                # An Official bot gets its updates from the app, never through the review
                # (#149). Developer mode never marks one Official, so its maintainer still can.
                update = (
                    None
                    if self._documents.official_open
                    else template_diff.load_update(config, self._roots.root, self._roots.user_root)
                )
                canvas.set_template_update(update)
            return
        template = self._open_template()
        if template is None:
            self._window.bot_canvas.set_template_context(None)
            return
        reserved = template_store.used_ids(template.id, self._roots.root, self._roots.user_root)
        self._window.bot_canvas.set_template_context(template.latest_version, reserved)

    def release_template(self) -> bool:
        """Release the open draft as its template's next version. False when refused or cancelled.

        Release takes the draft as it is on disk, so unsaved changes are saved first or
        the release does not happen. The template store refuses anything else wrong,
        having written nothing.
        """
        config = self._documents.loaded_config
        template = self._open_template()
        if config is None or template is None:
            QMessageBox.warning(
                self._window, "Release", "Only a template's draft, opened from File › Templates…, can be released."
            )
            return False
        if self._documents.has_unsaved_changes and not matches_clean(config, self._documents.clean_snapshot):
            reply = QMessageBox.question(
                self._window,
                "Release",
                "Release takes the draft as saved. Save your changes first?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if reply != QMessageBox.StandardButton.Save or not self._documents.save_bot():
                return False
        errors = self._window.bot_canvas.validation_errors()
        draft = BotConfig.load(template.draft_path)
        problems = template_store.release_problems(
            draft,
            template,
            errors,
            self._documents.subgraph_library,
            root=self._roots.root,
            user_root=self._roots.user_root,
        )
        if problems:
            self._show_release_refused(template.name, problems)
            return False
        replaced = template_store.replaced_nodes(draft, template)
        if replaced and not self._confirm_replaced(template.name, template.latest_version, replaced):
            return False
        number = template_store.next_version(template)
        note, ok = QInputDialog.getText(
            self._window, "Release", f"Releasing {template.name} v{number}. What changed, in one line?"
        )
        if not ok:
            return False
        try:
            entry = template_store.release(
                template.id,
                note,
                errors,
                root=self._roots.root,
                user_root=self._roots.user_root,
                library=self._documents.subgraph_library,
            )
        except template_store.ReleaseRefused as exc:
            self._show_release_refused(template.name, exc.problems)
            return False
        except OSError as exc:
            QMessageBox.critical(self._window, "Release", f"Could not release {template.name}:\n\n{exc}")
            return False
        self.refresh_template_context()
        QMessageBox.information(
            self._window, "Release", f"Released {template.name} v{entry.version}. New Bot offers it from now on."
        )
        return True

    def _confirm_replaced(self, name: str, latest: int | None, replaced: Sequence[str]) -> bool:
        """Ask before releasing a draft that replaces nodes rather than keeping them. True to go on."""
        listed = "\n".join(f"• {line}" for line in replaced)
        reply = QMessageBox.warning(
            self._window,
            "Release",
            f"This draft of {name} replaces nodes v{latest} has instead of keeping them:\n\n{listed}\n\n"
            "Bots are updated node by node, by id, so each of these reaches them as a removal and an "
            "addition, and any edge a user wired to the old node is dropped. Keeping the node and "
            "changing it would offer them the change instead.\n\nRelease anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _show_release_refused(self, name: str, problems: Sequence[str]) -> None:
        listed = "\n".join(f"\u2022 {p}" for p in problems[:12])
        more = f"\n\u2026and {len(problems) - 12} more" if len(problems) > 12 else ""
        # The page on screen draws what it is missing in red: a refused release, like a
        # refused save, is the moment to say so rather than before (#142).
        self._window.bot_canvas.param_panel.show_required_errors()
        QMessageBox.critical(self._window, "Release", f"{name} can't be released yet:\n\n{listed}{more}")
