"""Velopack's part of startup: first of all in a packaged build, and never in a checkout.

The hooks write the real registry, under scratch keys each test removes (``machine``).
"""

from __future__ import annotations

import ast
import subprocess
import sys
import time
import uuid
from types import SimpleNamespace

import pytest
import velopack
from bartleby.shared import file_types, install_key
from bartleby.shared.install_key import InstallRecord
from bartleby.shared.official_set import SET_FILE
from bartleby.shared.vendor import PRODUCT, VENDOR

from bartlebot.packaging import install_hooks
from bartlebot.paths import project_root


class _FakeApp:
    """Records what :func:`run_velopack` registers, in place of ``velopack.App``."""

    made: list[_FakeApp] = []

    def __init__(self):
        self.hooks: dict[str, object] = {}
        self.args: list[str] | None = None
        self.runs = 0
        _FakeApp.made.append(self)

    def __getattr__(self, name):
        if name.startswith("on_"):
            return lambda hook: self.hooks.__setitem__(name, hook)
        raise AttributeError(name)

    def set_args(self, args):
        self.args = args

    def run(self):
        self.runs += 1


@pytest.fixture
def fake_app(monkeypatch):
    _FakeApp.made = []
    monkeypatch.setattr(velopack, "App", _FakeApp)
    return _FakeApp


def test_a_checkout_never_reaches_velopack(monkeypatch, fake_app):
    monkeypatch.delattr(sys, "frozen", raising=False)

    install_hooks.run_velopack(["BartleBot.exe", "--veloapp-install", "1.0.0"])

    assert fake_app.made == []


def test_a_packaged_build_registers_every_hook_and_runs(monkeypatch, fake_app):
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    install_hooks.run_velopack(["BartleBot.exe", "--veloapp-install", "1.0.0"])

    (app,) = fake_app.made
    assert app.hooks == dict(install_hooks.HOOKS)
    assert app.args == ["BartleBot.exe", "--veloapp-install", "1.0.0"]
    assert app.runs == 1


def test_the_hooks_cover_velopacks_four_fast_callbacks():
    fast = {name for name in dir(velopack.App) if name.endswith("_fast_callback")}

    assert {method for method, _hook in install_hooks.HOOKS} == fast


@pytest.fixture
def machine(monkeypatch, tmp_path):
    """An installed BartleBot in ``tmp_path``, with the registry under scratch keys and Explorer's notice counted."""
    winreg = pytest.importorskip("winreg")
    scratch = rf"Software\Bartleby-test-{uuid.uuid4().hex}"
    monkeypatch.setattr(install_key, "REGISTRY_KEY", rf"{scratch}\Vendor")
    monkeypatch.setattr(file_types, "CLASSES_KEY", rf"{scratch}\Classes")
    notices = []
    monkeypatch.setattr(file_types, "notify", lambda: notices.append(1))
    root = tmp_path / "BartleBot"
    shipped = root / "current" / "_internal"
    shipped.mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(root / "current" / "BartleBot.exe"))
    monkeypatch.setattr(install_hooks, "install_dir", lambda: shipped)
    # The update check's staging folder is under it, so a hook never clears the machine's own.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    yield SimpleNamespace(root=root, shipped=shipped, stub=root / "BartleBot.exe", notices=notices)
    install_key.delete_tree(winreg.HKEY_CURRENT_USER, scratch)


ALL_SIX = (".bbot", ".bbcombat", ".bbsubgraph", ".bbduel", ".bbplayer", ".bbdeck")


def _commands() -> dict[str, str | None]:
    return {t.extension: file_types.registered_command(t) for t in install_hooks.own_types() + file_types.SHARED_TYPES}


def test_bartlebots_own_types_are_its_three():
    assert [(t.extension, t.prog_id, t.description) for t in install_hooks.own_types()] == [
        (".bbot", "BartleBot.Bot", "BartleBot bot"),
        (".bbcombat", "BartleBot.BotCombatStrategy", "BartleBot Bot Combat Strategy"),
        (".bbsubgraph", "BartleBot.Subgraph", "BartleBot subgraph"),
    ]


def test_the_executable_is_velopacks_stub_beside_current(tmp_path):
    assert install_hooks.installed_executable(tmp_path / "current" / "BartleBot.exe") == tmp_path / "BartleBot.exe"


def test_a_build_outside_current_names_its_own_exe(tmp_path):
    assert (
        install_hooks.installed_executable(tmp_path / "dist" / "BartleBot.exe") == tmp_path / "dist" / "BartleBot.exe"
    )


def test_the_install_dir_is_where_the_shipped_set_is():
    assert (install_hooks.install_dir() / SET_FILE).is_file()


def test_install_writes_the_key_and_registers_all_six_types(machine):
    install_hooks.after_install("1.0.0")

    assert install_key.read_all() == [InstallRecord(PRODUCT, machine.shipped, machine.stub)]
    assert _commands() == dict.fromkeys(ALL_SIX, f'"{machine.stub}" "%1"')
    assert machine.notices == [1]


@pytest.mark.parametrize("hook", [h for _method, h in install_hooks.HOOKS])
def test_a_hook_that_fails_prints_why_and_never_fails_velopacks_step(monkeypatch, capsys, hook):
    """A registry write that fails must not fail the install, update or uninstall around it (#154)."""

    def _fails(*_args, **_kwargs):
        raise OSError("the registry refused")

    for name in ("record_install", "remove_install", "_clear_staged_update"):
        monkeypatch.setattr(install_hooks, name, _fails)

    hook("1.0.0")

    if hook is not install_hooks.before_update:  # it does nothing, so nothing can fail
        assert "the registry refused" in capsys.readouterr().err


def test_update_writes_the_same_again(machine):
    install_hooks.after_install("1.0.0")
    install_key.delete(PRODUCT)  # something removed it

    install_hooks.after_update("1.0.1")

    assert install_key.read_all() == [InstallRecord(PRODUCT, machine.shipped, machine.stub)]
    assert set(_commands().values()) == {f'"{machine.stub}" "%1"'}


def _staged_update():
    from bartlebot.updates.feed import FEED_NAME, staging_dir  # noqa: PLC0415

    staging_dir().mkdir(parents=True)
    (staging_dir() / FEED_NAME).write_text("{}")
    (staging_dir() / "BartleBot-1.0.1-full.nupkg").write_bytes(b"package")
    return staging_dir()


@pytest.mark.parametrize("hook", ["after_update", "before_uninstall"])
def test_update_and_uninstall_remove_the_staged_update(machine, hook):
    """It is spent once applied, can hold a whole package, and is never the user's data."""
    staged = _staged_update()

    getattr(install_hooks, hook)("1.0.1")

    assert not staged.exists()


def test_before_update_does_nothing(machine):
    install_hooks.before_update("1.0.0")

    assert install_key.read_all() == []
    assert machine.notices == []


def test_uninstall_alone_removes_the_key_and_all_six_types(machine):
    install_hooks.after_install("1.0.0")

    install_hooks.before_uninstall("1.0.0")

    assert install_key.read_all() == []
    assert _commands() == dict.fromkeys(ALL_SIX)
    assert machine.notices == [1, 1]


def test_uninstall_hands_the_shared_types_to_the_other_product(machine, tmp_path):
    buddy = tmp_path / "BartleBuddy" / "BartleBuddy.exe"
    buddy.parent.mkdir()
    buddy.write_bytes(b"")
    install_key.write("BartleBuddy", buddy.parent, buddy)
    install_hooks.after_install("1.0.0")

    install_hooks.before_uninstall("1.0.0")

    assert [r.product for r in install_key.read_all()] == ["BartleBuddy"]
    commands = _commands()
    assert {commands[ext] for ext in (".bbduel", ".bbplayer", ".bbdeck")} == {f'"{buddy}" "%1"'}
    assert {commands[ext] for ext in (".bbot", ".bbcombat", ".bbsubgraph")} == {None}


def test_uninstall_removes_the_shared_types_when_the_other_products_exe_is_gone(machine, tmp_path):
    gone = tmp_path / "BartleBuddy" / "BartleBuddy.exe"
    install_key.write("BartleBuddy", gone.parent, gone)
    install_hooks.after_install("1.0.0")

    install_hooks.before_uninstall("1.0.0")

    assert _commands() == dict.fromkeys(ALL_SIX)


def test_the_hooks_run_well_inside_velopacks_limit(machine):
    started = time.perf_counter()
    install_hooks.after_install("1.0.0")
    install_hooks.after_update("1.0.1")
    install_hooks.before_uninstall("1.0.1")

    assert time.perf_counter() - started < install_hooks.UPDATE_LIMIT_SECONDS / 10


def _run(code: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, cwd=project_root())


def test_a_checkout_does_not_even_import_velopack():
    done = _run(
        "import sys\n"
        "from bartlebot.packaging.install_hooks import run_velopack\n"
        "run_velopack()\n"
        "print('velopack' in sys.modules)\n"
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "False"
    assert done.stderr == ""


def test_a_packaged_build_that_is_not_installed_still_starts():
    """CI's build is never installed: Velopack finds nothing, and ``run()`` returns."""
    done = _run(
        "import sys\n"
        "sys.frozen = True\n"
        "from bartlebot.packaging.install_hooks import run_velopack\n"
        "run_velopack(['BartleBot.exe'])\n"
        "print('started')\n"
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "started"


def test_bartlebot_py_hands_the_launch_to_velopack_before_anything_else():
    tree = ast.parse((project_root() / "bartlebot.py").read_text(encoding="utf-8"))
    (main,) = [node for node in tree.body if isinstance(node, ast.If)]

    first = main.body[0]
    assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)
    assert ast.unparse(first.value) == "run_velopack()"


def test_the_suite_never_reaches_the_machines_registry():
    """``conftest`` points both roots at a throwaway key, so an unpatched hook call writes nothing real."""
    assert not install_key.REGISTRY_KEY.endswith(rf"Software\{VENDOR}")
    assert file_types.CLASSES_KEY != r"Software\Classes"
