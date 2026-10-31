"""Hands-on utilities for driving several clients while authoring a bot (#81).

Recording a bot means walking four wizards through a dungeon by hand, and moving each one
separately is most of the work. These are the four utilities that take it away, the same
set a Deimos user reaches for while multiboxing:

* **XYZ sync** puts every other player on the source player's spot, in the same zone.
* **TP to player** ports every other player to the source player through the friends list,
  which is the one that crosses zones and realms. It is ``teleport_to_friend``'s hooked-player
  half, run outside a bot.
* **Speed** multiplies every player's walking speed, and keeps it multiplied across zone
  changes -- the game rewrites the value on every door.
* **Auto-combat** fights any battle a player is pulled into, with the open bot's default
  Duel Strategy, or the built-in one when it has none.
* **Auto-dialogue** advances every player's NPC dialogue as it opens, and declines any side
  quest offered on the way -- ``node_helpers.advance_dialog_once``, which a run's own dialogue
  pressing goes through too.
* **Speed-authoring** (#85) arms keys 1-0 inside a game window: each adds its hotbar node
  after the selected one and starts recording it, so a dungeon is recorded without leaving
  the game. ``BotCanvas.speed_insert`` places the node; the toggle only says when the keys
  are live, which ``slot_keys_armed`` decides.

The *source* of the first two is the game window that last had focus, falling back to
Player 1. "Last", because clicking a button in BartleBot takes the focus away from the game.

None of this belongs in a run. A run walks at the game's own speed -- its timeouts and a
recording's pacing assume it -- and fights with its own ``handle_combat`` nodes, which would
race an auto-combat task for the same cards, and advances dialogue with its own watcher. So a
run switches the toggles off before it starts (``suspend_for_run``), and every action refuses while one is in progress. A run
switches speed-authoring off too: its keys add nodes, and a run's canvas takes none.

Auto-combat refuses PvP exactly as a run does: ``restriction_for`` is the check the health
monitor's watcher makes, and a hit turns auto-combat off rather than fighting the duel.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from enum import StrEnum
from types import SimpleNamespace

from bartleby.runtime.game import wizard_names
from bartleby.runtime.launcher.auto_select import at_character_select
from bartleby.runtime.timing import DIALOG_FAST_POLL_INTERVAL, POLL_INTERVAL, SPEED_REAPPLY_INTERVAL

from bartlebot.bot.client_health_monitor import restriction_for
from bartlebot.nodes import node_helpers
from bartlebot.nodes.combat_runner import _run_combat, resolve_combat

logger = logging.getLogger(__name__)

# The largest value the game's int16 speed field holds.
_SPEED_VALUE_MAX = 0x7FFF

# The end of a player's client object's type name: ClientObject, or the game's own
# WizClientObject.
_CLIENT_OBJECT_TYPE = "ClientObject"


class AuthoringAction(StrEnum):
    """The utilities, by the id their hotkey is stored under in settings.

    In Bar A's order, which is also the Settings tab's and the default F-keys': the moves,
    then the toggles that act on every player, then speed-authoring, which acts on the canvas.
    """

    XYZ_SYNC = "xyz_sync"
    TP_TO_FOCUSED = "tp_to_focused"
    TOGGLE_SPEED = "toggle_speed"
    TOGGLE_COMBAT = "toggle_combat"
    TOGGLE_DIALOGUE = "toggle_dialogue"
    TOGGLE_SPEED_AUTHORING = "toggle_speed_authoring"


#: What each action is called on a button and in a settings row.
ACTION_LABELS: dict[AuthoringAction, str] = {
    AuthoringAction.XYZ_SYNC: "XYZ sync",
    AuthoringAction.TP_TO_FOCUSED: "Teleport to player",
    AuthoringAction.TOGGLE_SPEED: "Speed",
    AuthoringAction.TOGGLE_COMBAT: "Auto-combat",
    AuthoringAction.TOGGLE_DIALOGUE: "Auto-dialogue",
    AuthoringAction.TOGGLE_SPEED_AUTHORING: "Speed-authoring",
}


#: The keys speed-authoring takes while it is on: the favorites bar's slots, 1-9 then 0.
SLOT_KEYS: tuple[str, ...] = tuple(str(n) for n in (*range(1, 10), 0))


def slot_keys_armed(*, speed_authoring: bool, game_focused: bool, editing: bool, run_active: bool) -> bool:
    """Whether speed-authoring's keys 1-0 should be registered (#85).

    Only while it is switched on, since while registered the digits are taken from the
    game's chat as well; only while a game window has the keyboard, as for every
    authoring key; and only while the canvas can take a node, in Edit mode with no run.
    """
    return speed_authoring and game_focused and editing and not run_active


def boosted_speed_value(multiplier: float) -> int:
    """The game's speed field for walking *multiplier* times as fast.

    The field is a percentage *bonus* -- 0 is normal speed, 100 is double -- which is also
    how a mount's speed reaches it. Below 1x is clamped to normal: slowing a wizard down is
    not something anyone reaches for, and a negative bonus is untested ground.
    """
    return max(0, min(round((multiplier - 1.0) * 100), _SPEED_VALUE_MAX))


def source_index(clients: list, hwnd: int | None) -> int:
    """The index of the client whose window is *hwnd*, or 0 (Player 1) when none is."""
    for i, client in enumerate(clients):
        if hwnd is not None and getattr(client, "window_handle", None) == hwnd:
            return i
    return 0


class _ModuleLog:
    """The one method ``node_helpers.gather_at_player`` needs from a machine: ``log``.

    Outside a run there is no machine and no node to file a record under, so the helper's
    reasons go to this module's logger instead.
    """

    @staticmethod
    def log(message: str, level: int = logging.INFO) -> None:
        logger.log(level, "TP to player: %s", message)


class AuthoringTools:
    """The state behind the utilities: four toggles, and at most one action in flight.

    *clients* and *bot_config* are getters rather than values, because both change under
    it: clients hook and unhook, and the open bot is swapped whenever a file is opened.
    *on_change* is called whenever a toggle flips or an action starts or ends, so a surface
    can redraw; it is guarded, since a GUI exception must not kill a toggle's task.
    """

    def __init__(
        self,
        clients: Callable[[], list],
        bot_config: Callable[[], object | None],
        speed_multiplier: Callable[[], float],
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self._clients = clients
        self._bot_config = bot_config
        self._speed_multiplier = speed_multiplier
        self._on_change = on_change
        self._run_active = False

        self._speed_task: asyncio.Task | None = None
        # The field value this toggle writes, fixed when it is switched on: a multiplier
        # changed in Settings meanwhile would otherwise read the old boost as the game's
        # own value and "restore" it.
        self._boost_value = 0
        # The game's own value per window, refreshed whenever the game rewrites it, with the
        # character it was read from.
        self._originals: dict[int, tuple[int, int]] = {}
        # The character each window's last tick read, for the two-tick rule in ``_boost``.
        self._characters: dict[int, int] = {}

        self._combat_task: asyncio.Task | None = None
        self._fights: dict[int, asyncio.Task] = {}

        self._dialogue_task: asyncio.Task | None = None

        self._action_task: asyncio.Task | None = None

        self._speed_authoring = False

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def speed_on(self) -> bool:
        return self._speed_task is not None

    @property
    def combat_on(self) -> bool:
        return self._combat_task is not None

    @property
    def dialogue_on(self) -> bool:
        return self._dialogue_task is not None

    @property
    def speed_authoring_on(self) -> bool:
        """Whether keys 1-0 add hotbar nodes from inside a game window (#85)."""
        return self._speed_authoring

    def set_speed_authoring(self, on: bool) -> None:
        if on == self._speed_authoring:
            return
        if on and self._refuse_during_run("Speed-authoring"):
            return
        self._speed_authoring = on
        logger.info("Speed-authoring %s", "on: keys 1-0 add hotbar nodes from a game window" if on else "off")
        self._changed()

    @property
    def busy(self) -> bool:
        """Whether an XYZ sync or a TP is in flight."""
        return self._action_task is not None and not self._action_task.done()

    @property
    def run_active(self) -> bool:
        return self._run_active

    def _changed(self) -> None:
        if self._on_change:
            try:
                self._on_change()
            except Exception:
                logger.exception("AuthoringTools on_change raised")

    def _refuse_during_run(self, what: str) -> bool:
        if self._run_active:
            logger.info("%s is not available while a bot is running", what)
            return True
        return False

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def perform(self, action: AuthoringAction, source_hwnd: int | None) -> None:
        """Do *action*, the way its button or hotkey asks for it."""
        if action is AuthoringAction.XYZ_SYNC:
            await self.xyz_sync(source_hwnd)
        elif action is AuthoringAction.TP_TO_FOCUSED:
            await self.tp_to_player(source_hwnd)
        elif action is AuthoringAction.TOGGLE_SPEED:
            await self.set_speed(not self.speed_on)
        elif action is AuthoringAction.TOGGLE_COMBAT:
            await self.set_auto_combat(not self.combat_on)
        elif action is AuthoringAction.TOGGLE_SPEED_AUTHORING:
            self.set_speed_authoring(not self.speed_authoring_on)
        elif action is AuthoringAction.TOGGLE_DIALOGUE:
            await self.set_auto_dialogue(not self.dialogue_on)

    # ------------------------------------------------------------------
    # Speed
    # ------------------------------------------------------------------

    async def set_speed(self, on: bool) -> None:
        if on == self.speed_on:
            return
        if on:
            if self._refuse_during_run("Speed"):
                return
            multiplier = self._speed_multiplier()
            self._boost_value = boosted_speed_value(multiplier)
            self._speed_task = asyncio.create_task(self._speed_loop())
            logger.info("Speed on: every player walks at %.1fx", multiplier)
        else:
            task, self._speed_task = self._speed_task, None
            await _cancel(task)
            for client in list(self._clients()):
                await self._restore_speed(client)
            self._originals.clear()
            self._characters.clear()
            logger.info("Speed off: every player walks at their own speed")
        self._changed()

    async def _speed_loop(self) -> None:
        while True:
            for client in list(self._clients()):
                try:
                    await self._boost(client)
                except Exception:
                    # A client mid-zone-change fails every read; the next tick gets it.
                    logger.debug("Speed: could not boost hwnd=%s", getattr(client, "window_handle", "?"))
            await asyncio.sleep(SPEED_REAPPLY_INTERVAL)

    async def _boost(self, client) -> None:
        handle = client.window_handle
        seen = self._characters.pop(handle, None)
        character = await _live_character_id(client)
        if character == wizard_names.NO_CHARACTER:
            return
        self._characters[handle] = character
        # The same character two ticks running. A freed address the heap has since handed to
        # something else seldom reads as the same id twice.
        if character != seen:
            return
        current = await client.client_object.speed_multiplier()
        if current == self._boost_value:
            return
        # Anything but our own value was written by the game -- a zone change, a mount --
        # and is the one to give back.
        self._originals[handle] = (character, current)
        await client.client_object.write_speed_multiplier(self._boost_value)

    async def _restore_speed(self, client) -> None:
        self._characters.pop(client.window_handle, None)
        entry = self._originals.pop(client.window_handle, None)
        if entry is None:
            return
        character, original = entry
        try:
            # Only to the character it was read from, and only over our own value: one the
            # game has written since is already its own.
            if await _live_character_id(client) != character:
                return
            if await client.client_object.speed_multiplier() == self._boost_value:
                await client.client_object.write_speed_multiplier(original)
        except Exception:
            logger.debug("Speed: could not restore hwnd=%s", client.window_handle)

    # ------------------------------------------------------------------
    # Auto-combat
    # ------------------------------------------------------------------

    async def set_auto_combat(self, on: bool) -> None:
        if on == self.combat_on:
            return
        if on:
            if self._refuse_during_run("Auto-combat"):
                return
            self._combat_task = asyncio.create_task(self._combat_loop())
            logger.info("Auto-combat on")
        else:
            task, self._combat_task = self._combat_task, None
            await _cancel(task)
            await self._cancel_fights()
            logger.info("Auto-combat off")
        self._changed()

    async def _combat_loop(self) -> None:
        while True:
            clients = list(self._clients())
            for index, client in enumerate(clients):
                if client.window_handle in self._fights:
                    continue
                try:
                    if not await client.in_battle():
                        continue
                    hit = await restriction_for(client)
                except Exception:
                    continue
                if hit is not None:
                    logger.warning(
                        "Auto-combat off: P%d is in %s, which BartleBot does not automate",
                        index + 1,
                        hit.describe(),
                    )
                    # Off from inside its own task: drop the handle first so nothing awaits
                    # this task cancelling itself.
                    self._combat_task = None
                    await self._cancel_fights()
                    self._changed()
                    return
                dsl = self._dsl_for(index, len(clients))
                self._fights[client.window_handle] = asyncio.create_task(self._fight(client, index, dsl))
            await asyncio.sleep(POLL_INTERVAL)

    def _dsl_for(self, index: int, count: int) -> str:
        """Player *index*'s DSL from the open bot's default Duel Strategy, resolved as a run would."""
        stand_in = SimpleNamespace(bot_config=self._bot_config(), state="")
        dsls, _order, source, _level = resolve_combat(stand_in, True, count)
        logger.debug("Auto-combat: P%d fights with %s", index + 1, source)
        return dsls[index]

    async def _fight(self, client, index: int, dsl: str) -> None:
        logger.info("Auto-combat: P%d is in a fight", index + 1)
        try:
            await _run_combat(client, dsl, number=index + 1)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Auto-combat: P%d's fight could not be handled", index + 1)
        finally:
            self._fights.pop(client.window_handle, None)

    async def _cancel_fights(self) -> None:
        fights = list(self._fights.values())
        self._fights.clear()
        for task in fights:
            await _cancel(task)

    # ------------------------------------------------------------------
    # Auto-dialogue
    # ------------------------------------------------------------------

    async def set_auto_dialogue(self, on: bool) -> None:
        if on == self.dialogue_on:
            return
        if on:
            if self._refuse_during_run("Auto-dialogue"):
                return
            self._dialogue_task = asyncio.create_task(self._dialogue_loop())
            logger.info("Auto-dialogue on: side quests are declined")
        else:
            task, self._dialogue_task = self._dialogue_task, None
            await _cancel(task)
            logger.info("Auto-dialogue off")
        self._changed()

    async def _dialogue_loop(self) -> None:
        while True:
            # An exception is a client mid-zone-change or unhooking; the next tick gets it.
            results = await asyncio.gather(
                *[node_helpers.advance_dialog_once(c) for c in list(self._clients())], return_exceptions=True
            )
            # Straight on to the next page while a conversation is going, as a run's watcher does.
            await asyncio.sleep(DIALOG_FAST_POLL_INTERVAL if True in results else POLL_INTERVAL)

    # ------------------------------------------------------------------
    # XYZ sync and TP
    # ------------------------------------------------------------------

    async def xyz_sync(self, source_hwnd: int | None) -> None:
        """Put every other player in the source's zone on the source's spot, facing its way."""
        await self._run_action("XYZ sync", self._xyz_sync(source_hwnd))

    async def tp_to_player(self, source_hwnd: int | None) -> None:
        """Port every other player to the source player through the friends list."""
        await self._run_action("TP to player", self._tp_to_player(source_hwnd))

    async def _run_action(self, what: str, work) -> None:
        if self.busy:
            logger.info("%s ignored: another one is still in progress", what)
            work.close()
            return
        if self._refuse_during_run(what):
            work.close()
            return
        self._action_task = asyncio.ensure_future(work)
        self._changed()
        try:
            await self._action_task
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("%s failed", what)
        finally:
            self._action_task = None
            self._changed()

    async def _xyz_sync(self, source_hwnd: int | None) -> None:
        clients = list(self._clients())
        if len(clients) < 2:
            logger.info("XYZ sync: there is no other player to move")
            return
        index = source_index(clients, source_hwnd)
        source = clients[index]
        if await source.is_loading():
            logger.info("XYZ sync: P%d is on a loading screen", index + 1)
            return
        zone, xyz, yaw = await asyncio.gather(source.zone_name(), source.body.position(), source.body.yaw())

        others = [(i, c) for i, c in enumerate(clients) if i != index]
        zones = await asyncio.gather(*[c.zone_name() for _, c in others], return_exceptions=True)
        movers = [(i, c) for (i, c), z in zip(others, zones) if z == zone]
        # Across a zone a position means nothing, and a TP would drop the player in the void.
        if skipped := [i + 1 for (i, _), z in zip(others, zones) if z != zone]:
            logger.info("XYZ sync: player(s) %s are not in P%d's zone and stay put", skipped, index + 1)
        if not movers:
            return
        results = await asyncio.gather(
            *[c.teleport(xyz, yaw, wait_on_inuse=True, purge_on_after_unuser_fixer=True) for _, c in movers],
            return_exceptions=True,
        )
        moved = [i + 1 for (i, _), r in zip(movers, results) if not isinstance(r, Exception)]
        for (i, _), r in zip(movers, results):
            if isinstance(r, Exception):
                logger.warning("XYZ sync: P%d could not be moved: %s", i + 1, r)
        logger.info("XYZ sync: moved player(s) %s to P%d", moved, index + 1)

    async def _tp_to_player(self, source_hwnd: int | None) -> None:
        clients = list(self._clients())
        if len(clients) < 2:
            logger.info("TP to player: there is no other player to port")
            return
        index = source_index(clients, source_hwnd)
        # Read from the cache ClientManager fills, never logged: a wizard name in a log ends
        # up in a bug report.
        name = wizard_names.cached_name(clients[index])
        if not name:
            logger.warning("TP to player: P%d's wizard name has not been detected yet", index + 1)
            return
        if await node_helpers.gather_at_player(_ModuleLog, clients, index, name):
            logger.info("TP to player: every player is with P%d", index + 1)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def suspend_for_run(self) -> None:
        """Switch everything off before a run, and refuse everything until it ends."""
        await _cancel(self._action_task)
        await self.set_speed(False)
        await self.set_auto_combat(False)
        await self.set_auto_dialogue(False)
        self.set_speed_authoring(False)
        self._run_active = True
        self._changed()

    def resume_after_run(self) -> None:
        self._run_active = False
        self._changed()

    async def release(self, client) -> None:
        """Give *client* its own speed back and stop its fight, before it is unhooked.

        After the unhook nothing can write to it, and a boost left behind lasts until the
        player's next zone change.
        """
        await self._restore_speed(client)
        task = self._fights.pop(client.window_handle, None)
        await _cancel(task)

    async def shutdown(self) -> None:
        """Switch everything off, for a quit or an unhook of every client."""
        await _cancel(self._action_task)
        await self.set_speed(False)
        await self.set_auto_combat(False)
        await self.set_auto_dialogue(False)
        self.set_speed_authoring(False)


async def _live_character_id(client) -> int:
    """The id of the character *client*'s client object holds, or NO_CHARACTER if writing to it is unsafe.

    The client hook keeps the last address the game handed it, and nothing clears it when a
    character logs out: the game frees the object, and the hook goes on pointing at it until
    the next character loads. A read through it is harmless -- it comes back as a garbage
    id, which ``wizard_names`` simply drops -- but a write lands in freed heap memory, and the
    game crashes some time later, with a corrupted heap (0xc0000374) or an access violation
    in ntdll. Speed's write every tick did exactly that to clients switching characters.

    So before a write: the game is neither loading nor at character select, and the object
    still reads as a client object, which a freed one's vtable seldom leads to.
    """
    if await client.is_loading() or await at_character_select(client):
        return wizard_names.NO_CHARACTER
    if not (await client.client_object.maybe_read_type_name()).endswith(_CLIENT_OBJECT_TYPE):
        return wizard_names.NO_CHARACTER
    return await wizard_names.read_character_id(client)


async def _cancel(task: asyncio.Task | None) -> None:
    """Cancel *task* and wait for it to finish, unless it is the task doing the waiting."""
    if task is None or task.done() or task is asyncio.current_task():
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
