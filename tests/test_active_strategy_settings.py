"""Tests for the remembered active-BotCombatStrategy map in SettingsManager.

The map grows one entry per bot ever opened and survives deleting the strategy
an entry points at, so it accumulates dead rows.  ``prune_active_strategies``
clears those out, and these tests pin down the one thing it must never do:
prune by bot id.

Each test builds a private SettingsManager against ``tmp_path`` rather than
touching the ``%APPDATA%`` singleton.
"""

from __future__ import annotations

import json

import pytest

from bartlebot import settings_manager as sm_mod
from bartlebot.settings_manager import SettingsManager


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """A SettingsManager writing to tmp_path, isolated from the real singleton."""
    monkeypatch.setattr(sm_mod, "_appdata_dir", lambda: tmp_path)
    return SettingsManager()


# ---------------------------------------------------------------------------
# get / set round-trip
# ---------------------------------------------------------------------------


def test_set_and_get_round_trip(settings):
    settings.set_active_strategy("bot-1", "strat-1")
    assert settings.get_active_strategy("bot-1") == "strat-1"


def test_get_returns_none_for_an_unknown_bot(settings):
    assert settings.get_active_strategy("nobody") is None


def test_set_none_clears_the_entry(settings):
    settings.set_active_strategy("bot-1", "strat-1")
    settings.set_active_strategy("bot-1", None)
    assert settings.get_active_strategy("bot-1") is None


def test_set_persists_to_disk(settings, tmp_path):
    settings.set_active_strategy("bot-1", "strat-1")
    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["active_bot_strategies"] == {"bot-1": "strat-1"}


# ---------------------------------------------------------------------------
# prune_active_strategies
# ---------------------------------------------------------------------------


def test_prune_removes_entries_whose_strategy_is_gone(settings):
    settings.set_active_strategy("bot-1", "alive")
    settings.set_active_strategy("bot-2", "deleted")

    removed = settings.prune_active_strategies({"alive"})

    assert removed == 1
    assert settings.get_active_strategy("bot-1") == "alive"
    assert settings.get_active_strategy("bot-2") is None


def test_prune_keeps_entries_for_bots_it_has_never_heard_of(settings):
    """A bot saved outside bots/ still deserves its binding.

    This is the whole reason pruning keys on the strategy id: the set of known
    bots is not knowable, but the set of known strategies is.
    """
    settings.set_active_strategy("bot-stored-on-another-drive", "alive")

    removed = settings.prune_active_strategies({"alive"})

    assert removed == 0
    assert settings.get_active_strategy("bot-stored-on-another-drive") == "alive"


def test_prune_returns_zero_when_nothing_is_dead(settings):
    settings.set_active_strategy("bot-1", "alive")
    assert settings.prune_active_strategies({"alive", "other"}) == 0


def test_prune_on_an_empty_map_is_a_noop(settings):
    assert settings.prune_active_strategies({"alive"}) == 0


def test_prune_with_no_known_strategies_clears_everything(settings):
    settings.set_active_strategy("bot-1", "a")
    settings.set_active_strategy("bot-2", "b")

    assert settings.prune_active_strategies(set()) == 2
    assert settings.get("active_bot_strategies") == {}


def test_prune_persists_to_disk(settings, tmp_path):
    settings.set_active_strategy("bot-1", "alive")
    settings.set_active_strategy("bot-2", "dead")

    settings.prune_active_strategies({"alive"})

    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["active_bot_strategies"] == {"bot-1": "alive"}


def test_prune_does_not_rewrite_the_file_when_nothing_changes(settings, tmp_path):
    settings.set_active_strategy("bot-1", "alive")
    path = tmp_path / "settings.json"
    before = path.stat().st_mtime_ns

    settings.prune_active_strategies({"alive"})

    assert path.stat().st_mtime_ns == before


def test_prune_accepts_any_iterable(settings):
    """Callers pass a set comprehension over loaded strategies; a list must work too."""
    settings.set_active_strategy("bot-1", "alive")
    assert settings.prune_active_strategies(["alive"]) == 0


def test_prune_is_idempotent(settings):
    settings.set_active_strategy("bot-1", "alive")
    settings.set_active_strategy("bot-2", "dead")

    first = settings.prune_active_strategies({"alive"})
    second = settings.prune_active_strategies({"alive"})

    assert (first, second) == (1, 0)
