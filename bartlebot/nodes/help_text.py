"""Node help text that several nodes share, written once so it reads the same on each.

A param, a usage note or a hint that more than one node shows takes its text from here
(``docs/UI_TEXT.md``, N9). Text that belongs to one node stays in that node's spec.
"""

from __future__ import annotations

#: Zone, or From Zone: where the players are before the node does anything.
ZONE_AT_START = "The zone every player must be in when the node starts."
#: To Zone, Dungeon Zone, or a Zone checked on arrival.
ZONE_AT_END = "The zone every player must be in when the node ends."

INTERACT_XYZ = "Where players stand to get the interact prompt."
RESET_XYZ = "A nearby spot where the prompt doesn't appear. Players step here to clear the prompt before trying again."
INTERACT_PLAYERS = "Which players interact."

SEQUENTIAL_INTERACTION = (
    "When on, players use the object one at a time. Turn it on only for an object that serves one "
    "player at a time, since it's slower."
)
SEQUENTIAL_INTERACTION_HINT = "all players at once"
OBJECT_USE_DELAY_HELP = (
    "How long to wait between one player finishing and the next starting, with Sequential Interaction "
    "on. Raise it for an object that takes several seconds to reset, such as a teleporter."
)
#: The usage note for a node with Sequential Interaction, on what tells you to turn it on.
SEQUENTIAL_INTERACTION_NOTE = (
    "Turn on Sequential Interaction when the object serves one player at a time. The sign is that one "
    "player interacts and the others' X presses sometimes do nothing. It adds about one interaction's "
    "time per extra player."
)

#: The usage note for a node that recovers players who didn't arrive with the others.
ARRIVAL_NOTE = (
    "If only some players arrive, the others teleport to one who did before the node fails. This needs "
    "every player on the others' friends lists."
)

#: Where an author finds a UI path for a param that takes one.
UI_PATH_NOTE = "Copy UI paths from the UI Tree Browser."


def seconds(value: float) -> str:
    """A hint for a duration: "1 second", "10 seconds"."""
    return f"{value:g} second{'' if value == 1 else 's'}"
