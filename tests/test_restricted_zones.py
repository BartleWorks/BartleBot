"""Tests for the PvP restricted-zone guard (#44).

Three layers are covered, because a gap in any one of them is a way into a PvP zone:
the rule matching itself, the pre-run check that refuses to start such a bot, and the
in-run watcher that stops one that got there anyway.

The negative cases matter at least as much as the positive ones here. A guard that
bans PvE content is not a safer guard, it is a broken tool -- so the PvE zone names
that merely *look* like arenas are asserted explicitly.
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

import bartlebot.bot.client_health_monitor as health_module
from bartlebot import restricted_zones
from bartlebot.bot.client_health_monitor import ClientHealthMonitor
from bartlebot.bot.schema import BotConfig

# ---------------------------------------------------------------------------
# Rule matching
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "zone",
    [
        "Battlegrounds/BMH/BMH_Zone_02",
        "Battlegrounds/BMH/BMH_Zone_05",
        "Battlegrounds/Mirage/Mirage_CTF_01",
        "Battlegrounds/BG_PVE/BG_PVE_Zone_01",
        "Battlegrounds/BG_PVE/BG_PVE_Zone_04",
    ],
)
def test_every_beastmoon_zone_is_restricted(zone):
    """The world rule has to cover all three Battlegrounds branches, PvE-flagged included.

    BG_PVE is Beastmoon Monster Mayhem. It is PvE by the game's own flag but it is
    still Beastmoon, with the same polymorph rosters and event rewards, so it is in
    scope for a rule written as "no Beastmoon".
    """
    hit = restricted_zones.check_zone(zone)
    assert hit is not None
    assert hit.kind == restricted_zones.WORLD


def test_world_rule_covers_a_subzone_that_does_not_exist_yet():
    """A world rule must catch zones added to Battlegrounds after this was written.

    This is the whole reason the Beastmoon rule is a world match rather than eleven
    exact ones: a list of today's zones silently stops covering the world the moment
    KI ships a new Beastmoon map.
    """
    assert restricted_zones.check_zone("Battlegrounds/Future/BMH_Zone_99") is not None


@pytest.mark.parametrize(
    "zone",
    [
        "WizardCity/WC_Duel_Arena_New",
        "WizardCity/KT_Duel_Arena",
        "WizardCity/AV_Arena",
        "WizardCity/AZ_Arena",
        "WizardCity/CL_Arena",
        "WizardCity/DS_Arena",
        "WizardCity/GH_Arena",
        "WizardCity/LM_Arena",
        "WizardCity/MB_Arena",
        "WizardCity/MS_Arena",
        "WizardCity/PA_Arena",
        "WizardCity/ZF_Arena",
    ],
)
def test_every_pvp_match_arena_is_restricted(zone):
    hit = restricted_zones.check_zone(zone)
    assert hit is not None
    assert hit.kind == restricted_zones.EXACT


@pytest.mark.parametrize(
    "zone",
    [
        # The Arena lobby. Players queue here and are moved to a match zone; standing
        # in it is ordinary questing, and banning it would ban the shops and NPCs too.
        "WizardCity/WC_Duel_Arena",
        # PvE zones whose names contain "Arena". Every one of these is a farming
        # target, and a substring rule would ban the lot.
        "Darkmoor/Interiors/DM_Z01I18_ArenaOfSwords",
        "Darkmoor/Interiors/DM_Z01I21_ArenaOfWands",
        "DragonSpire/DS_A2_Battle/Interiors/DS_Arena_Gauntlet_6Room",
        "DragonSpire/DS_A2_Battle/DS_A2Z2_Arena",
        "Krokotopia/KT_Krokosphinx/KT_Arena",
        "Krokotopia/KT_Krokosphinx/Interiors/KT_Arena_T4",
        "Wysteria/Interiors/PA_Tournament_Arena01",
        "Housing_MonsterMagicArena/Interior",
        "Arcanum/Interiors/AR_Z01_ArbitersArena",
        # Tutorial and practice duels.
        "WizardCity/Tutorial_Duel_Arena",
        "WizardCity/Tutorial_Fusion_Arena_1",
        "ThePhantomZoneWorld/Dueling_Diego",
        # Raids are PvE, and automating PvE monotony is what this tool is for.
        "Raids/Raid_AZ",
        "Raids/Raid_PL_Fortress",
    ],
)
def test_pve_zones_are_not_restricted(zone):
    assert restricted_zones.check_zone(zone) is None


def test_a_world_named_like_a_restricted_one_is_not_matched():
    """World matching is equality on the segment, not a prefix or substring test."""
    assert restricted_zones.check_zone("BattlegroundsLite/Zone_01") is None


def test_unknown_zone_is_allowed():
    """A client mid-loading-screen has no zone, and that must not stop the bot.

    Failing closed here would fire on every zone transition in every bot.
    """
    assert restricted_zones.check_zone(None) is None
    assert restricted_zones.check_zone("") is None


def test_duel_flags_are_restricted_and_named():
    assert restricted_zones.check_duel(pvp=True, battleground=False) is restricted_zones.DUEL_PVP
    assert restricted_zones.check_duel(pvp=False, battleground=True) is restricted_zones.DUEL_BATTLEGROUND
    assert restricted_zones.check_duel(pvp=False, battleground=False) is None


# ---------------------------------------------------------------------------
# Pre-run bot config check
# ---------------------------------------------------------------------------


def _config(states):
    return BotConfig.model_validate(
        {"name": "t", "initial": "s0", "recovery_state": "s0", "states": states, "transitions": []}
    )


def test_check_bot_config_finds_a_restricted_zone_param():
    config = _config(
        [
            {"id": "s0", "node_type": "check_zone", "label": "Go", "params": {"zone": "WizardCity/MB_Arena"}},
        ]
    )
    found = restricted_zones.check_bot_config(config)
    assert len(found) == 1
    label, zone, hit = found[0]
    assert (label, zone) == ("Go", "WizardCity/MB_Arena")
    assert hit.reason == "PvP match arena"


def test_check_bot_config_checks_every_zone_shaped_key():
    """to_zone / from_zone / mark_zone / dungeon_zone all hold real zone paths.

    Only ``zone`` being checked would leave teleport_to_friend's destination as an
    unguarded route straight into an arena.
    """
    config = _config(
        [
            {"id": "s0", "node_type": "teleport_to_friend", "params": {"to_zone": "WizardCity/ZF_Arena"}},
            {"id": "s1", "node_type": "enter_dungeon", "params": {"dungeon_zone": "Battlegrounds/BMH/BMH_Zone_03"}},
            {"id": "s2", "node_type": "mark_location", "params": {"mark_zone": "WizardCity/PA_Arena"}},
        ]
    )
    assert len(restricted_zones.check_bot_config(config)) == 3


def test_check_bot_config_ignores_a_ui_row_label():
    """``zone_list_entry`` is the text of a row in a menu, not a zone path.

    It is excluded by the ``_zone`` suffix test rather than by a hardcoded skip list,
    so this guards the suffix convention rather than one key name.
    """
    config = _config(
        [{"id": "s0", "node_type": "enter_zone_via_list", "params": {"zone_list_entry": "WizardCity/MB_Arena"}}]
    )
    assert restricted_zones.check_bot_config(config) == []


def test_check_bot_config_passes_a_clean_bot():
    config = _config(
        [
            {"id": "s0", "node_type": "check_zone", "params": {"zone": "Raids/Raid_AZ"}},
            {"id": "s1", "node_type": "interact", "params": {"zone": "WizardCity/WC_Duel_Arena"}},
        ]
    )
    assert restricted_zones.check_bot_config(config) == []


def test_no_shipped_bot_enters_a_restricted_zone():
    """The bot library itself must be clean, or the app ships a bot it refuses to run."""
    from pathlib import Path

    offenders = []
    for path in sorted(Path("bots").glob("*.json")):
        config = BotConfig.model_validate_json(path.read_text(encoding="utf-8"))
        for label, zone, _ in restricted_zones.check_bot_config(config):
            offenders.append(f"{path.name}: {label} -> {zone}")
    assert offenders == []


# ---------------------------------------------------------------------------
# In-run watcher
# ---------------------------------------------------------------------------


def _client(zone="WizardCity/Unicorn_Way", *, in_battle=False, pvp=False, battleground=False):
    client = MagicMock()
    client.window_handle = 1
    client.zone_name = AsyncMock(return_value=zone)
    client.in_battle = AsyncMock(return_value=in_battle)
    client.duel = MagicMock()
    client.duel.pvp = AsyncMock(return_value=pvp)
    client.duel.battleground = AsyncMock(return_value=battleground)
    return client


def _monitor(clients, on_restricted):
    stop_flag = asyncio.Event()
    return ClientHealthMonitor(
        clients_ref=lambda: clients,
        stop_flag=stop_flag,
        on_pause=MagicMock(),
        on_restricted_zone=on_restricted,
    )


@pytest.fixture
def fast(monkeypatch):
    """Compress the watcher's own cadence.

    Patched on the monitor's module rather than on the constant's home, and this is a
    cadence rather than a ceiling -- nothing here races a timeout, the fakes answer
    immediately, so the interval can go as low as the Windows timer tick allows.
    """
    monkeypatch.setattr(health_module, "_RESTRICTED_ZONE_CHECK_INTERVAL", 0.001)


async def _run_watcher(monitor, timeout=1.0):
    await asyncio.wait_for(monitor._restricted_zone_watcher(), timeout=timeout)


@pytest.mark.asyncio
async def test_watcher_fires_on_a_restricted_zone(fast):
    fired = MagicMock()
    monitor = _monitor([_client("WizardCity/MB_Arena")], fired)
    await _run_watcher(monitor)
    assert fired.call_count == 1
    assert fired.call_args[0][0].pattern == "WizardCity/MB_Arena"


@pytest.mark.asyncio
async def test_watcher_fires_on_a_pvp_duel_in_an_unlisted_zone(fast):
    """The duel flags are what catch a PvP zone no rule names.

    This is the case the zone list structurally cannot cover: a map added by a future
    patch. The zone here is deliberately one the rules allow.
    """
    fired = MagicMock()
    monitor = _monitor([_client("WizardCity/Some_New_Arena", in_battle=True, pvp=True)], fired)
    await _run_watcher(monitor)
    assert fired.call_args[0][0] is restricted_zones.DUEL_PVP


@pytest.mark.asyncio
async def test_watcher_ignores_duel_flags_outside_combat(fast):
    """Out of combat the duel struct still holds the last fight's values.

    Reading them anyway would stop a bot standing in a questing zone because of a PvP
    match the player fought by hand an hour ago.
    """
    fired = MagicMock()
    client = _client(in_battle=False, pvp=True, battleground=True)
    monitor = _monitor([client], fired)
    with pytest.raises(asyncio.TimeoutError):
        await _run_watcher(monitor, timeout=0.2)
    fired.assert_not_called()
    client.duel.pvp.assert_not_called()


@pytest.mark.asyncio
async def test_watcher_checks_every_client_not_just_the_first(fast):
    fired = MagicMock()
    monitor = _monitor([_client(), _client(), _client("Battlegrounds/BMH/BMH_Zone_04")], fired)
    await _run_watcher(monitor)
    assert fired.call_args[0][0].pattern == "Battlegrounds"


@pytest.mark.asyncio
async def test_watcher_survives_a_failed_read(fast):
    """A client mid-transition raises from every one of these reads.

    Failing open costs one tick; failing closed would stop a run on any transient
    memory read, which is a thing that happens on every zone change.
    """
    fired = MagicMock()
    bad = _client()
    bad.zone_name = AsyncMock(side_effect=RuntimeError("dead"))
    monitor = _monitor([bad, _client("WizardCity/AZ_Arena")], fired)
    await _run_watcher(monitor)
    assert fired.call_args[0][0].pattern == "WizardCity/AZ_Arena"


@pytest.mark.asyncio
async def test_watcher_does_not_fire_in_a_normal_zone(fast):
    fired = MagicMock()
    monitor = _monitor([_client("Raids/Raid_AZ"), _client("WizardCity/WC_Duel_Arena")], fired)
    with pytest.raises(asyncio.TimeoutError):
        await _run_watcher(monitor, timeout=0.2)
    fired.assert_not_called()


@pytest.mark.asyncio
async def test_watcher_callback_exception_does_not_escape(fast, caplog):
    """Every callback on this class is guarded; this one decides whether the run stops."""
    monitor = _monitor([_client("WizardCity/CL_Arena")], MagicMock(side_effect=RuntimeError("gui blew up")))
    with caplog.at_level(logging.ERROR):
        await _run_watcher(monitor)
    assert "on_restricted_zone callback raised" in caplog.text
