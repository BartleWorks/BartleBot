"""Tests for bartlebot.paths.project_root — the single source of truth that
Phase 0 introduced to replace scattered ``Path.cwd()`` / ``Path(__file__)...``
derivations of the project root.
"""

from pathlib import Path

from bartlebot.paths import project_root


def test_project_root_contains_pyproject_toml():
    assert (project_root() / "pyproject.toml").is_file()


def test_project_root_contains_the_bartlebot_package():
    assert (project_root() / "bartlebot" / "__init__.py").is_file()


def test_project_root_is_independent_of_cwd(monkeypatch, tmp_path):
    """The bug this whole phase exists to fix: cwd must not matter."""
    before = project_root()
    monkeypatch.chdir(tmp_path)
    assert project_root() == before


def test_project_root_returns_an_absolute_path():
    assert Path(project_root()).is_absolute()
