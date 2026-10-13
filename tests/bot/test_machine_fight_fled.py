"""BotMachine.note_fight_fled: the guarded callback handle_combat reports a flee through (Stage 7 of epic #201)."""

from __future__ import annotations

import asyncio
import logging

from bartlebot.bot.machine import BotMachine
from bartlebot.bot.schema import BotConfig, StateModel, TransitionModel
from bartlebot.combat.combat_resolution import Level
from bartlebot.nodes.registry import NODE_SPECS


def _bot() -> BotConfig:
    return BotConfig(
        id="bot-f",
        name="Fled",
        initial="fight",
        recovery_state="fight",
        states=[StateModel(id="fight", node_type="flee"), StateModel(id="after", node_type="done")],
        transitions=[TransitionModel(trigger="advance", source="fight", dest="after", when="FAILURE")],
    )


async def _flee(machine, clients, params):
    """A stand-in for handle_combat's flee: report it, then fail as the node does."""
    machine.note_fight_fled(machine.state, Level.SHIPPED_DEFAULT)
    return "FAILURE"


async def _done(machine, clients, params):
    machine.stop()
    return "SUCCESS"


def _machine(on_fight_fled) -> tuple[BotMachine, list[str]]:
    ran: list[str] = []

    async def done(machine, clients, params):
        ran.append(machine.state)
        return await _done(machine, clients, params)

    bot = _bot()
    machine = BotMachine(
        clients=[],
        config=bot.to_machine_config(node_specs=NODE_SPECS),
        node_registry={"flee": _flee, "done": done},
        bot_config=bot,
        on_fight_fled=on_fight_fled,
        stop_grace_period=0.1,
    )
    return machine, ran


async def test_the_callback_hears_the_state_and_the_level():
    heard: list[tuple] = []
    machine, ran = _machine(lambda state_id, level: heard.append((state_id, level)))
    await asyncio.wait_for(machine.run(), 2.0)
    assert heard == [("fight", Level.SHIPPED_DEFAULT)]
    assert ran == ["after"]


async def test_a_callback_that_raises_never_stops_the_run(caplog):
    def boom(*_):
        raise RuntimeError("tab gone")

    machine, ran = _machine(boom)
    with caplog.at_level(logging.ERROR, logger="bartlebot.bot.machine"):
        await asyncio.wait_for(machine.run(), 2.0)
    assert ran == ["after"], "the run went on to the next state"
    assert any("on_fight_fled callback raised" in r.getMessage() for r in caplog.records)


def test_no_callback_is_no_error():
    machine, _ran = _machine(None)
    machine.note_fight_fled("fight", Level.YOUR_DEFAULT)
