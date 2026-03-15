"""Parameter schema types used to describe node parameters for the GUI editor."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BotOptionSpec:
    """Declares a bot-level option that a node requires.

    When a node with required_bot_options is present in a bot, the editor
    automatically injects these options into the bot config if they are absent.
    Options required by at least one node cannot be deleted or renamed by the user.

    Attributes:
        name:        Option name as used in machine.get_option().
        type:        One of "bool", "int", "float", "str".
        default:     Default value shown in the sidebar.
        description: Tooltip shown next to the option widget.
    """
    name: str
    type: str
    default: bool | int | float | str = 0.0
    description: str | None = None


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
        description:          Short description of what the node does.
        params:               Ordered list of parameter fields accepted by this node.
        returns:              Ordered list of possible return values from this node.
        required_bot_options: Bot-level options this node needs. The editor auto-injects
                              them and prevents deletion while the node is present.
    """
    description: str
    params: list[ParamField] = field(default_factory=list)
    returns: list[ReturnSpec] = field(default_factory=list)
    required_bot_options: list[BotOptionSpec] = field(default_factory=list)
