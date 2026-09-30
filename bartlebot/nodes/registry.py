from bartlebot.nodes.combat.check_health_and_mana import NODE_SPEC as _check_health_and_mana_spec
from bartlebot.nodes.combat.check_health_and_mana import run as check_health_and_mana_run
from bartlebot.nodes.combat.collect_wisps import NODE_SPEC as _collect_wisps_spec
from bartlebot.nodes.combat.collect_wisps import run as collect_wisps_run
from bartlebot.nodes.combat.handle_combat import NODE_SPEC as _handle_combat_spec
from bartlebot.nodes.combat.handle_combat import run as handle_combat_run
from bartlebot.nodes.combat.handle_potions import NODE_SPEC as _handle_potions_spec
from bartlebot.nodes.combat.handle_potions import run as handle_potions_run
from bartlebot.nodes.combat.wait_for_health import NODE_SPEC as _wait_for_health_spec
from bartlebot.nodes.combat.wait_for_health import run as wait_for_health_run
from bartlebot.nodes.interaction.click_path import NODE_SPEC as _click_path_spec
from bartlebot.nodes.interaction.click_path import run as click_path_run
from bartlebot.nodes.interaction.collect_reagent import NODE_SPEC as _collect_reagent_spec
from bartlebot.nodes.interaction.collect_reagent import run as collect_reagent_run
from bartlebot.nodes.interaction.interact import NODE_SPEC as _interact_spec
from bartlebot.nodes.interaction.interact import run as interact_run
from bartlebot.nodes.interaction.interact_menu import NODE_SPEC as _interact_menu_spec
from bartlebot.nodes.interaction.interact_menu import run as interact_menu_run
from bartlebot.nodes.interaction.wait_for_dialogue import NODE_SPEC as _wait_for_dialogue_spec
from bartlebot.nodes.interaction.wait_for_dialogue import run as wait_for_dialogue_run
from bartlebot.nodes.param_schema import NodeSpec, ParamField
from bartlebot.nodes.teleportation.go_to_world import NODE_SPEC as _go_to_world_spec
from bartlebot.nodes.teleportation.go_to_world import run as go_to_world_run
from bartlebot.nodes.teleportation.mark_location import NODE_SPEC as _mark_location_spec
from bartlebot.nodes.teleportation.mark_location import run as mark_location_run
from bartlebot.nodes.teleportation.sync_in_zone import NODE_SPEC as _sync_in_zone_spec
from bartlebot.nodes.teleportation.sync_in_zone import run as sync_in_zone_run
from bartlebot.nodes.teleportation.teleport_to_coordinates import NODE_SPEC as _teleport_to_coordinates_spec
from bartlebot.nodes.teleportation.teleport_to_coordinates import run as teleport_to_coordinates_run
from bartlebot.nodes.teleportation.teleport_to_friend import NODE_SPEC as _teleport_to_friend_spec
from bartlebot.nodes.teleportation.teleport_to_friend import run as teleport_to_friend_run
from bartlebot.nodes.teleportation.teleport_to_marked_location import NODE_SPEC as _teleport_to_marked_location_spec
from bartlebot.nodes.teleportation.teleport_to_marked_location import run as teleport_to_marked_location_run
from bartlebot.nodes.teleportation.teleport_to_world_hub import NODE_SPEC as _teleport_to_world_hub_spec
from bartlebot.nodes.teleportation.teleport_to_world_hub import run as teleport_to_world_hub_run
from bartlebot.nodes.utility.apply_level_scaling import NODE_SPEC as _apply_level_scaling_spec
from bartlebot.nodes.utility.apply_level_scaling import run as apply_level_scaling_run
from bartlebot.nodes.utility.change_realm import NODE_SPEC as _change_realm_spec
from bartlebot.nodes.utility.change_realm import run as change_realm_run
from bartlebot.nodes.utility.check_if_dropped import NODE_SPEC as _check_if_dropped_spec
from bartlebot.nodes.utility.check_if_dropped import run as check_if_dropped_run
from bartlebot.nodes.utility.check_level_scaling import NODE_SPEC as _check_level_scaling_spec
from bartlebot.nodes.utility.check_level_scaling import run as check_level_scaling_run
from bartlebot.nodes.utility.check_zone import NODE_SPEC as _check_zone_spec
from bartlebot.nodes.utility.check_zone import run as check_zone_run
from bartlebot.nodes.utility.quicksell import NODE_SPEC as _quicksell_spec
from bartlebot.nodes.utility.quicksell import run as quicksell_run
from bartlebot.nodes.utility.relog import NODE_SPEC as _relog_spec
from bartlebot.nodes.utility.relog import run as relog_run
from bartlebot.nodes.utility.sleep import NODE_SPEC as _sleep_spec
from bartlebot.nodes.utility.sleep import run as sleep_run
from bartlebot.nodes.zone_entry.enter_dungeon import NODE_SPEC as _enter_dungeon_spec
from bartlebot.nodes.zone_entry.enter_dungeon import run as enter_dungeon_run
from bartlebot.nodes.zone_entry.enter_gauntlet import NODE_SPEC as _enter_gauntlet_spec
from bartlebot.nodes.zone_entry.enter_gauntlet import run as enter_gauntlet_run
from bartlebot.nodes.zone_entry.enter_zone import NODE_SPEC as _enter_zone_spec
from bartlebot.nodes.zone_entry.enter_zone import run as enter_zone_run
from bartlebot.nodes.zone_entry.enter_zone_via_object import NODE_SPEC as _enter_zone_via_object_spec
from bartlebot.nodes.zone_entry.enter_zone_via_object import run as enter_zone_via_object_run

NODE_REGISTRY: dict = {
    "apply_level_scaling": apply_level_scaling_run,
    "change_realm": change_realm_run,
    "click_path": click_path_run,
    "enter_zone": enter_zone_run,
    "handle_potions": handle_potions_run,
    "teleport_to_world_hub": teleport_to_world_hub_run,
    "go_to_world": go_to_world_run,
    "sync_in_zone": sync_in_zone_run,
    "teleport_to_coordinates": teleport_to_coordinates_run,
    "enter_dungeon": enter_dungeon_run,
    "enter_gauntlet": enter_gauntlet_run,
    "interact": interact_run,
    "interact_menu": interact_menu_run,
    "quicksell": quicksell_run,
    "enter_zone_via_object": enter_zone_via_object_run,
    "handle_combat": handle_combat_run,
    "collect_reagent": collect_reagent_run,
    "check_health_and_mana": check_health_and_mana_run,
    "collect_wisps": collect_wisps_run,
    "mark_location": mark_location_run,
    "teleport_to_marked_location": teleport_to_marked_location_run,
    "teleport_to_friend": teleport_to_friend_run,
    "sleep": sleep_run,
    "wait_for_dialogue": wait_for_dialogue_run,
    "wait_for_health": wait_for_health_run,
    "check_if_dropped": check_if_dropped_run,
    "check_level_scaling": check_level_scaling_run,
    "check_zone": check_zone_run,
    "relog": relog_run,
}

NODE_SPECS: dict[str, NodeSpec] = {
    "apply_level_scaling": _apply_level_scaling_spec,
    "change_realm": _change_realm_spec,
    "click_path": _click_path_spec,
    "enter_zone": _enter_zone_spec,
    "handle_potions": _handle_potions_spec,
    "teleport_to_world_hub": _teleport_to_world_hub_spec,
    "go_to_world": _go_to_world_spec,
    "sync_in_zone": _sync_in_zone_spec,
    "teleport_to_coordinates": _teleport_to_coordinates_spec,
    "enter_dungeon": _enter_dungeon_spec,
    "enter_gauntlet": _enter_gauntlet_spec,
    "interact": _interact_spec,
    "interact_menu": _interact_menu_spec,
    "quicksell": _quicksell_spec,
    "enter_zone_via_object": _enter_zone_via_object_spec,
    "handle_combat": _handle_combat_spec,
    "collect_reagent": _collect_reagent_spec,
    "check_health_and_mana": _check_health_and_mana_spec,
    "collect_wisps": _collect_wisps_spec,
    "mark_location": _mark_location_spec,
    "teleport_to_marked_location": _teleport_to_marked_location_spec,
    "teleport_to_friend": _teleport_to_friend_spec,
    "sleep": _sleep_spec,
    "wait_for_dialogue": _wait_for_dialogue_spec,
    "wait_for_health": _wait_for_health_spec,
    "check_if_dropped": _check_if_dropped_spec,
    "check_level_scaling": _check_level_scaling_spec,
    "check_zone": _check_zone_spec,
    "relog": _relog_spec,
}

# Convenience alias — param schemas derived from NODE_SPECS so there is one source of truth
NODE_PARAM_SCHEMAS: dict[str, list[ParamField]] = {k: v.params for k, v in NODE_SPECS.items()}

# Ordered category groupings used by the GUI dropdown.
# Node types within each category are sorted alphabetically.
NODE_CATEGORIES: list[tuple[str, list[str]]] = [
    (
        "Zone Entry",
        [
            "enter_dungeon",
            "enter_gauntlet",
            "enter_zone",
            "enter_zone_via_object",
        ],
    ),
    (
        "Teleportation",
        [
            "go_to_world",
            "mark_location",
            "sync_in_zone",
            "teleport_to_coordinates",
            "teleport_to_friend",
            "teleport_to_marked_location",
            "teleport_to_world_hub",
        ],
    ),
    (
        "Combat",
        [
            "check_health_and_mana",
            "collect_wisps",
            "handle_combat",
            "handle_potions",
            "wait_for_health",
        ],
    ),
    (
        "Interaction",
        [
            "click_path",
            "collect_reagent",
            "interact",
            "interact_menu",
            "wait_for_dialogue",
        ],
    ),
    (
        "Utility",
        [
            "apply_level_scaling",
            "change_realm",
            "check_if_dropped",
            "check_level_scaling",
            "check_zone",
            "quicksell",
            "relog",
            "sleep",
        ],
    ),
]

NODE_TYPE_TO_CATEGORY: dict[str, str] = {
    node_type: category for category, node_types in NODE_CATEGORIES for node_type in node_types
}
