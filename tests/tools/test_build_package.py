"""Tests for tools/build_package.py: both packagers bundle the same files, and no install record ships."""

import sys

import pytest

from bartlebot.game.art_extraction import TYPE_DUMP_DIR
from bartlebot.gui.logo import LOGO_SVG
from bartlebot.gui.theming.icon_utils import ICONS_DIR
from bartlebot.packaging.self_check import game_data_files
from bartlebot.paths import project_root

sys.path.insert(0, str(project_root()))

from tools.build_package import (  # noqa: E402
    DATA_DIRS,
    DATA_FILES,
    ENTRY,
    METADATA,
    PACKAGERS,
    WHOLE_PACKAGES,
    strip_install_records,
)


def test_every_folder_the_app_reads_is_bundled():
    root = project_root()
    bundled = {root / folder for folder in DATA_DIRS} | {root / file for file in DATA_FILES}

    for folder in ("bots", "templates", "subgraphs", "strategies"):
        assert root / folder in bundled
    assert TYPE_DUMP_DIR in bundled
    assert ICONS_DIR.resolve() in {p.resolve() for p in bundled}
    assert LOGO_SVG.resolve() in {p.resolve() for p in bundled}
    assert all(path.parent in bundled for path in game_data_files())
    assert all(path.exists() for path in bundled)


@pytest.mark.parametrize("packager", sorted(PACKAGERS))
def test_both_packagers_bundle_the_same_lists(packager, tmp_path):
    command, exe = PACKAGERS[packager](tmp_path / "dist", tmp_path / "work")
    text = " ".join(command)

    for folder in DATA_DIRS:
        assert str(project_root() / folder) in text
    for file in DATA_FILES:
        assert str(project_root() / file) in text
    for name in (*METADATA, *WHOLE_PACKAGES):
        assert any(arg == name or arg.endswith(f"={name}") for arg in command), name
    assert str(ENTRY) in command
    assert exe.name == "BartleBot.exe"


def test_install_records_are_stripped_and_metadata_kept(tmp_path):
    info = tmp_path / "_internal" / "bartlebot-0.1.0.dist-info"
    info.mkdir(parents=True)
    for name in ("METADATA", "direct_url.json", "RECORD", "INSTALLER", "uv_cache.json", "entry_points.txt"):
        (info / name).write_text("x", encoding="utf-8")
    (tmp_path / "direct_url.json").write_text("not in a dist-info", encoding="utf-8")

    removed = strip_install_records(tmp_path)

    assert sorted(p.name for p in removed) == ["INSTALLER", "RECORD", "direct_url.json", "uv_cache.json"]
    assert sorted(p.name for p in info.iterdir()) == ["METADATA", "entry_points.txt"]
    assert (tmp_path / "direct_url.json").exists()
