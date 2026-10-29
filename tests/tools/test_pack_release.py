"""Tests for tools/pack_release.py: what ``vpk pack`` is told, and the one Velopack version."""

import json
import re
import sys
import tomllib
from pathlib import Path

from bartleby.shared.vendor import VENDOR

from bartlebot.packaging.product import NAME as PRODUCT
from bartlebot.paths import project_root

sys.path.insert(0, str(project_root()))

from bartlebot.gui.logo import LOGO_SVG  # noqa: E402
from bartlebot.updates.feed import INSTALL_NOTES_MARKER, REPOSITORY, parse  # noqa: E402
from tools import pack_release  # noqa: E402
from tools.pack_release import (  # noqa: E402
    INSTALL_FOOTER,
    INSTALL_URL,
    NOTES_NAME,
    PACK_ID,
    SETUP_NAME,
    SPLASH_PROGRESS_COLOR,
    VELOPACK_VERSION,
    main,
    pack_command,
    release_notes,
)
from tools.release_version import project_version  # noqa: E402

RELEASE_YML = project_root() / ".github" / "workflows" / "release.yml"
#: A markdown link or image whose target is neither a URL nor mail.
RELATIVE = re.compile(r"\]\((?!https?://|mailto:)")


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


def test_setup_shows_the_logo_while_it_installs(tmp_path):
    command = pack_command("1.2.0", build=tmp_path / "dist")

    assert Path(_value(command, "--splashImage")) == tmp_path / "dist" / "bartlebot-splash.png"


def test_the_splash_progress_bar_is_a_color_from_the_logo(tmp_path):
    """Velopack's default bar is #00FF00, which is in no part of the logo."""
    assert _value(pack_command("1.2.0", build=tmp_path), "--splashProgressColor") == SPLASH_PROGRESS_COLOR
    assert f'fill="{SPLASH_PROGRESS_COLOR}"' in LOGO_SVG.read_text(encoding="utf-8")


def test_the_shortcuts_are_on_the_desktop_and_in_the_start_menu(tmp_path):
    """The install folder is under %LocalAppData%, which a user would not look in for the app."""
    assert _value(pack_command("1.2.0", build=tmp_path), "--shortcuts") == "Desktop,StartMenuRoot"


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


def test_a_build_without_its_splash_is_refused_before_vpk_runs(tmp_path, capsys):
    (tmp_path / "BartleBot").mkdir()
    (tmp_path / "bartlebot.ico").touch()

    assert main(["1.2.0", "--vpk", "no-such-vpk", "--build", str(tmp_path)]) == 1
    assert "bartlebot-splash.png is missing" in capsys.readouterr().err


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


NOTES = """![SmartScreen](images/smartscreen.png)

See [where your data lives](../docs/INSTALLER.md), [the next part](#fixed) and [Microsoft](https://microsoft.com).
"""


def _notes(tmp_path, own="Fixed Foulgaze."):
    notes_dir = tmp_path / "release_notes"
    notes_dir.mkdir()
    (notes_dir / "1.2.0.md").write_text(own, encoding="utf-8")
    return notes_dir


def test_the_pack_is_given_the_composed_notes(tmp_path):
    assert Path(_value(pack_command("1.2.0", build=tmp_path), "--releaseNotes")) == tmp_path / NOTES_NAME


def test_links_in_the_versions_own_notes_are_made_absolute_at_the_tag(tmp_path):
    """A path relative to the notes file leads nowhere on the release page or in the popup."""
    own = release_notes("1.2.0", _notes(tmp_path, own=NOTES)).split(INSTALL_NOTES_MARKER)[0]
    repo = "https://github.com/BartleWorks/BartleBot"

    assert f"![SmartScreen]({repo}/raw/v1.2.0/release_notes/images/smartscreen.png)" in own
    assert f"[where your data lives]({repo}/blob/v1.2.0/docs/INSTALLER.md)" in own
    assert f"[the next part]({repo}/blob/v1.2.0/release_notes/1.2.0.md#fixed)" in own
    assert "[Microsoft](https://microsoft.com)" in own


def test_the_notes_are_the_versions_own_then_a_footer_linking_the_install_page(tmp_path):
    """The release page is the changelog; the install steps stay on the site, not in every release."""
    notes = release_notes("1.2.0", _notes(tmp_path))

    own, footer = notes.split(INSTALL_NOTES_MARKER)
    assert own.strip() == "Fixed Foulgaze."
    assert footer.strip() == INSTALL_FOOTER
    assert f"**{SETUP_NAME}**" in INSTALL_FOOTER and f"]({INSTALL_URL})" in INSTALL_FOOTER


def test_the_popup_shows_the_versions_own_notes_and_not_the_footer(tmp_path):
    feed = {
        "Assets": [
            {
                "PackageId": PRODUCT,
                "Version": "1.2.0",
                "Type": "Full",
                "FileName": "x.nupkg",
                "NotesMarkdown": release_notes("1.2.0", _notes(tmp_path)),
            }
        ]
    }

    (asset,) = parse(json.dumps(feed).encode())
    assert asset.notes == "Fixed Foulgaze."


def test_a_version_without_notes_is_refused_before_vpk_runs(tmp_path, capsys, monkeypatch):
    (tmp_path / "BartleBot").mkdir()
    (tmp_path / "bartlebot.ico").touch()
    (tmp_path / "bartlebot-splash.png").touch()
    monkeypatch.setattr(pack_release, "NOTES_DIR", tmp_path / "release_notes")

    assert main(["1.2.0", "--vpk", "no-such-vpk", "--build", str(tmp_path)]) == 1
    assert "no release notes" in capsys.readouterr().err


def test_the_checkouts_version_has_notes():
    """What a tag of the checkout's version would pack, so a missing piece fails here first."""
    own = release_notes(project_version()).split(INSTALL_NOTES_MARKER)[0]

    assert own.strip() and not RELATIVE.search(own)


def test_everything_the_release_notes_link_in_the_repository_is_there():
    """A file the notes link at the tag that is not in the repository is a broken image or a
    404 on the release page, which no build would notice."""
    at_tag = re.compile(rf"\({re.escape(REPOSITORY)}/(?:raw|blob)/v[^/]+/([^)#]+)")

    linked = at_tag.findall(release_notes(project_version()))

    assert [path for path in linked if not (project_root() / path).is_file()] == []
