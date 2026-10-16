"""Name keyring's Windows backend outright, rather than letting keyring look for one.

keyring finds its backends through package entry points, and a frozen build can lose
the metadata those are read from: keyring then falls back to a backend that refuses
every read and write, and every stored password is silently gone. Every product by
this vendor keeps its credentials in Windows Credential Manager, so each names that
backend at startup (``docs/RELEASING.md``). Elsewhere, keyring keeps its own choice.
"""

from __future__ import annotations

import sys

import keyring


def use_windows_backend() -> None:
    """Make Windows Credential Manager keyring's backend, on Windows; do nothing anywhere else."""
    if sys.platform != "win32":
        return
    from keyring.backends.Windows import WinVaultKeyring  # noqa: PLC0415 - Windows only

    keyring.set_keyring(WinVaultKeyring())
