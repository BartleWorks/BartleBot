"""File-based storage for subgraph definitions, across the same two roots as combat strategies.

  shipped — ``subgraphs/`` under the project root, read-only, what the repo ships.
  user    — ``subgraphs/`` under ``%APPDATA%/Bartleby/BartleBot``, where every
            app-initiated write lands; a user file shadows a shipped one of the same id.
            With ``BARTLEBOT_DEV`` set it is the project root, so a shipped definition
            is authored in place — see :mod:`bartlebot.store_roots`.

Files are named ``{id}.json``. The root argument convention, :class:`Origin` and the
shadowing rule are :mod:`bartlebot.store_roots`; this module mirrors
:mod:`bartlebot.strategy_store` function for function so a reader of one knows the
other. :class:`SubgraphLibrary` is the session's cached view over the pair, which is
what the composition functions in :mod:`bartlebot.subgraphs` take.

No Qt and no settings here: a test builds a library on a ``tmp_path``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from bartlebot.bot.schema import SubgraphDefinition
from bartlebot.store_roots import Origin, merged_by_id, origin_in, resolve_roots

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
    user_path = _subgraph_dir(user) / f"{subgraph_id}.json"
    if user_path.exists():
        return SubgraphDefinition.load(user_path)
    return SubgraphDefinition.load(_subgraph_dir(shipped) / f"{subgraph_id}.json")


def save_subgraph(
    defn: SubgraphDefinition, root: str | Path | None = None, user_root: str | Path | None = None
) -> None:
    """Write to the user root only; the shipped file is never touched."""
    _, user = resolve_roots(root, user_root)
    defn.save(_subgraph_dir(user) / f"{defn.id}.json")


def delete_subgraph(subgraph_id: str, root: str | Path | None = None, user_root: str | Path | None = None) -> None:
    """Remove the user copy. On a shipped id this is *revert*, not delete — see :class:`Origin`."""
    _, user = resolve_roots(root, user_root)
    (_subgraph_dir(user) / f"{subgraph_id}.json").unlink(missing_ok=True)


def origin_of(subgraph_id: str, root: str | Path | None = None, user_root: str | Path | None = None) -> Origin:
    shipped, user = resolve_roots(root, user_root)
    return origin_in(_subgraph_dir, subgraph_id, shipped, user)


def count_subgraph_uses(bots_dir: str | Path) -> dict[str, int]:
    """``{subgraph id: number of bot files linking it}`` under ``bots_dir``, recursively.

    Read from the raw JSON rather than through ``BotConfig`` so an unloadable bot
    costs a skipped file, not an exception, and so a bot on an older schema still
    counts. What the node browser's "Used by N bots" reads, once per dialog.
    """
    counts: dict[str, int] = {}
    root = Path(bots_dir)
    if not root.is_dir():
        return counts
    for path in sorted(root.rglob("*.json")):
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        ids = {
            r["source"]["subgraph_id"]
            for r in (data.get("regions") or [])
            if isinstance(r, dict) and isinstance(r.get("source"), dict) and r["source"].get("subgraph_id")
        }
        for sid in ids:
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
        self._errors: dict[str, str] = {}
        self.reload()

    @property
    def errors(self) -> dict[str, str]:
        """Definition id (file stem) → why its file failed to load, as of the last reload."""
        return dict(self._errors)

    def reload(self) -> None:
        shipped, user = resolve_roots(self._root, self._user_root)
        by_id: dict[str, SubgraphDefinition] = {}
        errors: dict[str, str] = {}
        for d in (_subgraph_dir(shipped), _subgraph_dir(user)):
            if not d.exists():
                continue
            for p in sorted(d.glob("*.json")):
                try:
                    defn = SubgraphDefinition.load(p)
                except Exception as exc:
                    logger.warning("Skipping unloadable subgraph definition %s: %s", p, exc)
                    errors[p.stem] = str(exc)
                    continue
                by_id[defn.id] = defn
                errors.pop(defn.id, None)
        self._by_id = by_id
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

    def save(self, defn: SubgraphDefinition) -> None:
        """Write ``defn`` to the user root and refresh the cache to match."""
        save_subgraph(defn, self._root, self._user_root)
        self.reload()

    def delete(self, subgraph_id: str) -> None:
        delete_subgraph(subgraph_id, self._root, self._user_root)
        self.reload()
