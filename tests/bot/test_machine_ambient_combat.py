"""Tests for BotMachine's ambient combat watcher and its session (#116).

The watcher takes over a fight no handle_combat node started: it pauses the FSM, cancels the
node (tests/bot/test_machine_preempt.py covers that half), and hands the fight to an
AmbientSession. These cover the session: who helps, when the fighter casts, who fights, where
everyone ends up, and whether the interrupted state runs again or the bot recovers.

SprintyCombat, the hub trip and wisp collection are patched out. Sightings, fight centers and
joining are real, driven through FakeClients: by default every FakeClient sees every other,
so they share a realm; ``see_each_other`` splits them.
"""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from wizwalker import XYZ

from bartlebot.bot import ambient_combat
from bartlebot.bot.machine import BotMachine
from bartlebot.nodes import combat_runner, node_helpers
from bartlebot.nodes.combat_and_healing import collect_wisps
from tests.conftest import FakeClient, FakeEntity, see_each_other

HOME = "WizardCity/WC_Streets/Unicorn_Way"


def _config(initial: str = "roam") -> dict:
    return {
        "initial": initial,
        "recovery_state": "recover",
        "states": [
            {"id": "roam", "node_type": "mock", "params": {}},
            {"id": "fight", "node_type": "handle_combat", "params": {}},
            {"id": "recover", "node_type": "mock", "params": {}},
        ],
        "transitions": [{"trigger": "advance", "source": "roam", "dest": "roam"}],
    }


def _client(x: float = 0.0) -> FakeClient:
    client = FakeClient()
    client.zone_name.return_value = HOME
    client.body.position.return_value = XYZ(x, 0.0, 0.0)
    return client


class _Fights:
    """Stands in for SprintyCombat: records who fought, with what, and what was true then.

    A fight ends as soon as it starts unless the test holds it with ``hold``.
    """

    def __init__(self):
        self.started: list[tuple] = []
        self.hold: dict = {}
        self.on_start = None
        self.raises: Exception | None = None

    async def __call__(self, client, dsl, watch):
        self.started.append((client, dsl))
        if self.on_start:
            self.on_start(client, watch)
        if (event := self.hold.get(client)) is not None:
            await event.wait()
        client.in_battle.return_value = False
        if self.raises is not None:
            raise self.raises

    def fought(self) -> list:
        return [client for client, _dsl in self.started]


@pytest.fixture
def fast(monkeypatch):
    """Compress the cadences: the watcher's poll, the session's, and joining's retry."""
    monkeypatch.setattr(node_helpers, "POLL_INTERVAL", 0.001)
    monkeypatch.setattr(ambient_combat, "POLL_INTERVAL", 0.001)
    monkeypatch.setattr(combat_runner, "POLL_INTERVAL", 0.001)
    monkeypatch.setattr(combat_runner, "COMBAT_TP_RETRY_INTERVAL", 0.01)


@pytest.fixture
def combat(monkeypatch):
    """Everything outside the session's own logic, patched where the session looks it up."""
    mocks = SimpleNamespace(
        resolve=MagicMock(return_value=(["dsl-0", "dsl-1", "dsl-2"], [], "test strategy")),
        seed=AsyncMock(),
        fights=_Fights(),
        poll_drops=AsyncMock(return_value=["Wand"]),
        hub=AsyncMock(return_value=True),
        wisps=AsyncMock(),
    )
    monkeypatch.setattr(combat_runner, "resolve_combat", mocks.resolve)
    monkeypatch.setattr(combat_runner, "seed_chat_baselines", mocks.seed)
    monkeypatch.setattr(combat_runner, "_run_combat", mocks.fights)
    monkeypatch.setattr(combat_runner, "poll_combat_drops", mocks.poll_drops)
    monkeypatch.setattr(ambient_combat, "_go_to_hub", mocks.hub)
    monkeypatch.setattr(collect_wisps, "collect_as_group", mocks.wisps)
    return mocks


async def _until(predicate, timeout: float = 3.0) -> None:
    async def _wait():
        while not predicate():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(_wait(), timeout)


async def _session(machine: BotMachine, combat) -> None:
    """Run the watcher until one session has finished and the FSM is running again."""
    task = asyncio.create_task(machine._ambient_combat_watcher())
    await _until(lambda: combat.poll_drops.await_count >= 1 and not machine.is_paused)
    machine.stop()
    await asyncio.wait_for(task, timeout=2.0)


def _teleports(client) -> list[tuple[float, float, float]]:
    return [(p.x, p.y, p.z) for (p,), _kw in client.teleport.call_args_list]


def _join_on_teleport(helper) -> None:
    """A teleport to the fight's center (FakeClient's duel position) puts *helper* in the fight."""

    async def teleport(where):
        if (where.x, where.y) == (500.0, 600.0):
            helper.in_battle.return_value = True

    helper.teleport.side_effect = teleport


# ---------------------------------------------------------------------------
# Who fights, and with what
# ---------------------------------------------------------------------------


async def test_a_lone_fighter_fights_at_once_then_goes_to_the_hub_and_recovery(fast, combat):
    """A single-client bot has no anchor, so the hub, then recovery (for most bots, a soft relog)."""
    fighter = _client()
    fighter.in_battle.return_value = True
    machine = BotMachine(clients=[fighter], config=_config(), node_registry={})
    during: list[tuple] = []
    combat.fights.on_start = lambda _c, _w: during.append((machine.is_paused, machine.is_ambient_combat_active))

    await _session(machine, combat)

    assert combat.fights.fought() == [fighter]
    assert during == [(True, True)]
    combat.hub.assert_awaited_once()
    assert machine.state == "recover"


async def test_the_strategy_is_resolved_per_slot_and_drops_are_credited_to_it(fast, combat):
    """Resolving for only the battling clients would hand slot 1 the strategy for slot 0."""
    elsewhere, fighter = _client(), _client()
    see_each_other(elsewhere)
    see_each_other(fighter)
    elsewhere.zone_name.return_value = "Elsewhere"
    fighter.in_battle.return_value = True
    machine = BotMachine(clients=[elsewhere, fighter], config=_config(), node_registry={})

    await _session(machine, combat)

    combat.resolve.assert_called_once_with(machine, use_default_strategy=True, num_clients=2)
    assert combat.fights.started == [(fighter, "dsl-1")]
    combat.poll_drops.assert_awaited_once_with(machine, [fighter], [1])


async def test_an_eligible_helper_joins_and_the_fighter_waits_for_it(fast, combat):
    fighter, helper = _client(), _client(x=2000.0)
    fighter.in_battle.return_value = True
    _join_on_teleport(helper)
    helper_in_when_fighter_started: list[bool] = []

    def on_start(client, _watch):
        if client is fighter:
            helper_in_when_fighter_started.append(helper.in_battle.return_value)

    combat.fights.on_start = on_start
    machine = BotMachine(clients=[fighter, helper], config=_config(), node_registry={})

    await _session(machine, combat)

    assert set(combat.fights.fought()) == {fighter, helper}
    assert helper_in_when_fighter_started == [True]
    assert _teleports(helper)[0] == (500.0, 600.0, 0.0)  # the fighter's duel center


async def test_a_client_on_another_realm_or_in_another_zone_is_left_alone(fast, combat):
    fighter, other_realm, other_zone = _client(), _client(), _client()
    see_each_other(fighter)
    see_each_other(other_realm)
    see_each_other(other_zone)
    other_zone.zone_name.return_value = "Elsewhere"
    fighter.in_battle.return_value = True
    machine = BotMachine(clients=[fighter, other_realm, other_zone], config=_config(), node_registry={})

    await _session(machine, combat)

    assert combat.fights.fought() == [fighter]
    other_zone.teleport.assert_not_awaited()
    other_realm.teleport.assert_not_awaited()


async def test_a_client_that_walks_into_the_fight_on_its_own_gets_its_combat_run(fast, combat):
    """The bug before #116: a client entering after the fight was picked up fought with no combat."""
    fighter, walker = _client(), _client()
    see_each_other(fighter)
    see_each_other(walker)
    fighter.in_battle.return_value = True
    gate = asyncio.Event()
    combat.fights.hold[fighter] = gate

    def on_start(client, _watch):
        if client is fighter:
            walker.in_battle.return_value = True
        elif client is walker:
            gate.set()

    combat.fights.on_start = on_start
    machine = BotMachine(clients=[fighter, walker], config=_config(), node_registry={})

    await _session(machine, combat)

    assert combat.fights.started == [(fighter, "dsl-0"), (walker, "dsl-1")]
    combat.poll_drops.assert_awaited_once_with(machine, [fighter, walker], [0, 1])


async def test_a_bystander_pulled_into_a_second_fight_gets_its_combat_run(fast, combat):
    fighter, bystander = _client(), _client()
    see_each_other(fighter)
    see_each_other(bystander)
    bystander.duel.position = AsyncMock(return_value=XYZ(9000.0, 0.0, 0.0))
    fighter.in_battle.return_value = True
    gate = asyncio.Event()
    combat.fights.hold[fighter] = gate

    def on_start(client, _watch):
        if client is fighter:
            bystander.in_battle.return_value = True
        elif client is bystander:
            gate.set()

    combat.fights.on_start = on_start
    machine = BotMachine(clients=[fighter, bystander], config=_config(), node_registry={})

    await _session(machine, combat)

    assert set(combat.fights.fought()) == {fighter, bystander}


async def test_a_fight_that_raises_on_one_client_still_collects_drops_and_resumes(fast, combat):
    fighter = _client()
    fighter.in_battle.return_value = True
    combat.fights.raises = RuntimeError("SprintyCombat lost the duel window")
    machine = BotMachine(clients=[fighter], config=_config(), node_registry={})

    await _session(machine, combat)

    combat.poll_drops.assert_awaited_once_with(machine, [fighter], [0])
    assert machine.is_paused is False


# ---------------------------------------------------------------------------
# Help from another zone or realm: porting to a fighter (Phase B)
# ---------------------------------------------------------------------------


def _ports(machine: BotMachine, fighter, *, refused=()):
    """Stands in for port_waves: every porter not in *refused* lands in *fighter*'s zone and realm."""

    async def port_waves(_machine, _clients, waiting, arrived, _port_map):
        assert arrived == [fighter]
        landed = [c for c in waiting if c not in refused]
        for client in landed:
            client.zone_name.return_value = HOME
        see_each_other(fighter, *landed)
        await node_helpers.read_sightings([fighter, *landed], machine)
        return [c for c in waiting if c in refused]

    return AsyncMock(side_effect=port_waves)


def _separate(machine: BotMachine) -> None:
    from bartlebot.options import player_realms

    machine.get_option = lambda name, default=None: player_realms.SEPARATE if name == player_realms.OPTION else default


async def test_a_helper_from_another_zone_ports_in_joins_and_the_bot_recovers(fast, combat, monkeypatch):
    """Its start is coordinates in a zone it has left, so it follows the fighter's choice; then recovery."""
    fighter, helper = _client(x=0.0), _client(x=7777.0)
    see_each_other(fighter)
    see_each_other(helper)
    helper.zone_name.return_value = "Elsewhere"
    fighter.in_battle.return_value = True
    _join_on_teleport(helper)
    helper_in_when_fighter_started: list[bool] = []

    def on_start(client, _watch):
        if client is fighter:
            helper_in_when_fighter_started.append(helper.in_battle.return_value)

    combat.fights.on_start = on_start
    machine = BotMachine(clients=[fighter, helper], config=_config(), node_registry={})
    machine.port_map = MagicMock()
    ports = _ports(machine, fighter)
    monkeypatch.setattr(ambient_combat.port_map, "port_waves", ports)

    await _session(machine, combat)

    ports.assert_awaited_once()
    assert combat.fights.started == [(fighter, "dsl-0"), (helper, "dsl-1")]
    assert helper_in_when_fighter_started == [True]  # a porter counts toward the hold
    assert (7777.0, 0.0, 0.0) not in _teleports(helper)
    assert machine.state == "recover"


async def test_a_helper_from_another_realm_of_the_zone_goes_home_and_the_state_runs_again(fast, combat, monkeypatch):
    """It goes back to its starting point, now on the fighter's realm: in a Together bot, where it belongs."""
    fighter, helper = _client(x=0.0), _client(x=2000.0)
    see_each_other(fighter)
    see_each_other(helper)
    fighter.in_battle.return_value = True
    _join_on_teleport(helper)
    machine = BotMachine(clients=[fighter, helper], config=_config(), node_registry={})
    machine.port_map = MagicMock()
    monkeypatch.setattr(ambient_combat.port_map, "port_waves", _ports(machine, fighter))

    await _session(machine, combat)

    assert set(combat.fights.fought()) == {fighter, helper}
    assert _teleports(helper)[-1] == (2000.0, 0.0, 0.0)
    combat.hub.assert_not_awaited()
    assert machine.state == "roam"


async def test_a_helper_landed_in_the_fight_by_its_port_is_not_teleported(fast, combat, monkeypatch):
    fighter, helper = _client(), _client()
    see_each_other(fighter)
    see_each_other(helper)
    fighter.in_battle.return_value = True
    machine = BotMachine(clients=[fighter, helper], config=_config(), node_registry={})
    machine.port_map = MagicMock()
    ports = _ports(machine, fighter)

    async def into_the_circle(*args):
        left = await ports.side_effect(*args)
        helper.in_battle.return_value = True
        return left

    monkeypatch.setattr(ambient_combat.port_map, "port_waves", AsyncMock(side_effect=into_the_circle))

    await _session(machine, combat)

    assert set(combat.fights.fought()) == {fighter, helper}
    assert (500.0, 600.0, 0.0) not in _teleports(helper)


async def test_a_refused_port_is_dropped_and_the_fight_goes_ahead(fast, combat, monkeypatch):
    fighter, refused = _client(x=0.0), _client(x=3000.0)
    see_each_other(fighter)
    see_each_other(refused)
    refused.zone_name.return_value = "Elsewhere"
    fighter.in_battle.return_value = True
    machine = BotMachine(clients=[fighter, refused], config=_config(), node_registry={})
    machine.port_map = MagicMock()
    monkeypatch.setattr(ambient_combat.port_map, "port_waves", _ports(machine, fighter, refused=[refused]))

    await _session(machine, combat)

    assert combat.fights.fought() == [fighter]
    refused.teleport.assert_not_awaited()


async def test_a_separate_bot_never_ports(fast, combat, monkeypatch):
    """A port would move the player onto the fighter's realm, breaking the split the bot is built on."""
    fighter, other_realm = _client(), _client()
    see_each_other(fighter)
    see_each_other(other_realm)
    fighter.in_battle.return_value = True
    machine = BotMachine(clients=[fighter, other_realm], config=_config(), node_registry={})
    machine.port_map = MagicMock()
    _separate(machine)
    ports = _ports(machine, fighter)
    monkeypatch.setattr(ambient_combat.port_map, "port_waves", ports)

    await _session(machine, combat)

    ports.assert_not_awaited()
    assert combat.fights.fought() == [fighter]
    other_realm.teleport.assert_not_awaited()


async def test_no_port_map_skips_porting(fast, combat, monkeypatch):
    fighter, other_zone = _client(), _client()
    see_each_other(fighter)
    see_each_other(other_zone)
    other_zone.zone_name.return_value = "Elsewhere"
    fighter.in_battle.return_value = True
    machine = BotMachine(clients=[fighter, other_zone], config=_config(), node_registry={})
    assert machine.port_map is None
    ports = _ports(machine, fighter)
    monkeypatch.setattr(ambient_combat.port_map, "port_waves", ports)

    await _session(machine, combat)

    ports.assert_not_awaited()
    assert combat.fights.fought() == [fighter]


# ---------------------------------------------------------------------------
# Holding the fighter's first cast
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self, now: float = 100.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def _held(fighter, helper, clock: _Clock, *, phase=None):
    from wizwalker.memory import DuelPhase

    fighter.duel.duel_phase = AsyncMock(return_value=phase or DuelPhase.planning)
    session = ambient_combat.AmbientSession(MagicMock(), [fighter, helper], [fighter], clock=clock)
    pending = asyncio.get_running_loop().create_future()
    return session, ambient_combat._Hold([helper]), {helper: pending}


async def test_the_hold_expires_twenty_seconds_after_the_first_planning_phase():
    fighter, helper = _client(), _client()
    clock = _Clock(100.0)
    session, hold, joins = _held(fighter, helper, clock)

    assert await session._released(fighter, hold, joins) is False  # planning first seen at 100
    clock.now = 100.0 + ambient_combat.AMBIENT_HELP_HOLD - 0.1
    assert await session._released(fighter, hold, joins) is False
    clock.now = 100.0 + ambient_combat.AMBIENT_HELP_HOLD
    assert await session._released(fighter, hold, joins) is True


async def test_a_join_moves_the_anchor_since_the_game_restarts_its_timer():
    fighter, helper = _client(), _client()
    clock = _Clock(100.0)
    session, hold, joins = _held(fighter, helper, clock)
    await session._released(fighter, hold, joins)
    session.joined_at[helper] = 110.0

    clock.now = 110.0 + ambient_combat.AMBIENT_HELP_HOLD - 0.1
    assert await session._released(fighter, hold, joins) is False
    clock.now = 110.0 + ambient_combat.AMBIENT_HELP_HOLD
    assert await session._released(fighter, hold, joins) is True


async def test_the_hold_waits_for_planning_to_start_counting():
    from wizwalker.memory import DuelPhase

    fighter, helper = _client(), _client()
    clock = _Clock(100.0)
    session, hold, joins = _held(fighter, helper, clock, phase=DuelPhase.execution)

    clock.now = 500.0
    assert await session._released(fighter, hold, joins) is False


async def test_an_unreadable_phase_counts_the_hold_from_detection():
    fighter, helper = _client(), _client()
    clock = _Clock(100.0)
    session, hold, joins = _held(fighter, helper, clock)
    fighter.duel.duel_phase = AsyncMock(side_effect=RuntimeError("unreadable"))

    clock.now = 100.0 + ambient_combat.AMBIENT_HELP_HOLD
    assert await session._released(fighter, hold, joins) is True


async def test_the_hold_ends_once_every_helper_is_in_or_has_given_up():
    fighter, helper = _client(), _client()
    session, hold, joins = _held(fighter, helper, _Clock())
    joins[helper].set_result(False)  # gave up

    assert await session._released(fighter, hold, joins) is True


# ---------------------------------------------------------------------------
# Where everyone ends up
# ---------------------------------------------------------------------------


async def test_a_helper_returns_to_its_start_and_the_fighter_goes_to_the_nearest_anchor(fast, combat):
    fighter, helper = _client(x=0.0), _client(x=2000.0)
    fighter.in_battle.return_value = True
    _join_on_teleport(helper)
    machine = BotMachine(clients=[fighter, helper], config=_config(), node_registry={})

    await _session(machine, combat)

    assert _teleports(helper)[-1] == (2000.0, 0.0, 0.0)
    assert _teleports(fighter) == [(2000.0, 0.0, 0.0)]  # the helper's starting point
    combat.hub.assert_not_awaited()
    assert machine.state == "roam"  # everyone is in its zone: the interrupted state runs again


async def test_a_fighter_can_anchor_on_a_client_on_another_realm(fast, combat):
    """A teleport to coordinates keeps the player on its own realm, so the other realm's spot is fair."""
    fighter, far, near = _client(x=0.0), _client(x=6000.0), _client(x=3000.0)
    see_each_other(fighter)
    see_each_other(far, near)
    fighter.in_battle.return_value = True
    machine = BotMachine(clients=[fighter, far, near], config=_config(), node_registry={})

    await _session(machine, combat)

    assert _teleports(fighter) == [(3000.0, 0.0, 0.0)]
    assert machine.state == "roam"


async def test_an_anchor_near_a_mob_is_passed_over(fast, combat):
    fighter, risky, safe = _client(x=0.0), _client(x=1000.0), _client(x=5000.0)
    see_each_other(fighter)
    see_each_other(risky)
    see_each_other(safe)
    mob = FakeEntity()
    mob.location.return_value = XYZ(1200.0, 0.0, 0.0)
    risky.get_mobs.return_value = [mob]
    fighter.in_battle.return_value = True
    machine = BotMachine(clients=[fighter, risky, safe], config=_config(), node_registry={})

    await _session(machine, combat)

    assert _teleports(fighter) == [(5000.0, 0.0, 0.0)]


async def test_a_failed_arrival_check_sends_that_player_to_the_hub(fast, combat):
    fighter, helper = _client(x=0.0), _client(x=2000.0)
    fighter.in_battle.return_value = True
    _join_on_teleport(helper)
    mob = FakeEntity()
    mob.location.return_value = XYZ(2500.0, 0.0, 0.0)  # walked up to the anchor during the fight

    async def arrive(_where):
        fighter.get_mobs.return_value = [mob]

    fighter.teleport.side_effect = arrive
    machine = BotMachine(clients=[fighter, helper], config=_config(), node_registry={})

    await _session(machine, combat)

    _machine, players, _hub = combat.hub.await_args.args
    assert players == [fighter]
    assert machine.state == "recover"


async def test_a_low_player_collects_wisps_before_anyone_moves(fast, combat):
    fighter, helper = _client(x=0.0), _client(x=2000.0)
    fighter.in_battle.return_value = True
    _join_on_teleport(helper)
    fighter.calc_health_ratio.return_value = 0.01  # defeated in a fight the group won: 1 health
    order: list[str] = []
    combat.wisps.side_effect = lambda *_a: order.append("wisps")

    async def moved(_where):
        order.append("moved")

    fighter.teleport.side_effect = moved
    machine = BotMachine(clients=[fighter, helper], config=_config(), node_registry={})

    await _session(machine, combat)

    assert order == ["wisps", "moved"]
    args = combat.wisps.await_args.args
    assert set(args[1]) == {fighter, helper}
    assert args[2:] == (1.0, 1.0, ambient_combat.WISP_COLLECT_TIMEOUT, True)


async def test_a_fled_fight_collects_hub_wisps_then_goes_to_recovery(fast, combat):
    fighter = _client()
    fighter.in_battle.return_value = True

    def flee(client, watch):
        watch.note_fled(client)
        client.zone_name.return_value = "WizardCity/WC_Hub"
        client.calc_mana_ratio.return_value = 0.0

    combat.fights.on_start = flee
    machine = BotMachine(clients=[fighter], config=_config(), node_registry={})

    await _session(machine, combat)

    combat.wisps.assert_awaited_once()
    fighter.teleport.assert_not_awaited()
    combat.hub.assert_not_awaited()
    assert machine.state == "recover"


async def test_an_exception_in_the_session_goes_to_recovery_and_still_resumes(fast, combat):
    """The resume is in a finally. Without it, one unreadable strategy leaves the run paused for good."""
    fighter = _client()
    fighter.in_battle.return_value = True
    combat.resolve.side_effect = RuntimeError("strategy file unreadable")
    machine = BotMachine(clients=[fighter], config=_config(), node_registry={})

    task = asyncio.create_task(machine._ambient_combat_watcher())
    await _until(lambda: combat.resolve.called and not machine.is_paused)
    machine.stop()
    await asyncio.wait({task}, timeout=2.0)

    assert machine.state == "recover"


async def test_a_bot_with_no_recovery_state_runs_the_interrupted_state_again(fast, combat, caplog):
    fighter = _client()
    fighter.in_battle.return_value = True
    config = _config()
    del config["recovery_state"]
    machine = BotMachine(clients=[fighter], config=config, node_registry={})
    caplog.set_level(logging.WARNING, logger="bartlebot.bot.machine")

    await _session(machine, combat)

    assert machine.state == "roam"
    assert any("has no recovery state" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# The flee limit
# ---------------------------------------------------------------------------


def test_the_third_flee_in_the_window_stops_the_bot_with_a_reason():
    stopped: list[str] = []
    machine = BotMachine(clients=[], config=_config(), node_registry={}, on_stopped=stopped.append)

    assert machine.note_flee() is False
    assert machine.note_flee() is False
    assert machine.note_flee() is True

    assert machine.is_stopped
    assert len(stopped) == 1 and "fled 3 fights" in stopped[0]


def test_flees_outside_the_window_do_not_count(monkeypatch):
    from bartlebot.bot import machine as machine_module

    now = [1000.0]
    monkeypatch.setattr(machine_module.time, "monotonic", lambda: now[0])
    machine = BotMachine(clients=[], config=_config(), node_registry={})

    machine.note_flee()
    machine.note_flee()
    now[0] += 601.0
    assert machine.note_flee() is False
    assert not machine.is_stopped


# ---------------------------------------------------------------------------
# What the watcher leaves alone, and the time limit
# ---------------------------------------------------------------------------


async def test_no_fight_is_taken_over_while_the_fsm_is_in_handle_combat(fast, combat):
    """handle_combat is already fighting it; a second SprintyCombat would contend for the same cards."""
    battling = _client()
    battling.in_battle.return_value = True
    machine = BotMachine(clients=[battling], config=_config(initial="fight"), node_registry={})
    machine.pause = MagicMock(wraps=machine.pause)

    task = asyncio.create_task(machine._ambient_combat_watcher())
    await asyncio.sleep(0.05)
    machine.stop()
    await asyncio.wait_for(task, timeout=2.0)

    combat.resolve.assert_not_called()
    assert combat.fights.started == []
    machine.pause.assert_not_called()


async def test_a_fight_the_fsm_reaches_first_is_left_to_handle_combat(fast, combat):
    """The in_battle reads are an await, and the FSM can enter handle_combat while they are out.

    The watcher re-checks the state once it holds the lock, so it neither pauses the FSM nor
    starts a second fight.
    """
    battling = _client()
    machine = BotMachine(clients=[battling], config=_config(), node_registry={})
    machine.pause = MagicMock(wraps=machine.pause)

    async def in_battle():
        machine.force_state("fight")
        return True

    battling.in_battle.side_effect = in_battle
    task = asyncio.create_task(machine._ambient_combat_watcher())
    await _until(lambda: battling.in_battle.await_count >= 1)
    machine.stop()
    await asyncio.wait_for(task, timeout=2.0)

    assert machine.state == "fight"
    combat.resolve.assert_not_called()
    machine.pause.assert_not_called()


async def test_a_fight_is_fled_at_the_ambient_time_limit(fast, combat, monkeypatch, caplog):
    """A fight no node started is held to AMBIENT_COMBAT_MAX_DURATION, then fled (#111)."""
    monkeypatch.setattr(ambient_combat, "AMBIENT_COMBAT_MAX_DURATION", 0.01)
    fighter = _client()
    fighter.in_battle.return_value = True
    machine = BotMachine(clients=[fighter], config=_config(), node_registry={})

    async def fight(client, _dsl, watch):
        # Stands in for SprintyCombat: flees at the first planning phase that says to.
        while not await watch.should_flee(SimpleNamespace(client=client)):
            await asyncio.sleep(0.001)
        watch.note_fled(client)
        client.in_battle.return_value = False

    monkeypatch.setattr(combat_runner, "_run_combat", fight)
    caplog.set_level(logging.WARNING)
    await _session(machine, combat)

    assert any("The fight ran past its time limit; fleeing" in r.getMessage() for r in caplog.records)
    assert machine.is_paused is False


async def test_a_helper_back_at_its_start_is_arrival_checked_too(fast, combat):
    fighter, helper = _client(x=0.0), _client(x=2000.0)
    fighter.in_battle.return_value = True
    mob = FakeEntity()
    mob.location.return_value = XYZ(2800.0, 0.0, 0.0)

    async def teleport(where):
        if (where.x, where.y) == (500.0, 600.0):
            helper.in_battle.return_value = True
        else:  # home: a patrol has wandered within SAFE_RADIUS since it left
            helper.get_mobs.return_value = [mob]

    helper.teleport.side_effect = teleport
    machine = BotMachine(clients=[fighter, helper], config=_config(), node_registry={})

    await _session(machine, combat)

    sent = [p for call in combat.hub.await_args_list for p in call.args[1]]
    assert helper in sent
    assert machine.state == "recover"


async def test_a_helper_that_never_got_in_goes_home(fast, combat, monkeypatch):
    monkeypatch.setattr(ambient_combat, "ENTER_COMBAT_TIMEOUT", 0.05)  # the subject: giving up
    fighter, helper = _client(x=0.0), _client(x=2000.0)
    fighter.in_battle.return_value = True
    moved_to = XYZ(500.0, 600.0, 0.0)

    async def teleport(where):
        helper.body.position.return_value = where  # moves, but the fight never takes it

    helper.teleport.side_effect = teleport
    machine = BotMachine(clients=[fighter, helper], config=_config(), node_registry={})

    await _session(machine, combat)

    assert combat.fights.fought() == [fighter]
    assert _teleports(helper)[0] == (moved_to.x, moved_to.y, moved_to.z)
    assert _teleports(helper)[-1] == (2000.0, 0.0, 0.0)
