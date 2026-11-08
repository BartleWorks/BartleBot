"""The template store's roots as the controller's flows share them, and two questions about a template file.

``TemplateRoots`` is the template store's two roots, which every store function takes, and
the two questions about a template file that opening and saving a document ask: what
its file is called in the header, and whether it may be opened for editing at all.
``Documents`` and ``Templates`` share one, so a test's ``tmp_path`` reaches both.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QMessageBox, QWidget

from bartlebot.bot.schema import BotConfig
from bartlebot.content import bot_roots
from bartlebot.content.store_roots import Origin, dev_mode
from bartlebot.content.templates import template_store

#: What the header and the window title call a template draft's file.
DRAFT_FILE_LABEL = "template draft"


class TemplateRoots:
    """The template store's two roots: None for the project root and the default user root."""

    def __init__(self, root: Path | None = None, user_root: Path | None = None) -> None:
        #: Where the template store is: the project root, or a test's tmp_path.
        self.root = root
        #: The user root the template store writes, when not the default; a test's tmp_path.
        self.user_root = user_root

    def kwargs(self) -> dict[str, Path | None]:
        """The template store's two roots, as every store function takes them."""
        return {"root": self.root, "user_root": self.user_root}

    @staticmethod
    def user_bots_dirs() -> tuple[Path, ...]:
        """Where the user's own bots are: what the Templates dialog counts, and Delete names."""
        return (bot_roots.user_bots_dir(),)

    @staticmethod
    def file_label(config: BotConfig, path: str | Path | None) -> str:
        """What the header calls the open document's file: a draft's name says nothing, every one is draft.json."""
        if config.is_template:
            return DRAFT_FILE_LABEL
        return Path(path).name if path else "Untitled"

    def may_edit_template(self, config: BotConfig, path: str, parent: QWidget | None) -> bool:
        """Whether a template file may be opened for editing. Says why not, over *parent*.

        Only a draft, never a released version. The user's own template's draft always;
        an Official one's only in dev mode, where its maintainer edits it (#164).
        """
        if Path(path).name != template_store.DRAFT_FILE:
            why = f"This is a released version of the template '{config.name}', which is never edited."
            hint = "To start a bot from a template, use New Bot."
        elif dev_mode():
            return True
        else:
            template = template_store.find_template(config.id, self.root, self.user_root)
            if (
                template is not None
                and template.origin is Origin.USER
                and template.draft_path.resolve() == Path(path).resolve()
            ):
                return True
            why = f"This is the draft of the Official template '{config.name}', which changes only with an app update."
            hint = "To make one of your own from it, use Duplicate in File › Templates…."
        QMessageBox.information(parent, "Open Bot", f"{why}\n\n{hint}")
        return False
