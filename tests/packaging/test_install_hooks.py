"""Velopack's part of startup: first of all in a packaged build, and never in a checkout."""

from __future__ import annotations

import ast
import subprocess
import sys

import pytest
import velopack

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


@pytest.mark.parametrize("hook", [hook for _method, hook in install_hooks.HOOKS])
def test_each_hook_returns_without_doing_anything(hook):
    assert hook("1.0.0") is None


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
