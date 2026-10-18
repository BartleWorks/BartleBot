import json
import os
import re
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator

from bartlebot.content.bot_tags import normalize_tags
from bartlebot.content.subgraphs.subgraph_tags import normalize_subgraph_tags

#: Return values that fall through to the bot's recovery state when a node's port
#: for them is left unrouted. Almost every failure in a graph ends up back at the
#: recovery node, so requiring the edge was per-node busywork that also put a
#: quarter of the library's edges on the canvas -- see docs/RETURN_ROUTING.md.
#: Deliberately only the two conventional failure names: an unrouted SUCCESS
#: landing on recovery would be nonsense, not a convenience.
FALLTHROUGH_RETURNS = ("FAILURE", "FALSE")


class XYZModel(BaseModel):
    x: float
    y: float
    z: float


class DropModel(BaseModel):
    """One item a bot can drop, as the wiki names it and as the game names it.

    ``category`` is whatever the Wizard101 Central Wiki page grouped the item under
    ("Reagents", "Hats", …). It is None for drops added by hand and for every bot
    written before drops carried a category — there is no way to derive it after
    the fact, so those simply stay uncategorized until the bot is re-imported.

    ``name`` is the wiki's title for the item, which is what the drop list shows
    and what makes an entry unique. It is *not* always what the game displays: a
    wiki page cannot hold two entries under one title, so it disambiguates
    variants by suffix ("Balance Crushing Pin (60, Death)") where the game shows
    only "Balance Crushing Pin (60)" and puts the school in an icon column.

    ``match_name`` carries that in-game form when it differs, and is None when the
    two agree — the common case, kept out of the file by ``exclude_none``. Any
    code comparing a drop against text read off the game's UI wants
    :attr:`match_key`, never ``name``. ``bartlebot/game/wiki_drops.py`` derives it.
    """

    name: str
    category: str | None = None
    match_name: str | None = None

    @property
    def match_key(self) -> str:
        """The name to match against text the game displays."""
        return self.match_name or self.name


#: The node type that writes variables. Named here, where the registry-free rules for a
#: subgraph definition need it; the node is ``bartlebot/nodes/control_flow/set_variables.py``.
SET_VARIABLES_NODE = "set_variables"


class VariableModel(BaseModel):
    """A value the bot learns during a run and reads back to choose its next node.

    Declared per bot, typed, and fresh when the run starts: every run begins at
    ``initial``, and only the bot itself changes it after that -- a ``set_variables``
    node. Never saved: nothing a variable holds outlives the run. ``values`` lists an
    enum's names and is left out for the other types. ``initial`` left unset starts a
    bool at false, an int at 0 and an enum at its first value (:func:`initial_value`).

    Kept loose here, like a condition row's value: a declaration that breaks a rule
    (a duplicate name, an initial value of the wrong type) still loads, so the editor can
    show it, and :mod:`bartlebot.bot.variables` refuses the run instead.
    """

    name: str
    type: Literal["bool", "int", "enum"]
    values: list[str] | None = None
    initial: bool | int | str | None = None
    description: str | None = None

    def initial_value(self) -> bool | int | str | None:
        """What the variable holds when a run starts."""
        if self.initial is not None:
            return self.initial
        if self.type == "bool":
            return False
        if self.type == "int":
            return 0
        return self.values[0] if self.values else None


class AssignmentModel(BaseModel):
    """One write to a variable: set it to ``value``, add ``value`` to an int, or toggle a bool.

    The rows of a ``set_variables`` node's ``assignments`` param, which are stored as
    written and parsed with this. ``value`` is unused by ``toggle``.
    """

    variable: str
    op: Literal["set", "add", "toggle"] = "set"
    value: bool | int | str | None = None


class FlagCondition(BaseModel):
    """A single guard on a transition: a bot option, or a variable compared with a value.

    An option row is ``{option, negate}``: ``option`` is a BotOptionModel.name, and
    ``negate`` inverts the truthiness check. A variable row is ``{variable, compare,
    value}``: ``variable`` names a :class:`VariableModel`, and the row is true when its
    current value ``compare``-s against ``value``. Only an int takes the four ordering
    comparisons; ``bartlebot.bot.variables`` refuses them on a bool or an enum. Exactly one of ``option`` and
    ``variable`` is set. Multiple FlagConditions are combined by their parent's ``op``
    field, so options and variables mix in one AND/OR expression.

    Every reader of ``option`` must pass over a variable row, whose ``option`` is None.
    """

    option: str | None = None
    negate: bool = False
    variable: str | None = None
    compare: Literal["==", "!=", "<", "<=", ">", ">="] | None = None
    value: bool | int | str | None = None

    @property
    def is_variable(self) -> bool:
        """Whether this row reads a variable rather than an option."""
        return self.variable is not None

    @model_validator(mode="after")
    def names_one_guard(self) -> "FlagCondition":
        if (self.option is None) == (self.variable is None):
            raise ValueError("a condition names an option or a variable, and not both")
        if self.variable is None:
            # Dropped, not refused: before variable rows these were unknown keys, which a
            # file could carry and pydantic ignored, and such a file must still load.
            self.compare = None
            self.value = None
            return self
        if self.negate:
            raise ValueError(f"the condition on variable {self.variable!r} cannot be negated: compare with != instead")
        if self.value is None:
            raise ValueError(f"the condition on variable {self.variable!r} needs a value to compare with")
        if self.compare is None:
            self.compare = "=="
        return self

    @model_serializer(mode="wrap")
    def _dump_one_shape(self, handler):
        """A variable row is written without ``negate``, which only an option row has."""
        data = handler(self)
        if self.variable is not None:
            data.pop("negate", None)
        return data


class FlagGroup(BaseModel):
    """A group of FlagConditions combined by a single AND/OR operator.

    Multiple groups are combined by TransitionModel.flag_groups_op (AND or OR).
    This enables expressions like ``(A AND B) OR C`` by using two groups:
    group 1 = [A, B] with op=AND, group 2 = [C] with op=AND, groups_op=OR.
    """

    flags: list[FlagCondition]
    op: Literal["AND", "OR"] = "AND"


class TransitionModel(BaseModel):
    # The run loop fires only "advance". Any other trigger made the edge's state raise from
    # advance(), outside the node's try, which ended the run; a bot from a share file or a
    # hand edit is refused when it loads instead.
    trigger: Literal["advance"]
    source: str
    dest: str
    # Expected return value from the source node (e.g. "SUCCESS", "FAILURE").
    # BotMachine auto-generates a condition method from this at construction time.
    # None means the transition fires unconditionally (no condition check).
    when: str | None = None
    # --- Flat (legacy) flag fields ---
    # Single-level list of option guards combined by flag_op.  Kept for backward
    # compatibility; if flag_groups is non-empty, it takes precedence.
    flags: list[FlagCondition] = Field(default_factory=list)
    flag_op: Literal["AND", "OR"] = "AND"
    # --- Grouped flag fields ---
    # Two-level flag expression: each group combines its flags internally with op,
    # then groups are combined with flag_groups_op.  Takes precedence over flags.
    flag_groups: list[FlagGroup] = Field(default_factory=list)
    flag_groups_op: Literal["AND", "OR"] = "OR"
    label: str | None = None
    # Writes to variables, applied in order when this edge is the one taken (#189): the
    # same rows a set_variables node holds. Left out of the file while empty, so no
    # existing bot's bytes change.
    assign: list[AssignmentModel] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def _dump_assign_only_when_set(self, handler):
        data = handler(self)
        if not self.assign:
            data.pop("assign", None)
        return data


class StateModel(BaseModel):
    id: str
    node_type: str
    label: str | None = None
    position: dict | None = None  # {"x": float, "y": float}, the node's top-left — GUI canvas layout only
    params: dict = Field(default_factory=dict)
    notes: str | None = None  # User-authored documentation shown in the Properties sidebar
    # Return values that deliberately end the run, mapped to the reason shown to the
    # user when they do. The counterweight to FALLTHROUGH_RETURNS: once an unrouted
    # failure means "recover", an author needs a way to say "no, this one is fatal"
    # -- check_zone finding the wrong world is the case that motivated it.
    # ``None`` rather than an empty dict so exclude_none keeps the key out of every
    # state in every bot file; read it through :meth:`stop_reason`.
    stops: dict[str, str] | None = None

    def stop_reason(self, return_name: str | None) -> str | None:
        """The author's reason for stopping on *return_name*, or None if it does not stop.

        An empty string is a real answer -- the port stops, the author just gave no
        reason -- so callers must test against None, not truthiness.
        """
        if return_name is None or not self.stops:
            return None
        return self.stops.get(return_name)


class BotOptionModel(BaseModel):
    name: str
    # The last four are declared by nodes only and never offered in the Add Option
    # dialog: they are a str / str_list underneath, with a richer control in Option Values.
    type: Literal[
        "bool",
        "int",
        "float",
        "str",
        "str_list",
        "player_list",
        "drop_keep_list",
        "friend_name",
        "realm",
        "player_realms",
    ]
    # The author's recommended value -- *not* the user's current one, which lives in
    # settings.json (see bartlebot.options.bot_options). None means the author has no opinion,
    # which is what lets the app setting below it be reachable at all.
    default: bool | int | float | str | list[int] | list[str] | None = None
    description: str | None = None
    # Bounds and unit, carried onto a *composed* declaration from its BotOptionSpec.
    # Derived, never persisted -- exactly like type and description: storable_options
    # rebuilds a derived option without them, and an author's own option never sets
    # one, so exclude_none keeps all three out of every file.
    minimum: float | None = None
    maximum: float | None = None
    suffix: str | None = None
    # The bool option this one is a child of, carried on the same way. A child is
    # indented under its parent in Option Values and disabled while the parent is off --
    # ``Sell Pets`` does nothing whatever it says while ``Enable Quicksell`` is off,
    # and a live-looking control that cannot affect the run is the thing to avoid.
    depends_on: str | None = None


#: The two reserved node types a definition's ports are drawn as. An FSM edge lands
#: on a state, not on a pin, so a port has to be a node an edge can target. They live
#: in a definition's ``states`` and are never composed into a bot, and they are
#: deliberately absent from ``NODE_SPECS``, so the node browser never offers them.
SUBGRAPH_ENTRY = "subgraph_entry"
SUBGRAPH_EXIT = "subgraph_exit"
PORT_NODE_TYPES = frozenset({SUBGRAPH_ENTRY, SUBGRAPH_EXIT})


class SubgraphInputBinding(BaseModel):
    """One thing an input drives: a member's param, or an input of a nested instance.

    For a member, ``state`` is its definition-local id and ``param`` the param's key.
    For a nested instance, ``region`` is its region's definition-local id and ``param``
    names one of *its* inputs, so the value goes in through the inner signature and
    never past it. Exactly one of ``state`` and ``region`` is set; ``region`` is None
    for a member and so is never written for one.
    """

    state: str | None = None
    param: str
    region: str | None = None

    @model_validator(mode="after")
    def names_one_target(self) -> "SubgraphInputBinding":
        if (self.state is None) == (self.region is None):
            raise ValueError("a binding names a state or a nested instance's region, and not both")
        return self


class SubgraphInput(BaseModel):
    """A knob a subgraph definition exposes, set per instance in ``SubgraphSource.inputs``.

    What the author owns is stored here: name, label, help, default and type. ``type``
    is a ``ParamField`` type name, the narrowest of the bound params' types, so its
    value is valid for every param it feeds; what the *type* owns -- range,
    placeholder, the widget -- comes from the bound fields when the form is drawn.
    ``default`` is what a fresh instance is prefilled with; it is *not* applied during
    composition -- an input the instance has not set leaves the definition's own value
    in place. ``panel`` names the inputs panel it is listed under, or None for the top.
    """

    name: str
    label: str | None = None
    type: str
    help: str | None = None
    default: object | None = None
    bindings: list[SubgraphInputBinding] = Field(default_factory=list)
    panel: str | None = None


class SubgraphPanel(BaseModel):
    """A heading on the call node's page. Layout only: no effect on binding or composition.

    ``kind="inputs"`` groups the inputs that name it. ``kind="node"`` exposes the member
    ``member`` *whole*: one input per param of its node type, worked out from the
    node's schema rather than stored, each valued in ``SubgraphSource.inputs`` under
    ``<member>.<key>`` -- which is how a recordable node keeps its Record button inside
    a subgraph. ``bindings`` lets other members' params share an exposed value, keyed
    by the exposed param.
    """

    id: str
    kind: Literal["inputs", "node"] = "inputs"
    label: str | None = None
    member: str | None = None
    bindings: dict[str, list[SubgraphInputBinding]] = Field(default_factory=dict)


class SubgraphPort(BaseModel):
    """Which member a port crosses at, in definition-local ids, as of the instance's snapshot.

    An entry names the ``member`` its one edge goes into; an exit lists the
    ``(member, outcome)`` pairs that feed it, stored under ``"from"``. Recorded per
    instance so a later definition change can move the bot's edges with the port.
    """

    model_config = ConfigDict(serialize_by_alias=True, validate_by_name=True, validate_by_alias=True)

    member: str | None = None
    from_: list[tuple[str, str | None]] | None = Field(default=None, alias="from")


class SubgraphSource(BaseModel):
    """The link that makes a region an instance of a subgraph definition.

    ``version`` is the definition version the stored members were composed from.
    ``inputs`` maps input name to value (and ``<member>.<key>`` to an exposed node's
    param); ``None`` or absence means unset. An instance sets inputs and nothing else:
    to change anything internal, detach. ``ports`` maps port id to where it crossed
    when the snapshot was composed. ``anchor`` is the call node's position, in the
    ``StateModel.position`` convention; members sit at ``anchor + definition position``.
    """

    subgraph_id: str
    version: int
    inputs: dict[str, object] = Field(default_factory=dict)
    ports: dict[str, SubgraphPort] = Field(default_factory=dict)
    anchor: dict = Field(default_factory=lambda: {"x": 0.0, "y": 0.0})


# Maps old named region color keys to numbered slots for backward compatibility.
_REGION_COLOR_MIGRATION: dict[str, int] = {
    "amber": 1,
    "teal": 2,
    "purple": 3,
    "red": 4,
    "blue": 5,
    "green": 6,
}


class RegionModel(BaseModel):
    id: str
    label: str
    members: list[str]  # state IDs
    color: int  # palette slot number (1-6)
    # Set only on a subgraph instance. None is excluded from every dump, so a region
    # without one is written exactly as before the field existed.
    source: SubgraphSource | None = None
    # True while a plain region is drawn as one node (#67). Never False: expanding sets it
    # back to None, so a region that was never collapsed is written exactly as before.
    # Meaningless on a linked region, which is always drawn as its call node.
    collapsed: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_named_color(cls, data):
        """Accept old string color keys (e.g. ``"amber"``) and convert to int."""
        if isinstance(data, dict):
            raw = data.get("color")
            if isinstance(raw, str) and raw in _REGION_COLOR_MIGRATION:
                data = dict(data)  # avoid mutating the original
                data["color"] = _REGION_COLOR_MIGRATION[raw]
        return data


class DefinitionMeta(BaseModel):
    """What makes an in-memory ``BotConfig`` a subgraph definition document rather than a bot.

    Opening a definition builds a ``BotConfig`` from its graph so the canvas, scene and
    undo need not know the difference; this carries the rest of the definition --
    its identity and its signature -- through undo and autosave with it. Never set on
    a bot file, so ``exclude_none`` keeps it out of every one.
    """

    id: str
    version: int = 1
    inputs: list[SubgraphInput] = Field(default_factory=list)
    panels: list[SubgraphPanel] = Field(default_factory=list)
    # The definition's categories (#172). Here rather than in the document's own ``tags``,
    # which hold bot tags and drop any other word on validation.
    tags: list[str] = Field(default_factory=list)

    _tags = field_validator("tags", mode="before")(normalize_subgraph_tags)


class TemplateRef(BaseModel):
    """Which template a bot was made from, and the version it is based on.

    ``version`` is the *base*: the released version the bot was created from, or last
    finished reviewing against. A monotonic integer like ``SubgraphSource.version``, and
    unrelated to ``BotConfig.version``. Written only by New Bot and by finishing a
    template update review -- never on load, never by a migration, and copied unchanged
    by Save As, since a copy of a bot has the same base.
    """

    id: str
    version: int


class ForkedFrom(BaseModel):
    """The Official bot a user's copy was made from (Make my own copy, #149).

    ``sha256`` is the Official bot's content hash when the copy was made
    (:func:`bartlebot.shared.content_hash.content_sha256`), so a later release that changes the
    Official bot can be told apart from one that does not. Written once, by the copy,
    and never by a load or a save; Save As copies it unchanged, since a copy of a copy
    came from the same Official bot.
    """

    id: str
    sha256: str


class SetupTarget(BaseModel):
    """One field a setup step asks the user to fill: a *fill-in*. Exactly one of three kinds.

    A node's param (``state`` and ``param``), a subgraph node's input (``region`` and
    ``input``, where a promoted node's param is the input ``<member>.<key>``), or a field
    of the bot itself (``field``; only ``drops``). Name, description and tags are not
    targets: the New Bot dialog asks for them.
    """

    state: str | None = None
    param: str | None = None
    region: str | None = None
    input: str | None = None
    field: Literal["drops"] | None = None

    @model_validator(mode="after")
    def _one_kind(self) -> "SetupTarget":
        kinds = [
            self.state is not None and self.param is not None,
            self.region is not None and self.input is not None,
            self.field is not None,
        ]
        given = [v for v in (self.state, self.param, self.region, self.input, self.field) if v is not None]
        if sum(kinds) != 1 or len(given) != (1 if self.field is not None else 2):
            raise ValueError("a setup target is state+param, region+input, or field, and only one of them")
        return self


class SetupStep(BaseModel):
    """One instruction a template gives the user, and the fill-ins it asks for.

    Every target is a fill-in, and nothing else marks one. A step is done when all of
    its targets are filled and valid; that is worked out from the bot each time
    (:mod:`bartlebot.content.templates.setup_steps`) and never stored.
    """

    text: str
    targets: list[SetupTarget] = Field(default_factory=list)


class TemplateMeta(BaseModel):
    """What makes a ``BotConfig`` a template -- a draft or a released version -- rather than a bot.

    Plays the part :class:`DefinitionMeta` plays for a definition: it rides along through
    undo and autosave with the rest of the config. Never set on a bot: New Bot drops it
    when it copies a version, so ``exclude_none`` keeps it out of every bot file. The
    bot reads ``setup`` from its base version instead of carrying a copy.
    """

    #: What the New Bot dialog says about the template, under its name.
    blurb: str = ""
    #: What a bot made from this version has to fill in before it can run.
    setup: list[SetupStep] = Field(default_factory=list)
    #: What this template's own ids start with: its own prefix first, then each one it
    #: inherited, back through every template it was duplicated from (#164). ``None`` for
    #: the templates made before forks, which mint ``t_`` alone. A fork mints only under
    #: the first, so nothing it adds can take an id its source has or could add later.
    id_prefixes: list[str] | None = None
    #: The template, and its released version, this one was duplicated from (#164).
    #: ``None`` for a template started blank. Written once, by Duplicate.
    forked_from: TemplateRef | None = None


class BotConfig(BaseModel):
    # Required, with no default_factory: a minted id is indistinguishable from a real
    # one, so a file missing it would silently get a fresh identity on every load --
    # and every user value keyed on that id would vanish with it. Fixed by saving once.
    id: str
    name: str
    description: str | None = None
    version: str = "1.0"
    clients: str | list[int] = "all"
    initial: str
    # State to jump to after a client reconnects. Required: a bot with nowhere to resume
    # only discovers that mid-run, in a log line, at the moment a crash has already
    # happened -- the same "ask before the run, not after" reasoning as recovery_readiness.
    recovery_state: str
    # No ``start_location``: nothing in the engine ever read it, ``sync_in_zone`` and the
    # bot templates make it moot, and it read as a promise the app would take you there.
    # Pydantic ignores it in an older file, and ``save()`` drops it.
    drops: list[DropModel] = Field(default_factory=list)
    authors: str | None = None
    # What the bot is *for* — see bartlebot/content/bot_tags.py. Filters the library panel.
    tags: list[str] = Field(default_factory=list)
    options: list[BotOptionModel] = Field(default_factory=list)
    # What the bot learns as it runs and reads back on its edges (docs/VARIABLES.md).
    # Left out of the file while empty, as history is, so a bot without any is written
    # exactly as before the field existed.
    variables: list[VariableModel] = Field(default_factory=list)
    states: list[StateModel]
    transitions: list[TransitionModel]
    regions: list[RegionModel] = Field(default_factory=list)
    # Set only while this config is a subgraph definition open as a document.
    definition: DefinitionMeta | None = None
    # Set only on a bot made from a template (New Bot), naming it and the base version.
    template: TemplateRef | None = None
    # Set only on a user's copy of an Official bot, naming the bot it was made from.
    forked_from: ForkedFrom | None = None
    # Set only on a template's draft or a released version of it, never on a bot.
    template_meta: TemplateMeta | None = None
    # The content hashes of what earlier saves replaced, newest first (#169); see
    # bartlebot.shared.content_hash. Left out of the file while empty, so a bot never
    # saved since it existed is written exactly as before.
    history: list[str] = Field(default_factory=list)
    # The content hash it had as its sender sent it, when an import wrote it changed:
    # its links rewritten to a copy, or as a copy itself (#171; bartlebot.shared.content_hash).
    # Dropped by the next save that changes it, and never in a share file.
    sent_sha256: str | None = None

    @property
    def is_definition(self) -> bool:
        """Whether this is a subgraph definition document rather than a bot."""
        return self.definition is not None

    @property
    def is_template(self) -> bool:
        """Whether this is a template, a draft or a released version, rather than a bot."""
        return self.template_meta is not None

    @field_validator("drops", mode="before")
    @classmethod
    def accept_bare_drop_names(cls, value):
        """Accept the pre-category form where each drop was just its name.

        Needed for more than old files on disk: UndoManager round-trips a config
        through model_dump/model_validate on every edit, and users carry bot files
        that scripts/migrate_drop_entries.py has never been run against.
        """
        if isinstance(value, list):
            return [{"name": v} if isinstance(v, str) else v for v in value]
        return value

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tag_list(cls, value):
        """Drop unknown tags, de-duplicate, and order by the vocabulary.

        Tolerant rather than strict — see :func:`bartlebot.content.bot_tags.normalize_tags`.
        Runs on every load *and* on UndoManager's model_dump/model_validate round-trip,
        so the stored order stays stable no matter how the editor wrote it.
        """
        return normalize_tags(value)

    @field_validator("id")
    @classmethod
    def id_is_not_blank(cls, value: str) -> str:
        """An empty id is the same failure as a missing one, one step later."""
        if not value.strip():
            raise ValueError("id must not be empty")
        return value

    @model_validator(mode="after")
    def initial_state_exists(self) -> "BotConfig":
        if not self.states and not self.initial and not self.recovery_state:
            # A blank bot: nothing on the canvas yet, so nothing for either role to name.
            # The editor gives both to the first node added, and a bot with no nodes
            # cannot be run.
            return self
        ids = {s.id for s in self.states}
        if self.initial not in ids:
            raise ValueError(f"initial state '{self.initial}' is not in states list")
        if self.recovery_state not in ids:
            raise ValueError(f"recovery_state '{self.recovery_state}' is not in states list")
        return self

    def sort_transitions(self) -> None:
        """Stable-sort transitions so flag-guarded edges precede plain when-only fallbacks.

        Edges with ``flags`` or ``flag_groups`` may not always fire (the flag condition
        could be false), so they must be evaluated before edges that fire unconditionally
        on a matching ``when`` result.  Edges with only a ``when`` value act as fallbacks.
        """
        self.transitions.sort(key=lambda t: (t.source, 0 if (t.flags or t.flag_groups) else 1))

    def to_machine_config(self, node_specs: dict | None = None) -> dict:
        """Convert to the flat dict format BotMachine expects.

        If node_specs (a dict mapping node_type -> NodeSpec) is provided, each state
        dict will include a ``max_consecutive_retries`` key when the corresponding
        NodeSpec declares one. BotMachine uses this for per-state idle detection.

        Flag conditions are compiled to condition method names that BotMachine's
        ``__getattr__`` resolves at runtime:
          - Single non-negated option flag, AND: ``_flag_<option>`` (simple, backward-compat)
          - All other cases: ``_flag_compound_<idx>`` with an entry in conditions_registry

        A row in the registry is ``{"option", "negate"}`` for an option and
        ``{"variable", "compare", "value"}`` for a variable, in the same groups.
        A conditioned transition also carries its condition as text under ``guard``,
        which the machine names in the log when that edge is the one taken, and one with
        assignments carries their rows under ``assign``, which the machine applies then.
        """
        from bartlebot.bot.variables import describe_guard  # noqa: PLC0415 - avoids an import cycle

        self.sort_transitions()
        conditions_registry: dict[str, dict] = {}
        trans_list = []
        for idx, t in enumerate(self.transitions):
            d: dict = {"trigger": t.trigger, "source": t.source, "dest": t.dest}
            if t.when:
                d["conditions"] = [f"_result_is_{t.when}"]
            if t.flag_groups:
                # Grouped form: two-level boolean expression.
                cond_name = f"_flag_compound_{idx}"
                d.setdefault("conditions", []).append(cond_name)
                conditions_registry[cond_name] = {
                    "groups": [{"flags": [_compiled_row(fc) for fc in g.flags], "op": g.op} for g in t.flag_groups],
                    "groups_op": t.flag_groups_op,
                }
            elif t.flags:
                only = t.flags[0]
                if len(t.flags) == 1 and not only.is_variable and not only.negate and t.flag_op == "AND":
                    # Simple case: keep the cheap _flag_<name> form so existing bots
                    # with one plain flag condition continue to work without a registry lookup.
                    d.setdefault("conditions", []).append(f"_flag_{only.option}")
                else:
                    cond_name = f"_flag_compound_{idx}"
                    d.setdefault("conditions", []).append(cond_name)
                    conditions_registry[cond_name] = {
                        "flags": [_compiled_row(fc) for fc in t.flags],
                        "op": t.flag_op,
                    }
            if (guard := describe_guard(t)) is not None:
                d["guard"] = guard
            if t.assign:
                d["assign"] = [row.model_dump() for row in t.assign]
            if t.label:
                d["label"] = t.label
            trans_list.append(d)
        states = []
        for s in self.states:
            sd = s.model_dump()
            if node_specs:
                spec = node_specs.get(s.node_type)
                if spec is not None and spec.max_consecutive_retries is not None:
                    sd["max_consecutive_retries"] = spec.max_consecutive_retries
            states.append(sd)
        return {
            "initial": self.initial,
            # Carried so BotMachine can resolve an unrouted FALLTHROUGH_RETURNS port
            # without a BotConfig -- it is optional there, but the fall-through is not.
            "recovery_state": self.recovery_state,
            "states": states,
            "transitions": trans_list,
            "conditions_registry": conditions_registry,
            # The declarations, from which the machine starts every value when a run begins.
            "variables": [v.model_dump() for v in self.variables],
        }

    @classmethod
    def load(cls, path: str | Path) -> "BotConfig":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls.model_validate(data)

    def file_dump(self) -> dict:
        """The JSON body of this bot, with derived declarations left out.

        An option a node requires is composed at load time from its NodeSpec, so
        writing it back would put 80 copies of one description in the library and
        let a value the user edited ride out to everyone who opens the bot. The
        prune is in :mod:`bartlebot.options.bot_options`; it keeps only what is
        authorial -- a hand-added option, or a required one whose default the
        author deliberately changed.

        Done at dump time rather than on ``self``: the in-memory config is what
        the editor is showing, and a save is not an edit.
        """
        from bartlebot.options.bot_options import storable_options  # noqa: PLC0415 - avoids an import cycle

        data = self.model_dump(exclude_none=True)
        data["options"] = [o.model_dump(exclude_none=True) for o in storable_options(self)]
        for key in ("variables", "history"):
            if not data[key]:
                del data[key]
        return data

    def save(self, path: str | Path) -> None:
        # A template's files are byte-exact in git (``templates/** -text``), since a
        # released version is hashed, so a draft is written with the same line endings
        # on every platform. Left to the platform, saving one on Windows rewrote every
        # line of it.
        atomic_write_json(self.file_dump(), path, newline="\n" if self.is_template else None)


def _compiled_row(fc: FlagCondition) -> dict:
    """One condition row as ``BotMachine`` evaluates it from ``conditions_registry``."""
    if fc.is_variable:
        return {"variable": fc.variable, "compare": fc.compare, "value": fc.value}
    return {"option": fc.option, "negate": fc.negate}


def uses_variables(transitions: list[TransitionModel], states: list[StateModel]) -> bool:
    """Whether a graph reads or writes a variable: a variable condition row, an edge assignment, or a set_variables node."""
    if any(s.node_type == SET_VARIABLES_NODE for s in states):
        return True
    if any(t.assign for t in transitions):
        return True
    return any(fc.is_variable for t in transitions for fc in [*t.flags, *(f for g in t.flag_groups for f in g.flags)])


def atomic_write_json(data: dict, path: str | Path, *, newline: str | None = None) -> None:
    """Write ``data`` to a sibling temp file, then atomically replace ``path``.

    Prevents corruption if the process crashes mid-write. ``newline`` is ``open``'s:
    ``None`` writes the platform's line endings, ``"\\n"`` the same bytes everywhere.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix=path.stem + "_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as f:
            json.dump(data, f, indent=2)
            # On disk before the rename: without it a power cut can leave the new name on an empty file (#154).
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


#: The charset a subgraph definition's local ids are drawn from. Member ids are
#: composed as ``<region id>_<local id>``, so a local id has to be safe to embed.
SUBGRAPH_LOCAL_ID = re.compile(r"^[A-Za-z0-9_]+$")


def definition_structure_errors(
    states: list[StateModel],
    transitions: list[TransitionModel],
    regions: list[RegionModel],
    inputs: list[SubgraphInput],
    panels: list[SubgraphPanel],
) -> list[str]:
    """Every way a definition's graph and signature break the rules a definition is held to.

    Shared by :class:`SubgraphDefinition`, which refuses a file that breaks one, and
    the definition document's validation, which lists them and blocks saving. Kept
    registry-free, as ``BotConfig``'s validation is: a binding's ``param`` is not
    checked against the node's ``ParamField``s here, and neither are types -- that
    needs the registry, and lives with the editor.
    """
    errors: list[str] = []
    seen: set[str] = set()
    by_id: dict[str, StateModel] = {}
    for s in states:
        if not SUBGRAPH_LOCAL_ID.match(s.id):
            errors.append(f"state id {s.id!r} is not a valid subgraph-local id")
        if s.id in seen:
            errors.append(f"duplicate state id {s.id!r}")
        seen.add(s.id)
        by_id[s.id] = s
    ports = {sid for sid, s in by_id.items() if s.node_type in PORT_NODE_TYPES}
    for t in transitions:
        if t.source not in seen or t.dest not in seen:
            errors.append(f"transition {t.source!r} -> {t.dest!r} leaves the definition")

    entries = [s for s in states if s.node_type == SUBGRAPH_ENTRY]
    exits = [s for s in states if s.node_type == SUBGRAPH_EXIT]
    if not entries:
        errors.append("a subgraph needs at least one Entry")
    targets: dict[str, str] = {}
    for e in entries:
        name = e.label or e.id
        out = [t for t in transitions if t.source == e.id]
        if len(out) != 1:
            errors.append(f"Entry '{name}' needs exactly one edge into the subgraph, not {len(out)}")
        elif out[0].dest in ports:
            errors.append(f"Entry '{name}' must lead to a node, not straight to another port")
        else:
            dest = out[0].dest
            if dest in targets:
                errors.append(f"Entries '{targets[dest]}' and '{name}' both lead to the same node")
            targets.setdefault(dest, name)
        if any(t.dest == e.id for t in transitions):
            errors.append(f"Entry '{name}' cannot have edges leading into it")
    for x in exits:
        if any(t.source == x.id for t in transitions):
            errors.append(f"Exit '{x.label or x.id}' cannot have edges leading out of it")
    # Variables belong to a bot. Two instances of one definition reading the bot's
    # variables by name would share one counter without saying so (docs/VARIABLES.md).
    if uses_variables(transitions, states):
        errors.append("a subgraph cannot use variables: they belong to the bot")

    panel_ids: set[str] = set()
    exposed: set[str] = set()
    input_panels: set[str] = set()
    for panel in panels:
        if panel.id in panel_ids:
            errors.append(f"duplicate panel id {panel.id!r}")
        panel_ids.add(panel.id)
        if panel.kind == "inputs":
            input_panels.add(panel.id)
            continue
        if panel.member not in seen or panel.member in ports:
            errors.append(f"panel {panel.id!r} exposes {panel.member!r}, which is not a node of this subgraph")
        elif panel.member in exposed:
            errors.append(f"{panel.member!r} is exposed twice")
        exposed.add(panel.member or "")

    driven: set[tuple[str, str]] = set()

    def drive(state: str, param: str, what: str) -> None:
        if state not in seen or state in ports:
            errors.append(f"{what} binds to unknown state {state!r}")
        elif state in exposed:
            errors.append(f"{what} binds '{param}' of {state!r}, which an exposed node panel already owns")
        elif (state, param) in driven:
            errors.append(f"'{param}' of {state!r} is bound twice")
        driven.add((state, param))

    instances = {r.id for r in regions if r.source is not None}
    forwarded: set[tuple[str, str]] = set()

    names: set[str] = set()
    for inp in inputs:
        if inp.name in names:
            errors.append(f"duplicate input name {inp.name!r}")
        names.add(inp.name)
        if inp.panel is not None and inp.panel not in input_panels:
            errors.append(f"input {inp.name!r} names panel {inp.panel!r}, which is not an inputs panel")
        for b in inp.bindings:
            if b.region is None:
                drive(b.state or "", b.param, f"input {inp.name!r}")
            elif b.region not in instances:
                errors.append(
                    f"input {inp.name!r} links to {b.region!r}, which is not a subgraph node of this subgraph"
                )
            elif (b.region, b.param) in forwarded:
                errors.append(f"input '{b.param}' of {b.region!r} is linked twice")
            else:
                forwarded.add((b.region, b.param))
    for panel in panels:
        for key, extra in panel.bindings.items():
            for b in extra:
                if b.region is not None:
                    errors.append(
                        f"the exposed '{key}' of {panel.member!r} cannot be shared with a subgraph node's input"
                    )
                else:
                    drive(b.state or "", b.param, f"the exposed '{key}' of {panel.member!r}")

    region_ids: set[str] = set()
    for r in regions:
        if not SUBGRAPH_LOCAL_ID.match(r.id):
            errors.append(f"region id {r.id!r} is not a valid subgraph-local id")
        if r.id in region_ids:
            errors.append(f"duplicate region id {r.id!r}")
        region_ids.add(r.id)
        for m in r.members:
            if m not in seen:
                errors.append(f"region {r.id!r} names unknown state {m!r}")
    return errors


class SubgraphDefinition(BaseModel):
    """A reusable subgraph, stored as one file in the subgraph library.

    A user-made node type: its inputs are its params, its Entry and Exit nodes
    (``SUBGRAPH_ENTRY`` / ``SUBGRAPH_EXIT`` states) are its ports, and its body is
    edited in a document of its own. Holds states, transitions and regions, every id
    local to this file and every position relative to ``(0, 0)``. A nested instance
    is stored the way a bot stores one -- its members as a snapshot among ``states``,
    its region carrying ``source`` -- and a plain region is kept as layout. It has no
    ``initial`` or ``recovery_state``: roles are bot-owned. Composition into a bot is
    :mod:`bartlebot.content.subgraphs.subgraphs`; storage is :mod:`bartlebot.content.subgraphs.subgraph_store`.
    """

    id: str
    name: str
    description: str | None = None
    version: int = 1
    # What the subgraph does, from the fixed vocabulary of bartlebot.content.subgraphs.subgraph_tags (#172).
    # Left out of the file while empty, so a definition never given one is written as before.
    tags: list[str] = Field(default_factory=list)
    inputs: list[SubgraphInput] = Field(default_factory=list)
    panels: list[SubgraphPanel] = Field(default_factory=list)
    states: list[StateModel]
    transitions: list[TransitionModel] = Field(default_factory=list)
    regions: list[RegionModel] = Field(default_factory=list)
    # The content hashes of what earlier saves replaced, newest first, and on a user's
    # override of an Official definition the hash of the Official one it was made from
    # (#169; bartlebot.shared.content_hash). Both stay out of the file while unset.
    history: list[str] = Field(default_factory=list)
    official_sha256: str | None = None
    # The content hash it had as its sender sent it, when an import wrote it changed:
    # its links rewritten to a copy, or as a copy itself (#171; bartlebot.shared.content_hash).
    # Dropped by the next save that changes it, and never in a share file.
    sent_sha256: str | None = None

    @field_validator("id")
    @classmethod
    def id_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("id must not be empty")
        return value

    _tags = field_validator("tags", mode="before")(normalize_subgraph_tags)

    @field_validator("version")
    @classmethod
    def version_is_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("version must be >= 1")
        return value

    @model_validator(mode="after")
    def structure_is_valid(self) -> "SubgraphDefinition":
        errors = definition_structure_errors(self.states, self.transitions, self.regions, self.inputs, self.panels)
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @classmethod
    def load(cls, path: str | Path) -> "SubgraphDefinition":
        with open(path, encoding="utf-8") as f:
            return cls.model_validate(json.load(f))

    def file_dump(self) -> dict:
        """The JSON body of this definition, as :meth:`save` writes it."""
        data = self.model_dump(exclude_none=True)
        if not data["history"]:
            del data["history"]
        if not data["tags"]:
            del data["tags"]
        return data

    def save(self, path: str | Path) -> None:
        atomic_write_json(self.file_dump(), path)
