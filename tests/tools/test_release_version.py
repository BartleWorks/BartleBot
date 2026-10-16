"""Tests for tools/release_version.py: a tag is released only when it names the version the app reports."""

import sys

import pytest

from bartlebot.paths import project_root

sys.path.insert(0, str(project_root()))

from tools import release_version  # noqa: E402
from tools.release_version import SEMVER, main, notes, problems, project_version  # noqa: E402

NOTES = "## Fixed\n\n- Foulgaze"


def test_a_matching_tag_has_no_problems():
    assert problems("v1.2.0", "1.2.0", "1.2.0", NOTES) == []


def test_a_tag_whose_version_differs_is_refused():
    (problem,) = problems("v1.2.1", "1.2.0", "1.2.0", NOTES)

    assert "'v1.2.1'" in problem and "'1.2.0'" in problem


@pytest.mark.parametrize("tag", ["1.2.0", "V1.2.0", "v1.2", "release-1.2.0"])
def test_a_tag_that_is_not_v_plus_the_version_is_refused(tag):
    assert problems(tag, "1.2.0", "1.2.0", NOTES)


def test_stale_metadata_is_refused():
    """A version bumped without ``uv sync`` would ship an app reporting the old one."""
    (problem,) = problems("v1.2.0", "1.2.0", "1.1.0", NOTES)

    assert "uv sync" in problem


def test_missing_metadata_is_refused():
    (problem,) = problems("v1.2.0", "1.2.0", None, NOTES)

    assert "uv sync" in problem


@pytest.mark.parametrize("version", ["1.2.0", "0.1.0", "1.0.0-beta.1", "10.20.30-rc.1"])
def test_semver_versions_pass(version):
    assert SEMVER.fullmatch(version)


@pytest.mark.parametrize("version", ["1.2", "01.2.0", "1.2.0.4", "1.2.0b1", "v1.2.0"])
def test_versions_velopack_cannot_take_are_refused(version):
    assert any("SemVer" in p for p in problems(f"v{version}", version, version, NOTES))


def test_the_checkout_version_is_semver_and_matches_the_metadata():
    """What a tag of the checkout's own version would see, so a bad bump fails here first."""
    version = project_version()

    assert problems(f"v{version}", version, release_version.installed_version(), notes(version)) == []


def test_main_prints_the_version_alone_and_exits_zero(capsys):
    version = project_version()

    assert main([f"v{version}"]) == 0
    assert capsys.readouterr().out == f"{version}\n"


def test_main_exits_one_and_says_why(capsys):
    assert main(["v999.0.0"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "'v999.0.0'" in captured.err


def test_project_version_reads_pyproject(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "x"\nversion = "3.4.5"\n', encoding="utf-8")

    assert project_version(pyproject) == "3.4.5"


def test_a_version_without_notes_is_refused():
    """The notes are the release's description and what the popup shows, so they come before the tag."""
    (problem,) = problems("v1.2.0", "1.2.0", "1.2.0", None)

    assert "release_notes/1.2.0.md is missing" in problem


def test_a_version_with_empty_notes_is_refused():
    (problem,) = problems("v1.2.0", "1.2.0", "1.2.0", " \n")

    assert "release_notes/1.2.0.md is empty" in problem


def test_notes_reads_the_versions_file(tmp_path):
    (tmp_path / "1.2.0.md").write_text(NOTES, encoding="utf-8")

    assert notes("1.2.0", tmp_path) == NOTES
    assert notes("1.3.0", tmp_path) is None
