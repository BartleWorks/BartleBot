"""File-based storage for subgraph definitions, across the same two roots as combat strategies.

  shipped — ``subgraphs/`` under the project root, read-only, what the repo ships.
  user    — ``subgraphs/`` under ``%APPDATA%/Bartleby/BartleBot``, where every
            app-initiated write lands; a user file shadows a shipped one of the same id.
            With ``BARTLEBOT_DEV`` set it is the project root, so a shipped definition
            is authored in place — see :mod:`bartlebot.content.store_roots`.

Files are named after the definition's name (``sync-players.json``) and found by the
id inside them; :mod:`bartlebot.content.store_roots` has the naming rules. The root argument convention, :class:`Origin` and the
shadowing rule are :mod:`bartlebot.content.store_roots`; this module mirrors
:mod:`bartlebot.combat.strategy_store` function for function so a reader of one knows the
other. :class:`SubgraphLibrary` is the session's cached view over the pair, which is
what the composition functions in :mod:`bartlebot.content.subgraphs.subgraphs` take.

No Qt and no settings here: a test builds a library on a ``tmp_path``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from bartlebot.bot.schema import SubgraphDefinition
from bartlebot.content.store_roots import (
    SOURCE_LABELS,  # noqa: F401  (re-exported: the subgraph surfaces import it from here)
    Origin,
    delete_by_id,
    index_dir,
    load_by_id,
    merged_by_id,
    origin_in,
    resolve_roots,
    save_by_id,
    source_label,  # noqa: F401  (re-exported)
)
from bartlebot.shared.content_hash import official_updated
from bartlebot.shared.store_files import record_save_in

logger = logging.getLogger(__name__)

#: The node browser lists a definition under this key prefix (``subgraph:<id>``) so an
#: insert can be told apart from a node type by the one string the dialog hands back.
SUBGRAPH_KEY_PREFIX = "subgraph:"


def _subgraph_dir(root: Path) -> Path:
    return root / "subgraphs"


def list_subgraphs(root: str | Path | None = None, user_root: str | Path | None = None) -> list[SubgraphDefinition]:
    shipped, user = resolve_roots(root, user_root)
    by_id = merged_by_id(_subgraph_dir(shipped), _subgraph_dir(user), SubgraphDefinition.load)
    return list(by_id.values())


def load_subgraph(
    subgraph_id: str, root: str | Path | None = None, user_root: str | Path | None = None
) -> SubgraphDefinition:
    shipped, user = resolve_roots(root, user_root)
    return load_by_id(_subgraph_dir(shipped), _subgraph_dir(user), subgraph_id, SubgraphDefinition.load)  # type: ignore[return-value]


def save_subgraph(
    defn: SubgraphDefinition,
    root: str | Path | None = None,
    user_root: str | Path | None = None,
    *,
    record: bool = True,
) -> None:
    """Write to the user root only; the shipped file is never touched.

    Records the history and, on an override, the Official base first
    (:func:`~bartlebot.shared.store_files.record_save_in`), into ``defn`` itself.
    ``record=False`` writes the history ``defn`` already has, as an import does (#171).
    """
    shipped, user = resolve_roots(root, user_root)
    if record:
        record_save_in(_subgraph_dir(shipped), _subgraph_dir(user), defn, SubgraphDefinition.load)
    save_by_id(_subgraph_dir(user), defn.id, defn.name, defn.save)


def delete_subgraph(subgraph_id: str, root: str | Path | None = None, user_root: str | Path | None = None) -> None:
    """Remove the user copy. On a shipped id this is *revert*, not delete — see :class:`Origin`."""
    _, user = resolve_roots(root, user_root)
    delete_by_id(_subgraph_dir(user), subgraph_id)


def origin_of(subgraph_id: str, root: str | Path | None = None, user_root: str | Path | None = None) -> Origin:
    shipped, user = resolve_roots(root, user_root)
    return origin_in(_subgraph_dir, subgraph_id, shipped, user)


def _linked_ids(path: Path) -> tuple[str, set[str]] | None:
    """``(bot name, ids of the subgraphs it links)`` from a bot file's raw JSON, or None if unreadable."""
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    ids = {
        r["source"]["subgraph_id"]
        for r in (data.get("regions") or [])
        if isinstance(r, dict) and isinstance(r.get("source"), dict) and r["source"].get("subgraph_id")
    }
    name = data.get("name")
    return (name if isinstance(name, str) and name else path.stem), ids


def _bot_files(bots_dirs) -> list[Path]:
    return sorted({p for d in bots_dirs if Path(d).is_dir() for p in Path(d).rglob("*.json")})


def bots_linking(subgraph_id: str, *bots_dirs: str | Path) -> list[str]:
    """The names of the bot files under ``bots_dirs`` that link ``subgraph_id``, sorted.

    Read as :func:`count_subgraph_uses` reads them. What the signature warning names
    (:mod:`bartlebot.content.subgraphs.subgraph_signature`). A bot file with no name is named by its file.
    """
    names = [found[0] for path in _bot_files(bots_dirs) if (found := _linked_ids(path)) and subgraph_id in found[1]]
    return sorted(names, key=str.casefold)


def count_subgraph_uses(*bots_dirs: str | Path) -> dict[str, int]:
    """``{subgraph id: number of bot files linking it}`` under every one of ``bots_dirs``, recursively.

    Read from the raw JSON rather than through ``BotConfig`` so an unloadable bot
    costs a skipped file, not an exception, and so a bot on an older schema still
    counts. What the node browser's "Used by N bots" reads, once per dialog. The
    library's folders are :func:`bartlebot.content.bot_roots.library_dirs`.
    """
    counts: dict[str, int] = {}
    for path in _bot_files(bots_dirs):
        found = _linked_ids(path)
        if found is None:
            continue
        for sid in found[1]:
            counts[sid] = counts.get(sid, 0) + 1
    return counts


class SubgraphLibrary:
    """One session's view of the two roots, cached between :meth:`reload` calls.

    The app holds one for its lifetime and reloads it on open and before every
    run; pure functions receive it rather than reaching for the roots themselves,
    so a test hands them one built on a ``tmp_path``.

    A file that fails to load is logged and remembered in :attr:`errors` rather
    than raised: one broken definition must not make every bot that links a
    *different* one unopenable. :meth:`get` answers ``None`` for it, and
    :meth:`error_for` says why, which is how composition tells ``invalid`` from
    ``missing``.
    """

    def __init__(self, root: str | Path | None = None, user_root: str | Path | None = None) -> None:
        self._root = root
        self._user_root = user_root
        self._by_id: dict[str, SubgraphDefinition] = {}
        self._shipped: dict[str, SubgraphDefinition] = {}
        self._errors: dict[str, str] = {}
        self.reload()

    @property
    def errors(self) -> dict[str, str]:
        """Definition id → why its file failed to load, as of the last reload.

        The id is the one the file declares, read without validating the rest, or its
        stem when even that cannot be read — see :func:`bartlebot.content.store_roots.declared_id`.
        """
        return dict(self._errors)

    def reload(self) -> None:
        shipped, user = resolve_roots(self._root, self._user_root)
        by_id: dict[str, SubgraphDefinition] = {}
        official: dict[str, SubgraphDefinition] = {}
        errors: dict[str, str] = {}
        for d in (_subgraph_dir(shipped), _subgraph_dir(user)):
            for item_id, p in index_dir(d).items():
                try:
                    defn = SubgraphDefinition.load(p)
                except Exception as exc:
                    logger.warning("Skipping unloadable subgraph definition %s: %s", p, exc)
                    errors[item_id] = str(exc)
                    continue
                by_id[defn.id] = defn
                errors.pop(defn.id, None)
                if shipped != user and d == _subgraph_dir(shipped):
                    official[defn.id] = defn
        self._by_id = by_id
        self._shipped = official
        self._errors = errors

    def get(self, subgraph_id: str) -> SubgraphDefinition | None:
        return self._by_id.get(subgraph_id)

    def error_for(self, subgraph_id: str) -> str | None:
        """Why ``subgraph_id`` is unavailable, when a file for it exists but did not load."""
        return self._errors.get(subgraph_id)

    def ids(self) -> list[str]:
        return sorted(self._by_id)

    def origin(self, subgraph_id: str) -> Origin:
        return origin_of(subgraph_id, self._root, self._user_root)

    def official_updated(self, subgraph_id: str) -> bool:
        """Whether ``subgraph_id`` is the user's override of an Official definition that has changed since.

        As of the last reload: the override records the Official one it was made from
        (``official_sha256``), and the Official one no longer hashes to it. Revert takes
        the new one, and the user's changes go with the override.
        """
        defn = self._by_id.get(subgraph_id)
        shipped = self._shipped.get(subgraph_id)
        return defn is not shipped and official_updated(defn, shipped)

    def nesting(self, subgraph_id: str) -> list[SubgraphDefinition]:
        """The definitions that link ``subgraph_id`` directly, by name.

        What deleting it reaches besides the bots that link it: each of these stops
        composing, so a bot linking one runs its snapshot, as for a missing one.
        """
        found = [
            d
            for d in self._by_id.values()
            if d.id != subgraph_id
            and any(r.source is not None and r.source.subgraph_id == subgraph_id for r in d.regions)
        ]
        return sorted(found, key=lambda d: d.name.casefold())

    @property
    def roots(self) -> tuple[Path, Path]:
        """``(shipped, user)``: the two roots this library reads, the same directory when nothing is Official."""
        return resolve_roots(self._root, self._user_root)

    def official(self, subgraph_id: str) -> SubgraphDefinition | None:
        """The Official definition of ``subgraph_id``, as it ships, whatever override is over it.

        When the two roots are one directory nothing is Official, and this is the one
        definition there is, as the maintainer's developer mode has it.
        """
        shipped, user = self.roots
        return self._by_id.get(subgraph_id) if shipped == user else self._shipped.get(subgraph_id)

    def save(self, defn: SubgraphDefinition, *, record: bool = True) -> None:
        """Write ``defn`` to the user root and refresh the cache to match. See :func:`save_subgraph`."""
        save_subgraph(defn, self._root, self._user_root, record=record)
        self.reload()

    def delete(self, subgraph_id: str) -> None:
        delete_subgraph(subgraph_id, self._root, self._user_root)
        self.reload()
