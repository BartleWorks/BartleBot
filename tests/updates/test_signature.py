"""Tests for bartlebot/updates/signature.py: whether a feed's signature verifies against trusted keys."""

from __future__ import annotations

import base64
import sys

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from bartlebot.paths import project_root
from bartlebot.updates.keys import TRUSTED_KEYS
from bartlebot.updates.signature import verify

sys.path.insert(0, str(project_root()))

from tools.sign_feed import public_text, sign  # noqa: E402

FEED = b'{"Assets":[{"PackageId":"BartleBot","Version":"1.2.0","Type":"Full"}]}'


@pytest.fixture
def key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
def keys(key) -> tuple[str, ...]:
    return (public_text(key),)


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
    assert TRUSTED_KEYS
    for key in TRUSTED_KEYS:
        raw = base64.b64decode(key, validate=True)
        assert len(raw) == 32
        Ed25519PublicKey.from_public_bytes(raw)
