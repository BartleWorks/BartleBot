"""Sign the update feed, check a signature, or make a new signing key.

    uv run python tools/sign_feed.py sign releases/releases.win.json
    uv run python tools/sign_feed.py verify releases/releases.win.json
    uv run python tools/sign_feed.py generate <private key file>

``release.yml``'s signing job runs ``sign`` and then ``verify``: ``sign`` reads the private
key from the ``FEED_SIGNING_KEY`` environment variable, the secret only that job can read,
and writes ``releases.win.json.sig`` beside the feed. ``verify`` checks the signature
against ``TRUSTED_KEYS``, the keys the app itself trusts, so a secret that does not match
them fails the release instead of shipping a feed no installed app will accept.

``generate`` makes a key pair, once, or when a key is replaced (``docs/RELEASING.md``,
"The signing key"). It writes the private key to a file of your choosing and prints the
public key for ``bartlebot/updates/keys.py``. The private key never goes in the repo, a
log or an issue.

Only ``cryptography`` and ``bartlebot/updates/`` are imported, so the signing job needs
nothing else installed.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bartlebot.updates.feed import verify  # noqa: E402
from bartlebot.updates.keys import TRUSTED_KEYS  # noqa: E402

#: The environment variable the signing job is given the private key in.
KEY_ENV_VAR = "FEED_SIGNING_KEY"


def private_key(text: str) -> Ed25519PrivateKey:
    """A private key from its base64 text: the 32-byte seed, as ``generate`` writes it."""
    raw = base64.b64decode(text.strip(), validate=True)
    if len(raw) != 32:
        raise ValueError(f"an Ed25519 private key is 32 bytes, not {len(raw)}")
    return Ed25519PrivateKey.from_private_bytes(raw)


def public_text(key: Ed25519PrivateKey) -> str:
    """The base64 of the 32 raw public key bytes: what ``TRUSTED_KEYS`` holds."""
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def private_text(key: Ed25519PrivateKey) -> str:
    raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    return base64.b64encode(raw).decode("ascii")


def sign(data: bytes, key: Ed25519PrivateKey) -> bytes:
    """The ``.sig`` file's contents for ``data``: the base64 of its 64 signature bytes, and a newline."""
    return base64.b64encode(key.sign(data)) + b"\n"


def signature_path(feed: Path) -> Path:
    """Where a feed's signature goes: beside it, named for it."""
    return feed.with_name(f"{feed.name}.sig")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    sign_cmd = commands.add_parser("sign", help=f"sign a feed with the key in ${KEY_ENV_VAR}")
    sign_cmd.add_argument("feed", type=Path)
    verify_cmd = commands.add_parser("verify", help="check a feed's signature against TRUSTED_KEYS")
    verify_cmd.add_argument("feed", type=Path)
    generate_cmd = commands.add_parser("generate", help="make a key pair, and print the public key")
    generate_cmd.add_argument("private_key_file", type=Path)
    args = parser.parse_args(argv)

    if args.command == "generate":
        if args.private_key_file.exists():
            print(f"{args.private_key_file} exists: a key is never overwritten", file=sys.stderr)
            return 1
        key = Ed25519PrivateKey.generate()
        args.private_key_file.write_text(private_text(key) + "\n", encoding="ascii")
        print(public_text(key))
        return 0

    if not args.feed.is_file():
        print(f"{args.feed} is missing", file=sys.stderr)
        return 1
    data = args.feed.read_bytes()

    if args.command == "sign":
        secret = os.environ.get(KEY_ENV_VAR, "")
        if not secret.strip():
            print(f"{KEY_ENV_VAR} is not set", file=sys.stderr)
            return 1
        try:
            key = private_key(secret)
        except ValueError as exc:
            print(f"{KEY_ENV_VAR} is not a signing key: {exc}", file=sys.stderr)
            return 1
        signature_path(args.feed).write_bytes(sign(data, key))
        print(f"signed {args.feed.name} with the key whose public half is {public_text(key)}")
        return 0

    signature = signature_path(args.feed)
    if not signature.is_file():
        print(f"{signature} is missing", file=sys.stderr)
        return 1
    if not verify(data, signature.read_bytes(), TRUSTED_KEYS):
        print(f"{args.feed.name}'s signature matches no key in TRUSTED_KEYS", file=sys.stderr)
        return 1
    print(f"{args.feed.name}'s signature verifies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
