"""The share file types, registered per user (#27).

These write the real registry, under a scratch key of their own that each test removes,
never under ``Software\\Classes``, so the machine's associations are never touched.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from bartlebot.shared import file_types, install_key
from bartlebot.shared.file_types import SHARED_TYPES, FileType

winreg = pytest.importorskip("winreg")

EXE = Path(r"C:\Apps\BartleBot\BartleBot.exe")
OTHER = Path(r"C:\Apps\BartleBuddy\BartleBuddy.exe")
OWN = (FileType(".bbot", "BartleBot.Bot", "BartleBot bot"),)


@pytest.fixture
def classes():
    key = rf"Software\Bartleby-test-{uuid.uuid4().hex}"
    yield key
    install_key.delete_tree(winreg.HKEY_CURRENT_USER, key)


def _default(path: str) -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            return winreg.QueryValueEx(key, "")[0]
    except OSError:
        return None


def _exists(path: str) -> bool:
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, path))
    except OSError:
        return False
    return True


def test_the_shared_types_are_the_vendors_three():
    assert [(t.extension, t.prog_id, t.description) for t in SHARED_TYPES] == [
        (".bbduel", "Bartleby.DuelStrategy", "Bartleby duel strategy"),
        (".bbplayer", "Bartleby.PlayerStrategy", "Bartleby player strategy"),
        (".bbdeck", "Bartleby.Deck", "Bartleby deck"),
    ]


def test_a_prog_id_is_the_owner_and_the_kind_in_words():
    assert file_types.prog_id("BartleBot", "bot_combat_strategy") == "BartleBot.BotCombatStrategy"


def test_registering_writes_the_description_icon_command_and_extension(classes):
    file_types.register(OWN, EXE, classes=classes)

    assert _default(rf"{classes}\BartleBot.Bot") == "BartleBot bot"
    assert _default(rf"{classes}\BartleBot.Bot\DefaultIcon") == f'"{EXE}",0'
    assert _default(rf"{classes}\BartleBot.Bot\shell\open\command") == f'"{EXE}" "%1"'
    assert _default(rf"{classes}\.bbot") == "BartleBot.Bot"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{classes}\.bbot\OpenWithProgids") as key:
        assert winreg.QueryValueEx(key, "BartleBot.Bot")[1] == winreg.REG_NONE


def test_registering_again_points_the_type_at_the_new_exe(classes):
    file_types.register(SHARED_TYPES, EXE, classes=classes)
    file_types.register(SHARED_TYPES, OTHER, classes=classes)

    assert {file_types.registered_command(t, classes=classes) for t in SHARED_TYPES} == {f'"{OTHER}" "%1"'}


def test_unregistering_removes_the_prog_id_and_the_extension(classes):
    file_types.register(OWN, EXE, classes=classes)

    file_types.unregister(OWN, classes=classes)

    assert not _exists(rf"{classes}\BartleBot.Bot")
    assert not _exists(rf"{classes}\.bbot")
    assert file_types.registered_command(OWN[0], classes=classes) is None


def test_unregistering_leaves_an_extension_another_program_claimed(classes):
    file_types.register(OWN, EXE, classes=classes)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{classes}\.bbot", 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "Someone.Else")

    file_types.unregister(OWN, classes=classes)

    assert _default(rf"{classes}\.bbot") == "Someone.Else"
    with (
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{classes}\.bbot\OpenWithProgids") as key,
        pytest.raises(OSError),
    ):
        winreg.QueryValueEx(key, "BartleBot.Bot")


def test_unregistering_what_was_never_registered_is_nothing(classes):
    file_types.unregister(OWN + SHARED_TYPES, classes=classes)

    assert not _exists(classes)


def test_the_default_is_classes_read_when_called(monkeypatch, classes):
    monkeypatch.setattr(file_types, "CLASSES_KEY", classes)

    file_types.register(OWN, EXE)

    assert file_types.registered_command(OWN[0]) == f'"{EXE}" "%1"'
