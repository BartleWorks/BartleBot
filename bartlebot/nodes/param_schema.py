"""Parameter schema types used to describe node parameters for the GUI editor."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParamField:
    """Describes one parameter accepted by a node type.

    Attributes:
        key:     JSON key used in the params dict.
        type:    Widget type: "str" | "int" | "float" | "bool" | "xyz".
        label:   Human-readable form label.
        help:    Tooltip / documentation text shown on hover.
        default: Value used when no existing param data is present.
    """
    key: str
    type: str
    label: str
    help: str = ""
    default: Any = None


@dataclass
class ReturnSpec:
    """Describes one return value that a node's run() coroutine may produce.

    Attributes:
        name:        The string the coroutine returns (e.g. "SUCCESS", "FAILURE").
        description: Human-readable explanation of when this value is returned.
    """
    name: str
    description: str = ""


@dataclass
class NodeSpec:
    """Full specification for a node type: description, parameters, and return values.

    This is the single source of truth for all node documentation.
    The GUI reads directly from NodeSpec; markdown docs are secondary.

    Attributes:
        description: Short description of what the node does.
        params:      Ordered list of parameter fields accepted by this node.
        returns:     Ordered list of possible return values from this node.
    """
    description: str
    params: list[ParamField] = field(default_factory=list)
    returns: list[ReturnSpec] = field(default_factory=list)
