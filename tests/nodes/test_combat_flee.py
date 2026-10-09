"""Tests for fleeing an unwinnable fight (#111): combat_runner's FightWatch, run_fight and _flee.

The watch decides per fight, from what each of our players reports at the start of a planning
phase. These drive it with reports directly, so no card or duel memory is involved; the report
itself is covered separately against fake combat members.
"""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bartlebot.nodes import combat_runner
from bartlebot.nodes.combat_runner import FightEnd, FightWatch, RoundReport, run_fight
from tests.conftest import FakeClient

_ROUNDS = combat_runner.FLEE_AFTER_ROUNDS


class _Log:
    def __init__(self):
        self.lines: list[tuple[str, int]] = []

    def __call__(self, message: str, level: int = logging.INFO) -> None:
        self.lines.append((message, level))

    def warnings(self) -> list[str]:
        return [m for m, level in self.lines if level >= logging.WARNING]


def _client(gid: int) -> FakeClient:
    client = FakeClient()
    client.client_object.global_id_full = AsyncMock(return_value=gid)
    return client


async def _watch(*gids: int) -> tuple[FightWatch, list, _Log]:
    clients = [_client(gid) for gid in gids]
    log = _Log()
    watch = FightWatch({c: n for n, c in enumerate(clients, start=1)}, log)
    await watch.identify()
    return watch, clients, log


def _combat(client) -> SimpleNamespace:
    return SimpleNamespace(client=client)


def _reports(table: dict):
    """Patch the round report: *table* maps (gid, round) to (has_damage, dot) or None for unreadable.

    Each player's round is counted per call, the way consecutive planning phases arrive.
    """
    rounds: dict[int, int] = {}

    async def _report(_combat, gid, _ours):
        rounds[gid] = rounds.get(gid, 0) + 1
        entry = table.get((gid, rounds[gid]), table.get((gid, "*")))
        if entry is None:
            return None
        fight, has_damage, dot = entry
        return RoundReport(rounds[gid], frozenset(fight), has_damage, dot)

    return patch.object(combat_runner, "_round_report", side_effect=_report)


async def _round(watch: FightWatch, clients: list) -> list[bool]:
    return list(await asyncio.gather(*(watch.should_flee(_combat(c)) for c in clients)))


# ---------------------------------------------------------------------------
# FightWatch
# ---------------------------------------------------------------------------


async def test_an_unwinnable_fight_flees_every_one_of_our_players_in_it():
    watch, clients, log = await _watch(11, 22)
    fight = {11, 22}
    with _reports({(11, "*"): (fight, False, False), (22, "*"): (fight, False, False)}):
        decisions = [await _round(watch, clients) for _ in range(_ROUNDS)]

    assert decisions == [[False, False]] * (_ROUNDS - 1) + [[True, True]]
    assert log.warnings() == [
        f"P1, P2: no castable damage card and no damage over time on the enemies for {_ROUNDS} rounds; fleeing"
    ]


async def test_one_player_with_damage_left_carries_the_fight_and_restarts_the_count():
    watch, clients, _log = await _watch(11, 22)
    fight = {11, 22}
    table = {(11, "*"): (fight, False, False), (22, "*"): (fight, False, False), (22, 2): (fight, True, False)}
    with _reports(table):
        decisions = [await _round(watch, clients) for _ in range(_ROUNDS + 2)]

    # Round 2 had damage, so the run of hopeless rounds starts again at round 3.
    assert [any(d) for d in decisions] == [False] * (_ROUNDS + 1) + [True]


async def test_damage_over_time_on_the_enemies_keeps_the_fight_going():
    watch, clients, _log = await _watch(11)
    with _reports({(11, "*"): ({11}, False, True)}):
        decisions = [await _round(watch, clients) for _ in range(_ROUNDS + 1)]

    assert not any(d[0] for d in decisions)


async def test_two_fights_are_judged_apart():
    """P1 is alone and out of damage; P2 is in another duel with damage to spare."""
    watch, clients, _log = await _watch(11, 22)
    with _reports({(11, "*"): ({11}, False, False), (22, "*"): ({22}, True, False)}):
        decisions = [await _round(watch, clients) for _ in range(_ROUNDS)]

    assert decisions[-1] == [True, False]


async def test_a_player_whose_report_never_arrives_counts_as_able_to_deal_damage(monkeypatch):
    monkeypatch.setattr(combat_runner, "FLEE_VOTE_TIMEOUT", 0.05)
    watch, clients, _log = await _watch(11, 22)
    with _reports({(11, "*"): ({11, 22}, False, False)}):  # P2's report is unreadable every round
        decisions = [await _round(watch, clients) for _ in range(_ROUNDS)]

    assert not any(any(d) for d in decisions)


async def test_an_unreadable_id_turns_off_the_judgement_but_not_the_time_limit():
    log = _Log()
    client = FakeClient()
    client.client_object.global_id_full = AsyncMock(side_effect=RuntimeError("unreadable"))
    watch = FightWatch({client: 1}, log)
    await watch.identify()
    with _reports({(11, "*"): ({11}, False, False)}) as report:
        assert await watch.should_flee(_combat(client)) is False
        report.assert_not_called()

        watch.flee_all("The fight ran past its time limit")
        assert await watch.should_flee(_combat(client)) is True
    assert log.warnings() == ["The fight ran past its time limit; fleeing"]


async def test_flee_all_needs_no_vote():
    watch, clients, _log = await _watch(11, 22)
    watch.flee_all("The fight ran past its time limit")
    with _reports({}) as report:
        assert await _round(watch, clients) == [True, True]
    report.assert_not_called()


# ---------------------------------------------------------------------------
# The round report
# ---------------------------------------------------------------------------


def _member(owner: int, team: int, *, dead: bool = False, effects=()) -> MagicMock:
    participant = MagicMock()
    participant.team_id = AsyncMock(return_value=team)
    participant.hanging_effects = AsyncMock(
        return_value=[SimpleNamespace(effect_type=AsyncMock(return_value=e)) for e in effects]
    )
    member = MagicMock()
    member.owner_id = AsyncMock(return_value=owner)
    member.get_participant = AsyncMock(return_value=participant)
    member.is_dead = AsyncMock(return_value=dead)
    return member


def _report_combat(members: list, round_num: int = 4) -> SimpleNamespace:
    client = FakeClient()
    client.duel.round_num = AsyncMock(return_value=round_num)
    return SimpleNamespace(client=client, get_members=AsyncMock(return_value=members))


async def test_the_report_names_our_players_in_the_duel_and_reads_the_enemies_effects():
    from wizwalker.memory import SpellEffects

    members = [
        _member(11, team=1),
        _member(22, team=1),
        _member(99, team=1),  # someone else's wizard: in the duel, but not ours
        _member(500, team=2, effects=[SpellEffects.modify_incoming_damage, SpellEffects.damage_over_time]),
    ]
    with patch.object(combat_runner, "_holds_castable_damage", AsyncMock(return_value=True)):
        report = await combat_runner._round_report(_report_combat(members), 11, frozenset({11, 22, 33}))

    assert report == RoundReport(4, frozenset({11, 22}), True, True)


async def test_a_defeated_player_reports_no_damage_whatever_it_holds():
    members = [_member(11, team=1, dead=True), _member(500, team=2)]
    with patch.object(combat_runner, "_holds_castable_damage", AsyncMock(return_value=True)):
        report = await combat_runner._round_report(_report_combat(members), 11, frozenset({11}))

    assert report.has_damage is False
    assert report.dot_on_enemies is False


async def test_a_report_that_cannot_find_its_own_player_is_unreadable():
    members = [_member(22, team=1), _member(500, team=2)]
    assert await combat_runner._round_report(_report_combat(members), 11, frozenset({11, 22})) is None


async def test_only_castable_damage_cards_count():
    heal, hit = object(), object()
    combat = SimpleNamespace(get_castable_cards=AsyncMock(return_value=[heal]))

    async def _is_damage(card, _template):
        return card is hit

    with patch("wizwalker.extensions.wizsprinter.sprinty_combat.does_card_contain_reqs", side_effect=_is_damage):
        assert await combat_runner._holds_castable_damage(combat) is False
        combat.get_castable_cards.return_value = [heal, hit]
        assert await combat_runner._holds_castable_damage(combat) is True


# ---------------------------------------------------------------------------
# Clicking Flee
# ---------------------------------------------------------------------------


@pytest.fixture
def ui(monkeypatch):
    """The two UI reads/clicks _flee uses, with the confirmation showing once Flee is clicked."""
    from bartlebot.nodes import node_helpers
    from bartlebot.nodes.paths import FLEE_BUTTON_PATH, FLEE_CONFIRM_PATH

    state = SimpleNamespace(clicks=[], flee_clickable=True, confirm_appears=True)

    async def click(_client, path):
        if path == FLEE_BUTTON_PATH and not state.flee_clickable:
            return False
        state.clicks.append("flee" if path == FLEE_BUTTON_PATH else "confirm")
        return True

    async def visible(_client, path):
        return path == FLEE_CONFIRM_PATH and state.confirm_appears and "flee" in state.clicks

    monkeypatch.setattr(node_helpers, "click_by_path", click)
    monkeypatch.setattr(node_helpers, "is_visible_by_path", visible)
    return state


async def test_fleeing_clicks_flee_then_confirms(ui):
    watch, (client,), log = await _watch(11)
    await combat_runner._flee(_combat(client), watch)

    assert ui.clicks == ["flee", "confirm"]
    assert watch.fled == {1}
    assert ("P1: fled", logging.INFO) in log.lines


async def test_a_flee_with_no_confirmation_is_left_for_the_next_round(ui, monkeypatch):
    monkeypatch.setattr(combat_runner, "FLEE_CONFIRM_TIMEOUT", 0.05)
    ui.confirm_appears = False
    watch, (client,), _log = await _watch(11)
    await combat_runner._flee(_combat(client), watch)

    assert ui.clicks == ["flee"]
    assert watch.fled == set()


async def test_a_defeated_player_flees_from_its_defeat_panel(ui):
    ui.flee_clickable = False
    watch, (client,), _log = await _watch(11)

    async def defeated_flee():
        ui.clicks.append("flee")

    await combat_runner._flee(SimpleNamespace(client=client, flee_button=defeated_flee), watch)

    assert ui.clicks == ["flee", "confirm"]
    assert watch.fled == {1}


async def test_the_watched_combat_flees_instead_of_casting():
    from wizwalker.extensions.wizsprinter import SprintyCombat

    watch = SimpleNamespace(should_flee=AsyncMock(return_value=True))
    combat = combat_runner._watched_combat_class()(FakeClient(), MagicMock(), watch)
    with (
        patch.object(combat_runner, "_flee", AsyncMock()) as flee,
        patch.object(SprintyCombat, "handle_round", AsyncMock()) as cast,
    ):
        await combat.handle_round()
        flee.assert_awaited_once_with(combat, watch)
        cast.assert_not_awaited()

        watch.should_flee.return_value = False
        await combat.handle_round()
        cast.assert_awaited_once()


# ---------------------------------------------------------------------------
# run_fight
# ---------------------------------------------------------------------------


async def test_a_fight_that_ends_on_its_own_is_finished(monkeypatch):
    monkeypatch.setattr(combat_runner, "_run_combat", AsyncMock())
    result = await run_fight([_client(11), _client(22)], ["a", "b"], [1, 2], time_limit=None, log=_Log())

    assert result == combat_runner.FightResult(FightEnd.FINISHED, [])


async def test_the_time_limit_calls_for_a_flee_and_the_fight_ends_fled(monkeypatch):
    async def fight(client, _dsl, watch):
        # Stands in for SprintyCombat: flees at the first planning phase that says to.
        while not await watch.should_flee(_combat(client)):
            await asyncio.sleep(0.001)
        watch.note_fled(client)

    monkeypatch.setattr(combat_runner, "_run_combat", fight)
    log = _Log()
    with _reports({(11, "*"): ({11}, True, False)}):
        result = await run_fight([_client(11)], ["a"], [1], time_limit=0.05, log=log)

    assert result.end is FightEnd.FLED
    assert log.warnings() == ["The fight ran past its time limit; fleeing"]


async def test_a_fight_that_outlasts_the_flee_is_cancelled_as_stuck(monkeypatch):
    monkeypatch.setattr(combat_runner, "FLEE_TIMEOUT", 0.05)
    cancelled = asyncio.Event()

    async def fight(_client, _dsl, _watch):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(combat_runner, "_run_combat", fight)
    result = await run_fight([_client(11)], ["a"], [1], time_limit=0.05, log=_Log())

    assert result.end is FightEnd.STUCK
    assert cancelled.is_set()


async def test_one_player_raising_does_not_stop_the_others(monkeypatch):
    finished: list[str] = []

    async def fight(_client, dsl, _watch):
        if dsl == "boom":
            raise RuntimeError("lost the duel window")
        await asyncio.sleep(0.01)
        finished.append(dsl)

    monkeypatch.setattr(combat_runner, "_run_combat", fight)
    result = await run_fight([_client(11), _client(22)], ["boom", "ok"], [1, 2], time_limit=None, log=_Log())

    assert result.end is FightEnd.FINISHED
    assert finished == ["ok"]
    assert [(n, str(e)) for n, e in result.errors] == [(1, "lost the duel window")]
