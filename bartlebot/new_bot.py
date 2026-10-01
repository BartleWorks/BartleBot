"""What New Bot does, without the dialog: the starting points, the bot it builds and its file name.

New Bot is how a bot is started (#115). The user picks a starting point -- a blank bot,
or the latest released version of a template -- and names the bot, and :func:`create_bot`
builds it. The controller saves it at once, so a new bot is never an unsaved document
that a plain Save could land on something else, and a bot abandoned half set up is still
in the library.

A bot made from a template records which one, and which version, in ``template``
(:class:`~bartlebot.bot.schema.TemplateRef`). This is one of the two places that field is
written; the other is finishing a template update review. It is what lets a later release
of the template be offered to the bot.

Only released versions are starting points. A template with a draft and nothing released
is not offered: the draft is its maintainer's working copy (see :mod:`bartlebot.template_store`).

No Qt and no settings here: the dialog (``gui/new_bot_dialog.py``) is a form over these
functions, and a test calls them on a ``tmp_path``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from bartlebot import template_store
from bartlebot.bot.schema import BotConfig, StateModel, TemplateRef
from bartlebot.bot_folders import UNBADGED_FOLDERS
from bartlebot.bot_tags import normalize_tags
from bartlebot.store_roots import slugify

logger = logging.getLogger(__name__)

#: The stem a bot's file gets when its name has no letter or digit to make one from.
FALLBACK_STEM = "new-bot"

BOT_FILE_SUFFIX = ".json"

#: What a file name may not contain on Windows, plus the control characters.
_ILLEGAL_IN_FILE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True)
class StartingPoint:
    """A template as New Bot offers it: its latest released version."""

    template_id: str
    version: int
    config: BotConfig

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def blurb(self) -> str:
        meta = self.config.template_meta
        return meta.blurb if meta is not None else ""

    @property
    def summary(self) -> str:
        """The blurb's first paragraph: what a card has room for. The whole blurb is its tooltip."""
        return self.blurb.strip().split("\n\n", 1)[0].strip()

    @property
    def icon_tag(self) -> str | None:
        """The tag whose medallion stands for this template on its card: its first.

        Tags are stored in vocabulary order, Rewards before Activities, so a template
        that exists for one reward (Spellements, Reagents) wears that, and the rest wear
        what they do (Boss, Dungeon). ``None`` for a template with no tags.
        """
        return self.config.tags[0] if self.config.tags else None


@dataclass(frozen=True)
class BotDetails:
    """What the New Bot dialog asks for."""

    name: str
    description: str = ""
    tags: list[str] = field(default_factory=list)


def starting_points(root: str | Path | None = None, user_root: str | Path | None = None) -> list[StartingPoint]:
    """Every template with a released version, by name. One that fails to load is skipped."""
    points: list[StartingPoint] = []
    for template in template_store.list_templates(root, user_root):
        version = template.latest_version
        if version is None:
            continue
        try:
            config = BotConfig.load(template.version_path(version))
        except Exception:
            logger.warning("Not offering template %s: v%d does not load", template.name, version, exc_info=True)
            continue
        points.append(StartingPoint(template_id=template.id, version=version, config=config))
    return points


def create_bot(start: StartingPoint | None, details: BotDetails) -> BotConfig:
    """The bot New Bot makes from ``start``, or a blank one-node bot for ``None``.

    A copy of the version with a fresh id, ``template`` recording where it came from,
    and no ``template_meta`` -- that is what makes it a bot. The setup steps are not
    copied in: the bot reads them from its base version. An empty description stays
    empty, so the generated one shows (``bot_summary.describe``).
    """
    if start is None:
        config = BotConfig(
            id=str(uuid4()),
            name="",
            initial="state_1",
            recovery_state="state_1",
            states=[StateModel(id="state_1", node_type="teleport_to_world_hub")],
            transitions=[],
        )
    else:
        config = start.config.model_copy(deep=True)
        config.id = str(uuid4())
        config.template_meta = None
        config.template = TemplateRef(id=start.template_id, version=start.version)
    config.name = details.name.strip()
    config.description = details.description.strip() or None
    config.tags = normalize_tags(list(details.tags))
    return config


def file_stem(text: str) -> str:
    """The file stem for a display name: ``Lamia's Lair`` -> ``lamia-s-lair``. Never empty."""
    return slugify(text) or FALLBACK_STEM


def clean_stem(text: str) -> str:
    """A stem the user typed, made safe to write: no extension, path or illegal character.

    Their case and spacing are kept, unlike :func:`file_stem`'s -- they chose them.
    Falls back to :data:`FALLBACK_STEM` when nothing usable is left.
    """
    stem = text.strip()
    if stem.lower().endswith(BOT_FILE_SUFFIX):
        stem = stem[: -len(BOT_FILE_SUFFIX)]
    stem = _ILLEGAL_IN_FILE_NAME.sub("", stem).strip(" .")
    return stem or FALLBACK_STEM


def unique_file_name(stem: str, folder: str | Path) -> str:
    """``<stem>.json``, or ``<stem>-2.json``, ``-3`` … past whatever ``folder`` already holds.

    Compared without case, as Windows compares file names. A folder that does not exist
    yet holds nothing.
    """
    folder = Path(folder)
    taken = {p.name.lower() for p in folder.iterdir()} if folder.is_dir() else set()
    name, n = f"{stem}{BOT_FILE_SUFFIX}", 1
    while name.lower() in taken:
        n += 1
        name = f"{stem}-{n}{BOT_FILE_SUFFIX}"
    return name


def library_folders(bots_dir: str | Path) -> list[str]:
    """The bot library's folders a new bot can be saved in, in the library's order.

    By folder name, so the numbers set the order (see :mod:`bartlebot.bot_folders`).
    Empty ones are listed too, unlike in the library panel: a new bot is how one fills.
    """
    base = Path(bots_dir)
    names = {p.name for p in base.iterdir() if p.is_dir()} if base.is_dir() else set()
    return sorted(n for n in names if n not in UNBADGED_FOLDERS and not n.startswith((".", "_")))
