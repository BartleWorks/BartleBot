"""The public keys an update feed may be signed by.

Each is the base64 of a 32-byte Ed25519 public key. ``release.yml`` signs every feed with
the private half of one of them, a secret only a tag build can read, and the app offers
an update only when the feed's signature verifies against a key here
(:func:`bartlebot.updates.feed.verify`).

**A key is replaced by a release.** Add the new key here and release, signed by the old
one: every installed copy accepts that feed, and the update carries the new list. Once
that release is out, the next can be signed by the new key, and a later one can drop the
old. Back the new key up before its first release, since losing a key before its
replacement ships strands every installed copy on the version they have
(``docs/RELEASING.md``, "The signing key").
"""

from __future__ import annotations

#: Every key a feed may be signed by. The first is the one ``release.yml`` signs with.
TRUSTED_KEYS: tuple[str, ...] = ("e5m1nNVaoqafsCNWjK0FfFv9laezKUY4HoSStS0AhL8=",)
