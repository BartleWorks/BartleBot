"""The ordered pair of storage roots every two-root store reads and writes across.

  shipped — the project root (``bartlebot.paths.project_root``), read-only,
            what the repo ships and version-controls.
  user    — ``%APPDATA%/Bartleby/BartleBot`` (``bartlebot.shared.vendor.product_dir``),
            where every app-initiated write lands.

Reads resolve user-first: a user file with the same id shadows the shipped one.
Writes and deletes only ever touch the user root, so editing a shipped file copies
it into the user root on save, keyed by the same id — an override, not a fork.
Deleting the user copy of a shipped id is "revert to default"; the shipped file
itself is never touched by the app.

Lifted out of :mod:`bartlebot.strategy_store` so the subgraph library
(:mod:`bartlebot.subgraph_store`) shares one :class:`Origin` and one root
resolution rather than a copy of each. Every store function takes an optional
``root``, which sets the shipped root and, unless ``user_root`` is also given,
doubles as the user root — so a single sandbox directory in a test behaves like
one flat store. Pass ``user_root`` explicitly to exercise the shadowing.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import Enum
from pathlib import Path

from bartlebot.paths import project_root
from bartlebot.shared.vendor import product_dir

logger = logging.getLogger(__name__)


class Origin(Enum):
    """Where the file behind an id actually is — what makes Delete honest.

    Deleting only ever removes the user copy (see the module docstring), so on a
    ``SHIPPED_OVERRIDE`` the act is *revert*, not delete: the shipped file
    reappears underneath. A surface that offers "Delete" on one of those is
    promising something the store will not do.
    """

    #: Shipped only. Nothing to delete; editing it will create a user copy.
    SHIPPED = "shipped"
    #: A user copy shadowing a shipped file of the same id. Deleting reverts.
    SHIPPED_OVERRIDE = "shipped_override"
    #: User-only — created here, or a shipped file that no longer ships. Deleting removes it.
    USER = "user"
    #: No file under either root.
    MISSING = "missing"


def resolve_roots(root: str | Path | None, user_root: str | Path | None) -> tuple[Path, Path]:
    """``(shipped, user)`` for the argument convention every store function shares."""
    shipped = Path(root) if root is not None else project_root()
    if user_root is not None:
        user = Path(user_root)
    elif root is not None:
        user = Path(root)
    else:
        user = product_dir()
    return shipped, user


def merged_by_id(shipped_dir: Path, user_dir: Path, loader: Callable[[Path], object]) -> dict[str, object]:
    """Load every ``*.json`` under both dirs, keyed by ``.id``, user entries winning on collision.

    A file the loader rejects is skipped, not raised: one broken file must not
    take the whole library down with it.
    """
    by_id: dict[str, object] = {}
    for d in (shipped_dir, user_dir):
        if not d.exists():
            continue
        for p in sorted(d.glob("*.json")):
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
    in_user = (dir_fn(user) / f"{uuid}.json").exists()
    if shipped == user:
        return Origin.USER if in_user else Origin.MISSING
    in_shipped = (dir_fn(shipped) / f"{uuid}.json").exists()
    if in_shipped:
        return Origin.SHIPPED_OVERRIDE if in_user else Origin.SHIPPED
    return Origin.USER if in_user else Origin.MISSING
