"""Parameter schema types used to describe node parameters for the GUI editor."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParamField:
    """Describes one parameter accepted by a node type.

    Attributes:
        key:         JSON key used in the params dict.
        type:        Widget type: "str" | "text" | "int" | "float" | "bool" | "xyz".
                     "text" renders as a multi-line QPlainTextEdit.
        label:       Human-readable form label.
        help:        Tooltip / documentation text shown on hover.
        default:     Value used when no existing param data is present.
        disabled_by: Key of a bool field in the same schema; when that field is
                     True this field's widget is disabled. Empty string = no link.
        required:    When True the param must be explicitly set in state.params
                     (i.e. the user must open the properties panel and save).
                     Used by the GUI to flag unconfigured nodes as errors.
    """
    key: str
    type: str
    label: str
    help: str = ""
    default: Any = None
    disabled_by: str = ""
    required: bool = False


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
