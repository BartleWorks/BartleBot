"""The app-wide art_changed signal, and following it for a widget's lifetime (#239)."""

from __future__ import annotations

import logging

from PyQt6 import sip
from PyQt6.QtWidgets import QWidget

from bartlebot.gui.art_signal import announce_art_changed, art_signal, follow_art


def test_there_is_one_signal_for_the_app(qapp):
    assert art_signal() is art_signal()


def test_a_follower_redraws_on_each_announcement(qapp):
    owner = QWidget()
    calls: list[None] = []
    follow_art(owner, lambda: calls.append(None))

    announce_art_changed()
    announce_art_changed()

    assert len(calls) == 2


def test_a_destroyed_owner_is_never_redrawn(qapp):
    owner = QWidget()
    calls: list[None] = []
    follow_art(owner, lambda: calls.append(None))

    sip.delete(owner)
    announce_art_changed()

    assert calls == []


def test_a_redraw_that_raises_is_logged_and_the_others_still_run(qapp, caplog):
    first, second = QWidget(), QWidget()
    calls: list[str] = []

    def broken() -> None:
        raise RuntimeError("boom")

    follow_art(first, broken)
    follow_art(second, lambda: calls.append("second"))

    with caplog.at_level(logging.ERROR, logger="bartlebot.gui.art_signal"):
        announce_art_changed()

    assert calls == ["second"]
    assert "after the game art changed raised" in caplog.text
