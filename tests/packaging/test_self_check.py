"""``bartlebot.py --self-check``: every check passes from the checkout, and a missing store fails by name."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys

import keyring
import pytest
from keyring.backends import fail

from bartlebot.game.art_extraction import TYPE_DUMP_PATTERN
from bartlebot.packaging import self_check
from bartlebot.packaging.self_check import (
    CHECKS,
    FLAG,
    CheckFailed,
    check_bots,
    check_keyring,
    check_shared_set,
    check_subgraphs,
    check_templates,
    game_data_files,
    run_checks,
)
from bartlebot.paths import project_root
from bartlebot.shared.combat_defaults import SHIPPED_SOLO_DUEL_ID

#: The checks that read the root they are given, and the folder each reads there.
STORE_CHECKS = {
    "bots": (check_bots, "bots"),
    "templates": (check_templates, "templates"),
    "subgraphs": (check_subgraphs, "subgraphs"),
    "shared set": (check_shared_set, "strategies"),
}


@pytest.fixture(scope="module")
def shipped_copy(tmp_path_factory):
    """A copy of the four shipped stores, for a test to break."""
    root = tmp_path_factory.mktemp("shipped")
    for _check, folder in STORE_CHECKS.values():
        shutil.copytree(project_root() / folder, root / folder)
    return root


def _store_checks():
    return tuple((name, fn) for name, (fn, _folder) in STORE_CHECKS.items())


def test_the_self_check_passes_from_the_checkout():
    """The command CI runs on a build, run on the checkout: a missing file fails here first."""
    done = subprocess.run(
        [sys.executable, str(project_root() / "bartlebot.py"), FLAG],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=project_root(),
    )

    lines = done.stdout.splitlines()
    assert done.returncode == 0, done.stdout + done.stderr
    assert [line.split(":")[0].split(maxsplit=1)[1] for line in lines[:-1]] == [name for name, _fn in CHECKS]
    assert all(line.startswith("ok") for line in lines[:-1])
    assert lines[-1] == f"{len(CHECKS)} of {len(CHECKS)} checks passed"


def test_the_shipped_stores_pass_from_a_copy(shipped_copy):
    results = run_checks(shipped_copy, _store_checks())

    assert [r.line() for r in results if not r.ok] == []


@pytest.mark.parametrize("name", list(STORE_CHECKS))
def test_a_missing_store_folder_fails_its_own_check_by_name(name, tmp_path):
    for check, folder in STORE_CHECKS.values():
        if check is not STORE_CHECKS[name][0]:
            shutil.copytree(project_root() / folder, tmp_path / folder)

    results = {r.name: r for r in run_checks(tmp_path, _store_checks())}

    failed = [n for n, r in results.items() if not r.ok]
    assert failed == [name]
    assert f"{tmp_path / STORE_CHECKS[name][1]} is missing" in results[name].detail


def test_a_bot_that_does_not_load_is_named(tmp_path):
    (tmp_path / "bots" / "01_wizard_city").mkdir(parents=True)
    (tmp_path / "bots" / "01_wizard_city" / "broken.json").write_text("{", encoding="utf-8")

    with pytest.raises(CheckFailed, match="bots/01_wizard_city/broken.json does not load"):
        check_bots(tmp_path)


def test_an_empty_store_fails(tmp_path):
    (tmp_path / "bots").mkdir()

    with pytest.raises(CheckFailed, match="no bot in"):
        check_bots(tmp_path)


def test_a_shipped_default_missing_from_the_set_fails(tmp_path):
    shutil.copytree(project_root() / "strategies", tmp_path / "strategies")
    for path in (tmp_path / "strategies" / "duel").glob("*.json"):
        if json.loads(path.read_bytes())["id"] == SHIPPED_SOLO_DUEL_ID:
            path.unlink()

    with pytest.raises(CheckFailed, match=SHIPPED_SOLO_DUEL_ID):
        check_shared_set(tmp_path)


def test_one_failing_check_stops_none_of_the_others(tmp_path):
    def boom(_root):
        raise RuntimeError("no such thing")

    results = run_checks(tmp_path, (("first", boom), ("second", lambda _root: "fine")))

    assert [(r.name, r.ok, r.detail) for r in results] == [
        ("first", False, "RuntimeError: no such thing"),
        ("second", True, "fine"),
    ]


def test_main_prints_a_line_per_check_and_returns_the_failures(tmp_path):
    def gone(_root):
        raise CheckFailed("it is gone")

    out = io.StringIO()

    failed = self_check.main(out, tmp_path, (("good", lambda _root: "found it"), ("bad", gone)))

    assert failed == 1
    assert out.getvalue().splitlines() == ["ok    good: found it", "FAIL  bad: it is gone", "1 of 2 checks passed"]


def test_every_file_in_game_data_is_checked():
    """A snapshot added without a line in game_data_files() would ship unchecked."""
    data = project_root() / "bartlebot" / "game" / "data"
    shipped = {p.name for p in data.glob("*.json") if not TYPE_DUMP_PATTERN.fullmatch(p.name)}

    assert {p.name for p in game_data_files()} == shipped


@pytest.fixture
def restore_keyring():
    before = keyring.get_keyring()
    yield
    keyring.set_keyring(before)


def test_keyring_with_no_backend_fails(restore_keyring):
    keyring.set_keyring(fail.Keyring())

    with pytest.raises(CheckFailed, match="keyring found no backend"):
        check_keyring(project_root())


@pytest.mark.skipif(sys.platform != "win32", reason="Credential Manager is Windows only")
def test_keyring_on_another_backend_fails_on_windows(restore_keyring):
    from keyring.backends.null import Keyring as NullKeyring

    keyring.set_keyring(NullKeyring())

    with pytest.raises(CheckFailed, match="not Windows Credential Manager"):
        check_keyring(project_root())
