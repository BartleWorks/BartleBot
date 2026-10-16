"""The update feed: where it is, whether its signature verifies, and what it offers.

Velopack's feed, ``releases.win.json``, lists every package of a release with its SHA-256,
and Velopack refuses a package whose hash differs from the feed's. That protects the
packages, not the feed: anyone who could swap a package could swap the feed beside it.
So CI signs the feed, with an Ed25519 key only a tag build can read, into
``releases.win.json.sig`` (``tools/sign_feed.py``), and the app accepts a feed only when
that signature verifies against a key in :data:`~bartlebot.updates.keys.TRUSTED_KEYS`.

Velopack never reads a feed itself. The verified bytes are written into a local folder,
the packages it needs are downloaded beside them, and that folder is Velopack's source
(:mod:`bartlebot.updates.check`). So the feed Velopack checks each package against is the
one that verified, and never a second copy fetched after it.

``docs/INSTALLER.md`` ("The update check") has the rest, and how a key is replaced.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
import shutil
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from bartlebot.shared.vendor import PRODUCT, local_vendor_dir
from bartlebot.updates.keys import TRUSTED_KEYS

logger = logging.getLogger(__name__)

#: Velopack's feed for the default channel on Windows.
FEED_NAME = "releases.win.json"
#: The feed's signature: the base64 of 64 Ed25519 signature bytes over the feed's exact bytes.
SIGNATURE_NAME = f"{FEED_NAME}.sig"

#: The repository a release is published from.
REPOSITORY = f"https://github.com/The-World-Tree/{PRODUCT}"
#: Where an installed app looks: the assets of the latest published release. GitHub
#: redirects each name to that release's asset, and a draft is never "latest".
FEED_URL = f"{REPOSITORY}/releases/latest/download/"
#: Names another place to read the feed and its packages from, a URL or a local folder,
#: for trying an update before anything is published (``docs/INSTALLER.md``). It changes
#: where, never what is trusted: the feed must still verify against :data:`TRUSTED_KEYS`.
FEED_ENV_VAR = "BARTLEBOT_UPDATE_FEED"

#: How long one request may take, connecting and reading, before the check gives up.
REQUEST_TIMEOUT_SECONDS = 20
#: How much of a package is read at a time, and so how often a download reports progress.
CHUNK_BYTES = 256 * 1024

#: A version as Velopack writes it: SemVer 2, with the build metadata it ignores.
_SEMVER = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?")


class FeedError(Exception):
    """The feed could not be used. The message says why, in words a user can read."""


class Unreachable(FeedError):
    """The feed, its signature or a package could not be read: offline, or not there."""


class BadSignature(FeedError):
    """The feed's signature does not verify against any trusted key, or is missing."""


class BadFeed(FeedError):
    """The feed verified but could not be read as one."""


@dataclass(frozen=True)
class Asset:
    """One package in the feed: a full one, or a delta from the release before it."""

    version: str
    kind: str
    file_name: str
    sha256: str
    size: int
    notes: str = ""

    @property
    def full(self) -> bool:
        return self.kind == "Full"


@dataclass(frozen=True)
class SignedFeed:
    """A feed whose signature verified: its exact bytes, where it came from, and its packages."""

    data: bytes
    location: str
    assets: tuple[Asset, ...]

    def latest(self) -> Asset | None:
        """The newest full package, the release the feed offers."""
        full = [a for a in self.assets if a.full]
        return max(full, key=lambda a: version_key(a.version)) if full else None


def version_key(version: str) -> tuple:
    """A sort key for a SemVer 2 version: a release sorts after its pre-releases (semver.org, 11).

    A version that is not SemVer sorts before every one that is, so it is never offered.
    """
    match = _SEMVER.fullmatch(version.strip())
    if match is None:
        return (-1,)
    major, minor, patch, pre = match.groups()
    if pre is None:
        return (int(major), int(minor), int(patch), 1, ())
    parts = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split("."))
    return (int(major), int(minor), int(patch), 0, parts)


def newer(version: str, than: str | None) -> bool:
    """Whether ``version`` is a later release than ``than``; any version is later than none."""
    if than is None:
        return version_key(version) > (-1,)
    return version_key(version) > version_key(than)


def location() -> str:
    """Where the feed is read from: :data:`FEED_URL`, unless :data:`FEED_ENV_VAR` names another."""
    return os.environ.get(FEED_ENV_VAR) or FEED_URL


def staging_dir() -> Path:
    """``%LocalAppData%\\Bartleby\\BartleBot\\updates``: the verified feed and its packages, for Velopack.

    Local rather than roaming, since a package is tens of megabytes, and never inside the
    install directory, which the update replaces.
    """
    return local_vendor_dir() / PRODUCT / "updates"


def clear_staging(folder: Path | None = None) -> None:
    """Remove the staged feed and packages: spent once Velopack has applied them, or the app uninstalled."""
    shutil.rmtree(folder or staging_dir(), ignore_errors=True)


def _is_url(where: str) -> bool:
    return where.startswith(("https://", "http://"))


def _join(where: str, name: str) -> str:
    return where + name if where.endswith("/") else f"{where}/{name}"


def _open(where: str, name: str):
    """A readable stream of ``name`` at ``where``, a URL or a folder. Raises :class:`Unreachable`."""
    if _is_url(where):
        request = urllib.request.Request(_join(where, name), headers={"User-Agent": f"{PRODUCT}-updater"})
        try:
            return urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS)
        except urllib.error.HTTPError as exc:
            raise Unreachable(f"{name} is not there ({exc.code}).") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise Unreachable(f"{name} could not be read: {getattr(exc, 'reason', exc)}.") from exc
    try:
        return open(Path(where) / name, "rb")
    except OSError as exc:
        raise Unreachable(f"{name} could not be read: {exc.strerror or exc}.") from exc


def read(where: str, name: str) -> bytes:
    """All of ``name`` at ``where``."""
    with _open(where, name) as stream:
        try:
            return stream.read()
        except (TimeoutError, OSError) as exc:
            raise Unreachable(f"{name} could not be read: {exc}.") from exc


def verify(data: bytes, signature: bytes, keys: Iterable[str] = TRUSTED_KEYS) -> bool:
    """Whether ``signature`` (the ``.sig`` file's contents) is a signature of ``data`` by one of ``keys``."""
    try:
        raw = base64.b64decode(signature.strip(), validate=True)
    except (binascii.Error, ValueError):
        return False
    if len(raw) != 64:
        return False
    for key in keys:
        public = Ed25519PublicKey.from_public_bytes(base64.b64decode(key))
        try:
            public.verify(raw, data)
        except InvalidSignature:
            continue
        return True
    return False


def parse(data: bytes) -> tuple[Asset, ...]:
    """The packages a feed lists, for this product. Raises :class:`BadFeed`."""
    try:
        listed = json.loads(data.decode("utf-8"))["Assets"]
        return tuple(
            Asset(
                version=str(a["Version"]),
                kind=str(a["Type"]),
                file_name=str(a["FileName"]),
                sha256=str(a.get("SHA256", "")),
                size=int(a.get("Size", 0)),
                notes=str(a.get("NotesMarkdown") or ""),
            )
            for a in listed
            if a.get("PackageId") == PRODUCT
        )
    except (UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
        raise BadFeed(f"The feed could not be read: {exc}.") from exc


def fetch(where: str | None = None, keys: Iterable[str] = TRUSTED_KEYS) -> SignedFeed:
    """Read the feed and its signature from ``where``, verify it, and parse it.

    Raises :class:`Unreachable` when either cannot be read, :class:`BadSignature` when
    the signature does not verify, and :class:`BadFeed` when a verified feed is not one.
    """
    where = where or location()
    data = read(where, FEED_NAME)
    try:
        signature = read(where, SIGNATURE_NAME)
    except Unreachable as exc:
        raise BadSignature(f"The feed has no signature: {exc}") from exc
    if not verify(data, signature, keys):
        raise BadSignature("The feed's signature does not match any key this version trusts.")
    return SignedFeed(data=data, location=where, assets=parse(data))


def download(where: str, name: str, folder: Path, progress: Callable[[int, int], None] | None = None) -> Path:
    """Copy ``name`` from ``where`` into ``folder``, reporting ``(bytes so far, total)``. The file written.

    Written beside its final name first and renamed once whole, so a download that stops
    part way never leaves a package Velopack could mistake for a complete one. Velopack
    checks the hash; this only moves bytes.
    """
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    partial = folder / f"{name}.part"
    with _open(where, name) as stream:
        total = int(getattr(stream, "headers", {}).get("Content-Length", 0) or 0) if _is_url(where) else 0
        if not total and not _is_url(where):
            total = (Path(where) / name).stat().st_size
        done = 0
        try:
            with open(partial, "wb") as out:
                while chunk := stream.read(CHUNK_BYTES):
                    out.write(chunk)
                    done += len(chunk)
                    if progress is not None:
                        progress(done, total)
        except (TimeoutError, OSError) as exc:
            partial.unlink(missing_ok=True)
            raise Unreachable(f"{name} stopped downloading: {exc}.") from exc
    os.replace(partial, target)
    return target


def stage(feed: SignedFeed, folder: Path) -> Path:
    """Empty ``folder`` and write the verified feed into it, for Velopack to read. The folder."""
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    (folder / FEED_NAME).write_bytes(feed.data)
    return folder
