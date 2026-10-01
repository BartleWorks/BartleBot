"""File-based storage for bot templates: one folder per template, a draft and its released versions.

  templates/                 top level, beside ``subgraphs/`` and ``strategies/``
    boss-farmer/             named from the template's name, as :func:`store_roots.slugify` names files
      template.json          the index: the template's id, and every released version
      draft.json             a ``BotConfig`` with ``template_meta``; the maintainer edits this
      v1.json                a released copy of the draft, never edited again
      v2.json

A template is *fork* semantics: New Bot copies a released version into a new bot and
records which one in the bot's ``template`` (:class:`~bartlebot.bot.schema.TemplateRef`),
so a later release can be offered to it as an update. Users are only ever offered
released versions; the draft is the maintainer's working copy and is never shown to them.

The folder's name is cosmetic. A template is found by the id in its ``template.json``,
which the draft and every version also carry as their own ``id``, so renaming a
template never has to move its folder.

Template-owned ids
    Every node and subgraph instance a draft mints is ``t_``-prefixed
    (:data:`TEMPLATE_ID_PREFIX`) and is never reused once released -- :func:`used_ids` is
    the set a draft's editor has to stay clear of. A bot mints only ``state_N`` and
    unprefixed instance ids, so a node a later template version adds can never take an
    id the bot already has.

Only the shipped root is read and written for now. Before 1.0.0 the maintainer is the
only author, and only with ``BARTLEBOT_DEV`` set, where the two roots are one directory
anyway. User templates would add the user root the way :mod:`bartlebot.subgraph_store`
does, and nothing here rules that out: the root goes through
:func:`bartlebot.store_roots.resolve_roots`, and templates are found by id.

No Qt and no settings here: a test builds a store on a ``tmp_path``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from bartlebot.bot.schema import BotConfig, atomic_write_json
from bartlebot.store_roots import resolve_roots, slugify

logger = logging.getLogger(__name__)

#: What every id a template draft mints starts with: its nodes (``t_4``) and its
#: subgraph instances (``t_sync_players_1``, whose members follow as ``t_sync_players_1_state_2``).
TEMPLATE_ID_PREFIX = "t_"

INDEX_FILE = "template.json"
DRAFT_FILE = "draft.json"


class ReleasedVersion(BaseModel):
    """One entry in a template's index: a ``vN.json`` written by Release."""

    version: int
    file: str
    #: Of the version file's bytes as released; a released file never changes.
    sha256: str
    note: str = ""
    #: ISO date of the release.
    released: str


class TemplateIndex(BaseModel):
    """``template.json``: which template a folder holds, and what has been released from it."""

    id: str
    versions: list[ReleasedVersion] = Field(default_factory=list)

    @property
    def latest(self) -> ReleasedVersion | None:
        """The highest released version, or ``None`` before the first release."""
        return max(self.versions, key=lambda v: v.version, default=None)

    def entry(self, version: int) -> ReleasedVersion | None:
        return next((v for v in self.versions if v.version == version), None)

    @classmethod
    def load(cls, path: str | Path) -> TemplateIndex:
        with open(path, encoding="utf-8") as f:
            return cls.model_validate(json.load(f))

    def save(self, path: str | Path) -> None:
        atomic_write_json(self.model_dump(), path)


@dataclass(frozen=True)
class Template:
    """A template as the store found it: its folder, its index and what to call it."""

    folder: Path
    index: TemplateIndex
    #: From the latest released version, or the draft before the first release.
    name: str

    @property
    def id(self) -> str:
        return self.index.id

    @property
    def latest_version(self) -> int | None:
        latest = self.index.latest
        return latest.version if latest is not None else None

    @property
    def draft_path(self) -> Path:
        return self.folder / DRAFT_FILE

    def version_path(self, version: int) -> Path:
        """The file holding released ``version``. Raises ``LookupError`` if it was never released."""
        entry = self.index.entry(version)
        if entry is None:
            raise LookupError(f"template {self.id} has no released version {version}")
        return self.folder / entry.file


def templates_dir(root: str | Path | None = None, user_root: str | Path | None = None) -> Path:
    """The ``templates/`` folder the store reads: the shipped root's, for now (see the module docstring)."""
    shipped, _user = resolve_roots(root, user_root)
    return shipped / "templates"


def _name_in(path: Path) -> str | None:
    """The ``name`` a config file declares, read without validating the rest of it."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    name = data.get("name") if isinstance(data, dict) else None
    return name if isinstance(name, str) else None


def _read(folder: Path) -> Template | None:
    """The template in ``folder``, or ``None`` if it has no readable index."""
    index_path = folder / INDEX_FILE
    if not index_path.is_file():
        return None
    try:
        index = TemplateIndex.load(index_path)
    except Exception:
        logger.warning("Skipping template folder %s: unreadable %s", folder, INDEX_FILE, exc_info=True)
        return None
    latest = index.latest
    named_by = folder / latest.file if latest is not None else folder / DRAFT_FILE
    return Template(folder=folder, index=index, name=_name_in(named_by) or folder.name)


def list_templates(root: str | Path | None = None, user_root: str | Path | None = None) -> list[Template]:
    """Every template under the store, sorted by name. A folder with no readable index is skipped."""
    base = templates_dir(root, user_root)
    if not base.is_dir():
        return []
    found: dict[str, Template] = {}
    for folder in sorted(p for p in base.iterdir() if p.is_dir()):
        template = _read(folder)
        if template is None:
            continue
        if template.id in found:
            logger.warning("Two template folders declare id %s; ignoring %s", template.id, folder.name)
            continue
        found[template.id] = template
    return sorted(found.values(), key=lambda t: t.name.lower())


def find_template(
    template_id: str, root: str | Path | None = None, user_root: str | Path | None = None
) -> Template | None:
    return next((t for t in list_templates(root, user_root) if t.id == template_id), None)


def _require(template_id: str, root: str | Path | None, user_root: str | Path | None) -> Template:
    template = find_template(template_id, root, user_root)
    if template is None:
        raise FileNotFoundError(f"No template declares id {template_id} under {templates_dir(root, user_root)}")
    return template


def latest_version(template_id: str, root: str | Path | None = None, user_root: str | Path | None = None) -> int | None:
    """The highest released version of ``template_id``, or ``None`` if it has none or does not exist."""
    template = find_template(template_id, root, user_root)
    return template.latest_version if template is not None else None


def load_version(
    template_id: str, version: int, root: str | Path | None = None, user_root: str | Path | None = None
) -> BotConfig:
    """Released ``version`` of ``template_id``. Raises ``FileNotFoundError`` / ``LookupError`` if absent."""
    return BotConfig.load(_require(template_id, root, user_root).version_path(version))


def load_draft(template_id: str, root: str | Path | None = None, user_root: str | Path | None = None) -> BotConfig:
    """The draft of ``template_id``. Raises ``FileNotFoundError`` if the template or its draft is absent."""
    return BotConfig.load(_require(template_id, root, user_root).draft_path)


def save_draft(config: BotConfig, root: str | Path | None = None, user_root: str | Path | None = None) -> Path:
    """Write ``config`` as the draft of the template its ``id`` names. Returns the path written.

    A template the store does not have yet gets a folder named from ``config.name``,
    numbered past one already taken, and an index with nothing released. Never writes a
    version: only Release does that.
    """
    if not config.is_template:
        raise ValueError(f"{config.name!r} has no template_meta, so it is a bot, not a template draft")
    template = find_template(config.id, root, user_root)
    if template is not None:
        folder = template.folder
    else:
        base = templates_dir(root, user_root)
        stem = slugify(config.name) or slugify(config.id) or "template"
        folder, n = base / stem, 1
        while folder.exists():
            n += 1
            folder = base / f"{stem}-{n}"
        folder.mkdir(parents=True)
        TemplateIndex(id=config.id).save(folder / INDEX_FILE)
    path = folder / DRAFT_FILE
    config.save(path)
    return path


def used_ids(template_id: str, root: str | Path | None = None, user_root: str | Path | None = None) -> frozenset[str]:
    """Every state and region id any released version of ``template_id`` has used.

    What a draft's editor must never mint again: an id a version used and a later one
    retired still names that node in every bot based on the earlier version.
    """
    template = _require(template_id, root, user_root)
    ids: set[str] = set()
    for entry in template.index.versions:
        config = BotConfig.load(template.folder / entry.file)
        ids.update(s.id for s in config.states)
        ids.update(r.id for r in config.regions)
    return frozenset(ids)
