"""Node: enter and complete a combat encounter using WizSprinter."""

import asyncio
import logging
import time

from bartlebot import player_realms
from bartlebot.nodes import node_helpers
from bartlebot.nodes.combat_runner import (
    FightCentre,
    FightEnd,
    duel_centre,
    join_fight,
    resolve_combat,
    run_fight,
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
            "exceeds this many seconds, exit early with FAILURE. A fight still going by then is fled "
            "first. 0 = no limit."
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
            "No valid target found, a player had no mana, players failed to enter and complete combat "
            "within the timeout, or the fight could not be won and was fled.",
        ),
    ],
    required_bot_options=[player_realms.SPEC],
    max_consecutive_retries=5,
)

from bartlebot.nodes.timing import (
    ENTER_COMBAT_TIMEOUT,
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
         Return FAILURE if any player has no mana: it could only pass (#111).
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
         later teleport goes there instead of to the mob (see combat_runner.FightCentre).
         Player Realms: Together -- before anyone teleports, ensure_together() ports anyone not
         confirmed with the others to the leader; FAILURE if that port fails. Once all are in
         battle, one realm and one duel circle are checked; a split is fought out, then
         repaired after step 7 (FAILURE if that port fails).
      6. Run SprintyCombat for all clients in parallel until combat ends (run_fight). A fight
         that cannot be won is fled (#111), and so is one still going when max_duration
         elapses; either returns FAILURE.
      7. Start a background poll of chat for drop announcements, which reports each via
         machine.on_drop(); the node returns without waiting for it.

    FAILURE: a player has no mana; target not found after retries; max_duration exceeded;
    clients did not enter combat within ENTER_COMBAT_TIMEOUT; the fight was fled.  DSL
    resolution itself cannot fail — resolve_combat falls back to the built-in default at
    every level.
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

    # A player with no mana can only pass, and a group of them can only lose or run out the
    # clock (#111). A fight that goes that way anyway is fled; this one need not start.
    if empty := await _players_without_mana(clients):
        machine.log(
            f"FAILURE: {', '.join(f'P{n}' for n in empty)} {'has' if len(empty) == 1 else 'have'} no mana;"
            " not starting a fight",
            logging.WARNING,
        )
        return "FAILURE"

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

    # 3. Teleport clients to their target (sequential or simultaneous). A Together bot
    # does everything it can not to fight split: anyone not confirmed with the others ports
    # to the leader before anyone engages, and the node fails if that port fails. A Separate
    # bot shares a fight centre only between players seen on one realm, so look before
    # anyone moves.
    if await node_helpers.ensure_together(machine, clients, where="before the fight") is (
        node_helpers.TogetherOutcome.FAILED
    ):
        return "FAILURE"
    share = None
    if player_realms.machine_is_separate(machine):
        await node_helpers.read_sightings(clients, machine)
        share = lambda a, b: machine.are_together([a, b])  # noqa: E731
    fight = FightCentre(share)

    async def find_target(client):
        return await _find_target(client, enemy_name)

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
            if not await join_fight(machine, clients[idx], idx, fight, ENTER_COMBAT_TIMEOUT, find_target=find_target):
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
                join_fight(machine, c, i, fight, ENTER_COMBAT_TIMEOUT, find_target=find_target)
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

    # A Together bot's players should be in one fight now. If they are not -- one on
    # another realm after all, or pulled into a second duel -- the fights are finished
    # rather than abandoned mid-duel, and the players brought together afterwards.
    split = not await _in_one_fight(machine, clients)

    machine.log("All clients in combat — running SprintyCombat")

    # 5. Run SprintyCombat in parallel using the per-client DSL resolved earlier. A fight that
    # cannot be won is fled rather than passed out to the end (#111). max_duration is enforced
    # here too, since a fight running long is exactly what it is meant to catch; the players
    # are already in battle, so a fight still going then is fled rather than walked away from.
    time_limit = max_duration - (time.monotonic() - _node_start) if max_duration > 0 else None
    fought = await run_fight(
        clients,
        per_client_dsl,
        list(range(1, len(clients) + 1)),
        time_limit=time_limit,
        log=machine.log,
    )
    for number, exc in fought.errors:
        machine.log(f"P{number}: combat raised {exc!r}", logging.WARNING)
    if fought.end is FightEnd.STUCK:
        machine.log(
            f"FAILURE: max_duration {max_duration:.0f}s exceeded during SprintyCombat, and the fight"
            " did not end after a flee was called for",
            logging.WARNING,
        )
        return "FAILURE"
    if fought.end is FightEnd.FLED:
        machine.log("FAILURE: fled the fight", logging.WARNING)
        return "FAILURE"
    if fought.errors:
        machine.log("FAILURE: combat raised on some players", logging.WARNING)
        return "FAILURE"

    machine.log("SprintyCombat finished for all clients", logging.DEBUG)

    # 6. Watch the chat log for this fight's drop announcements. They are a server
    # round-trip that lands after the duel has locally ended, so this polls rather
    # than reading once -- in the background, so the next node need not wait for it.
    start_combat_drop_poll(machine, clients, list(range(len(clients))))

    # 7. After a split fight, bring everyone back to the leader before the bot moves on.
    if split:
        outcome = await node_helpers.ensure_together(machine, clients, where="after the fight")
        if outcome is node_helpers.TogetherOutcome.FAILED:
            machine.log("FAILURE: the players fought apart and could not be brought together", logging.WARNING)
            return "FAILURE"

    machine.log("SUCCESS: combat complete")
    return "SUCCESS"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _players_without_mana(clients: list) -> list[int]:
    """The 1-based numbers of players at zero mana. One whose mana is unreadable is not counted."""
    ratios = await asyncio.gather(*(c.calc_mana_ratio() for c in clients), return_exceptions=True)
    return [i + 1 for i, ratio in enumerate(ratios) if not isinstance(ratio, BaseException) and ratio <= 0]


async def _find_target(client, enemy_name: str):
    """Return the closest matching entity, or None if not found."""
    if enemy_name:
        return await node_helpers.find_closest_fight_target(client, enemy_name)
    return await client.find_closest_mob()


async def _in_one_fight(machine, clients: list) -> bool:
    """Whether a Together bot's players, all in battle, are in one fight. True when Separate.

    Seeing each other proves one realm, not one duel: two duels can sit side by side. One
    realm and one duel circle does. A centre that cannot be read is no evidence of a split.
    """
    if len(clients) < 2 or player_realms.machine_is_separate(machine):
        return True
    check = await node_helpers.read_sightings(clients, machine)
    centres = [c for c in await asyncio.gather(*(duel_centre(c) for c in clients)) if c is not None]
    one_circle = all(node_helpers.distance(c, centres[0]) <= node_helpers.POSITION_MARGIN for c in centres)
    if check.all_together() and one_circle:
        return True
    groups = [[i + 1 for i in group] for group in check.groups()]
    machine.log(
        f"The players are in separate fights (seen together: {groups}); finishing them, then bringing "
        "everyone together",
        logging.WARNING,
    )
    return False
