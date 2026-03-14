from bartlebot.nodes.go_to_zone import run as go_to_zone_run, NODE_SPEC as _go_to_zone_spec
from bartlebot.nodes.handle_combat import run as handle_combat_run, NODE_SPEC as _handle_combat_spec
from bartlebot.nodes.param_schema import NodeSpec, ParamField

NODE_REGISTRY: dict = {
    "go_to_zone": go_to_zone_run,
    "handle_combat": handle_combat_run,
}

NODE_SPECS: dict[str, NodeSpec] = {
    "go_to_zone": _go_to_zone_spec,
    "handle_combat": _handle_combat_spec,
}

# Convenience alias — param schemas derived from NODE_SPECS so there is one source of truth
NODE_PARAM_SCHEMAS: dict[str, list[ParamField]] = {
    k: v.params for k, v in NODE_SPECS.items()
}
