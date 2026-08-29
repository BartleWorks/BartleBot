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
        type:        One of "bool", "int", "float", "str", "player_list".
        default:     Default value shown in the sidebar.
        description: Tooltip shown next to the option widget.
    """

    name: str
    type: str
    default: bool | int | float | str | list = 0.0
    description: str | None = None


@dataclass
class ParamField:
    """Describes one parameter accepted by a node type.

    Attributes:
        key:          JSON key used in the params dict.
        type:         Widget type: "str" | "text" | "int" | "float" | "bool" | "tri_bool" | "xyz" |
                      "str_list" | "int_choice" | "str_choice" | "player_list" | "player_slot" |
                      "drop_keep_list" | "wiki_page" | "app_setting_str".
                      "text" renders as a multi-line QPlainTextEdit.
                      "tri_bool" is a bool that can also be left unset, meaning "use the app setting named by
                      app_setting_key". Stored as true / false / null, and null is what a bot carries unless
                      its author pinned an answer. The node resolves it; see quicksell.resolve_app_defaults.
                      "player_slot" is the single-select sibling of "player_list": one 1-based client
                      index, or 0 for none.
                      "app_setting_str" is a string the *user* owns rather than the bot — the widget edits
                      the SettingsManager general-settings key named by app_setting_key, and get_params()
                      never writes the key to the bot file. Use it when no bot should ever ship a value,
                      which is a stronger claim than "tri_bool"'s "no bot needs to"; teleport_to_friend's
                      friend_name is the case it was built for.
                      "drop_keep_list" is a str_list of quicksell keep-list keywords, shown alongside the bot's
                      drop table with a checkbox per drop; the value on disk is a plain list of strings.
                      "wiki_page" records the Wizard101 Central page a node's data came from. Editor-only —
                      no node reads it at run time. Stored as a bare page title, never a URL; resolve it
                      through bartlebot.wiki_pages.resolve_wiki_page() and never by hand.
        label:        Human-readable form label.
        help:         Tooltip / documentation text shown on hover.
        default:      Value used when no existing param data is present.
        default_hint: Short description of the default behaviour shown as a dimmed hint
                      below the input widget when the field is optional. Leave blank for
                      required fields or fields where the default needs no explanation.
        placeholder:  Ghost text shown inside an empty text widget. Useful for multi-line
                      "text" fields where the format is not obvious from the label alone.
        disabled_by:  Key of a bool field in the same schema; when that field is
                      True this field's widget is disabled. Empty string = no link.
        enabled_by:   The mirror of disabled_by: this field's widget is disabled while the
                      named bool field is False. Pairing the two on opposite fields is how
                      mutually exclusive alternatives are expressed — see teleport_to_friend,
                      where the target is either a hooked player or a name, never both.
        app_setting_key: For type="tri_bool": the SettingsManager general-settings key holding the
                      app-level answer this field falls back to. The widget reads it to label the
                      unset choice with the value currently in force.
        required:     When True the param must be explicitly set in state.params
                      (i.e. the user must open the properties panel and save).
                      Used by the GUI to flag unconfigured nodes as errors.
    """

    key: str
    type: str
    label: str
    help: str = ""
    default: Any = None
    default_hint: str = ""
    placeholder: str = ""
    disabled_by: str = ""
    enabled_by: str = ""
    app_setting_key: str = ""
    required: bool = False
    choices: list[Any] = field(default_factory=list)
    """The ordered dropdown options.

    For ``type="int_choice"``: a list of integers.

    For ``type="str_choice"``: a list of strings, or of ``(value, label)`` pairs when what
    the bot file stores differs from what the user should read — go_to_world stores the
    world's zone prefix ("DragonSpire") and shows its display name ("Dragonspyre").
    """


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
        description:             Short description of what the node does.
        params:                  Ordered list of parameter fields accepted by this node.
        returns:                 Ordered list of possible return values from this node.
        required_bot_options:    Bot-level options this node needs. The editor auto-injects
                                 them and prevents deletion while the node is present.
        max_consecutive_retries: Maximum number of times the FSM may self-loop on this
                                 node type before BotMachine stops the bot. None = no limit.
                                 Set by the node author based on worst-case tick duration.
    """

    description: str
    params: list[ParamField] = field(default_factory=list)
    returns: list[ReturnSpec] = field(default_factory=list)
    required_bot_options: list[BotOptionSpec] = field(default_factory=list)
    max_consecutive_retries: int | None = None
    usage_notes: str = ""
    """Free-text field for gotchas, prerequisites, and common usage patterns shown in the node browser."""
    see_also: list[str] = field(default_factory=list)
    """List of related node_type keys shown as clickable links in the node browser."""
    safety_warning: str | None = None
    """Warning message shown in the safety dialog before a bot run. None = no warning."""
