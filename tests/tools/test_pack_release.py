"""Tests for tools/pack_release.py: what ``vpk pack`` is told, and the one Velopack version."""

import sys
import tomllib
from pathlib import Path

from bartlebot.paths import project_root
from bartlebot.shared.vendor import PRODUCT, VENDOR

sys.path.insert(0, str(project_root()))

from tools.pack_release import PACK_ID, VELOPACK_VERSION, main, pack_command  # noqa: E402

RELEASE_YML = project_root() / ".github" / "workflows" / "release.yml"


def _value(command: list[str], flag: str) -> str:
    return command[command.index(flag) + 1]


def test_the_pack_names_the_product_its_publisher_and_its_exe(tmp_path):
    command = pack_command("1.2.0", "vpk", tmp_path / "dist", tmp_path / "releases")

    assert command[:2] == ["vpk", "pack"]
    assert _value(command, "--packId") == PACK_ID == PRODUCT
    assert _value(command, "--packVersion") == "1.2.0"
    assert _value(command, "--packTitle") == PRODUCT
    assert _value(command, "--packAuthors") == VENDOR
    assert _value(command, "--mainExe") == "BartleBot.exe"
    assert Path(_value(command, "--packDir")) == tmp_path / "dist" / "BartleBot"
    assert Path(_value(command, "--icon")) == tmp_path / "dist" / "bartlebot.ico"
    assert Path(_value(command, "--outputDir")) == tmp_path / "releases"


def test_only_a_start_menu_shortcut_is_made(tmp_path):
    """``vpk pack``'s default also puts one on the Desktop, which the plan does not promise."""
    assert _value(pack_command("1.2.0", build=tmp_path), "--shortcuts") == "StartMenuRoot"


def test_no_portable_zip_is_made(tmp_path):
    """Setup.exe is the one way to install: a portable copy never sees the updater."""
    assert "--noPortable" in pack_command("1.2.0", build=tmp_path)


def test_one_velopack_version_for_the_package_and_vpk():
    lock = tomllib.loads((project_root() / "uv.lock").read_text(encoding="utf-8"))
    (locked,) = [p["version"] for p in lock["package"] if p["name"] == "velopack"]
    workflow = RELEASE_YML.read_text(encoding="utf-8")

    assert locked == VELOPACK_VERSION
    assert f'VELOPACK_VERSION: "{VELOPACK_VERSION}"' in workflow


def test_a_missing_build_is_refused_before_vpk_runs(tmp_path, capsys):
    assert main(["1.2.0", "--vpk", "no-such-vpk", "--build", str(tmp_path)]) == 1
    assert "build_package.py" in capsys.readouterr().err


def _steps() -> list[str]:
    """``release.yml``'s step names and commands, in order, one string per step."""
    return RELEASE_YML.read_text(encoding="utf-8").split("\n      - ")[1:]


def _index(steps: list[str], text: str) -> int:
    (found,) = [i for i, step in enumerate(steps) if text in step]
    return found


def test_the_release_workflow_runs_only_on_a_v_tag():
    workflow = RELEASE_YML.read_text(encoding="utf-8")

    assert 'tags: ["v*"]' in workflow
    assert "branches" not in workflow and "workflow_dispatch" not in workflow


def test_the_tag_is_checked_before_anything_is_built_and_the_pack_follows_the_download():
    steps = _steps()

    assert _index(steps, "release_version.py") < _index(steps, "build_package.py")
    assert _index(steps, "build_package.py") < _index(steps, "download github")
    assert _index(steps, "download github") < _index(steps, "pack_release.py") < _index(steps, "upload github")


def test_the_release_is_left_as_a_draft():
    """Publishing is the maintainer's step: what ships is Official, and Official means tested in game."""
    upload = _steps()[_index(_steps(), "upload github")]

    assert "--publish" not in upload
