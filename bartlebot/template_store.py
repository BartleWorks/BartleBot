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

Release
    :func:`release` is the only way a version is written: no save produces one, in dev
    mode or not, so a draft cannot reach users by accident. It copies the saved draft's
    bytes as they are to ``v(N+1).json`` and appends the version, its SHA-256 and a change
    note to the index, or writes nothing at all and raises :class:`ReleaseRefused`
    (:func:`release_problems` says why). A released file is never written again, and
    a ``vN.json`` already on disk that the index does not list is never overwritten:
    Release refuses until it is removed.

Setup steps
    A version lists what the user has to fill in (``template_meta.setup``), and a
    fill-in is blank in every release, which Release enforces. :mod:`bartlebot.setup_steps`
    reads them back for a bot through its ``template``.

Template-owned ids
    Every node and subgraph instance a draft mints is ``t_``-prefixed
    (:data:`TEMPLATE_ID_PREFIX`) and is never reused once released -- :func:`used_ids` is
    the set a draft's editor has to stay clear of. A bot mints only ``state_N`` and
    unprefixed instance ids, so a node a later template version adds can never take an
    id the bot already has.

Two roots
    Templates are read from ``templates/`` under both roots
    (:func:`bartlebot.store_roots.resolve_roots`): the Official ones shipped in the
    install directory, and the user's own. :func:`save_draft` and :func:`release` write
    only to the user root. A template is copied into a bot rather than linked, so a user
    template never overrides an Official one: a user-root folder declaring an Official
    template's id is ignored, with a warning, and the Official template is used. Before
    1.0.0 the maintainer is the only author, with ``BARTLEBOT_DEV`` set, where the two
    roots are one directory and every template reads as the user's.

No Qt and no settings here: a test builds a store on a ``tmp_path``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field

from bartlebot import setup_steps
from bartlebot.bot.schema import BotConfig, atomic_write_json
from bartlebot.store_roots import Origin, resolve_roots, slugify
from bartlebot.subgraph_store import SubgraphLibrary
from bartlebot.subgraphs import outermost_linked_regions

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
        # The same line endings on every platform, as a draft is saved: see BotConfig.save.
        atomic_write_json(self.model_dump(), path, newline="\n")


@dataclass(frozen=True)
class Template:
    """A template as the store found it: its folder, its index and what to call it."""

    folder: Path
    index: TemplateIndex
    #: From the latest released version, or the draft before the first release.
    name: str
    #: :attr:`Origin.SHIPPED` for an Official template, :attr:`Origin.USER` for one under
    #: the user root, which is every template when the two roots are one directory.
    origin: Origin = Origin.USER

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


TEMPLATES_DIRNAME = "templates"


def templates_dirs(root: str | Path | None = None, user_root: str | Path | None = None) -> tuple[Path, Path]:
    """The ``(shipped, user)`` ``templates/`` folders; the same folder twice when the roots are one."""
    shipped, user = resolve_roots(root, user_root)
    return shipped / TEMPLATES_DIRNAME, user / TEMPLATES_DIRNAME


def templates_dir(root: str | Path | None = None, user_root: str | Path | None = None) -> Path:
    """The ``templates/`` folder the store writes: the user root's."""
    return templates_dirs(root, user_root)[1]


def _name_in(path: Path) -> str | None:
    """The ``name`` a config file declares, read without validating the rest of it."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    name = data.get("name") if isinstance(data, dict) else None
    return name if isinstance(name, str) else None


def _read(folder: Path, origin: Origin) -> Template | None:
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
    return Template(folder=folder, index=index, name=_name_in(named_by) or folder.name, origin=origin)


def list_templates(root: str | Path | None = None, user_root: str | Path | None = None) -> list[Template]:
    """Every template under both roots, sorted by name. A folder with no readable index is skipped.

    Of two folders declaring one id, the first read wins, the shipped root's before the
    user's: an Official template is never shadowed (see the module docstring).
    """
    shipped_dir, user_dir = templates_dirs(root, user_root)
    if shipped_dir == user_dir:
        bases = [(user_dir, Origin.USER)]
    else:
        bases = [(shipped_dir, Origin.SHIPPED), (user_dir, Origin.USER)]
    found: dict[str, Template] = {}
    for base, origin in bases:
        if not base.is_dir():
            continue
        for folder in sorted(p for p in base.iterdir() if p.is_dir()):
            template = _read(folder, origin)
            if template is None:
                continue
            prior = found.get(template.id)
            if prior is None:
                found[template.id] = template
            elif prior.origin is Origin.SHIPPED and origin is Origin.USER:
                logger.warning(
                    "Ignoring template folder %s: it declares the id of the Official template %s, "
                    "and an Official template is never overridden",
                    folder,
                    prior.name,
                )
            else:
                logger.warning("Two template folders declare id %s; ignoring %s", template.id, folder.name)
    return sorted(found.values(), key=lambda t: t.name.lower())


def find_template(
    template_id: str, root: str | Path | None = None, user_root: str | Path | None = None
) -> Template | None:
    return next((t for t in list_templates(root, user_root) if t.id == template_id), None)


def _require(template_id: str, root: str | Path | None, user_root: str | Path | None) -> Template:
    template = find_template(template_id, root, user_root)
    if template is None:
        raise FileNotFoundError(f"No template declares id {template_id} under {templates_dirs(root, user_root)}")
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

    A template the store does not have yet gets a folder under the user root named from
    ``config.name``, numbered past one already taken, and an index with nothing released.
    Never writes a version: only Release does that. Raises ``PermissionError`` for an
    Official template, which only an app release changes.
    """
    if not config.is_template:
        raise ValueError(f"{config.name!r} has no template_meta, so it is a bot, not a template draft")
    template = find_template(config.id, root, user_root)
    if template is not None and template.origin is Origin.SHIPPED:
        raise PermissionError(f"{template.name} is an Official template, which only an app release changes")
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
    """Every node and subgraph instance id any released version of ``template_id`` has used.

    What a draft's editor must never mint again: an id a version used and a later one
    retired still names that node in every bot based on the earlier version.
    """
    return _used_by(_require(template_id, root, user_root))


def _used_by(template: Template) -> frozenset[str]:
    ids: set[str] = set()
    for entry in template.index.versions:
        ids |= _ids_in(BotConfig.load(template.folder / entry.file))
    return frozenset(ids)


def retired_ids(template: Template) -> frozenset[str]:
    """Ids an earlier version used that the latest one no longer has: never to be used again.

    A bot based on that earlier version still has a node under each, so a draft that
    brought one back would make a later update apply to the wrong node.
    """
    latest = template.index.latest
    if latest is None:
        return frozenset()
    current = _ids_in(BotConfig.load(template.folder / latest.file))
    return _used_by(template) - current


def _ids_in(config: BotConfig) -> frozenset[str]:
    """Its node and subgraph instance ids. A plain region is layout, not something a bot is updated by."""
    return frozenset({s.id for s in config.states} | {r.id for r in config.regions if r.source is not None})


def next_version(template: Template) -> int:
    """The number Release gives ``template``'s next version."""
    return (template.latest_version or 0) + 1


def release_problems(
    draft: BotConfig, template: Template, canvas_errors: Sequence[str] = (), library=None
) -> list[str]:
    """Why ``draft`` cannot be released as ``template``'s next version: empty when it can.

    ``canvas_errors`` are the editor's error lines for the draft, which the caller
    supplies, since working them out needs the scene. ``library`` (a ``SubgraphLibrary``)
    is what a fill-in on a named subgraph input is read through; see
    :func:`bartlebot.setup_steps.draft_problems`.
    """
    problems = list(canvas_errors)
    if template.origin is Origin.SHIPPED:
        problems.append("It is an Official template, which only an app release changes.")
    stray = template.folder / f"v{next_version(template)}.json"
    if stray.exists():
        # A release that failed between its two writes left it. Nothing under a
        # template is ever overwritten, a file the index does not list included.
        problems.append(
            f"{template.folder.name}/{stray.name} is already there, but the index does not list it: "
            "a release that failed halfway left it. Remove the file first."
        )
    if not draft.is_template:
        problems.append("It has no template_meta, so it is a bot, not a template draft.")
    if draft.id != template.id:
        problems.append(f"Its id {draft.id} is not the template's, {template.id}.")
    if draft.template is not None:
        problems.append("It records a template of its own; only a bot made from one does.")
    foreign = sorted(i for i in _ids_in(draft) if not i.startswith(TEMPLATE_ID_PREFIX))
    if foreign:
        problems.append(f"Not template-owned (no {TEMPLATE_ID_PREFIX} prefix): {', '.join(foreign)}.")
    reused = sorted(_ids_in(draft) & retired_ids(template))
    if reused:
        problems.append(f"Reuses ids an earlier version retired: {', '.join(reused)}.")
    problems.extend(setup_steps.draft_problems(draft, library))
    return problems


def replaced_nodes(draft: BotConfig, template: Template) -> list[str]:
    """What ``draft`` replaces rather than keeps, against the latest version: one line per kind of node.

    A node the latest version has, gone from the draft, while the draft adds one of the
    same node type (or a subgraph node of the same subgraph). Not a refusal, since a node
    can be replaced on purpose, but worth asking about. A bot is updated by id, so a
    replaced node reaches it as a removal and an addition: every edge the user wired to
    the old node is dropped, where a node kept and changed would have offered its change.
    Nodes inside a subgraph node are left out; their ids come from the subgraph node's.
    """
    latest = template.index.latest
    if latest is None:
        return []
    before = _loose_by_kind(BotConfig.load(template.folder / latest.file))
    after = _loose_by_kind(draft)
    lines = []
    for kind, old in before.items():
        new = after.get(kind, [])
        old_ids, new_ids = {i for _, i in old}, {i for _, i in new}
        gone = [(label, i) for label, i in old if i not in new_ids]
        fresh = [(label, i) for label, i in new if i not in old_ids]
        if gone and fresh:
            lines.append(f"{_listed(gone)} → {_listed(fresh)}")
    return lines


def _loose_by_kind(config: BotConfig) -> dict[str, list[tuple[str, str]]]:
    """(name, id) of each node not inside a subgraph node, and of each outermost subgraph node, by kind."""
    outermost = outermost_linked_regions(config.regions)
    members = {m for r in outermost for m in r.members}
    kinds: dict[str, list[tuple[str, str]]] = {}
    for s in config.states:
        if s.id not in members:
            kinds.setdefault(s.node_type, []).append((s.label or s.node_type, s.id))
    for r in outermost:
        kinds.setdefault(f"subgraph:{r.source.subgraph_id}", []).append((r.label or r.id, r.id))
    return kinds


def _listed(nodes: Sequence[tuple[str, str]]) -> str:
    return ", ".join(f"'{label}' ({node_id})" for label, node_id in nodes)


class ReleaseRefused(Exception):
    """Release wrote nothing. ``problems`` lists why, one line each."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


def _atomic_write_bytes(data: bytes, path: Path) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix=path.stem + "_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def release(
    template_id: str,
    note: str,
    canvas_errors: Sequence[str] = (),
    *,
    root: str | Path | None = None,
    user_root: str | Path | None = None,
    today: date | None = None,
    library=None,
) -> ReleasedVersion:
    """Release the saved draft of ``template_id`` as its next version, and return the index entry.

    Releases ``draft.json`` as it is on disk, so the caller saves first: an unsaved edit
    is not part of the release. Raises :class:`ReleaseRefused`, having written nothing,
    for an empty note or anything :func:`release_problems` finds. ``library`` is the
    subgraph library the draft's fill-ins are read through: the one under the same
    roots when none is given.
    """
    template = _require(template_id, root, user_root)
    data = template.draft_path.read_bytes()
    draft = BotConfig.model_validate(json.loads(data))
    if library is None:
        library = SubgraphLibrary(root, user_root)
    problems = release_problems(draft, template, canvas_errors, library)
    note = note.strip()
    if not note:
        problems.append("A release needs a note saying what changed.")
    if problems:
        raise ReleaseRefused(problems)
    number = next_version(template)
    entry = ReleasedVersion(
        version=number,
        file=f"v{number}.json",
        sha256=hashlib.sha256(data).hexdigest(),
        note=note,
        released=(today or date.today()).isoformat(),
    )
    _atomic_write_bytes(data, template.folder / entry.file)
    index = template.index.model_copy(deep=True)
    index.versions.append(entry)
    index.save(template.folder / INDEX_FILE)
    logger.info("Released %s v%d: %s", template.name, number, note)
    return entry


def sha256_matches(template: Template, entry: ReleasedVersion) -> bool:
    """Whether a released file still has the bytes it was released with."""
    return hashlib.sha256((template.folder / entry.file).read_bytes()).hexdigest() == entry.sha256
