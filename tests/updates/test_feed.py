"""Tests for bartlebot/updates/feed.py: the signature, reading the feed, and moving packages."""

from __future__ import annotations

import base64
import json
import logging
import sys

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bartlebot.paths import project_root
from bartlebot.updates import feed
from bartlebot.updates.check import Status, check
from bartlebot.updates.feed import (
    FEED_NAME,
    SIGNATURE_NAME,
    BadFeed,
    BadSignature,
    Unreachable,
    fetch,
    newer,
    parse,
    verify,
    version_key,
)
from bartlebot.updates.keys import TRUSTED_KEYS

sys.path.insert(0, str(project_root()))

from tools.sign_feed import public_text, sign  # noqa: E402


def feed_bytes(*assets: tuple[str, str, str]) -> bytes:
    """A feed as ``vpk pack`` writes it, one asset per ``(version, kind, file name)``."""
    listed = [
        {
            "PackageId": "BartleBot",
            "Version": version,
            "Type": kind,
            "FileName": name,
            "SHA1": "0" * 40,
            "SHA256": "A" * 64,
            "Size": 1234,
        }
        for version, kind, name in assets
    ]
    return json.dumps({"Assets": listed}).encode("utf-8")


FEED = feed_bytes(
    ("1.2.0", "Full", "BartleBot-1.2.0-full.nupkg"),
    ("1.2.0", "Delta", "BartleBot-1.2.0-delta.nupkg"),
    ("1.1.0", "Full", "BartleBot-1.1.0-full.nupkg"),
)


@pytest.fixture
def key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
def keys(key) -> tuple[str, ...]:
    return (public_text(key),)


def publish(folder, data: bytes, key: Ed25519PrivateKey | None):
    """``data`` as a release's feed in ``folder``, signed by ``key`` (unsigned for None)."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / FEED_NAME).write_bytes(data)
    if key is not None:
        (folder / SIGNATURE_NAME).write_bytes(sign(data, key))
    return folder


# -- the signature --------------------------------------------------------------------


def test_a_feed_signed_by_a_trusted_key_verifies(key, keys):
    assert verify(FEED, sign(FEED, key), keys)


def test_a_feed_with_one_changed_byte_does_not(key, keys):
    changed = bytearray(FEED)
    changed[FEED.index(b"1.2.0")] = ord("9")
    assert not verify(bytes(changed), sign(FEED, key), keys)


def test_a_feed_signed_by_another_key_does_not(key, keys):
    assert not verify(FEED, sign(FEED, Ed25519PrivateKey.generate()), keys)


def test_any_trusted_key_will_do(key):
    """A key is replaced by a release that trusts both, so either has to verify."""
    old, new = Ed25519PrivateKey.generate(), key
    both = (public_text(old), public_text(new))
    assert verify(FEED, sign(FEED, old), both)
    assert verify(FEED, sign(FEED, new), both)


@pytest.mark.parametrize("signature", [b"", b"not base64 at all!", base64.b64encode(b"short"), b"\x00\xff"])
def test_a_signature_that_is_not_one_does_not_verify(keys, signature):
    assert not verify(FEED, signature, keys)


def test_every_trusted_key_is_an_ed25519_public_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey  # noqa: PLC0415

    assert TRUSTED_KEYS
    for key in TRUSTED_KEYS:
        raw = base64.b64decode(key, validate=True)
        assert len(raw) == 32
        Ed25519PublicKey.from_public_bytes(raw)


# -- fetching ----------------------------------------------------------------------


def test_a_signed_feed_is_fetched_and_parsed(tmp_path, key, keys):
    signed = fetch(str(publish(tmp_path, FEED, key)), keys)

    assert signed.data == FEED
    assert signed.latest().version == "1.2.0"
    assert signed.latest().file_name == "BartleBot-1.2.0-full.nupkg"


def test_a_changed_feed_is_refused_and_logged_as_a_warning(tmp_path, key, keys, caplog):
    folder = publish(tmp_path, FEED, key)
    (folder / FEED_NAME).write_bytes(FEED.replace(b"1.2.0", b"1.2.1", 1))

    with pytest.raises(BadSignature):
        fetch(str(folder), keys)
    with caplog.at_level(logging.INFO):
        result = check(str(folder), current="1.1.0", keys=keys)
    assert result.status is Status.REFUSED
    assert result.offer is None
    assert [r.levelno for r in caplog.records if r.name == "bartlebot.updates.check"] == [logging.WARNING]


def test_a_feed_signed_by_another_key_is_refused_and_logged_as_a_warning(tmp_path, keys, caplog):
    folder = publish(tmp_path, FEED, Ed25519PrivateKey.generate())

    with caplog.at_level(logging.INFO):
        result = check(str(folder), current="1.1.0", keys=keys)
    assert result.status is Status.REFUSED
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_a_feed_with_no_signature_is_refused(tmp_path, keys):
    with pytest.raises(BadSignature):
        fetch(str(publish(tmp_path, FEED, None)), keys)


def test_no_feed_at_all_is_unreachable_and_never_a_warning(tmp_path, keys, caplog):
    with pytest.raises(Unreachable):
        fetch(str(tmp_path / "nothing here"), keys)
    with caplog.at_level(logging.INFO):
        result = check(str(tmp_path / "nothing here"), current="1.1.0", keys=keys)
    assert result.status is Status.UNREACHABLE
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_an_unreachable_server_is_unreachable(keys):
    # Nothing listens on the discard port, so the connection is refused at once.
    with pytest.raises(Unreachable):
        fetch("http://127.0.0.1:9/", keys)


def test_a_signed_feed_that_is_not_json_is_a_bad_feed(tmp_path, key, keys):
    with pytest.raises(BadFeed):
        fetch(str(publish(tmp_path, b"<html>not a feed</html>", key)), keys)


def test_another_products_packages_are_not_this_ones():
    data = json.loads(FEED)
    data["Assets"].append({**data["Assets"][0], "PackageId": "BartleBuddy", "Version": "9.0.0"})
    assert {a.version for a in parse(json.dumps(data).encode())} == {"1.2.0", "1.1.0"}


def test_the_notes_are_the_feeds_markdown():
    data = json.loads(FEED)
    data["Assets"][0]["NotesMarkdown"] = "## Fixed\n\n- Foulgaze"
    (latest,) = [a for a in parse(json.dumps(data).encode()) if a.full and a.version == "1.2.0"]
    assert latest.notes == "## Fixed\n\n- Foulgaze"


def test_the_notes_stop_where_the_install_page_starts():
    """Whoever is offered an update has installed already, so the popup leaves the install page out."""
    data = json.loads(FEED)
    data["Assets"][0]["NotesMarkdown"] = (
        f"## Fixed\n\n- Foulgaze\n\n{feed.INSTALL_NOTES_MARKER}\n\n## Install\n\nSetup.exe"
    )
    (latest,) = [a for a in parse(json.dumps(data).encode()) if a.full and a.version == "1.2.0"]
    assert latest.notes == "## Fixed\n\n- Foulgaze"


def test_the_location_is_the_latest_release_unless_the_variable_names_another(monkeypatch, tmp_path):
    monkeypatch.delenv(feed.FEED_ENV_VAR, raising=False)
    assert feed.location() == "https://github.com/The-World-Tree/BartleBot/releases/latest/download/"
    monkeypatch.setenv(feed.FEED_ENV_VAR, str(tmp_path))
    assert feed.location() == str(tmp_path)


# -- versions ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("later", "earlier"),
    [
        ("1.2.0", "1.1.9"),
        ("1.10.0", "1.9.0"),
        ("2.0.0", "1.99.99"),
        ("1.0.0", "1.0.0-rc.1"),
        ("1.0.0-rc.2", "1.0.0-rc.1"),
        ("1.0.0-rc.10", "1.0.0-rc.9"),
        ("1.0.0-beta", "1.0.0-alpha"),
        ("1.0.0-alpha.beta", "1.0.0-alpha.1"),
    ],
)
def test_versions_sort_as_semver_says(later, earlier):
    assert version_key(later) > version_key(earlier)
    assert newer(later, earlier)
    assert not newer(earlier, later)


def test_a_version_is_not_newer_than_itself_and_any_is_newer_than_none():
    assert not newer("1.2.0", "1.2.0")
    assert newer("0.0.1", None)


def test_a_version_that_is_not_semver_is_never_newer():
    assert not newer("latest", "0.0.1")
    assert not newer("latest", None)


# -- packages ----------------------------------------------------------------------


def test_a_package_is_copied_whole_with_its_progress(tmp_path):
    source = tmp_path / "release"
    source.mkdir()
    (source / "pkg.nupkg").write_bytes(b"x" * (feed.CHUNK_BYTES * 2 + 5))
    seen = []

    written = feed.download(
        str(source), "pkg.nupkg", tmp_path / "staged", lambda done, total: seen.append((done, total))
    )

    assert written.read_bytes() == (source / "pkg.nupkg").read_bytes()
    assert seen[-1] == (feed.CHUNK_BYTES * 2 + 5, feed.CHUNK_BYTES * 2 + 5)
    assert not list((tmp_path / "staged").glob("*.part"))


def test_a_missing_package_leaves_nothing_behind(tmp_path):
    with pytest.raises(Unreachable):
        feed.download(str(tmp_path / "release"), "pkg.nupkg", tmp_path / "staged")
    assert not list((tmp_path / "staged").iterdir())


def test_staging_empties_the_folder_and_writes_the_verified_bytes(tmp_path, key, keys):
    folder = tmp_path / "staged"
    folder.mkdir()
    (folder / "old-full.nupkg").write_bytes(b"stale")
    signed = fetch(str(publish(tmp_path / "release", FEED, key)), keys)

    feed.stage(signed, folder)

    assert [p.name for p in folder.iterdir()] == [FEED_NAME]
    assert (folder / FEED_NAME).read_bytes() == FEED


def test_staging_is_local_app_data_under_the_vendor(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert feed.staging_dir() == tmp_path / "Bartleby" / "BartleBot" / "updates"
