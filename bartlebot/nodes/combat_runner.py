"""What a BartleBot fight needs from the bot: its strategy, its drops, and getting into it.

The fight itself is bartleby-core's (``bartleby.runtime.combat``): running each player's
combat, fleeing a fight that cannot be won, checking a DSL, and holding a fighter's first
cast. What stays here reads a bot: :func:`resolve_combat`, the DSLs a fight runs from the
four levels of ``combat_resolution``; the chat-log drop poll after a fight, through the
machine's baselines; and joining a fight already under way (:class:`FightCenter`,
:func:`join_fight`). ``handle_combat`` and the ambient watcher use both.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

from bartleby.runtime.timing import (
    COMBAT_DROP_MESSAGE_POLL,
    COMBAT_DROP_MESSAGE_TIMEOUT,
    COMBAT_TP_RETRY_INTERVAL,
    POLL_INTERVAL,
)
from bartleby.shared.combat_defaults import LAST_RESORT_DSL, slot_dsls

from bartlebot.game import drop_detection

if TYPE_CHECKING:
    from bartlebot.combat.combat_resolution import Level

logger = logging.getLogger(__name__)

#: Spelled as ``combat_resolution.LAST_RESORT_SOURCE``, which this module cannot import at load.
_BUILTIN_SOURCE = "BartleBot built-in default"


class ResolvedCombat(NamedTuple):
    """What :func:`resolve_combat` hands a fight: a DSL per player slot, the order to enter, and where it came from."""

    dsls: list[str]
    order: list[int]
    source: str
    """The phrase the log names it by (``FightResolution.source_label``)."""
    level: "Level"
    """Which of the four levels answered, so a flee can say whether a default was running."""


def resolve_combat(
    machine,
    use_default_strategy: bool,
    num_clients: int,
    *,
    ambient: bool = False,
) -> ResolvedCombat:
    """Return the per-slot DSLs, combat order, source label and level for the fight *machine* is on.

    The duel comes from the four levels in :mod:`bartlebot.combat.combat_resolution`: the
    user's Bot Combat Strategy for the bot, the bot's Official one, the user's Solo or Team
    default, the shipped default. ``use_default_strategy`` is the node's param: True runs the
    bot's default fight, False the fight on ``machine.state``. ``ambient`` is a fight no node
    started, which the label says.

    Each 1-based player slot gets its assignment's DSL, or the linked player strategy's.
    A slot with none, or whose player strategy cannot be read, gets ``LAST_RESORT_DSL``,
    and so does every slot when nothing can be read at all. Never raises: a fight always
    has something to run, and the label says what.
    """
    # Lazy imports to avoid circular imports and keep startup fast.
    from bartlebot.combat.combat_resolution import Level, resolve_fight  # noqa: PLC0415
    from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415

    last_resort = ResolvedCombat([LAST_RESORT_DSL] * num_clients, [], _BUILTIN_SOURCE, Level.LAST_RESORT)
    bot_cfg = machine.bot_config
    if bot_cfg is None:
        return last_resort

    state_id = None if use_default_strategy else getattr(machine, "state", "")
    try:
        pick = SettingsManager.instance().get_active_strategy(bot_cfg.id)
        res = resolve_fight(bot_cfg, state_id, num_clients, pick, ambient=ambient)
    except Exception:
        logger.exception("resolve_combat: could not resolve the fight -- using the built-in default")
        return last_resort
    if res.duel is None:
        logger.error("resolve_combat: no strategy file could be read -- using the built-in default")
        return last_resort._replace(source=res.source_label)
    logger.debug("resolve_combat: %s", res.source_label)
    dcs = res.duel

    def player(pcs_id: str):
        pcs = res.lookup.player(pcs_id)
        if pcs is None:
            logger.warning(
                "resolve_combat: failed to load PlayerCombatStrategy %s — affected players use built-in default",
                pcs_id,
            )
        return pcs

    # 1-based player numbers, P1 first, as the clients are indexed from 0.
    per_client_dsl = slot_dsls(dcs, player, num_clients)

    combat_order: list[int] = dcs.combat_order if dcs.combat_order is not None else []

    return ResolvedCombat(per_client_dsl, combat_order, res.source_label, res.level)


async def seed_chat_baselines(machine, clients: list) -> None:
    """Give every client in *clients* a chat-log baseline if it does not have one.

    Called before a fight starts. A client that already has a baseline keeps it: that
    is what lets an announcement which missed the previous fight's poll window still be
    detected by this fight's. Re-snapshotting here would absorb it instead, which is
    the bug this whole path exists to fix.
    """
    unseeded = [c for c in clients if machine.chat_baseline(c) is None]
    if not unseeded:
        return
    snapshots = await asyncio.gather(*[drop_detection.snapshot_chat(c) for c in unseeded], return_exceptions=True)
    for client, snap in zip(unseeded, snapshots):
        # An unreadable chat log seeds "" rather than staying unseeded: leaving it None
        # would make the next fight seed from a log that already holds this fight's
        # drops, silently losing them. "" over-reports at worst, and only once.
        machine.set_chat_baseline(client, snap if isinstance(snap, str) else "")


async def poll_combat_drops(machine, clients: list, slots: list[int]) -> list[str]:
    """Watch each client's chat log for drops from the fight that just ended.

    ``clients`` and ``slots`` are parallel: ``slots[i]`` is the player slot of
    ``clients[i]``, which is what the drop is attributed to. They differ whenever the
    caller is handling a subset of the client list, as the ambient watcher does.

    Polls rather than reading once, because the announcement is a server round-trip
    that lands after the duel has locally ended — and end-of-fight is exactly when a
    long session lags hardest. Every client is polled concurrently: serially, a group
    of four would pay four full timeouts on a fight that dropped nothing.

    Returns the item names found, for logging. Each is already reported via
    ``machine.on_drop``.
    """

    async def _one(client, slot: int) -> list[str]:
        before = machine.chat_baseline(client)
        if before is None:
            # Never seeded — there is nothing to diff against, and treating this as ""
            # would report the entire existing chat log as drops. Seed and report none.
            machine.set_chat_baseline(client, await drop_detection.snapshot_chat(client))
            return []
        drops, baseline = await drop_detection.poll_new_drops(
            client, before, COMBAT_DROP_MESSAGE_TIMEOUT, COMBAT_DROP_MESSAGE_POLL
        )
        # Stored on a miss too: poll_new_drops hands back the caller's own baseline
        # unchanged, so a late announcement is still ahead of it next fight.
        machine.set_chat_baseline(client, baseline)
        for name in drops:
            machine.on_drop(name, 1, slot)
        return drops

    results = await asyncio.gather(*[_one(c, s) for c, s in zip(clients, slots)], return_exceptions=True)

    found: list[str] = []
    for slot, result in zip(slots, results):
        if isinstance(result, Exception):
            # A failed read must not fail the fight that already succeeded. Logged here
            # rather than through machine.log: this usually runs in the background, after
            # the node that started it has finished.
            logger.debug("Drop detection error on client %d: %s", slot, result)
            continue
        found.extend(result)
    return found


def start_combat_drop_poll(machine, clients: list, slots: list[int]) -> None:
    """Run ``poll_combat_drops`` for a finished fight in the background.

    The fight's caller returns at once instead of waiting out the poll window, which a
    fight that drops nothing pays in full. ``machine.start_drop_poll`` owns the task and
    cancels it when the next fight's poll starts; its docstring says why that is safe.
    """

    async def _poll() -> None:
        try:
            found = await poll_combat_drops(machine, clients, slots)
        except Exception:
            logger.exception("Drop poll for client(s) %s raised", slots)
            return
        if found:
            logger.info("Detected %d drop(s) on client(s) %s: %s", len(found), slots, ", ".join(found))
        else:
            logger.debug("No drops detected on client(s) %s", slots)

    machine.start_drop_poll(asyncio.create_task(_poll()))


# ---------------------------------------------------------------------------
# Joining a fight already under way
# ---------------------------------------------------------------------------


@dataclass
class _Fight:
    publisher: object
    center: object
    members: list = field(default_factory=list)
    """Every client seen in battle in this fight, the publisher first."""
    center_blocked: bool = False
    """The center put a client back where it started instead of into the fight (#157)."""


class FightCenter:
    """The center of each fight our players are joining, published by the first of them seen in battle.

    Once a fight starts its mob walks to its seat, or is hidden or swapped, so a late client
    re-aiming at the mob aims at a moving or absent target (#110). The center of the duel
    circle stays put, and teleporting there joins the fight just as teleporting onto an
    engaged mob does.

    The center is read from the duel the client is in, and only once in_battle() has read
    true: outside a fight the duel struct still holds the last fight's data.

    *share* says whether one client's center is any use to another. None shares it with
    everybody, which is right when every player is on one realm. Otherwise pass the machine's
    record of who shares a realm: a center on another realm is the coordinates of a fight the
    client is not in, and teleporting there starts a second one. Each group then publishes a
    center of its own.

    Some centers cannot be stood on (#157). A zone can have an obstacle in its duel area that
    the fight clears, and the spot it took stays invalid afterwards: a player teleported there
    is put straight back at its last valid position, outside the circle. Once a client sees
    that, the center is marked blocked for the whole fight, and every later attempt aims at a
    player already in it instead. A seat is a spot inside the circle a player stands on.
    """

    def __init__(self, share: Callable[[object, object], bool] | None = None):
        self._share = share
        self._fights: list[_Fight] = []

    def _shares(self, fight: _Fight, client) -> bool:
        return self._share is None or fight.publisher is client or self._share(fight.publisher, client)

    def _fight_for(self, client) -> _Fight | None:
        """The fight *client* is in, else the first on its realm: the one it would join."""
        for fight in self._fights:
            if client in fight.members:
                return fight
        for fight in self._fights:
            if self._shares(fight, client):
                return fight
        return None

    def is_center_blocked(self, client) -> bool:
        """Whether the center *client* would aim at is known to put players back where they started."""
        fight = self._fight_for(client)
        return fight is not None and fight.center_blocked

    def mark_center_blocked(self, client) -> None:
        """Record that the center *client* aims at cannot be stood on, for every client joining that fight."""
        if (fight := self._fight_for(client)) is not None:
            fight.center_blocked = True

    async def member_position(self, client, skip: int = 0):
        """Where a player in *client*'s fight stands, or None when no member's position can be read.

        *skip* rotates which member comes first, so successive attempts try each in turn; a
        member whose position cannot be read is passed over for the next.
        """
        fight = self._fight_for(client)
        members = [m for m in fight.members if m is not client] if fight is not None else []
        for i in range(len(members)):
            if (where := await _position(members[(skip + i) % len(members)])) is not None:
                return where
        return None

    def groups(self) -> list[list]:
        """Every fight's members, each list in the order they were seen in battle."""
        return [list(fight.members) for fight in self._fights]

    def center_for(self, client):
        """The center *client* should aim at, or None while nobody it shares a realm with has one."""
        fight = self._fight_for(client)
        return fight.center if fight is not None else None

    async def publish(self, machine, client, index: int) -> None:
        """Record *client* as in its group's fight, publishing the center if there is none yet.

        Call only while *client* is in battle.
        """
        if any(client in fight.members for fight in self._fights):
            return
        try:
            center = await client.duel.position()
        except Exception as exc:
            if (fight := self._fight_for(client)) is not None:
                _join(fight, client)  # nothing better to go on than the realm
                return
            machine.log(
                f"Client {index}: fight center unreadable ({exc!r}); late clients keep aiming at the mob",
                logging.DEBUG,
            )
            return
        # A second fight on one realm has a circle of its own; one within a hair is this one.
        for fight in self._fights:
            if self._shares(fight, client) and _distance(fight.center, center) <= _SAME_CIRCLE:
                _join(fight, client)
                return
        self._fights.append(_Fight(client, center, [client]))
        where = await _position(client)
        machine.log(
            f"Client {index} is in battle; publishing its fight center"
            + (f" ({where.distance(center):.1f} units from it)" if where is not None else ""),
            logging.DEBUG,
        )

    async def is_over(self, client) -> bool:
        """Whether the fight *client* would join has ended: nobody seen in it is still in battle.

        False while *client* has no center to aim at, and False when a read fails: giving up on
        a fight still going leaves a player out of it, which is worse than one wasted teleport.
        """
        fight = self._fight_for(client)
        if fight is None:
            return False
        for member in fight.members:
            try:
                if await member.in_battle():
                    return False
            except Exception:
                return False
        return True


def _join(fight: _Fight, client) -> None:
    if client not in fight.members:
        fight.members.append(client)


#: Two duel centers this close are one circle. A center is read from the duel itself, so every
#: client in one fight reads the same point; this is only room for float noise.
_SAME_CIRCLE = 10.0


def _distance(a, b) -> float:
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2) ** 0.5


async def join_fight(
    machine,
    client,
    index: int,
    fight: FightCenter,
    timeout: float,
    *,
    find_target: Callable[[object], Awaitable[object]] | None = None,
) -> bool:
    """Teleport *client* into a fight, retrying every COMBAT_TP_RETRY_INTERVAL until it is in battle.

    Once *fight* has a center for the client, each attempt teleports there, and a client that
    gets in first publishes its own center for the rest. A center that puts the client back
    exactly where it started is marked blocked (see FightCenter), and from then on attempts aim
    at a player in the fight instead, a different one each time, or at the center again when
    no member's position can be read. Before that, *find_target* says what
    to aim at: handle_combat passes its find-the-mob, which is how a fight starts at all. The
    ambient watcher passes nothing, so a client only ever teleports to a published center: a
    teleport to "the closest mob" could start a second fight.

    Gives up, returning False, at *timeout* or once the fight it would join is over (see
    FightCenter.is_over): a late client teleporting onto an empty circle joins nothing.

    A target that disappears is waited out rather than given up on: an encounter that swaps
    or hides its mob as its fight starts may still pull the player in before the timeout.
    Giving up at once also said nothing, which left a player standing still for the rest
    of the wait with no line in the log to say why.

    Each re-teleport logs how far it moved the player. Zero means the player was already
    on the mob or the center, so the retry could not have started the fight.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    attempt = 0
    target_lost = False
    aimed_at = None
    member_attempts = 0
    waiting_noted = False
    while True:
        attempt += 1
        start = None  # where the client stood before a teleport to the center, to see it put back
        if (center := fight.center_for(client)) is not None:
            if await fight.is_over(client):
                machine.log(f"Client {index}: the fight ended before it could join", logging.DEBUG)
                return False
            target, aim = center, "the fight center"
            if fight.is_center_blocked(client):
                if (seat := await fight.member_position(client, member_attempts)) is not None:
                    target, aim = seat, "a player in the fight"
                    member_attempts += 1
            if aim != aimed_at:
                machine.log(f"Client {index}: teleporting to {aim} on attempt {attempt}", logging.DEBUG)
                aimed_at = aim
            before = await _position(client)
            try:
                await client.teleport(target)
            except Exception as exc:
                machine.log(f"Client {index}: teleport to {aim} failed ({exc!r})", logging.DEBUG)
            else:
                await _log_re_teleport(machine, client, index, attempt, before if attempt > 1 else None)
                if target is center:
                    start = before
        elif find_target is None:
            if not waiting_noted:
                machine.log(f"Client {index}: no fight center yet; waiting without teleporting", logging.DEBUG)
                waiting_noted = True
        elif (target := await find_target(client)) is None:
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

        # Poll in_battle() for up to COMBAT_TP_RETRY_INTERVAL (or remaining time). After a
        # teleport to the center, also watch for the game putting the client back where it
        # started: seen away from the start, then on it exactly. No distance threshold: the game
        # restores the stored last valid position, so the coordinates come back identical.
        retry_at = min(loop.time() + COMBAT_TP_RETRY_INTERVAL, deadline)
        moved = False
        while loop.time() < retry_at:
            if await client.in_battle():
                await fight.publish(machine, client, index)
                return True
            if start is not None and (now := await _position(client)) is not None:
                if not _same_place(now, start):
                    moved = True
                elif moved:
                    if not fight.is_center_blocked(client):  # known already if no seat could be read
                        fight.mark_center_blocked(client)
                        machine.log(
                            f"Client {index}: the fight center put it back where it started;"
                            " joining by a player in the fight instead",
                            logging.INFO,
                        )
                    break
            await asyncio.sleep(POLL_INTERVAL)

        if loop.time() >= deadline:
            break

    return False


async def duel_center(client):
    """The center of *client*'s duel, or None when it cannot be read."""
    try:
        return await client.duel.position()
    except Exception:
        return None


async def _log_re_teleport(machine, client, index: int, attempt: int, before) -> None:
    """Log how far a re-teleport moved the player; *before* is None on the first attempt."""
    if before is not None and (after := await _position(client)) is not None:
        machine.log(
            f"Client {index}: not in battle; re-teleport {attempt} moved {before.distance(after):.1f} units",
            logging.DEBUG,
        )


def _same_place(a, b) -> bool:
    """Whether two positions are identical. XYZ defines no equality of its own."""
    return tuple(a) == tuple(b)


async def _position(client):
    """The client's position, or None when it cannot be read; only a log line depends on it."""
    try:
        return await client.body.position()
    except Exception:
        return None
