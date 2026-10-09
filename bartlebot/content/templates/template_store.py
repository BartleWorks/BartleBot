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
    fill-in is blank in every release, which Release enforces. :mod:`bartlebot.content.templates.setup_steps`
    reads them back for a bot through its ``template``.

Template-owned ids
    Every node and subgraph instance a draft mints carries the template's own prefix
    (:func:`own_id_prefix`) and is never reused once released -- :func:`used_ids` is the
    set a draft's editor has to stay clear of. A bot mints only ``state_N`` and
    unprefixed instance ids, so a node a later template version adds can never take an
    id the bot already has. The templates made before forks, the shipped ones among
    them, mint ``t_`` (:data:`TEMPLATE_ID_PREFIX`); one made in the app mints
    ``t<first 6 hex of its id>_`` (:func:`fork_prefix`), and a fork also keeps the ids it
    inherited, under its source's prefixes (:func:`id_prefixes`). So nothing a fork adds
    can take an id its source has or adds later, and a fork of a fork none of its
    parent's (#164).

User templates
    :func:`new_template` starts one blank and :func:`duplicate_template` forks one from
    another's latest release, recording ``forked_from``. :func:`rename_template` and
    :func:`delete_template` act on the user's own only: an Official template changes
    only with an app update, and nothing released with the app is ever removed.

Two roots
    Templates are read from ``templates/`` under both roots
    (:func:`bartlebot.content.store_roots.resolve_roots`): the Official ones shipped in the
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
import shutil
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from bartlebot.bot.schema import BotConfig, TemplateMeta, TemplateRef, atomic_write_json
from bartlebot.content.store_roots import Origin, resolve_roots, slugify
from bartlebot.content.subgraphs.subgraph_store import SubgraphLibrary
from bartlebot.content.subgraphs.subgraphs import outermost_linked_regions
from bartlebot.content.templates import setup_steps

logger = logging.getLogger(__name__)

#: What every id a template made before forks mints starts with -- the shipped ones among
#: them: its nodes (``t_4``) and its subgraph instances (``t_sync_players_1``, whose
#: members follow as ``t_sync_players_1_state_2``). A template made in the app mints
#: under :func:`fork_prefix` instead.
TEMPLATE_ID_PREFIX = "t_"

_HEX_DIGITS = frozenset("0123456789abcdef")


def fork_prefix(template_id: str) -> str:
    """``t<first 6 hex of the id>_``: what a template made in the app mints its ids under (#164).

    Distinct per template, so a fork's ids never collide with its source's, nor a fork
    of a fork with its parent's. Never ``t_``, and never the start of another prefix:
    every one is eight characters.
    """
    digits = "".join(c for c in template_id.lower() if c in _HEX_DIGITS)
    return f"t{digits[:6]:0<6}_"


def id_prefixes(config: BotConfig) -> tuple[str, ...]:
    """Every prefix ``config``'s template-owned ids may carry: its own first, then each inherited one.

    ``config`` is a draft or a released version. ``t_`` alone for a template made before
    forks, which records none.
    """
    meta = config.template_meta
    if meta is None or not meta.id_prefixes:
        return (TEMPLATE_ID_PREFIX,)
    return tuple(meta.id_prefixes)


def own_id_prefix(config: BotConfig) -> str:
    """The prefix ``config``'s draft mints new ids under: the first of :func:`id_prefixes`."""
    return id_prefixes(config)[0]


def is_template_owned(item_id: str, prefixes: Iterable[str]) -> bool:
    """Whether ``item_id`` carries one of ``prefixes``: a node or subgraph node the template owns."""
    return item_id.startswith(tuple(prefixes))


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
    #: What the template is called, when Rename has renamed it since its latest release
    #: (#164): a released file never changes, so the name it carries cannot. Release
    #: clears it, since the version it writes carries the draft's name. ``None`` (left out
    #: of the file) means the latest version's name, or the draft's before the first
    #: release. A name typed into the draft itself is not public until released.
    name: str | None = None

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
        atomic_write_json(self.model_dump(exclude_none=True), path, newline="\n")


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
    name = index.name or _name_in(named_by) or folder.name
    return Template(folder=folder, index=index, name=name, origin=origin)


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


class NameTaken(ValueError):
    """Another template already has the name. The message is for the user."""


def _names_of(template: Template) -> set[str]:
    """Every name ``template`` goes by: the one its card shows, and its draft's when that differs."""
    names = {template.name}
    draft_name = _name_in(template.draft_path)
    if draft_name:
        names.add(draft_name)
    return names


def name_clash(
    name: str, template_id: str | None = None, root: str | Path | None = None, user_root: str | Path | None = None
) -> str | None:
    """The name of another template that ``name`` matches without case, or ``None`` when it is free.

    ``template_id`` is the template being named, whose own names never clash with it.
    Official and Custom templates share one namespace, since New Bot lists both, and a
    draft's name counts as well as the released one: a rename a draft carries reaches
    the card at its next release.
    """
    wanted = name.strip().casefold()
    for template in list_templates(root, user_root):
        if template.id == template_id:
            continue
        for taken in _names_of(template):
            if taken.casefold() == wanted:
                return taken
    return None


def draft_name_refused(config: BotConfig, root: str | Path | None = None, user_root: str | Path | None = None) -> str:
    """Why ``config``, a draft about to be saved, may not have its name: ``""`` when it may.

    Asked only when the name differs from the one the draft on disk has, so two templates
    that already shared a name before this rule still save.
    """
    template = find_template(config.id, root, user_root)
    if template is not None and _name_in(template.draft_path) == config.name:
        return ""
    taken = name_clash(config.name, config.id, root, user_root)
    return f"There is already a template called '{taken}'. Choose another name." if taken is not None else ""


def save_draft(config: BotConfig, root: str | Path | None = None, user_root: str | Path | None = None) -> Path:
    """Write ``config`` as the draft of the template its ``id`` names. Returns the path written.

    A template the store does not have yet gets a folder under the user root named from
    ``config.name``, numbered past one already taken, and an index with nothing released.
    Never writes a version: only Release does that. Raises ``PermissionError`` for an
    Official template, which only an app release changes, and :class:`NameTaken` for a
    new name another template has (:func:`draft_name_refused`).
    """
    if not config.is_template:
        raise ValueError(f"{config.name!r} has no template_meta, so it is a bot, not a template draft")
    template = find_template(config.id, root, user_root)
    if template is not None and template.origin is Origin.SHIPPED:
        raise PermissionError(f"{template.name} is an Official template, which only an app release changes")
    refused = draft_name_refused(config, root, user_root)
    if refused:
        raise NameTaken(refused)
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


def _keep_name(template: Template, name: str) -> None:
    """Record ``name`` in the index while the latest release says otherwise (Rename, #164).

    A released file never changes, so a rename since the last release lives in the
    index until the next one carries it. Writes nothing when the index already agrees.
    """
    latest = template.index.latest
    released_name = _name_in(template.folder / latest.file) if latest is not None else None
    wanted = name if latest is not None and name != released_name else None
    if wanted != template.index.name:
        template.index.model_copy(update={"name": wanted}).save(template.folder / INDEX_FILE)


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
    draft: BotConfig,
    template: Template,
    canvas_errors: Sequence[str] = (),
    library=None,
    *,
    root: str | Path | None = None,
    user_root: str | Path | None = None,
) -> list[str]:
    """Why ``draft`` cannot be released as ``template``'s next version: empty when it can.

    ``canvas_errors`` are the editor's error lines for the draft, which the caller
    supplies, since working them out needs the scene. ``library`` (a ``SubgraphLibrary``)
    is what a fill-in on a named subgraph input is read through; see
    :func:`bartlebot.content.templates.setup_steps.draft_problems`. ``root`` and ``user_root`` are where
    a fork's source is looked up, to check the ids it inherited.
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
    prefixes = id_prefixes(draft)
    foreign = sorted(i for i in _ids_in(draft) if not is_template_owned(i, prefixes))
    if foreign:
        problems.append(f"Not template-owned (no {' or '.join(prefixes)} prefix): {', '.join(foreign)}.")
    problems.extend(_borrowed(draft, prefixes, root, user_root))
    reused = sorted(_ids_in(draft) & retired_ids(template))
    if reused:
        problems.append(f"Reuses ids an earlier version retired: {', '.join(reused)}.")
    problems.extend(setup_steps.draft_problems(draft, library))
    return problems


def _borrowed(
    draft: BotConfig, prefixes: Sequence[str], root: str | Path | None, user_root: str | Path | None
) -> list[str]:
    """A fork's ids under an inherited prefix that the version it was duplicated from lacks.

    Its source mints under that prefix, so any such id could be one the source adds in a
    later release. The editor never mints one; this is what keeps a hand edit out of a
    release. Nothing to check when the source or its version is not in the store.
    """
    meta = draft.template_meta
    inherited = tuple(prefixes[1:])
    if meta is None or meta.forked_from is None or not inherited:
        return []
    under = {i for i in _ids_in(draft) if is_template_owned(i, inherited)}
    if not under:
        return []
    source = find_template(meta.forked_from.id, root, user_root)
    if source is None:
        return []
    try:
        seed = _ids_in(BotConfig.load(source.version_path(meta.forked_from.version)))
    except (LookupError, OSError, ValueError):
        return []
    stray = sorted(under - seed)
    if not stray:
        return []
    return [
        f"Uses ids under {source.name}'s prefix that its v{meta.forked_from.version} does not have: "
        f"{', '.join(stray)}. {source.name} may add those later, so a copy adds nodes under its own prefix only."
    ]


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
    problems = release_problems(draft, template, canvas_errors, library, root=root, user_root=user_root)
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
    # The version now carries the draft's name, which is all a renamed index held.
    index.name = None
    index.save(template.folder / INDEX_FILE)
    logger.info("Released %s v%d: %s", template.name, number, note)
    return entry


def sha256_matches(template: Template, entry: ReleasedVersion) -> bool:
    """Whether a released file still has the bytes it was released with."""
    return hashlib.sha256((template.folder / entry.file).read_bytes()).hexdigest() == entry.sha256


# ---------------------------------------------------------------------------
# User templates: new, duplicate, rename, delete (#164)
# ---------------------------------------------------------------------------


def new_template(name: str, *, root: str | Path | None = None, user_root: str | Path | None = None) -> Template:
    """Start a blank template under the user root, with a draft and nothing released, and return it.

    The draft has no nodes, as a blank bot has none, and mints under its own
    :func:`fork_prefix`.
    """
    template_id = str(uuid4())
    draft = BotConfig(
        id=template_id,
        name=name.strip() or "New template",
        initial="",
        recovery_state="",
        states=[],
        transitions=[],
        template_meta=TemplateMeta(id_prefixes=[fork_prefix(template_id)]),
    )
    save_draft(draft, root, user_root)
    return _require(template_id, root, user_root)


def duplicate_source(template: Template) -> int | None:
    """The released version Duplicate seeds a fork of ``template`` from: its latest, or ``None``.

    ``None`` means the draft, for the user's own template before its first release.
    Raises ``LookupError`` for an Official one with nothing released, which has nothing
    a user may copy: its draft is the maintainer's working copy.
    """
    if template.latest_version is not None:
        return template.latest_version
    if template.origin is Origin.SHIPPED:
        raise LookupError(f"{template.name} has no released version to duplicate")
    return None


def duplicate_template(
    source: Template, name: str, *, root: str | Path | None = None, user_root: str | Path | None = None
) -> Template:
    """Fork ``source`` into a new template under the user root, and return it.

    The fork's draft is ``source``'s latest released version (its draft, for the user's
    own before a first release), with a new id, ``name``, and ``template_meta`` recording
    ``forked_from`` and the fork's :func:`id_prefixes`: its own first, then every one it
    inherits. A copy of an unreleased draft records no ``forked_from``, since it has no
    released base. It has no history and no ``template``, and nothing released.
    """
    version = duplicate_source(source)
    seed = BotConfig.load(source.version_path(version) if version is not None else source.draft_path)
    inherited = id_prefixes(seed)
    template_id = str(uuid4())
    while fork_prefix(template_id) in inherited:
        template_id = str(uuid4())
    fork = seed.model_copy(deep=True)
    fork.id = template_id
    fork.name = name.strip() or source.name
    fork.template = None
    fork.forked_from = None
    fork.history = []
    meta = fork.template_meta.model_copy(deep=True) if fork.template_meta is not None else TemplateMeta()
    meta.id_prefixes = [fork_prefix(template_id), *inherited]
    meta.forked_from = TemplateRef(id=source.id, version=version) if version is not None else None
    fork.template_meta = meta
    save_draft(fork, root, user_root)
    logger.info("Duplicated template %s as %s", source.name, fork.name)
    return _require(template_id, root, user_root)


def rename_template(
    template_id: str, name: str, *, root: str | Path | None = None, user_root: str | Path | None = None
) -> Template:
    """Rename the user's own template now: its draft, and the index until a release carries the name.

    Released files keep the name they were released with. When the two roots are one
    directory, as in developer mode, a template with a release renames its draft alone:
    that folder is what ships, so a name in its index would reach users with no release
    behind it. Its next release carries the name (:func:`renamed_in_draft`).

    Raises ``PermissionError`` for an Official template, :class:`NameTaken` for a name
    another template has, and ``ValueError`` for a blank one.
    """
    name = name.strip()
    if not name:
        raise ValueError("A template needs a name")
    template = _require(template_id, root, user_root)
    if template.origin is Origin.SHIPPED:
        raise PermissionError(f"{template.name} is an Official template, which only an app release changes")
    draft = BotConfig.load(template.draft_path)
    draft.name = name
    save_draft(draft, root, user_root)
    if not rename_waits_for_release(template, root, user_root):
        _keep_name(template, name)
    return _require(template_id, root, user_root)


def rename_waits_for_release(
    template: Template, root: str | Path | None = None, user_root: str | Path | None = None
) -> bool:
    """Whether a rename of ``template`` waits for its next release: a released template, with the roots one.

    That is developer mode, where the folder is the one the app ships, and the index's
    name would reach users before any release carried it.
    """
    shipped_dir, user_dir = templates_dirs(root, user_root)
    return shipped_dir == user_dir and bool(template.index.versions)


def renamed_in_draft(template: Template) -> str | None:
    """The name ``template``'s draft carries when it differs from the one its card shows, else ``None``.

    The card shows the latest release's name, or the index's after a rename; a name the
    draft alone carries is one its next release will.
    """
    if template.latest_version is None:
        return None
    draft_name = _name_in(template.draft_path)
    return draft_name if draft_name and draft_name != template.name else None


def deletion_refused(template: Template, root: str | Path | None = None, user_root: str | Path | None = None) -> str:
    """Why ``template`` may not be deleted, or ``""`` when it may.

    An Official template is never removed: a user's bot may be based on any version
    released with the app. When the two roots are one directory, as in developer mode,
    the same holds for one with a released version, since that folder is what ships.
    """
    if template.origin is Origin.SHIPPED:
        return f"{template.name} is an Official template, which is never removed."
    shipped_dir, user_dir = templates_dirs(root, user_root)
    if shipped_dir == user_dir and template.index.versions:
        return f"{template.name} has been released with the app, and nothing released is ever removed."
    return ""


def delete_template(template_id: str, *, root: str | Path | None = None, user_root: str | Path | None = None) -> None:
    """Delete the user's own template: its folder, draft and every released version.

    The bots made from it keep running, without setup steps or update reviews: they
    carry everything else themselves. Raises ``PermissionError`` when
    :func:`deletion_refused` says why not.
    """
    template = _require(template_id, root, user_root)
    refused = deletion_refused(template, root, user_root)
    if refused:
        raise PermissionError(refused)
    shutil.rmtree(template.folder)
    # Released steps are cached for good, which a deleted version would outlive.
    setup_steps.forget_released()
    logger.info("Deleted template %s", template.name)


def _template_of(path: Path) -> tuple[str, str] | None:
    """``(bot name, template id)`` from a bot file's raw JSON, or None when it names no template."""
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("template"), dict):
        return None
    template_id = data["template"].get("id")
    if not isinstance(template_id, str):
        return None
    name = data.get("name")
    return (name if isinstance(name, str) and name else path.stem), template_id


def _bot_files(bots_dirs: Iterable[str | Path]) -> list[Path]:
    return sorted({p for d in bots_dirs if Path(d).is_dir() for p in Path(d).rglob("*.json")})


def bots_based_on(template_id: str, *bots_dirs: str | Path) -> list[str]:
    """The names of the bot files under ``bots_dirs`` made from ``template_id``, sorted.

    Read from the raw JSON, so a bot that no longer loads still counts. What Delete names.
    """
    found = [hit[0] for p in _bot_files(bots_dirs) if (hit := _template_of(p)) and hit[1] == template_id]
    return sorted(found, key=str.casefold)


def count_bots_based_on(*bots_dirs: str | Path) -> dict[str, int]:
    """``{template id: number of bot files under bots_dirs made from it}``: what the Templates dialog shows."""
    counts: dict[str, int] = {}
    for path in _bot_files(bots_dirs):
        hit = _template_of(path)
        if hit is not None:
            counts[hit[1]] = counts.get(hit[1], 0) + 1
    return counts
