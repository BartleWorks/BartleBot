"""Where bot files are: the Official ones shipped in the install directory, and the user's own.

  shipped — ``bots/`` under the project root (the install directory): what ships, read
            only, and replaced whole by every app update.
  user    — the folder the ``bot_library_path`` setting names: ``bots/`` under the user
            root by default (``%APPDATA%/Bartleby/BartleBot/bots``). A relative path is
            taken against the user root, and an absolute one as it is.

New Bot, Save, Save As and crash recovery write only to the user root (#168). Both
roots keep the same world folders, and the library lists the two as one. A bot is
copied rather than linked, so a user bot never overrides an Official one the way a
subgraph or a strategy does: a user-root file declaring an Official bot's id is left
out, with a warning, and the Official bot is the one listed.

Developer mode (``BARTLEBOT_DEV``) points the user root at the project root, as it does
for every store, so the default user folder *is* the repo's ``bots/`` and the
maintainer edits the shipped bots in place.

No Qt here; the setting is read only when a caller does not pass the folder itself.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from bartlebot.bot.schema import BotConfig
from bartlebot.paths import project_root
from bartlebot.shared import content_hash
from bartlebot.store_roots import declared_id, dev_mode, is_inside, resolve_roots

logger = logging.getLogger(__name__)

#: The folder bots are in under either root, and the setting's default.
BOTS_DIRNAME = "bots"


def shipped_bots_dir() -> Path:
    """``bots/`` in the install directory: the Official bots."""
    return project_root() / BOTS_DIRNAME


def user_bots_dir(configured: str | Path | None = None) -> Path:
    """The folder for the user's own bots: ``configured``, else the ``bot_library_path`` setting.

    A relative path is taken against the user root, never the working directory or the
    install directory: resolving it against the working directory once emptied the
    library for anyone who launched the app from elsewhere.
    """
    if configured is None:
        from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415 - settings are optional here

        configured = SettingsManager.instance().get("canvas", "bot_library_path", default=BOTS_DIRNAME)
    path = Path(configured)
    if path.is_absolute():
        return path
    _shipped, user = resolve_roots(None, None)
    return user / path


def library_dirs(configured: str | Path | None = None) -> list[Path]:
    """``[shipped, user]``, or one folder when they are the same one, as in developer mode."""
    shipped, user = shipped_bots_dir(), user_bots_dir(configured)
    return [shipped] if _same(shipped, user) else [shipped, user]


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return a == b


def is_shipped(path: str | Path, configured: str | Path | None = None) -> bool:
    """Whether ``path`` is an Official bot: under the shipped folder, and not under the user's.

    Never true in developer mode, where every bot is the maintainer's to edit.
    """
    if dev_mode():
        return False
    return is_inside(path, shipped_bots_dir()) and not is_inside(path, user_bots_dir(configured))


def may_write(path: str | Path, configured: str | Path | None = None) -> bool:
    """Whether the app may write a bot to ``path``: anywhere outside the install directory.

    The user's own folder counts as outside it even when a setting puts it inside, and
    developer mode may write anywhere, since the install directory is the repo.
    """
    if dev_mode() or is_inside(path, user_bots_dir(configured)):
        return True
    return not is_inside(path, project_root())


@dataclass(frozen=True)
class LibraryFile:
    """One bot file the library lists."""

    path: Path
    #: The world folder it is in, by its name on disk; ``None`` for a file at the top.
    folder: str | None
    #: Whether it is an Official bot, from the shipped folder.
    shipped: bool


def library_files(shipped_dir: Path, user_dir: Path) -> list[LibraryFile]:
    """Every bot file in both folders, by world folder and then file name.

    A user file declaring the id of a shipped one is left out with a warning; see the
    module docstring. When the two folders are one, every file is listed once, as the
    user's.
    """
    roots = [(user_dir, False)] if _same(shipped_dir, user_dir) else [(shipped_dir, True), (user_dir, False)]
    official: set[str] = set()
    found: list[LibraryFile] = []
    for base, shipped in roots:
        if not base.is_dir():
            continue
        paths = [(None, p) for p in sorted(base.glob("*.json"))]
        for sub in sorted(d for d in base.iterdir() if d.is_dir()):
            paths.extend((sub.name, p) for p in sorted(sub.glob("*.json")))
        for folder, path in paths:
            item_id = declared_id(path)
            if shipped:
                official.add(item_id)
            elif item_id in official:
                logger.warning(
                    "Ignoring %s: it declares the id of an Official bot, and an Official bot is never overridden",
                    path,
                )
                continue
            found.append(LibraryFile(path, folder, shipped))
    found.sort(key=lambda f: (f.folder is None, f.folder or "", f.path.name.lower()))
    return found


def record_save(config: BotConfig, path: str | Path) -> None:
    """Record in ``config``'s history what saving it to ``path`` replaces (#169).

    A bot has one file and never overrides, so what a save replaces is the file at
    ``path``, when that is this bot: the same id. Anything else there (nothing, or
    another bot Save As is about to write over) is not this bot's past, and ``config``
    keeps the history it has. :func:`bartlebot.shared.content_hash.record_save` has the
    rules; ``config`` is changed in place, so the open bot says what was written.
    """
    stored = None
    if Path(path).is_file() and declared_id(Path(path)) == config.id:
        try:
            stored = BotConfig.load(path)
        except Exception:
            logger.warning("Could not read %s to record what saving it replaces", path, exc_info=True)
    content_hash.record_save(config, stored=stored)
