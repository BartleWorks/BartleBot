"""Tests for bartlebot/updates/check.py: what a check finds, and getting an update ready for Velopack."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bartlebot.updates import check as checks
from bartlebot.updates.check import Status, UpdateError, apply_on_exit, installed, prepare, should_popup
from bartlebot.updates.feed import FEED_NAME, fetch
from tests.updates.test_feed import FEED, publish
from tools.sign_feed import public_text


@pytest.fixture
def key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
def release(tmp_path, key):
    """1.2.0's release folder: its signed feed, its full package and its delta from 1.1.0."""
    folder = publish(tmp_path / "release", FEED, key)
    (folder / "BartleBot-1.2.0-full.nupkg").write_bytes(b"full")
    (folder / "BartleBot-1.2.0-delta.nupkg").write_bytes(b"delta")
    return folder


@pytest.fixture
def keys(key):
    return (public_text(key),)


# -- what a check finds --------------------------------------------------------------


def test_a_newer_release_is_offered(release, keys, caplog):
    with caplog.at_level(logging.INFO):
        result = checks.check(str(release), current="1.1.0", keys=keys)

    assert result.status is Status.OFFERED
    assert result.offer.version == "1.2.0"
    assert result.offer.current == "1.1.0"
    assert result.offer.release_page == "https://github.com/The-World-Tree/BartleBot/releases/tag/v1.2.0"
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.parametrize("current", ["1.2.0", "1.3.0"])
def test_the_same_or_an_older_release_is_not(release, keys, current):
    assert checks.check(str(release), current=current, keys=keys).status is Status.UP_TO_DATE


def test_the_popup_opens_once_per_version_and_again_for_a_newer_one():
    assert should_popup("1.2.0", None)
    assert not should_popup("1.2.0", "1.2.0")
    assert should_popup("1.3.0", "1.2.0")
    assert not should_popup("1.2.0", "1.3.0")


def test_a_checkout_is_never_an_installed_build():
    assert not installed()


def test_an_installed_build_is_one_with_velopacks_manifest_beside_it(tmp_path):
    exe = tmp_path / "current" / "BartleBot.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    assert not installed(exe), "CI's build, packaged but never installed"
    (exe.parent / checks.VELOPACK_MANIFEST).write_text("1.2.0")
    assert installed(exe)


# -- preparing ----------------------------------------------------------------------


class FakeManager:
    """Velopack's UpdateManager over the staged folder, as far as prepare uses it."""

    def __init__(self, folder, *, deltas=("BartleBot-1.2.0-delta.nupkg",), fail_deltas=False, info=True):
        self.folder = folder
        self.deltas = deltas
        self.fail_deltas = fail_deltas
        self.has_info = info
        self.downloads: list[set[str]] = []
        self.applied: list[tuple] = []

    def check_for_updates(self):
        if not self.has_info:
            return None
        self.feed_seen = (self.folder / FEED_NAME).read_bytes()
        return SimpleNamespace(
            TargetFullRelease=SimpleNamespace(FileName="BartleBot-1.2.0-full.nupkg", Version="1.2.0"),
            DeltasToTarget=[SimpleNamespace(FileName=name) for name in self.deltas],
        )

    def download_updates(self, info):
        present = {p.name for p in self.folder.iterdir()}
        self.downloads.append(present)
        if self.fail_deltas and "BartleBot-1.2.0-full.nupkg" not in present:
            raise RuntimeError("the delta did not apply")

    def wait_exit_then_apply_updates(self, info, silent=False, restart=True):
        self.applied.append(("wait_exit_then_apply_updates", silent, restart))

    def apply_updates_and_restart(self, info):  # pragma: no cover - must never be called
        raise AssertionError("exits the process past the quit path")


def _offer(release, keys):
    return checks.check(str(release), current="1.1.0", keys=keys).offer


def test_velopack_reads_the_feed_that_verified_and_gets_only_the_delta(release, keys, tmp_path):
    managers = []
    prepared = prepare(
        _offer(release, keys),
        tmp_path / "staged",
        manager_for=lambda f: managers.append(FakeManager(f)) or managers[-1],
    )

    (manager,) = managers
    assert manager.feed_seen == FEED
    assert manager.downloads == [{FEED_NAME, "BartleBot-1.2.0-delta.nupkg"}]
    assert prepared.version == "1.2.0"


def test_with_no_delta_from_this_version_the_full_package_is_downloaded(release, keys, tmp_path):
    manager = None

    def make(folder):
        nonlocal manager
        manager = FakeManager(folder, deltas=())
        return manager

    prepare(_offer(release, keys), tmp_path / "staged", manager_for=make)
    assert manager.downloads == [{FEED_NAME, "BartleBot-1.2.0-full.nupkg"}]


def test_a_delta_that_will_not_apply_falls_back_to_the_full_package(release, keys, tmp_path):
    manager = None

    def make(folder):
        nonlocal manager
        manager = FakeManager(folder, fail_deltas=True)
        return manager

    prepare(_offer(release, keys), tmp_path / "staged", manager_for=make)
    assert "BartleBot-1.2.0-full.nupkg" in manager.downloads[-1]
    assert len(manager.downloads) == 2


def test_velopack_finding_no_update_is_an_update_error(release, keys, tmp_path):
    with pytest.raises(UpdateError):
        prepare(_offer(release, keys), tmp_path / "staged", manager_for=lambda f: FakeManager(f, info=False))


def test_progress_reaches_the_caller(release, keys, tmp_path):
    seen = []
    prepare(
        _offer(release, keys),
        tmp_path / "staged",
        progress=lambda done, total: seen.append((done, total)),
        manager_for=FakeManager,
    )
    assert seen[-1] == (len(b"delta"), len(b"delta"))


@pytest.mark.parametrize("restart", [False, True])
def test_an_update_applies_after_the_app_exits_and_never_exits_it(release, keys, tmp_path, restart):
    managers = []
    prepared = prepare(
        _offer(release, keys),
        tmp_path / "staged",
        manager_for=lambda f: managers.append(FakeManager(f)) or managers[-1],
    )

    apply_on_exit(prepared, restart=restart)

    assert managers[0].applied == [("wait_exit_then_apply_updates", False, restart)]


def test_the_offer_carries_the_verified_feed(release, keys):
    assert _offer(release, keys).feed.data == fetch(str(release), keys).data
