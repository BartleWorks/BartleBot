"""Node: enter and complete a combat encounter using WizSprinter."""

import asyncio
import logging
import time
from collections.abc import Callable

from bartlebot import player_realms
from bartlebot.nodes import node_helpers
from bartlebot.nodes.combat_runner import (
    _run_combat,
    resolve_combat,
    seed_chat_baselines,
    start_combat_drop_poll,
)
from bartlebot.nodes.param_schema import NodeSpec, ParamField, ReturnSpec

PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="enemy",
        type="str",
        label="Enemy Name",
        help=(
            "Entity name of the enemy to initiate combat with. An entity with exactly this name "
            "is preferred; failing that, any name containing it, so advanced mode's 'Adv_' copy is "
            "still found. A StandIn (the actor that speaks a pre-battle dialogue) and a boss's "
            "'-mini' pet copy are never picked that way. Wildcards (* ? []) match the whole name "
            "instead, so use '*Bandit*' to match anywhere and 'Bandit*' to match only names "
            "starting with it. Leave empty to use the closest available mob."
        ),
        default="",
        default_hint="targets the closest available mob",
    ),
    ParamField(
        key="wiki_page",
        type="wiki_page",
        label="Wiki Page",
        help=(
            "Wizard101 Central page for the boss this node fights, e.g. 'Creature:Lost Soul'. "
            "Paste the page's URL and it will be stored as the title. Records where the bot's drop "
            "table came from so it can be refreshed later — a dungeon with two bosses records one "
            "page per fight. Leave empty for mob fights, which have no single page. "
            "Not used while the bot runs."
        ),
        default="",
        required=False,
        default_hint="no source page recorded",
    ),
    ParamField(
        key="use_default_strategy",
        type="bool",
        label="Use default strategy",
        help=(
            "When unchecked, this fight node appears in the Combat Tab's State Assignments section "
            "where you can assign a specific Duel Strategy to it. "
            "When checked, the Bot Strategy's default Duel Strategy is used."
        ),
        default=True,
    ),
    ParamField(
        key="max_duration",
        type="float",
        label="Max Duration (s)",
        help=(
            "If the entire combat node (target acquisition + waiting for battle + SprintyCombat) "
            "exceeds this many seconds, exit early with FAILURE. "
            "0 = no limit."
        ),
        default=600.0,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Initiates and completes a combat encounter. Teleports all players to the "
        "target enemy, waits for them to enter battle, and then runs SprintyCombat "
        "until the duel ends. Detects item drops via chat during the encounter."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All players have exited combat successfully."),
        ReturnSpec(
            "FAILURE",
            "No valid target found, or players failed to enter and complete combat within the timeout.",
        ),
    ],
    required_bot_options=[player_realms.SPEC],
    max_consecutive_retries=5,
)

from bartlebot.nodes.timing import (
    COMBAT_TP_RETRY_INTERVAL,
    ENTER_COMBAT_TIMEOUT,
    POLL_INTERVAL,
    TARGET_ACQUIRE_DELAY,
    TARGET_ACQUIRE_RETRIES,
    jitter,
)


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: node_helpers.find_closest_fight_target(), SprintyClient.find_closest_mob(),
          SprintyClient.tp_to(), SprintyClient.in_battle(), SprintyCombat

    Flow:
      1. Resolve combat DSL via the Duel Combat Strategy assigned to this state, or the
         active Bot Combat Strategy's default, or the BartleBot built-in default.
      2. Resolve target entity for each client (by enemy name or closest mob), retrying
         TARGET_ACQUIRE_RETRIES times. Return FAILURE if target not found.
      3. If max_duration is set and has elapsed, return FAILURE.
      4. Snapshot chat to enable drop detection after combat.
      5. Teleport clients to their targets:
         - If the resolved Duel Combat Strategy sets a combat order: send clients one at
           a time in that order, waiting for each to report in_battle().
         - Otherwise: teleport all clients simultaneously, retrying every COMBAT_TP_RETRY_INTERVAL
           until all report in_battle(). Return FAILURE if ENTER_COMBAT_TIMEOUT expires.
         Either way, once any client is in battle its fight centre is published, and every
         later teleport goes there instead of to the mob (see _FightCentre).
      6. If max_duration is set and has elapsed, return FAILURE.
      7. Run SprintyCombat for all clients in parallel until combat ends.
      8. Start a background poll of chat for drop announcements, which reports each via
         machine.on_drop(); the node returns without waiting for it.

    FAILURE: target not found after retries; max_duration exceeded; clients did not enter
    combat within ENTER_COMBAT_TIMEOUT.  DSL resolution itself cannot fail — resolve_combat
    falls back to the built-in default at every level.
    """
    max_duration: float = float(params.get("max_duration", 0.0))
    _node_start = time.monotonic()

    def _check_max_duration() -> bool:
        """Return True if max_duration is set and has been exceeded."""
        return max_duration > 0 and time.monotonic() - _node_start > max_duration

    enemy_name: str = params.get("enemy", "")
    use_default_strategy: bool = params.get("use_default_strategy", True)

    # Resolve per-client DSL list, combat order, and source label.
    per_client_dsl, combat_order, dsl_source = resolve_combat(machine, use_default_strategy, len(clients))

    enemy_display = repr(enemy_name) if enemy_name else "(closest)"
    machine.log(f"handle_combat: enemy={enemy_display}, DSL source={dsl_source!r}")
    if per_client_dsl:
        machine.log(
            f"Combat DSL (P1): {per_client_dsl[0][:80]}{'…' if len(per_client_dsl[0]) > 80 else ''}",
            logging.DEBUG,
        )

    # 1. Resolve target entity for each client, retrying to allow enemies time to load.
    targets = None
    results: list = []
    for attempt in range(TARGET_ACQUIRE_RETRIES):
        results = list(await asyncio.gather(*[_find_target(c, enemy_name) for c in clients]))
        if all(results):
            targets = results
            break
        if attempt < TARGET_ACQUIRE_RETRIES - 1:
            machine.log(
                f"Target not found on attempt {attempt + 1}/{TARGET_ACQUIRE_RETRIES},"
                f" waiting {TARGET_ACQUIRE_DELAY}s for enemy to load\u2026",
                logging.DEBUG,
            )
            await asyncio.sleep(jitter(TARGET_ACQUIRE_DELAY))

    if not targets:
        none_idx = [i for i, t in enumerate(results) if t is None]
        machine.log(
            f"FAILURE: target not found for client(s) {none_idx} after"
            f" {TARGET_ACQUIRE_RETRIES} attempt(s) (enemy={enemy_display})",
            logging.WARNING,
        )
        return "FAILURE"

    if _check_max_duration():
        machine.log(
            f"FAILURE: max_duration {max_duration:.0f}s exceeded during target acquisition",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log(f"Targets resolved for {len(clients)} client(s)", logging.DEBUG)

    # 2. Make sure every client has a chat baseline to diff against. Clients that
    # already carry one from an earlier fight keep it, so an announcement that missed
    # that fight's poll window is still ahead of the baseline and gets caught below.
    await seed_chat_baselines(machine, clients)

    # 3. Teleport clients to their target (sequential or simultaneous). A Separate bot shares
    # a fight centre only between players seen on one realm, so look before anyone moves.
    share = None
    if player_realms.machine_is_separate(machine):
        await node_helpers.read_sightings(clients, machine)
        share = lambda a, b: machine.are_together([a, b])  # noqa: E731
    fight = _FightCentre(share)
    if combat_order:
        machine.log(f"Entering combat sequentially: order={combat_order}", logging.DEBUG)
        for client_num in combat_order:
            idx = client_num - 1  # user-facing 1-based → 0-based list index
            if idx < 0 or idx >= len(clients):
                machine.log(
                    f"combat_order: skipping client {client_num} — not connected (have {len(clients)} client(s))",
                    logging.DEBUG,
                )
                continue
            if not await _tp_and_wait_for_combat(machine, clients[idx], idx, enemy_name, fight, ENTER_COMBAT_TIMEOUT):
                machine.log(
                    f"FAILURE: client {client_num} did not enter combat within {ENTER_COMBAT_TIMEOUT}s",
                    logging.WARNING,
                )
                return "FAILURE"
    else:
        # 4. Teleport all clients to their targets, retrying until in combat
        machine.log("Teleporting all clients to target (retrying until in combat)…", logging.DEBUG)
        results = await asyncio.gather(
            *[
                _tp_and_wait_for_combat(machine, c, i, enemy_name, fight, ENTER_COMBAT_TIMEOUT)
                for i, c in enumerate(clients)
            ]
        )
        if not all(results):
            not_in_battle = [i for i, ok in enumerate(results) if not ok]
            machine.log(f"Clients not in battle at timeout: {not_in_battle}", logging.DEBUG)
            machine.log(
                f"FAILURE: timed out after {ENTER_COMBAT_TIMEOUT}s waiting for clients to enter combat",
                logging.WARNING,
            )
            return "FAILURE"

    if _check_max_duration():
        machine.log(
            f"FAILURE: max_duration {max_duration:.0f}s exceeded before SprintyCombat",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log("All clients in combat — running SprintyCombat")

    # 5. Run SprintyCombat in parallel using the per-client DSL resolved earlier.
    # Enforced against max_duration too: a fight running long is exactly the case
    # the timeout is supposed to catch, not just the wait to enter it.
    combat_awaitable = asyncio.gather(*[_run_combat(c, dsl) for c, dsl in zip(clients, per_client_dsl)])
    if max_duration > 0:
        remaining = max_duration - (time.monotonic() - _node_start)
        try:
            await asyncio.wait_for(combat_awaitable, timeout=max(remaining, 0))
        except TimeoutError:
            machine.log(
                f"FAILURE: max_duration {max_duration:.0f}s exceeded during SprintyCombat",
                logging.WARNING,
            )
            return "FAILURE"
    else:
        await combat_awaitable

    machine.log("SprintyCombat finished for all clients", logging.DEBUG)

    # 6. Watch the chat log for this fight's drop announcements. They are a server
    # round-trip that lands after the duel has locally ended, so this polls rather
    # than reading once -- in the background, so the next node need not wait for it.
    start_combat_drop_poll(machine, clients, list(range(len(clients))))

    machine.log("SUCCESS: combat complete")
    return "SUCCESS"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _find_target(client, enemy_name: str):
    """Return the closest matching entity, or None if not found."""
    if enemy_name:
        return await node_helpers.find_closest_fight_target(client, enemy_name)
    return await client.find_closest_mob()


class _FightCentre:
    """The centre of this node run's fight, published by the first client seen in battle.

    Once a fight starts its mob walks to its seat, or is hidden or swapped, so a late client
    re-aiming at the mob aims at a moving or absent target (#110). The centre of the duel
    circle stays put, and teleporting there joins the fight just as teleporting onto an
    engaged mob does.

    The centre is read from the duel the client is in, and only once in_battle() has read
    true: outside a fight the duel struct still holds the last fight's data.

    *share* says whether one client's centre is any use to another. None shares it with
    everybody, which is right when every player is on one realm. A Separate bot (#132)
    passes the machine's record of who shares a realm: a centre on another realm is the
    coordinates of a fight the client is not in, and teleporting there starts a second one.
    Each group then publishes a centre of its own.
    """

    def __init__(self, share: Callable[[object, object], bool] | None = None):
        self._share = share
        self._centres: list[tuple[object, object]] = []  # (publisher, centre)

    def centre_for(self, client):
        """The centre *client* should aim at, or None while nobody it shares a realm with has one."""
        for publisher, centre in self._centres:
            if self._share is None or publisher is client or self._share(publisher, client):
                return centre
        return None

    async def publish(self, machine, client, index: int) -> None:
        """Record *client*'s fight centre if its group has none yet. Call only while it is in battle."""
        if self.centre_for(client) is not None:
            return
        try:
            centre = await client.duel.position()
        except Exception as exc:
            machine.log(
                f"Client {index}: fight centre unreadable ({exc!r}); late clients keep aiming at the mob",
                logging.DEBUG,
            )
            return
        if self.centre_for(client) is not None:  # another client published while this one read
            return
        self._centres.append((client, centre))
        where = await _position(client)
        machine.log(
            f"Client {index} is in battle; publishing its fight centre"
            + (f" ({where.distance(centre):.1f} units from it)" if where is not None else ""),
            logging.DEBUG,
        )


async def _tp_and_wait_for_combat(
    machine, client, index: int, enemy_name: str, fight: _FightCentre, timeout: float
) -> bool:
    """Teleport to target, retrying every COMBAT_TP_RETRY_INTERVAL seconds until in combat.

    Once *fight* has a centre, each attempt teleports there instead of re-finding the mob,
    and a client that gets in first publishes its own centre for the rest.

    A target that disappears is waited out rather than given up on: an encounter that swaps
    or hides its mob as its fight starts may still pull the player in before the timeout.
    Giving up at once also said nothing, which left a player standing still for the rest
    of the wait with no line in the log to say why.

    Each re-teleport logs how far it moved the player. Zero means the player was already
    on the mob or the centre, so the retry could not have started the fight.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    attempt = 0
    target_lost = False
    aimed_at_centre = False
    while True:
        attempt += 1
        if (centre := fight.centre_for(client)) is not None:
            if not aimed_at_centre:
                machine.log(f"Client {index}: teleporting to the fight centre on attempt {attempt}", logging.DEBUG)
                aimed_at_centre = True
            before = await _position(client) if attempt > 1 else None
            try:
                await client.teleport(centre)
            except Exception as exc:
                machine.log(f"Client {index}: teleport to the fight centre failed ({exc!r})", logging.DEBUG)
            else:
                await _log_re_teleport(machine, client, index, attempt, before)
        elif (target := await _find_target(client, enemy_name)) is None:
            if not target_lost:
                machine.log(
                    f"Client {index}: target not found on attempt {attempt}; waiting for the fight without teleporting",
                    logging.DEBUG,
                )
                target_lost = True
        else:
            if target_lost:
                machine.log(f"Client {index}: target found again on attempt {attempt}", logging.DEBUG)
                target_lost = False
            before = await _position(client) if attempt > 1 else None
            await client.tp_to(target)
            await _log_re_teleport(machine, client, index, attempt, before)

        # Poll in_battle() for up to COMBAT_TP_RETRY_INTERVAL (or remaining time)
        retry_at = min(loop.time() + COMBAT_TP_RETRY_INTERVAL, deadline)
        while loop.time() < retry_at:
            if await client.in_battle():
                await fight.publish(machine, client, index)
                return True
            await asyncio.sleep(POLL_INTERVAL)

        if loop.time() >= deadline:
            break

    return False


async def _log_re_teleport(machine, client, index: int, attempt: int, before) -> None:
    """Log how far a re-teleport moved the player; *before* is None on the first attempt."""
    if before is not None and (after := await _position(client)) is not None:
        machine.log(
            f"Client {index}: not in battle; re-teleport {attempt} moved {before.distance(after):.1f} units",
            logging.DEBUG,
        )


async def _position(client):
    """The client's position, or None when it cannot be read; only a log line depends on it."""
    try:
        return await client.body.position()
    except Exception:
        return None
