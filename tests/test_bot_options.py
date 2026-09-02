"""Where a bot option's value comes from.

Four levels — the user's own value, the author's declaration, the app setting,
the node default — and the tests that matter are the ones pinning down which
wins, and what counts as "I have no value here". A stored ``[]`` or ``false`` is
an answer; only an absent key is silence. Confusing those two is the bug this
whole split exists to remove, and ``exclude_none=True`` in ``BotConfig.save``
sets the same trap one file over.

The store half runs against a private SettingsManager in ``tmp_path``, the
policy half against nothing at all — ``resolve_option`` takes plain values, so
precedence needs no settings file, no bot and no clients to exercise.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bartlebot import settings_manager as sm_mod
from bartlebot.app_controller import AppController
from bartlebot.bot.machine import BotMachine
from bartlebot.bot.schema import BotConfig, BotOptionModel
from bartlebot.bot_options import (
    SOURCE_APP_SETTING,
    SOURCE_BOT_FILE,
    SOURCE_NODE_DEFAULT,
    SOURCE_OVERRIDE,
    UNSET,
    resolve_option,
    value_matches_type,
)
from bartlebot.settings_manager import SettingsManager


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """A SettingsManager writing to tmp_path, isolated from the real singleton."""
    monkeypatch.setattr(sm_mod, "_appdata_dir", lambda: tmp_path)
    return SettingsManager()


def _config(**overrides) -> BotConfig:
    data = {
        "id": "test-bot",
        "name": "Test Bot",
        "initial": "s0",
        "recovery_state": "s0",
        "states": [{"id": "s0", "node_type": "interact"}],
        "transitions": [],
    }
    data.update(overrides)
    return BotConfig.model_validate(data)


def _machine(config: BotConfig) -> BotMachine:
    return BotMachine(
        clients=[],
        config=config.to_machine_config(),
        node_registry={"interact": AsyncMock(return_value="SUCCESS")},
        bot_config=config,
    )


# ---------------------------------------------------------------------------
# resolve_option — precedence
# ---------------------------------------------------------------------------


def test_the_users_own_value_wins():
    got = resolve_option("Safety Delay", override=1.0, declaration=5.0, app_value=3.0, spec_default=3.0)

    assert (got.value, got.source) == (1.0, SOURCE_OVERRIDE)


def test_a_declaration_beats_the_app_setting():
    got = resolve_option("Safety Delay", declaration=5.0, app_value=3.0, spec_default=3.0)

    assert (got.value, got.source) == (5.0, SOURCE_BOT_FILE)


def test_no_opinion_in_the_file_falls_through_to_the_app_setting():
    got = resolve_option("Safety Delay", declaration=None, app_value=3.0, spec_default=9.0)

    assert (got.value, got.source) == (3.0, SOURCE_APP_SETTING)


def test_an_option_with_no_app_setting_falls_to_the_node_default():
    got = resolve_option("Skip to boss", declaration=None, spec_default=False)

    assert (got.value, got.source) == (False, SOURCE_NODE_DEFAULT)


def test_an_unset_option_resolves_to_the_declaration_and_stores_nothing(settings):
    """The common case: a bot the user has never touched."""
    got = resolve_option(
        "Safety Delay",
        override=settings.get_option_override("test-bot", "Safety Delay"),
        declaration=5.0,
        spec_default=3.0,
    )

    assert got.source == SOURCE_BOT_FILE
    assert settings.get("bot_option_overrides") == {}


# ---------------------------------------------------------------------------
# resolve_option — falsy values are answers
# ---------------------------------------------------------------------------


def test_an_empty_player_list_override_is_an_answer_not_silence():
    got = resolve_option("Level Scaling Players", override=[], declaration=[2, 3], declared_type="player_list")

    assert (got.value, got.source) == ([], SOURCE_OVERRIDE)


def test_a_false_override_is_an_answer_not_silence():
    got = resolve_option("Skip to boss", override=False, declaration=True, declared_type="bool")

    assert (got.value, got.source) == (False, SOURCE_OVERRIDE)


def test_a_false_declaration_is_an_answer_not_silence():
    got = resolve_option("Skip to boss", declaration=False, app_value=True, spec_default=True)

    assert (got.value, got.source) == (False, SOURCE_BOT_FILE)


# ---------------------------------------------------------------------------
# resolve_option — a stale override
# ---------------------------------------------------------------------------


def test_an_override_of_the_wrong_type_is_discarded():
    """The author retyped the option; the stored value is now meaningless, and
    nothing downstream would raise on it — it would just reach the node."""
    got = resolve_option("Skip to boss", override="yes", declaration=True, declared_type="bool")

    assert (got.value, got.source) == (True, SOURCE_BOT_FILE)


def test_an_int_override_survives_a_float_declaration():
    """Three NodeSpecs ship Safety Delay as ``3``, and JSON cannot tell 3 from 3.0."""
    got = resolve_option("Safety Delay", override=3, declaration=5.0, declared_type="float")

    assert (got.value, got.source) == (3, SOURCE_OVERRIDE)


def test_an_override_of_an_untyped_option_is_kept():
    """Discarding on a type nobody declared would throw away a good value."""
    got = resolve_option("Whatever", override="anything", declaration=None, spec_default=1)

    assert got.source == SOURCE_OVERRIDE


@pytest.mark.parametrize(
    ("value", "declared_type", "expected"),
    [
        (True, "bool", True),
        (1, "bool", False),  # bool is an int subclass; the reverse must not hold
        (3, "int", True),
        (True, "int", False),
        (3, "float", True),
        (3.5, "float", True),
        ("3", "float", False),
        ("x", "str", True),
        ([1, 2], "player_list", True),
        ([1, "2"], "player_list", False),
        ("anything", None, True),
    ],
)
def test_value_matches_type(value, declared_type, expected):
    assert value_matches_type(value, declared_type) is expected


# ---------------------------------------------------------------------------
# The override store
# ---------------------------------------------------------------------------


def test_an_absent_override_reads_as_unset(settings):
    assert settings.get_option_override("test-bot", "Safety Delay") is UNSET


def test_set_and_get_round_trip(settings):
    settings.set_option_override("test-bot", "Safety Delay", 1.5)

    assert settings.get_option_override("test-bot", "Safety Delay") == 1.5


@pytest.mark.parametrize("value", [[], False, 0, ""])
def test_a_falsy_value_round_trips_and_is_distinct_from_unset(settings, value):
    settings.set_option_override("test-bot", "Opt", value)

    assert settings.get_option_override("test-bot", "Opt") == value
    assert settings.get_option_override("test-bot", "Opt") is not UNSET


def test_set_persists_to_disk(settings, tmp_path):
    settings.set_option_override("test-bot", "Safety Delay", 1.5)
    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))

    assert on_disk["bot_option_overrides"] == {"test-bot": {"Safety Delay": 1.5}}


def test_dropping_an_override_makes_it_unset_again(settings):
    settings.set_option_override("test-bot", "Safety Delay", 1.5)
    settings.drop_option_override("test-bot", "Safety Delay")

    assert settings.get_option_override("test-bot", "Safety Delay") is UNSET


def test_dropping_the_last_override_drops_the_bot_row(settings):
    settings.set_option_override("test-bot", "Safety Delay", 1.5)
    settings.drop_option_override("test-bot", "Safety Delay")

    assert settings.get("bot_option_overrides") == {}


def test_dropping_one_override_leaves_the_others(settings):
    settings.set_option_override("test-bot", "Safety Delay", 1.5)
    settings.set_option_override("test-bot", "Level Scaling Players", [2, 3])
    settings.drop_option_override("test-bot", "Safety Delay")

    assert settings.get("bot_option_overrides") == {"test-bot": {"Level Scaling Players": [2, 3]}}


def test_dropping_an_override_that_was_never_set_is_harmless(settings):
    settings.drop_option_override("test-bot", "Safety Delay")

    assert settings.get("bot_option_overrides") == {}


def test_two_bots_keep_separate_values(settings):
    settings.set_option_override("bot-a", "Safety Delay", 1.0)
    settings.set_option_override("bot-b", "Safety Delay", 9.0)

    assert settings.get_option_override("bot-a", "Safety Delay") == 1.0
    assert settings.get_option_override("bot-b", "Safety Delay") == 9.0


# ---------------------------------------------------------------------------
# machine.get_option — the four levels, wired together
# ---------------------------------------------------------------------------


def test_the_machine_prefers_the_users_stored_value():
    SettingsManager.instance().set_option_override("test-bot", "Safety Delay", 1.0)
    config = _config(options=[BotOptionModel(name="Safety Delay", type="float", default=5.0)])

    assert _machine(config).get_option("Safety Delay") == 1.0


def test_the_machine_prefers_a_declaration_over_the_app_setting():
    SettingsManager.instance().set_general("default_safety_delay", 7.5)
    config = _config(options=[BotOptionModel(name="Safety Delay", type="float", default=5.0)])

    assert _machine(config).get_option("Safety Delay") == 5.0


def test_a_declaration_with_no_opinion_yields_to_the_app_setting():
    """What replaces the load-time rewrite: the file is left alone, and Safety
    Delay — a property of the machine, not the bot — follows the user."""
    SettingsManager.instance().set_general("default_safety_delay", 7.5)
    config = _config(options=[BotOptionModel(name="Safety Delay", type="float", default=None)])

    assert _machine(config).get_option("Safety Delay") == 7.5


def test_an_option_the_bot_never_declares_still_resolves_from_its_node():
    """Phase 2 stops writing injected declarations to the file at all."""
    config = _config(options=[])

    assert _machine(config).get_option("Safety Delay") == 3.0


def test_an_option_nothing_declares_falls_back_to_the_callers_default():
    """``_flag_<name>`` asking about an option that has been deleted."""
    config = _config(options=[])

    assert _machine(config).get_option("No Such Option", default=False) is False


def test_a_stale_override_does_not_reach_the_node():
    SettingsManager.instance().set_option_override("test-bot", "Skip to boss", "yes")
    config = _config(options=[BotOptionModel(name="Skip to boss", type="bool", default=True)])

    assert _machine(config).get_option("Skip to boss") is True


def test_the_resolution_is_logged_once_per_option(caplog):
    config = _config(options=[BotOptionModel(name="Safety Delay", type="float", default=5.0)])
    machine = _machine(config)

    with caplog.at_level("INFO", logger="bartlebot.bot.machine"):
        for _ in range(5):
            machine.get_option("Safety Delay")

    assert len([r for r in caplog.records if "Safety Delay" in r.message]) == 1


# ---------------------------------------------------------------------------
# A bot id is what every stored value hangs off
# ---------------------------------------------------------------------------


def test_a_bot_with_no_id_fails_validation():
    """Minting one silently would give the file a fresh identity on every load,
    and take every value the user stored against it."""
    data = {
        "name": "Test Bot",
        "initial": "s0",
        "recovery_state": "s0",
        "states": [{"id": "s0", "node_type": "interact"}],
        "transitions": [],
    }

    with pytest.raises(ValueError, match="id"):
        BotConfig.model_validate(data)


def test_a_bot_with_a_blank_id_fails_validation():
    with pytest.raises(ValueError, match="id"):
        BotConfig.model_validate(
            {
                "id": "  ",
                "name": "Test Bot",
                "initial": "s0",
                "recovery_state": "s0",
                "states": [{"id": "s0", "node_type": "interact"}],
                "transitions": [],
            }
        )


def test_save_as_mints_a_new_id(tmp_path, monkeypatch):
    """Two files sharing an id share every value the user stores against it.

    Driven through the real method with a stand-in ``self`` — it touches the
    window only to relabel it, and standing up a MainWindow would test Qt.
    """
    from PyQt6.QtWidgets import QFileDialog  # noqa: PLC0415

    dest = tmp_path / "fork.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(dest), "")))
    config = _config()
    original_id = config.id
    controller = SimpleNamespace(
        _loaded_config=config,
        _last_save_path=None,
        _has_unsaved_changes=True,
        _delete_autosave=lambda *a: None,
        _window=SimpleNamespace(
            set_bot_name=lambda *a: None,
            update_recent_menu=lambda *a: None,
            bot_canvas=SimpleNamespace(refresh_placeholder=lambda: None),
        ),
    )

    AppController._save_bot_as(controller)

    assert config.id != original_id
    assert BotConfig.load(dest).id == config.id


def test_a_new_bot_is_born_with_an_id():
    controller = SimpleNamespace(
        _delete_autosave=lambda *a: None,
        _has_unsaved_changes=False,
        _loaded_config=None,
        _last_save_path=None,
        _start_enabled=lambda: False,
        _window=SimpleNamespace(
            set_bot_name=lambda *a: None,
            set_save_enabled=lambda *a: None,
            set_close_enabled=lambda *a: None,
            set_start_enabled=lambda *a: None,
            left_sidebar=SimpleNamespace(set_config=lambda *a: None),
            bot_canvas=SimpleNamespace(setup=lambda *a: None, enter_edit_mode=lambda: None),
        ),
    )

    AppController._new_bot(controller)

    assert controller._loaded_config.id
