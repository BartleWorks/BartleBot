"""Tests for BotCombatProfile, DuelProfile, CombatBehavior models and DSL resolution."""

from bartlebot.bot.combat_profile import BotCombatProfile, CombatBehavior, DuelProfile
from bartlebot.bot.schema import BotConfig
from bartlebot.defaults.combat_config import BARTLEBOT_DEFAULT_COMBAT_CONFIG
from bartlebot.nodes.combat_runner import _resolve_duel_profile_dsl

# ---------------------------------------------------------------------------
# CombatBehavior
# ---------------------------------------------------------------------------


def test_combat_behavior_defaults():
    b = CombatBehavior()
    assert b.players == []
    assert b.dsl == ""


def test_combat_behavior_with_players():
    b = CombatBehavior(players=[1, 3], dsl="any<blade>")
    assert b.players == [1, 3]
    assert b.dsl == "any<blade>"


# ---------------------------------------------------------------------------
# DuelProfile
# ---------------------------------------------------------------------------


def test_duel_profile_defaults():
    p = DuelProfile(name="test")
    assert p.use_bartlebot_default is True
    assert p.behaviors == []


def test_duel_profile_resolve_dsl_uses_default_when_flagged():
    p = DuelProfile(name="test", use_bartlebot_default=True, behaviors=[CombatBehavior(players=[1], dsl="hit")])
    assert p.resolve_dsl_for_client(0) is None


def test_duel_profile_resolve_dsl_matches_player_slot():
    p = DuelProfile(
        name="test",
        use_bartlebot_default=False,
        behaviors=[
            CombatBehavior(players=[1], dsl="fire hit"),
            CombatBehavior(players=[2, 3, 4], dsl="blade support"),
        ],
    )
    assert p.resolve_dsl_for_client(0) == "fire hit"  # P1 → index 0
    assert p.resolve_dsl_for_client(1) == "blade support"  # P2 → index 1
    assert p.resolve_dsl_for_client(2) == "blade support"  # P3 → index 2
    assert p.resolve_dsl_for_client(3) == "blade support"  # P4 → index 3


def test_duel_profile_resolve_dsl_falls_back_to_first_behavior():
    p = DuelProfile(
        name="test",
        use_bartlebot_default=False,
        behaviors=[CombatBehavior(players=[1], dsl="solo")],
    )
    # P3 is not assigned; should fall back to first behavior
    assert p.resolve_dsl_for_client(2) == "solo"


def test_duel_profile_resolve_dsl_returns_none_when_no_behaviors():
    p = DuelProfile(name="test", use_bartlebot_default=False, behaviors=[])
    assert p.resolve_dsl_for_client(0) is None


# ---------------------------------------------------------------------------
# BotCombatProfile
# ---------------------------------------------------------------------------


def test_bot_combat_profile_always_has_default_duel_profile():
    p = BotCombatProfile(name="My Profile")
    assert "default" in p.duel_profiles


def test_bot_combat_profile_model_validator_adds_default_if_missing():
    data = {"name": "Test", "duel_profiles": {"boss": {"name": "boss"}}}
    p = BotCombatProfile.model_validate(data)
    assert "default" in p.duel_profiles


def test_bot_combat_profile_get_duel_profile_returns_named():
    p = BotCombatProfile(
        name="X", duel_profiles={"default": DuelProfile(name="default"), "boss": DuelProfile(name="boss")}
    )
    assert p.get_duel_profile("boss").name == "boss"


def test_bot_combat_profile_get_duel_profile_falls_back_to_default():
    p = BotCombatProfile(name="X")
    result = p.get_duel_profile("nonexistent")
    assert result.name == "default"


def test_bot_combat_profile_save_load_roundtrip(tmp_path):
    profile = BotCombatProfile(
        name="Fire Team",
        duel_profiles={
            "default": DuelProfile(
                name="default",
                use_bartlebot_default=False,
                behaviors=[CombatBehavior(players=[1], dsl="any<hit>")],
            )
        },
    )
    path = tmp_path / "profile.json"
    profile.save(path)
    loaded = BotCombatProfile.load(path)
    assert loaded.name == "Fire Team"
    assert not loaded.duel_profiles["default"].use_bartlebot_default
    assert loaded.duel_profiles["default"].behaviors[0].dsl == "any<hit>"


# ---------------------------------------------------------------------------
# BotConfig — new fields
# ---------------------------------------------------------------------------


VALID_DATA = {
    "name": "Test Bot",
    "initial": "state_a",
    "states": [{"id": "state_a", "node_type": "enter_zone", "params": {}}],
    "transitions": [{"trigger": "advance", "source": "state_a", "dest": "state_a"}],
}


def test_botconfig_combat_profiles_defaults_to_empty():
    config = BotConfig.model_validate(VALID_DATA)
    assert config.combat_profiles == []
    assert config.active_combat_profile_name is None


def test_botconfig_active_combat_profile_returns_none_when_empty():
    config = BotConfig.model_validate(VALID_DATA)
    assert config.active_combat_profile is None


def test_botconfig_active_combat_profile_returns_named_profile():
    profile = BotCombatProfile(name="Storm Team")
    config = BotConfig.model_validate(VALID_DATA)
    config.combat_profiles = [profile]
    config.active_combat_profile_name = "Storm Team"
    assert config.active_combat_profile is profile


def test_botconfig_active_combat_profile_returns_none_for_missing_name():
    profile = BotCombatProfile(name="Fire Team")
    config = BotConfig.model_validate(VALID_DATA)
    config.combat_profiles = [profile]
    config.active_combat_profile_name = "Nonexistent"
    assert config.active_combat_profile is None


def test_botconfig_preserves_deprecated_fields():
    data = {**VALID_DATA, "default_combat_config": "any<hit>", "use_bartlebot_default_combat_config": False}
    config = BotConfig.model_validate(data)
    assert config.default_combat_config == "any<hit>"
    assert config.use_bartlebot_default_combat_config is False


# ---------------------------------------------------------------------------
# _resolve_duel_profile_dsl
# ---------------------------------------------------------------------------


class _FakeMachineWithProfile:
    def __init__(self, bot_config):
        self.bot_config = bot_config

    def log(self, *args, **kwargs):
        pass

    def get_option(self, name, default=None):
        return default


def _make_config_with_profile(duel_profiles: dict | None = None) -> BotConfig:
    config = BotConfig.model_validate(VALID_DATA)
    profile = BotCombatProfile(name="Test")
    if duel_profiles:
        profile.duel_profiles.update(duel_profiles)
    config.combat_profiles = [profile]
    config.active_combat_profile_name = "Test"
    return config


def test_resolve_duel_profile_uses_built_in_when_no_active_profile():
    config = BotConfig.model_validate(VALID_DATA)
    machine = _FakeMachineWithProfile(config)
    dsls, source = _resolve_duel_profile_dsl(machine, "boss", 2)
    assert dsls == [BARTLEBOT_DEFAULT_COMBAT_CONFIG, BARTLEBOT_DEFAULT_COMBAT_CONFIG]
    assert "no active combat profile" in source


def test_resolve_duel_profile_falls_back_to_default_for_unknown_name():
    config = _make_config_with_profile()
    machine = _FakeMachineWithProfile(config)
    dsls, source = _resolve_duel_profile_dsl(machine, "unknown_fight", 1)
    assert dsls == [BARTLEBOT_DEFAULT_COMBAT_CONFIG]  # default profile uses built-in default
    assert "default" in source


def test_resolve_duel_profile_returns_custom_dsl_per_client():
    hitter_dsl = "any<hit>"
    support_dsl = "any<blade>"
    duel = DuelProfile(
        name="boss",
        use_bartlebot_default=False,
        behaviors=[
            CombatBehavior(players=[1], dsl=hitter_dsl),
            CombatBehavior(players=[2, 3, 4], dsl=support_dsl),
        ],
    )
    config = _make_config_with_profile({"boss": duel})
    machine = _FakeMachineWithProfile(config)
    dsls, source = _resolve_duel_profile_dsl(machine, "boss", 4)
    assert dsls[0] == hitter_dsl
    assert dsls[1] == support_dsl
    assert dsls[2] == support_dsl
    assert dsls[3] == support_dsl
    assert "boss" in source


def test_resolve_duel_profile_falls_back_to_built_in_for_none_dsl():
    duel = DuelProfile(name="boss", use_bartlebot_default=False, behaviors=[])
    config = _make_config_with_profile({"boss": duel})
    machine = _FakeMachineWithProfile(config)
    dsls, _ = _resolve_duel_profile_dsl(machine, "boss", 2)
    assert all(d == BARTLEBOT_DEFAULT_COMBAT_CONFIG for d in dsls)
