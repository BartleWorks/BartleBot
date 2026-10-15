"""The game folder field: the line under it, and Browse… (#227)."""

from __future__ import annotations

from pathlib import Path

from bartlebot.gui.widgets.inputs.game_folder_input import (
    FOLDER_EMPTY,
    FOLDER_FOUND,
    FOLDER_MISSING,
    FOLDER_OK,
    GameFolderInput,
    folder_status,
)


def _install(folder: Path) -> Path:
    (folder / "Bin").mkdir(parents=True)
    (folder / "Bin" / "WizardGraphicalClient.exe").write_bytes(b"")
    return folder


def test_the_line_says_what_the_folder_is(tmp_path):
    game = _install(tmp_path / "Wizard101")

    assert folder_status("", None) == (FOLDER_EMPTY, False)
    assert folder_status(str(tmp_path), None) == (FOLDER_MISSING, True)
    assert folder_status(str(game), None) == (FOLDER_OK, False)
    assert folder_status(str(game), game, "a Steam library") == (FOLDER_FOUND.format(source="a Steam library"), False)


def test_browse_starts_at_the_folder_in_the_field_and_a_cancel_changes_nothing(qapp, tmp_path):
    field = GameFolderInput()
    field.set_path(tmp_path)
    asked: list[str] = []

    def cancel(_parent, start: str) -> str:
        asked.append(start)
        return ""

    field.choose_directory = cancel
    field.browse()

    assert asked == [str(tmp_path)]
    assert field.path() == str(tmp_path)


def test_every_edit_says_changed(qapp, tmp_path):
    field = GameFolderInput()
    heard: list[None] = []
    field.changed.connect(lambda: heard.append(None))

    field.edit.setText("C:\\")
    field.choose_directory = lambda _parent, _start: str(tmp_path)
    field.browse()

    assert len(heard) == 2
    assert field.path() == str(tmp_path)
