"""What the run request reads from the clients before it starts a bot.

Three reads, all memory reads and so async, made in one task before any of the run's
dialogs open: which players are in a fight, which level scaling players already have an
elixir active (#141), and each player's key bindings. The run request shows its dialogs from
the task's done callback, outside any task, for the reason in "qasync + Python 3.13" in
bartlebot/gui/CLAUDE.md.

**A player in a fight refuses the run (#203).** Everything before the first node needs the
players out of battle: decks cannot be changed mid-fight, and forming the group opens the
social window over the cards, which is how players come to sit out rounds until the game
asks "You Are Idle". The user can end a fight in seconds; the bot would have to guess
whether to fight it or flee it.

**So does an action BartleBot cannot press.** Every action in ``GameAction`` is required of
every player, whatever the bot: a player whose account has one bound only with Ctrl, Shift
or Alt, only to the mouse, or only to a key the chat line shares, would have a node fail
partway through the run, or type into chat. The user fixes it in the game's key bindings;
``docs/KEY_BINDINGS.md``. A key shared with any other action is pressed, and only logged.

Players are named by number, as everywhere else the run speaks to the user.
"""

from __future__ import annotations

import asyncio
import logging
from typing import NamedTuple

from bartlebot.clients import game_keys
from bartlebot.game.key_bindings import GameAction, KeyMap, action_label
from bartlebot.nodes.combat_runner import slots_in_battle

logger = logging.getLogger(__name__)


class RunProbe(NamedTuple):
    fighting: list[int]
    """1-based numbers of the players in a fight. A client that cannot be read is not counted."""
    elixir_players: tuple[int, ...]
    """1-based numbers of the level scaling players with an elixir already active."""
    key_problems: tuple[str, ...] = ()
    """One sentence per action a player has no key for: "Player 2: Interact is bound only to …"."""


async def probe_before_run(clients: list, scaling_players) -> RunProbe:
    """Read who is fighting, who among *scaling_players* already has an elixir, and every key binding.

    Never raises for an unreadable client: these reads gate dialogs, and a client that
    cannot be read must not make the bot unrunnable. A failed elixir read warns about nobody,
    and a client whose bindings cannot be read is taken to have the defaults (``game_keys``).
    """
    flags, keys = await asyncio.gather(
        asyncio.gather(*(c.in_battle() for c in clients), return_exceptions=True),
        asyncio.gather(*(game_keys.key_map(c, refresh=True) for c in clients)),
    )
    fighting = [slot + 1 for slot in slots_in_battle(flags)]

    elixirs: tuple[int, ...] = ()
    if scaling_players and not fighting:
        from bartlebot.nodes.utility.handle_level_scaling import players_with_elixir  # noqa: PLC0415

        try:
            elixirs = tuple(await players_with_elixir(clients, scaling_players))
        except Exception:
            logger.warning("Could not read the players' elixirs before the run", exc_info=True)
    return RunProbe(fighting, elixirs, key_problems(keys))


def key_problems(keys: list[KeyMap]) -> tuple[str, ...]:
    """Each action a player has no key a press can use for, as a sentence; *keys* in player order.

    A key the action shares with another is fine to press, and logged: the other action
    happens too, which the player may want to know when something opens unasked.
    """
    problems: list[str] = []
    for number, key_map in enumerate(keys, 1):
        for action in GameAction:
            choice = key_map.choose(action)
            if choice.code is None:
                problems.append(f"Player {number}: {action_label(action)} {choice.problem}.")
            elif choice.shared_with:
                logger.warning(
                    "Player %d: %s's key, %s, also does %s in the game",
                    number,
                    action_label(action),
                    choice.input.text,
                    " and ".join(action_label(other) for other in choice.shared_with),
                )
    return tuple(problems)


def refusal_text(fighting: list[int]) -> str:
    """The dialog's text when *fighting* (1-based player numbers) are in a fight."""
    if len(fighting) == 1:
        who, verb, pronoun = f"Player {fighting[0]}", "is", "it"
    else:
        who, verb, pronoun = f"Players {', '.join(str(n) for n in fighting[:-1])} and {fighting[-1]}", "are", "them"
    return (
        f"{who} {verb} in a fight. Finish or flee {pronoun}, then start the bot again.\n\n"
        "Before the first node runs, BartleBot applies decks and forms the group, and neither "
        "can be done mid-fight."
    )


def key_refusal_text(problems: tuple[str, ...]) -> str:
    """The dialog's text when a player has an action BartleBot cannot press (``docs/UI_TEXT.md``, M2)."""
    return (
        "BartleBot can't press every action this run needs.\n\n"
        + "\n".join(problems)
        + "\n\nIn the game's Keybinding tab, give each of these actions a key without Ctrl, Shift or Alt "
        "that nothing else uses. Either of an action's two keys works. Then run the bot again."
    )
