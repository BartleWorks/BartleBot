"""Tests for tools/sign_feed.py: making a key, signing the feed, and checking it against TRUSTED_KEYS."""

from __future__ import annotations

import sys

import pytest

from bartlebot.paths import project_root
from bartlebot.updates.feed import verify

sys.path.insert(0, str(project_root()))

import tools.sign_feed as sign_feed  # noqa: E402
from tools.sign_feed import KEY_ENV_VAR, main, private_key, public_text  # noqa: E402

FEED = b'{"Assets":[]}'


@pytest.fixture
def made_key(tmp_path, capsys):
    """A key ``generate`` made: its file, and the public key it printed."""
    path = tmp_path / "key.txt"
    assert main(["generate", str(path)]) == 0
    return path, capsys.readouterr().out.strip()


def test_generate_writes_the_private_key_and_prints_its_public_half(made_key):
    path, public = made_key
    assert public_text(private_key(path.read_text())) == public


def test_generate_never_overwrites_a_key(made_key):
    path, _ = made_key
    before = path.read_text()
    assert main(["generate", str(path)]) == 1
    assert path.read_text() == before


def test_sign_then_verify_against_the_keys_the_app_trusts(made_key, tmp_path, monkeypatch):
    path, public = made_key
    feed = tmp_path / "releases.win.json"
    feed.write_bytes(FEED)
    monkeypatch.setenv(KEY_ENV_VAR, path.read_text())
    monkeypatch.setattr(sign_feed, "TRUSTED_KEYS", (public,))

    assert main(["sign", str(feed)]) == 0
    signature = tmp_path / "releases.win.json.sig"
    assert verify(FEED, signature.read_bytes(), (public,))
    assert main(["verify", str(feed)]) == 0


def test_verify_fails_a_secret_the_app_does_not_trust(made_key, tmp_path, monkeypatch, capsys):
    """So a wrong secret fails the release, rather than shipping a feed no install accepts."""
    path, _ = made_key
    feed = tmp_path / "releases.win.json"
    feed.write_bytes(FEED)
    monkeypatch.setenv(KEY_ENV_VAR, path.read_text())

    assert main(["sign", str(feed)]) == 0
    assert main(["verify", str(feed)]) == 1
    assert "TRUSTED_KEYS" in capsys.readouterr().err


def test_sign_without_the_secret_fails(tmp_path, monkeypatch):
    feed = tmp_path / "releases.win.json"
    feed.write_bytes(FEED)
    monkeypatch.delenv(KEY_ENV_VAR, raising=False)
    assert main(["sign", str(feed)]) == 1
    assert not (tmp_path / "releases.win.json.sig").exists()


def test_sign_with_a_secret_that_is_not_a_key_fails(tmp_path, monkeypatch):
    feed = tmp_path / "releases.win.json"
    feed.write_bytes(FEED)
    monkeypatch.setenv(KEY_ENV_VAR, "c2hvcnQ=")
    assert main(["sign", str(feed)]) == 1


def test_verify_without_a_signature_fails(tmp_path):
    feed = tmp_path / "releases.win.json"
    feed.write_bytes(FEED)
    assert main(["verify", str(feed)]) == 1
