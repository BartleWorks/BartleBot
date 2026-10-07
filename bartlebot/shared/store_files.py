"""Finding, naming and writing a store's files by the id inside them, in one directory or across a pair.

The file-level half of :mod:`bartlebot.store_roots`, moved here so the shared strategy
library (:mod:`bartlebot.shared.strategy_library`) can use it without importing
anything product-specific. What a product's roots *are* stays with the product;
everything below takes its directories as arguments.

Files are named after the item's *name*, not its id (#99): ``Default Support`` saves
as ``default-support.json``. The id lives only inside the file, so every lookup by id
goes through :func:`index_dir`, which reads each file's declared id. The rules that
come out of that:

- A name that slugs to one already taken in the same directory gets ``-2``, ``-3``, … .
  A name with nothing filename-safe in it falls back to the id.
- Renaming moves the file on the next save. The new file is written before the old
  one is deleted, so a crash between the two leaves both files, never neither.
- Two files in one directory declaring the same id (that crash, or a file copied by
  hand without changing its id) resolve to the newer one, with a warning.
- A file from before this scheme, named ``{id}.json``, keeps loading, and is renamed
  the next time it is saved. One whose JSON has no id is indexed under its stem.
- Shadowing is by id, never by filename: a user copy of a shipped file may be named
  differently from it, having been renamed.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Callable
from enum import Enum
from pathlib import Path

from bartlebot.shared.content_hash import record_save

logger = logging.getLogger(__name__)


class Origin(Enum):
    """Where the file behind an id actually is — what makes Delete honest.

    Deleting only ever removes the user copy, so on a ``SHIPPED_OVERRIDE`` the act is
    *revert*, not delete: the shipped file reappears underneath. A surface that offers
    "Delete" on one of those is promising something the store will not do.
    """

    #: Shipped only. Nothing to delete; editing it will create a user copy.
    SHIPPED = "shipped"
    #: A user copy shadowing a shipped file of the same id. Deleting reverts.
    SHIPPED_OVERRIDE = "shipped_override"
    #: User-only — created here, or a shipped file that no longer ships. Deleting removes it.
    USER = "user"
    #: No file under either root.
    MISSING = "missing"


#: How much of a name a filename carries. The full name is in the file.
_MAX_SLUG = 60

#: Device names Windows refuses as a filename stem, whatever the extension.
_RESERVED_STEMS = frozenset(
    {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
)

#: ``stem-2``, ``stem-3`` — the suffix :func:`save_by_id` adds on a collision.
_NUMBERED = re.compile(r"^(?P<stem>.*?)-(?P<n>\d+)$")

#: ``dir`` → (what its listing looked like, ``[(path, declared id), …]``). Rebuilt whenever
#: a file in it is added, removed or rewritten, which the listing's stats show.
_scan_cache: dict[Path, tuple[frozenset[tuple[str, int, int]], list[tuple[Path, str]]]] = {}


def slugify(name: str) -> str:
    """``Default Team Boss Duel (No Feints)`` → ``default-team-boss-duel-no-feints``.

    Lowercase, so two names differing only in case collide here, as they would on
    a Windows filesystem, and get numbered apart rather than overwrite each other.
    Accents fold to their base letter; anything else not ``[a-z0-9]`` becomes a
    hyphen. May return ``""``, for a name made only of symbols.
    """
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    return slug[:_MAX_SLUG].rstrip("-")


def declared_id(path: Path) -> str:
    """The id ``path`` declares, read without validating the rest of the file.

    Falls back to the stem when the file has no readable id — unparseable, or
    written before the id was always stored — which for a file from before #99
    is the id, since those were named ``{id}.json``.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return path.stem
    item_id = data.get("id") if isinstance(data, dict) else None
    return item_id if isinstance(item_id, str) and item_id else path.stem


def _scan(d: Path) -> list[tuple[Path, str]]:
    """``[(path, declared id), …]`` for every ``*.json`` in ``d``, sorted by path."""
    entries = []
    for p in sorted(d.glob("*.json")):
        try:
            entries.append((p, p.stat()))
        except OSError:  # removed between the listing and the stat
            continue
    signature = frozenset((p.name, st.st_mtime_ns, st.st_size) for p, st in entries)
    cached = _scan_cache.get(d)
    if cached is not None and cached[0] == signature:
        return cached[1]
    scanned = [(p, declared_id(p)) for p, _ in entries]
    _scan_cache[d] = (signature, scanned)
    return scanned


def _mtime(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def index_dir(d: Path) -> dict[str, Path]:
    """``{id: path}`` for ``d``. Of two files declaring one id, the newer wins, with a warning."""
    found: dict[str, Path] = {}
    for p, item_id in _scan(d):
        prior = found.get(item_id)
        if prior is None:
            found[item_id] = p
            continue
        keep, drop = (p, prior) if _mtime(p) >= _mtime(prior) else (prior, p)
        logger.warning("Two files in %s declare id %s; using %s and ignoring %s", d, item_id, keep.name, drop.name)
        found[item_id] = keep
    return found


def find_by_id(d: Path, item_id: str) -> Path | None:
    """The file in ``d`` declaring ``item_id``, or ``None``."""
    return index_dir(d).get(item_id)


def load_by_id(shipped_dir: Path, user_dir: Path, item_id: str, loader: Callable[[Path], object]) -> object:
    """Load ``item_id`` from the user dir, else the shipped one. Raises ``FileNotFoundError`` if neither has it."""
    path = find_by_id(user_dir, item_id) or find_by_id(shipped_dir, item_id)
    if path is None:
        raise FileNotFoundError(f"No file declares id {item_id} in {user_dir} or {shipped_dir}")
    return loader(path)


def load_in(d: Path, item_id: str, loader: Callable[[Path], object]) -> object | None:
    """``item_id``'s file in ``d``, loaded, or ``None`` when ``d`` has none or it does not load."""
    path = find_by_id(d, item_id)
    if path is None:
        return None
    try:
        return loader(path)
    except Exception:
        logger.warning("Could not read %s to record what a save replaces", path, exc_info=True)
        return None


def record_save_in(shipped_dir: Path, user_dir: Path, item, loader: Callable[[Path], object]) -> None:
    """Set ``item``'s history and Official base from what a save into ``user_dir`` replaces.

    What every two-root store calls before it writes: see
    :func:`bartlebot.shared.content_hash.record_save`. The two directories being one
    (developer mode, a single-root sandbox) means nothing is Official, so nothing is
    overridden.
    """
    stored = load_in(user_dir, item.id, loader)
    shipped = None if shipped_dir == user_dir else load_in(shipped_dir, item.id, loader)
    record_save(item, stored=stored, shipped=shipped)


def _filename_for(d: Path, item_id: str, name: str) -> Path:
    """Where ``item_id`` named ``name`` should live in ``d``, given what else is there.

    A file already carrying the right stem, numbered or not, keeps its name, so
    re-saving ``support-2.json`` does not move it to a since-freed ``support.json``.
    """
    scanned = _scan(d)
    own = [p for p, i in scanned if i == item_id]
    taken = {p.name.lower() for p, i in scanned if i != item_id}
    stem = slugify(name) or slugify(item_id) or "untitled"
    for p in own:
        match = _NUMBERED.match(p.stem.lower())
        if p.stem.lower() == stem or (match and match.group("stem") == stem):
            return p
    n = 1
    candidate = stem
    while candidate in _RESERVED_STEMS or f"{candidate}.json" in taken:
        n += 1
        candidate = f"{stem}-{n}"
    return d / f"{candidate}.json"


def save_by_id(d: Path, item_id: str, name: str, write: Callable[[Path], None]) -> Path:
    """Write ``item_id`` into ``d`` under a filename from ``name``, then remove its old file(s).

    ``write`` does the writing, given the path. The new file is written first, so
    a crash leaves a duplicate that :func:`index_dir` resolves, not a lost item.
    Returns the path written.
    """
    target = _filename_for(d, item_id, name)
    stale = [p for p, i in _scan(d) if i == item_id and p.name.lower() != target.name.lower()]
    write(target)
    for p in stale:
        logger.info("Renamed %s to %s", p.name, target.name)
        p.unlink(missing_ok=True)
    return target


def delete_by_id(d: Path, item_id: str) -> None:
    """Remove every file in ``d`` declaring ``item_id`` — duplicates too, or one would reappear."""
    for p, i in _scan(d):
        if i == item_id:
            p.unlink(missing_ok=True)


def merged_by_id(shipped_dir: Path, user_dir: Path, loader: Callable[[Path], object]) -> dict[str, object]:
    """Load every ``*.json`` under both dirs, keyed by ``.id``, user entries winning on collision.

    A file the loader rejects is skipped, not raised: one broken file must not
    take the whole library down with it.
    """
    by_id: dict[str, object] = {}
    for d in (shipped_dir, user_dir):
        for p in index_dir(d).values():
            try:
                item = loader(p)
            except Exception:
                logger.warning("Skipping unloadable %s", p, exc_info=True)
                continue
            by_id[item.id] = item  # type: ignore[attr-defined]
    return by_id


def origin_in(dir_fn: Callable[[Path], Path], uuid: str, shipped: Path, user: Path) -> Origin:
    """Which root(s) hold ``uuid`` under the directory ``dir_fn`` maps each root to.

    Returns :attr:`Origin.USER` for both roots when they are the same directory
    (the single-root sandbox a test passes as ``root`` alone), since nothing is
    shipped in that arrangement and everything written is the user's.
    """
    in_user = find_by_id(dir_fn(user), uuid) is not None
    if shipped == user:
        return Origin.USER if in_user else Origin.MISSING
    in_shipped = find_by_id(dir_fn(shipped), uuid) is not None
    if in_shipped:
        return Origin.SHIPPED_OVERRIDE if in_user else Origin.SHIPPED
    return Origin.USER if in_user else Origin.MISSING
