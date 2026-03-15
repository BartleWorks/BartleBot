from bartlebot.nodes.action.collect_wisps import run as collect_wisps_run, NODE_SPEC as _collect_wisps_spec
from bartlebot.nodes.action.enter_dungeon import run as enter_dungeon_run, NODE_SPEC as _enter_dungeon_spec
from bartlebot.nodes.action.go_to_world_hub import run as go_to_world_hub_run, NODE_SPEC as _go_to_world_hub_spec
from bartlebot.nodes.action.go_to_xyz import run as go_to_xyz_run, NODE_SPEC as _go_to_xyz_spec
from bartlebot.nodes.action.go_to_zone import run as go_to_zone_run, NODE_SPEC as _go_to_zone_spec
from bartlebot.nodes.action.handle_combat import run as handle_combat_run, NODE_SPEC as _handle_combat_spec
from bartlebot.nodes.action.interact import run as interact_run, NODE_SPEC as _interact_spec
from bartlebot.nodes.action.interact_to_zone import run as interact_to_zone_run, NODE_SPEC as _interact_to_zone_spec
from bartlebot.nodes.action.mark_location import run as mark_location_run, NODE_SPEC as _mark_location_spec
from bartlebot.nodes.action.recall_to_marked_location import run as recall_to_marked_location_run, NODE_SPEC as _recall_to_marked_location_spec
from bartlebot.nodes.condition.check_if_dropped import run as check_if_dropped_run, NODE_SPEC as _check_if_dropped_spec
from bartlebot.nodes.condition.check_level_scaling import run as check_level_scaling_run, NODE_SPEC as _check_level_scaling_spec
from bartlebot.nodes.param_schema import NodeSpec, ParamField

NODE_REGISTRY: dict = {
    "go_to_zone": go_to_zone_run,
    "go_to_world_hub": go_to_world_hub_run,
    "go_to_xyz": go_to_xyz_run,
    "enter_dungeon": enter_dungeon_run,
    "interact": interact_run,
    "interact_to_zone": interact_to_zone_run,
    "handle_combat": handle_combat_run,
    "collect_wisps": collect_wisps_run,
    "mark_location": mark_location_run,
    "recall_to_marked_location": recall_to_marked_location_run,
    "check_if_dropped": check_if_dropped_run,
    "check_level_scaling": check_level_scaling_run,
}

NODE_SPECS: dict[str, NodeSpec] = {
    "go_to_zone": _go_to_zone_spec,
    "go_to_world_hub": _go_to_world_hub_spec,
    "go_to_xyz": _go_to_xyz_spec,
    "enter_dungeon": _enter_dungeon_spec,
    "interact": _interact_spec,
    "interact_to_zone": _interact_to_zone_spec,
    "handle_combat": _handle_combat_spec,
    "collect_wisps": _collect_wisps_spec,
    "mark_location": _mark_location_spec,
    "recall_to_marked_location": _recall_to_marked_location_spec,
    "check_if_dropped": _check_if_dropped_spec,
    "check_level_scaling": _check_level_scaling_spec,
}

# Convenience alias — param schemas derived from NODE_SPECS so there is one source of truth
NODE_PARAM_SCHEMAS: dict[str, list[ParamField]] = {
    k: v.params for k, v in NODE_SPECS.items()
}
