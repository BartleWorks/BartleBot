"""go_to_world node — take all players to any world via the World Tree spiral door."""

import asyncio
import logging

from bartlebot import worlds
from bartlebot.nodes import node_helpers, wizard_city
from bartlebot.nodes.param_schema import BotOptionSpec, NodeSpec, ParamField, ReturnSpec
from bartlebot.nodes.zone_entry import enter_zone, enter_zone_via_list

# ── Zone names ─────────────────────────────────────────────────────────────────
# The chamber the World Tree spiral door stands in, one zone in from Ravenwood. It is
# also where the door drops you when the world you pick is Wizard City.
WORLD_TREE_ZONE = "WizardCity/WC_Ravenwood_Teleporter"

# ── Navigation coordinates ─────────────────────────────────────────────────────
# Ravenwood → World Tree chamber. The reset point is back down the path, well clear of
# both this trigger and the Ravenwood → Commons one at y ≈ -2200 (see wizard_city).
_WORLD_TREE_XYZ = {"x": 20.49, "y": 1862.74, "z": 69.76}
_WORLD_TREE_RESET_XYZ = {"x": 17.61, "y": 1403.27, "z": 62.69}

# The spiral door inside the chamber, and a point where its interact prompt does NOT
# appear. The reset is ~470 units from the door and ~2500 from the chamber's exit back to
# Ravenwood (y ≈ -3150), so resetting off the door cannot trip the exit instead.
_SPIRAL_DOOR_XYZ = {"x": -14.88, "y": -179.86, "z": 89.21}
_SPIRAL_DOOR_RESET_XYZ = {"x": 4.5, "y": -650.71, "z": 89.21}


PARAM_SCHEMA: list[ParamField] = [
    ParamField(
        key="world",
        type="str_choice",
        label="World",
        help=(
            "The world to travel to. Players end up in that world's spiral door chamber; "
            "follow this node with Teleport To World Hub to reach its hub zone.\n\n"
            "The character must already have unlocked the world — the spiral door only "
            "lists worlds it can send you to."
        ),
        default="WizardCity",
        choices=[(world, worlds.label(world)) for world in worlds.SPIRAL_DOOR_WORLDS],
        required=True,
    ),
]

NODE_SPEC = NodeSpec(
    description=(
        "Takes all players to any world through the World Tree spiral door: home to the dorm, "
        "out to Ravenwood, in to the World Tree, then the spiral door to the chosen world. "
        "Works from anywhere, since pressing HOME does."
    ),
    params=PARAM_SCHEMA,
    returns=[
        ReturnSpec("SUCCESS", "All players are in the chosen world."),
        ReturnSpec(
            "FAILURE",
            "Players could not reach the dorm or Ravenwood; the World Tree chamber was not "
            "reached; the spiral door did not open; the world was not offered by the door "
            "(most often: the character has not unlocked it); or the teleport did not land.",
        ),
    ],
    required_bot_options=[
        BotOptionSpec(
            name="Safety Delay",
            type="float",
            default=3.0,
            minimum=0.0,
            suffix=" s",
            description=(
                "Seconds to allow for game-server responses when waiting for prompts or menus. "
                "Increase on high-latency connections."
            ),
        ),
    ],
    max_consecutive_retries=5,
    usage_notes=(
        "This node lands players in the destination world's spiral door chamber, which is "
        "rarely where a bot wants them — follow it with Teleport To World Hub.\n\n"
        "Players already in the chosen world are left where they are and the node returns "
        "SUCCESS without travelling, so it is cheap to put at the top of a bot as a "
        "'get everyone to the right world' step."
    ),
)


def _counts_as_arrived(zone: str | None, zone_pattern: str) -> bool:
    """True when `zone` is in the target world *and* is somewhere a bot can carry on from.

    The dorm is the exception, and it is not an obscure one: it matches "WizardCity/*"
    because fnmatch's "*" crosses "/", and HOME puts every client in it, so it is the
    ordinary starting position for a bot rather than a rare one. Counting it as arrival
    returned SUCCESS with everyone still inside the dorm, which is the same thing step 3
    below refuses to do when it stops at Ravenwood instead of the dorm it just came
    through (#157).

    Only Wizard City is affected: no other world's pattern matches the dorm at all.
    """
    return node_helpers.zone_matches(zone, zone_pattern) and zone != wizard_city.DORM_ZONE


async def run(machine, clients: list, params: dict) -> str:
    """
    APIs: client.zone_name(), wizard_city.go_to_ravenwood(), enter_zone.run(),
          enter_zone_via_list.run()

    Flow:
      1. If every client's zone is already in the chosen world, return SUCCESS without
         moving anyone. The dorm does not count, even though it matches "WizardCity/*" —
         see _counts_as_arrived.
      2. wizard_city.go_to_ravenwood(): unequip castles, HOME to the dorm, Dorm → Ravenwood.
         Return FAILURE if it fails.
      3. If the chosen world is Wizard City, return SUCCESS here. Deliberately not at the
         dorm in step 2, even though the dorm is a WizardCity zone: Ravenwood is somewhere a
         bot can carry on from, and the inside of a dorm is not.
      4. enter_zone.run() Ravenwood → the World Tree chamber. Return FAILURE if it fails.
      5. enter_zone_via_list.run() on the spiral door, matching the world's display name in
         any of the game's seven locales. Arrival is verified against the glob "<World>/*",
         so no per-world table of spiral chamber zone names is needed.

    FAILURE: could not reach the dorm or Ravenwood; could not enter the World Tree chamber;
    the spiral door never opened; the world was not listed on any page of the door (usually
    an unlocked-worlds problem); the teleport did not complete.
    """
    world: str = params["world"]
    zone_pattern = worlds.zone_pattern(world)
    names = worlds.display_names(world)
    if not names:
        # A world key the snapshot cannot name is unusable: there is no text to search the
        # door for. Fail loudly rather than paging through the list matching nothing.
        machine.log(
            f"FAILURE: no display name known for world {world!r}. "
            f"Regenerate bartlebot/data/world_names.json with tools/dump_world_names.py",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log(f"go_to_world: {worlds.label(world)} ({zone_pattern})")

    # 1. Skip the whole trip if everyone is already there.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    machine.log(f"Current client zones: {list(zones)}", logging.DEBUG)
    if all(_counts_as_arrived(z, zone_pattern) for z in zones):
        machine.log(f"SUCCESS: all clients already in {worlds.label(world)}")
        return "SUCCESS"

    # 2. HOME → dorm → Ravenwood. go_to_ravenwood logs its own failure reason.
    if not await wizard_city.go_to_ravenwood(machine, clients):
        return "FAILURE"

    # 3. Wizard City needs no spiral door — Ravenwood is already in it.
    if world == "WizardCity":
        machine.log("SUCCESS: all clients in Wizard City (Ravenwood)")
        return "SUCCESS"

    # 4. Ravenwood → World Tree chamber.
    machine.log("Navigating Ravenwood → World Tree…")
    if (
        await enter_zone.run(
            machine,
            clients,
            {
                "from_zone": wizard_city.RAVENWOOD_ZONE,
                "to_zone": WORLD_TREE_ZONE,
                "xyz": _WORLD_TREE_XYZ,
                "reset_xyz": _WORLD_TREE_RESET_XYZ,
            },
        )
        != "SUCCESS"
    ):
        machine.log("FAILURE: Ravenwood → World Tree failed", logging.WARNING)
        return "FAILURE"

    # 5. Use the spiral door. The primary entry is the en-US name; the rest are the other
    #    locales, because the door lists whatever language the client is running in.
    primary, *aliases = names
    machine.log(f"Using the spiral door to reach {primary!r}…")
    result = await enter_zone_via_list.run(
        machine,
        clients,
        {
            "zone": WORLD_TREE_ZONE,
            "to_zone": zone_pattern,
            "interact_xyz": _SPIRAL_DOOR_XYZ,
            "reset_xyz": _SPIRAL_DOOR_RESET_XYZ,
            "zone_list_entry": primary,
            "zone_list_aliases": aliases,
        },
    )
    if result != "SUCCESS":
        machine.log(
            f"FAILURE: the spiral door did not take players to {primary!r}. "
            f"If the door opened but the world was not on any page, this character has "
            f"probably not unlocked it yet.",
            logging.WARNING,
        )
        return "FAILURE"

    machine.log(f"SUCCESS: all clients in {primary}")
    return "SUCCESS"
