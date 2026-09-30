"""Whether a bot's players share one realm or keep to their own: the ``Player Realms`` option.

Most bots want every player on one realm and in one area, because players on different
realms enter separate battles and separate dungeons. A few want the opposite: the reagent
bots run best with each player on its own realm, not competing for the same spawns. #132.

**A node param says *where* the players go; this option says *how they are arranged*.** No
node carries a param that decides the arrangement, so the two can never contradict each
other. ``change_realm`` reads this option to decide between moving the group and spreading
it, rather than having a split param of its own (#131).

Every node that reads the option declares :data:`SPEC`, the same object, so it appears once
in Options however many nodes read it. The author sets the bot's default; the user may still
overrule it, as for any bot option.

**A node that merges players by nature** -- ``teleport_to_friend`` ports everyone onto one
realm, which is its whole purpose -- declares ``NodeSpec.merges_players``, and a bot holding
one cannot be Separate. That is enforced at three points: the Options and Setup rows offer
only Together, the node carries an error while the bot's declared default is Separate, and
the run refuses to start when the value resolves to Separate anyway (a hand-edited file, a
leftover override). :func:`separate_blocked_by` is the one rule all three read.
"""

from __future__ import annotations

from bartlebot.nodes.param_schema import BotOptionSpec
from bartlebot.option_groups import GROUP_TEAM

OPTION = "Player Realms"
OPTION_TYPE = "player_realms"

TOGETHER = "Together"
SEPARATE = "Separate"
VALUES: tuple[str, ...] = (TOGETHER, SEPARATE)

SPEC = BotOptionSpec(
    name=OPTION,
    group=GROUP_TEAM,
    type=OPTION_TYPE,
    default=TOGETHER,
    description=(
        "Together: every player on one realm and in one area. A player found on another realm "
        "is ported to the others before it can enter a separate battle or dungeon.\n\n"
        "Separate: never move a player onto another player's realm. Change Realm gives each "
        "player a realm of its own, and any split already made is left as it is. For bots "
        "like the reagent collectors, where players on one realm compete for the same spawns."
    ),
)


def _specs(specs=None):
    if specs is not None:
        return specs
    from bartlebot.nodes.registry import NODE_SPECS  # noqa: PLC0415 - avoids an import cycle

    return NODE_SPECS


def separate_blocked_by(config, specs=None) -> list[str]:
    """The labels of every state in *config* that puts all players on one realm.

    Empty when the bot may be Separate. A state's label, or its id when it has none, is
    what the author sees on the canvas.
    """
    registry = _specs(specs)
    blocked: list[str] = []
    for state in config.states:
        spec = registry.get(state.node_type)
        if spec is not None and spec.merges_players:
            blocked.append(state.label or state.id)
    return blocked


def blocked_reason(blockers: list[str]) -> str:
    """Why Separate is not on offer, naming the nodes responsible."""
    names = ", ".join(repr(b) for b in blockers)
    noun, verb = ("node", "puts") if len(blockers) == 1 else ("nodes", "put")
    return f"This bot cannot be Separate: its {noun} {names} {verb} every player on one realm."


def run_refusal(config, value, specs=None) -> str | None:
    """Why a run of *config* resolving Player Realms to *value* must not start, or None.

    The last of the three enforcers, and the one a hand-edited file or a leftover override
    still reaches: the other two only stop the value being chosen.
    """
    if not is_separate(value):
        return None
    blockers = separate_blocked_by(config, specs)
    if not blockers:
        return None
    return f"{blocked_reason(blockers)}\n\nSet {OPTION} back to {TOGETHER} in Setup, or take the node out of the bot."


def declares_separate(config) -> bool:
    """Whether *config*'s own file declares Separate -- the author's choice, not a user's.

    This is what the canvas marks a merging node against: an error there is the author's
    to fix. A user's override is the Setup row's and the pre-run check's.
    """
    return any(opt.name == OPTION and is_separate(opt.default) for opt in config.options)


def is_separate(value) -> bool:
    """Whether a resolved option value asks for Separate. Anything unrecognized is Together."""
    return value == SEPARATE


def machine_is_separate(machine) -> bool:
    """Whether the running bot keeps its players to their own realms."""
    return is_separate(machine.get_option(OPTION, TOGETHER))
