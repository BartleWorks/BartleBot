"""Tests for tools/build_package.py: every file the app reads is bundled, and no install record ships."""

import sys

from bartlebot.game.art_extraction import TYPE_DUMP_DIR
from bartlebot.gui.logo import LOGO_SVG
from bartlebot.gui.theming.icon_utils import ICONS_DIR
from bartlebot.logging_setup import HEADER_LIBRARIES
from bartlebot.packaging.self_check import game_data_files
from bartlebot.paths import project_root

sys.path.insert(0, str(project_root()))

from tools.build_package import (  # noqa: E402
    DATA_DIRS,
    DATA_FILES,
    ENTRY,
    METADATA,
    WHOLE_PACKAGES,
    pyinstaller_command,
    strip_install_records,
    write_icon,
    write_splash,
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


def test_the_command_bundles_every_list(tmp_path):
    command, exe = pyinstaller_command(tmp_path / "dist", tmp_path / "work")
    text = " ".join(command)

    for folder in DATA_DIRS:
        assert str(project_root() / folder) in text
    for file in DATA_FILES:
        assert str(project_root() / file) in text
    for name in (*METADATA, *WHOLE_PACKAGES):
        assert any(arg == name or arg.endswith(f"={name}") for arg in command), name
    assert str(ENTRY) in command
    assert exe.name == "BartleBot.exe"


def test_every_version_the_session_header_names_is_bundled():
    """Without its metadata a library's line in the header reads ``unknown`` in a build."""
    assert "bartlebot" in METADATA
    assert set(HEADER_LIBRARIES) <= set(METADATA)


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


def test_the_command_carries_the_icon_when_given_one(tmp_path):
    icon = tmp_path / "bartlebot.ico"

    with_icon, _exe = pyinstaller_command(tmp_path / "dist", tmp_path / "work", icon)
    without, _exe = pyinstaller_command(tmp_path / "dist", tmp_path / "work")

    assert with_icon[with_icon.index("--icon") + 1] == str(icon)
    assert "--icon" not in without


def test_the_icon_is_the_logo_at_every_window_icon_size(qapp, tmp_path):
    from PIL import Image

    from bartlebot.gui.logo import ICON_SIZES

    icon = write_icon(tmp_path)

    assert icon == tmp_path / "bartlebot.ico"
    with Image.open(icon) as image:
        assert sorted(image.info["sizes"]) == sorted((size, size) for size in ICON_SIZES)


def test_the_splash_is_the_logo_square_with_room_below_the_tree_for_the_bar(qapp, tmp_path):
    """Velopack lays an opaque 12 px bar, scaled as the image is, along the splash's bottom edge."""
    from PIL import Image

    from tools.export_logo import SPLASH_SIZE

    splash = write_splash(tmp_path)

    assert splash == tmp_path / "bartlebot-splash.png"
    with Image.open(splash) as image:
        rgba = image.convert("RGBA")
    assert rgba.size == (SPLASH_SIZE, SPLASH_SIZE)
    assert rgba.getextrema()[3] == (255, 255), "the bar would float over a transparent corner"
    background = rgba.getpixel((0, 0))
    bar_rows = rgba.crop((0, SPLASH_SIZE - 12, SPLASH_SIZE, SPLASH_SIZE))
    assert [color for _, color in bar_rows.getcolors()] == [background], "the bar would cover the tree"
