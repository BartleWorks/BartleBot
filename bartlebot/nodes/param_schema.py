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
        minimum:     Lowest value the option may take, or None for no floor. Enforced in
                     two places, and both are needed: the control clamps what can be
                     typed, and resolve_option discards an out-of-range value that
                     arrived from a hand-edited settings.json or bot file. A negative
                     Safety Delay is the case -- it fails silently rather than loudly,
                     since asyncio.sleep returns at once and every window polled *for*
                     that many seconds expires on its first check.
        maximum:     Highest value the option may take, or None for no ceiling.
        suffix:      Unit shown inside the control, e.g. " s". Presentation only; the
                     stored value is the bare number.
        depends_on: Name of a **bool bot option** this one is a child of. The Setup
                     surface indents the child under it and disables it while the parent
                     resolves false; nothing else changes -- the child is still resolved
                     the ordinary four-level way, and its stored value survives the
                     parent being switched off and back on.

                     This is for an option that cannot affect the run at all while
                     another is off: ``Sell Pets`` says nothing whatever it holds when
                     ``Enable Quicksell`` is off. Unlike ``required_when`` the option is
                     still *composed* -- the author is not saying it does not exist, the
                     user has switched it off, and it has to come back on unchanged.
        required_when: Key of a bool param on the *same node*; the option is required
                     only while that param is True. Empty string = always required.

                     This is for a choice only the author can say is available at all:
                     Challenge Mode exists in a handful of dungeons and nowhere else, so
                     offering the user a switch for it in the other sixty is noise they
                     cannot act on. The author states the fact about their dungeon in a
                     param, and the *option* -- whether to use it -- stays the user's.
    """

    name: str
    type: str
    default: bool | int | float | str | list = 0.0
    description: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    suffix: str | None = None
    depends_on: str = ""
    required_when: str = ""


@dataclass
class ParamField:
    """Describes one parameter accepted by a node type.

    Attributes:
        key:          JSON key used in the params dict.
        type:         Widget type: "str" | "text" | "int" | "float" | "bool" | "xyz" |
                      "str_list" | "int_choice" | "str_choice" | "player_list" | "player_slot" |
                      "wiki_page" | "nav_zone".
                      "nav_zone" is a zone on the navigation map, picked from a list grouped by
                      world (bartlebot/gui/widgets/zone_picker.py). Only the zone path is stored:
                      the map already says which world it is in.
                      "text" renders as a multi-line QPlainTextEdit.
                      "player_slot" is the single-select sibling of "player_list": one 1-based client
                      index, or 0 for none.
                      There is deliberately no param type for a value the *user* owns. "tri_bool" and
                      "app_setting_str" were two such, invented before there was anywhere else for a
                      user's value to live; both are gone. A value the user owns is a **bot option**
                      with a null declaration — see bartlebot/bot_options.py — which reaches every node
                      in the bot at once instead of once per node that thought to offer it.
                      "wiki_page" records the Wizard101 Central page a node's data came from. Editor-only —
                      no node reads it at run time. Stored as a bare page title, never a URL; resolve it
                      through bartlebot.wiki_pages.resolve_wiki_page() and never by hand.
        label:        Human-readable form label.
        help:         Tooltip / documentation text shown on hover.
        default:      Value used when no existing param data is present.
        minimum:      Lowest value a numeric param may take, or None for no floor.
        maximum:      Highest value a numeric param may take, or None for no ceiling.
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
    minimum: float | None = None
    maximum: float | None = None
    placeholder: str = ""
    disabled_by: str = ""
    enabled_by: str = ""
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
