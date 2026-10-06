"""Where bots live: the Official ones in the install directory, and the user's own (#168)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from bartlebot import bot_roots
from bartlebot.paths import project_root
from bartlebot.shared.vendor import product_dir
from bartlebot.store_roots import DEV_ENV_VAR


def _bot(path: Path, bot_id: str, name: str = "Bot") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": bot_id, "name": name}), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# The user's folder
# ---------------------------------------------------------------------------


def test_the_default_user_folder_is_under_the_user_root():
    assert bot_roots.user_bots_dir() == product_dir() / "bots"


def test_a_relative_setting_resolves_against_the_user_root_not_the_install_directory():
    assert bot_roots.user_bots_dir("data/bots") == product_dir() / "data" / "bots"


def test_an_absolute_setting_is_used_as_it_is(tmp_path):
    assert bot_roots.user_bots_dir(str(tmp_path / "mine")) == tmp_path / "mine"


def test_the_setting_is_read_when_no_folder_is_passed(tmp_path):
    from bartlebot.settings_manager import SettingsManager

    SettingsManager.instance().set("canvas", "bot_library_path", value=str(tmp_path / "set"))
    assert bot_roots.user_bots_dir() == tmp_path / "set"


def test_developer_mode_makes_the_two_folders_one(monkeypatch):
    monkeypatch.setenv(DEV_ENV_VAR, "1")
    assert bot_roots.user_bots_dir() == project_root() / "bots"
    assert bot_roots.library_dirs() == [project_root() / "bots"]


def test_outside_developer_mode_the_library_reads_both_folders():
    assert bot_roots.library_dirs() == [project_root() / "bots", product_dir() / "bots"]


# ---------------------------------------------------------------------------
# What may be written
# ---------------------------------------------------------------------------


def test_a_shipped_bot_is_shipped_and_may_not_be_written():
    path = project_root() / "bots" / "01_wizard_city" / "any.json"
    assert bot_roots.is_shipped(path)
    assert not bot_roots.may_write(path)


def test_nothing_in_the_install_directory_may_be_written(tmp_path):
    assert not bot_roots.may_write(project_root() / "somewhere" / "bot.json")
    assert bot_roots.may_write(tmp_path / "Desktop" / "bot.json")
    assert bot_roots.may_write(product_dir() / "bots" / "01_wizard_city" / "bot.json")


def test_a_user_folder_set_inside_the_install_directory_may_still_be_written():
    inside = project_root() / "my-bots"
    assert bot_roots.may_write(inside / "bot.json", configured=str(inside))


def test_developer_mode_may_write_shipped_bots(monkeypatch):
    monkeypatch.setenv(DEV_ENV_VAR, "1")
    path = project_root() / "bots" / "01_wizard_city" / "any.json"
    assert not bot_roots.is_shipped(path)
    assert bot_roots.may_write(path)


# ---------------------------------------------------------------------------
# The merged listing
# ---------------------------------------------------------------------------


def test_the_library_lists_both_folders_by_world_folder(tmp_path):
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    a = _bot(shipped / "01_wizard_city" / "a.json", "a")
    b = _bot(user / "01_wizard_city" / "b.json", "b")
    c = _bot(user / "04_mooshu" / "c.json", "c")
    top = _bot(user / "loose.json", "d")

    files = bot_roots.library_files(shipped, user)

    assert [(f.path, f.folder, f.shipped) for f in files] == [
        (a, "01_wizard_city", True),
        (b, "01_wizard_city", False),
        (c, "04_mooshu", False),
        (top, None, False),
    ]


def test_a_user_bot_declaring_an_official_id_is_left_out_with_a_warning(tmp_path, caplog):
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    official = _bot(shipped / "01_wizard_city" / "official.json", "same-id", "Official")
    _bot(user / "01_wizard_city" / "hand-copy.json", "same-id", "Hand copy")

    with caplog.at_level(logging.WARNING, logger="bartlebot.bot_roots"):
        files = bot_roots.library_files(shipped, user)

    assert [f.path for f in files] == [official]
    assert "never overridden" in caplog.text


def test_one_folder_is_listed_once_as_the_users(tmp_path):
    only = _bot(tmp_path / "01_wizard_city" / "a.json", "a")
    files = bot_roots.library_files(tmp_path, tmp_path)
    assert [(f.path, f.shipped) for f in files] == [(only, False)]


def test_missing_folders_list_nothing(tmp_path):
    assert bot_roots.library_files(tmp_path / "no", tmp_path / "nope") == []
