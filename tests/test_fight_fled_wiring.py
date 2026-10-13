"""How a flee reaches the Combat tab (epic #201, Stage 7).

``AppController._on_machine_fight_fled``, bound to a stand-in as the other controller tests
do: the machine's ``on_fight_fled`` callback, with the bot id bound when the machine is built.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bartlebot.app_controller import AppController
from bartlebot.combat.combat_resolution import Level
from bartlebot.combat.flee_marks import FleeMarks


@pytest.fixture
def controller():
    refreshes: list[str] = []
    tab = SimpleNamespace(refresh_source=lambda: refreshes.append("refresh"))
    ns = SimpleNamespace(_flee_marks=FleeMarks(), _window=SimpleNamespace(left_sidebar=SimpleNamespace(combat_tab=tab)))
    ns.fled = AppController._on_machine_fight_fled.__get__(ns)
    ns.refreshes = refreshes
    return ns


def test_a_flee_on_a_default_is_counted_and_redrawn(controller):
    controller.fled("bot-1", "boss", Level.SHIPPED_DEFAULT)
    controller.fled("bot-1", "boss", Level.YOUR_DEFAULT)

    assert controller._flee_marks.counts("bot-1") == {"boss": 2}
    assert controller.refreshes == ["refresh", "refresh"]


def test_a_flee_on_anything_else_changes_nothing(controller):
    controller.fled("bot-1", "boss", Level.PICK)

    assert controller._flee_marks.counts("bot-1") == {}
    assert controller.refreshes == []


def test_the_mark_lands_on_the_bot_that_fled(controller):
    """The id is the running bot's, bound when the machine was built, not whichever is open now."""
    controller.fled("bot-ran", "boss", Level.SHIPPED_DEFAULT)

    assert controller._flee_marks.counts("bot-ran") == {"boss": 1}
    assert controller._flee_marks.counts("bot-open") == {}
