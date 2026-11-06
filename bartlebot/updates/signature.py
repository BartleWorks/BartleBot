"""Whether a feed's signature verifies: the one check the app and the release's signing job share.

``tools/sign_feed.py`` runs in ``release.yml``'s ``sign`` job, where the signing key is,
with only ``cryptography`` installed, so that no other dependency's code runs beside the
key (``docs/RELEASING.md``, "A release"). So this module imports only ``cryptography`` and
:mod:`bartlebot.updates.keys`. :mod:`bartlebot.updates.feed`, which needs the rest of the
app, imports :func:`verify` from here. ``test_sign_feed_imports_only_cryptography_and_the_signature``
fails an import that would break the job.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from bartlebot.updates.keys import TRUSTED_KEYS


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
