"""Tests for the client sidebar's poll-rate policy.

The Players tab polls every client's zone and XYZ on a timer, and WizWalker's
memory reads are synchronous on the event loop thread — so how often it polls is
a question about contention with a running bot, not just about GUI smoothness.
The rule lives in a pure function precisely so it can be pinned down here without
a QApplication.
"""

from bartlebot.gui.sidebar.sections.client_section import client_refresh_interval
from bartlebot.nodes.timing import (
    CLIENT_REFRESH_INTERVAL_HIDDEN,
    CLIENT_REFRESH_INTERVAL_IDLE,
    CLIENT_REFRESH_INTERVAL_RUN,
)


class TestVisible:
    def test_idle_polls_fastest(self) -> None:
        assert client_refresh_interval(bot_running=False, players_visible=True) == CLIENT_REFRESH_INTERVAL_IDLE

    def test_a_running_bot_backs_off(self) -> None:
        assert client_refresh_interval(bot_running=True, players_visible=True) == CLIENT_REFRESH_INTERVAL_RUN


class TestHidden:
    def test_hidden_and_idle_uses_the_hidden_rate(self) -> None:
        assert client_refresh_interval(bot_running=False, players_visible=False) == CLIENT_REFRESH_INTERVAL_HIDDEN

    def test_hidden_wins_over_running(self) -> None:
        """Visibility is checked before bot state, and this is the case that proves it.

        Hidden-and-running is the highest-contention, lowest-value combination:
        the bot needs the event loop and nothing on screen is reading the result.
        """
        assert client_refresh_interval(bot_running=True, players_visible=False) == CLIENT_REFRESH_INTERVAL_HIDDEN


def test_intervals_are_ordered_slowest_when_least_useful() -> None:
    """Guards a future edit that would make a hidden tab poll faster than a visible one."""
    assert CLIENT_REFRESH_INTERVAL_HIDDEN >= CLIENT_REFRESH_INTERVAL_RUN > CLIENT_REFRESH_INTERVAL_IDLE
