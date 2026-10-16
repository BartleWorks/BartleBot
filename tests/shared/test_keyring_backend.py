"""keyring's backend is named outright, so a frozen build that lost its entry points still keeps passwords."""

from __future__ import annotations

import sys

import keyring
import pytest
from keyring.backends import fail

from bartlebot.shared import keyring_backend


@pytest.fixture
def restore_keyring():
    before = keyring.get_keyring()
    yield
    keyring.set_keyring(before)


@pytest.mark.skipif(sys.platform != "win32", reason="Credential Manager is Windows only")
def test_windows_uses_credential_manager_whatever_keyring_found(restore_keyring):
    from keyring.backends.Windows import WinVaultKeyring

    keyring.set_keyring(fail.Keyring())

    keyring_backend.use_windows_backend()

    assert isinstance(keyring.get_keyring(), WinVaultKeyring)


def test_elsewhere_keyring_keeps_its_own_choice(restore_keyring, monkeypatch):
    chosen = fail.Keyring()
    keyring.set_keyring(chosen)
    monkeypatch.setattr(keyring_backend.sys, "platform", "linux")

    keyring_backend.use_windows_backend()

    assert keyring.get_keyring() is chosen
