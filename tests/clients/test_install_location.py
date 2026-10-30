"""BartleBot hands wizwalker the game folder at startup (#227).

``apply_install`` and its own tests moved to core's ``bartleby/runtime/clients/`` in Stage
11 of #167; this is when BartleBot calls it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bartlebot import app_controller
from bartlebot.app_controller import AppController


class _Stop(Exception):
    """Raised by the stand-in for the first thing after the install is applied."""


def test_the_app_applies_it_at_startup_before_anything_asks_wizwalker(monkeypatch):
    """Before the handler is made, and so before the window, the launcher or a client exists."""
    calls: list[str] = []
    monkeypatch.setattr(app_controller, "apply_install", lambda: calls.append("install"))
    monkeypatch.setattr(app_controller, "apply_cache_dir", lambda: None)
    monkeypatch.setattr(app_controller, "install_app_icon", lambda _app: None)
    monkeypatch.setattr(AppController, "_apply_theme", lambda _self: None)

    def handler():
        calls.append("handler")
        raise _Stop

    monkeypatch.setattr(app_controller, "WizSprinter", handler)
    app = SimpleNamespace(
        setOrganizationName=lambda _n: None, setApplicationName=lambda _n: None, setStyle=lambda _s: None
    )

    with pytest.raises(_Stop):
        AppController(app)

    assert calls == ["install", "handler"]
