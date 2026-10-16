"""Whether a newer release is out, and getting it ready for Velopack to apply.

The check runs once, after the window is up, and only in an installed build with
Settings › General's "Check for updates at startup" on (``gui/update_prompt.py`` asks
:func:`installed`). Help › Check for Updates… runs it too, whatever the setting. Nothing
here touches Qt, and every call that waits on the network runs in a worker thread.

Being offline is ordinary, so it is logged at INFO and says nothing on screen. A feed
whose signature does not verify is logged as a WARNING and its update is never offered
(:mod:`bartlebot.updates.feed`).

Once the user picks *Install when I close BartleBot* or *Restart now*, :func:`prepare`
stages the verified feed in a local folder, asks Velopack what it needs from there,
downloads just that (the deltas from this version when the feed has them, the full
package otherwise), and has Velopack download it from the folder, checking each package
against the verified feed's SHA-256. :func:`apply_on_exit` then hands it to Velopack's
updater, which waits for this process to exit before it replaces the install.
"""

from __future__ import annotations

import enum
import importlib.metadata
import logging
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bartlebot.packaging.build_kind import is_packaged
from bartlebot.updates import feed as feeds
from bartlebot.updates.feed import BadFeed, BadSignature, FeedError, SignedFeed, Unreachable, newer
from bartlebot.updates.keys import TRUSTED_KEYS

logger = logging.getLogger(__name__)

#: The manifest Velopack writes beside the exe it installed. A build without one, such as
#: CI's or one unpacked by hand, is packaged but not installed, and has nothing to update.
VELOPACK_MANIFEST = "sq.version"


def installed(executable: Path | None = None) -> bool:
    """Whether this process is a build Velopack installed: the only kind that updates itself."""
    if not is_packaged() and executable is None:
        return False
    exe = Path(executable or sys.executable)
    return (exe.parent / VELOPACK_MANIFEST).is_file()


def current_version() -> str:
    """This build's version, the one number ``pyproject.toml`` holds (``docs/RELEASING.md``)."""
    try:
        return importlib.metadata.version("bartlebot")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0"


class Status(enum.Enum):
    UP_TO_DATE = "up to date"
    OFFERED = "offered"
    UNREACHABLE = "unreachable"
    REFUSED = "refused"


@dataclass(frozen=True)
class Offer:
    """A newer release, from a feed that verified."""

    version: str
    current: str
    notes: str
    feed: SignedFeed

    @property
    def release_page(self) -> str:
        """The release on GitHub, where its notes are when the feed carries none."""
        return f"{feeds.REPOSITORY}/releases/tag/v{self.version}"


@dataclass(frozen=True)
class CheckResult:
    status: Status
    offer: Offer | None = None
    #: Why, when the check was unreachable or refused, in words a user can read.
    reason: str = ""


def check(where: str | None = None, current: str | None = None, keys: Iterable[str] = TRUSTED_KEYS) -> CheckResult:
    """Read and verify the feed, and say whether it offers a release newer than ``current``. Blocks."""
    current = current or current_version()
    try:
        signed = feeds.fetch(where, keys)
    except Unreachable as exc:
        logger.info("Could not check for updates: %s", exc)
        return CheckResult(Status.UNREACHABLE, reason=str(exc))
    except (BadSignature, BadFeed) as exc:
        logger.warning("Not offering an update: %s", exc)
        return CheckResult(Status.REFUSED, reason=str(exc))
    latest = signed.latest()
    if latest is None or not newer(latest.version, current):
        logger.info("BartleBot %s is up to date", current)
        return CheckResult(Status.UP_TO_DATE)
    logger.info("BartleBot %s is available; this is %s", latest.version, current)
    return CheckResult(Status.OFFERED, Offer(latest.version, current, latest.notes, signed))


def should_popup(version: str, last_offered: str | None) -> bool:
    """Whether ``version`` opens the popup: once per version, and again only for a newer one."""
    return newer(version, last_offered)


class UpdateError(FeedError):
    """The update could not be downloaded or handed to Velopack."""


@dataclass
class PreparedUpdate:
    """An update Velopack has downloaded and checked, ready to apply."""

    version: str
    manager: Any
    info: Any


def _velopack_manager(folder: Path) -> Any:
    import velopack  # noqa: PLC0415 - only an installed build gets this far

    return velopack.UpdateManager(str(folder))


def prepare(
    offer: Offer,
    folder: Path | None = None,
    progress: Callable[[int, int], None] | None = None,
    manager_for: Callable[[Path], Any] = _velopack_manager,
) -> PreparedUpdate:
    """Stage the offer's verified feed, download what Velopack needs into it, and let Velopack check it. Blocks.

    Raises :class:`UpdateError`, or :class:`~bartlebot.updates.feed.Unreachable` for a
    package that could not be read.
    """
    folder = feeds.stage(offer.feed, folder or feeds.staging_dir())
    manager = manager_for(folder)
    try:
        info = manager.check_for_updates()
    except Exception as exc:
        raise UpdateError(f"Velopack could not read the update: {exc}") from exc
    if info is None:
        raise UpdateError(f"Velopack found no update to {offer.version} in the feed.")
    target = info.TargetFullRelease
    deltas = [d.FileName for d in info.DeltasToTarget]
    for name in deltas or [target.FileName]:
        feeds.download(offer.feed.location, name, folder, progress)
    try:
        manager.download_updates(info)
    except Exception as exc:
        if not deltas:
            raise UpdateError(f"The update did not pass Velopack's check: {exc}") from exc
        # A delta that will not apply falls back to the full package, as Velopack itself
        # would if the full package were in the folder.
        logger.warning("The update's deltas did not apply, so downloading the full package: %s", exc)
        feeds.download(offer.feed.location, target.FileName, folder, progress)
        try:
            manager.download_updates(info)
        except Exception as again:
            raise UpdateError(f"The update did not pass Velopack's check: {again}") from again
    logger.info("Downloaded BartleBot %s", offer.version)
    return PreparedUpdate(version=offer.version, manager=manager, info=info)


def apply_on_exit(prepared: PreparedUpdate, *, restart: bool) -> None:
    """Have Velopack's updater apply ``prepared`` once this process exits, and start the new version if ``restart``.

    Never ``apply_updates_and_restart``: that exits the process itself, past the quit path
    that unhooks every client and gives the game its display settings back.
    """
    prepared.manager.wait_exit_then_apply_updates(prepared.info, silent=False, restart=restart)
    logger.info(
        "BartleBot %s installs when BartleBot closes%s", prepared.version, " and then starts again" if restart else ""
    )
