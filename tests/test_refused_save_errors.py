"""A refused save or release draws the page's empty required fields red (#142).

Until then an empty required field is gray: it turns red when it is touched and left
empty, or when something is refused for it. Run is never refused -- it is disabled while
the bot has errors -- so the two refusals are a definition's save and a template's release.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from PyQt6.QtWidgets import QMessageBox

from bartlebot.app_controller import AppController


def _controller(errors):
    fake = SimpleNamespace(_window=MagicMock())
    fake._window.bot_canvas.validation_errors.return_value = errors
    return fake


def test_a_refused_save_draws_the_missing_fields_red(qapp, monkeypatch):
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)
    fake = _controller(["Required param 'zone' is empty"])

    assert AppController._refused_for_errors(fake, SimpleNamespace(name="Sync players"), "Save to Library")

    fake._window.bot_canvas.param_panel.show_required_errors.assert_called_once()


def test_a_save_with_nothing_wrong_draws_nothing(qapp):
    fake = _controller([])

    assert not AppController._refused_for_errors(fake, SimpleNamespace(name="Sync players"), "Save to Library")

    fake._window.bot_canvas.param_panel.show_required_errors.assert_not_called()


def test_a_refused_release_draws_the_missing_fields_red(qapp, monkeypatch):
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)
    fake = _controller([])

    AppController._show_release_refused(fake, "Dungeon Farmer", ["Required param 'zone' is empty"])

    fake._window.bot_canvas.param_panel.show_required_errors.assert_called_once()
