from bartlebot.nodes.collect_wisps import run as collect_wisps_run, NODE_SPEC as _collect_wisps_spec
from bartlebot.nodes.enter_dungeon import run as enter_dungeon_run, NODE_SPEC as _enter_dungeon_spec
from bartlebot.nodes.go_to_world_hub import run as go_to_world_hub_run, NODE_SPEC as _go_to_world_hub_spec
from bartlebot.nodes.go_to_xyz import run as go_to_xyz_run, NODE_SPEC as _go_to_xyz_spec
from bartlebot.nodes.go_to_zone import run as go_to_zone_run, NODE_SPEC as _go_to_zone_spec
from bartlebot.nodes.handle_combat import run as handle_combat_run, NODE_SPEC as _handle_combat_spec
from bartlebot.nodes.interact import run as interact_run, NODE_SPEC as _interact_spec
from bartlebot.nodes.interact_to_zone import run as interact_to_zone_run, NODE_SPEC as _interact_to_zone_spec
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
}

# Convenience alias — param schemas derived from NODE_SPECS so there is one source of truth
NODE_PARAM_SCHEMAS: dict[str, list[ParamField]] = {
    k: v.params for k, v in NODE_SPECS.items()
}
