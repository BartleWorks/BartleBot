import json
import os
import re
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from bartlebot.bot_tags import normalize_tags


class XYZModel(BaseModel):
    x: float
    y: float
    z: float


class DropModel(BaseModel):
    """One item a bot can drop, as the wiki names it and as the game names it.

    ``category`` is whatever the Wizard101 Central page grouped the item under
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
    :attr:`match_key`, never ``name``. ``bartlebot/wiki_drops.py`` derives it.
    """

    name: str
    category: str | None = None
    match_name: str | None = None

    @property
    def match_key(self) -> str:
        """The name to match against text the game displays."""
        return self.match_name or self.name


class FlagCondition(BaseModel):
    """A single bot-option guard on a transition.

    ``option`` is a BotOptionModel.name; ``negate`` inverts the truthiness check.
    Multiple FlagConditions are combined by their parent's ``op`` field.
    """

    option: str
    negate: bool = False


class FlagGroup(BaseModel):
    """A group of FlagConditions combined by a single AND/OR operator.

    Multiple groups are combined by TransitionModel.flag_groups_op (AND or OR).
    This enables expressions like ``(A AND B) OR C`` by using two groups:
    group 1 = [A, B] with op=AND, group 2 = [C] with op=AND, groups_op=OR.
    """

    flags: list[FlagCondition]
    op: Literal["AND", "OR"] = "AND"


class TransitionModel(BaseModel):
    trigger: str
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


class StateModel(BaseModel):
    id: str
    node_type: str
    label: str | None = None
    position: dict | None = None  # {"x": float, "y": float} — GUI canvas layout only
    params: dict = Field(default_factory=dict)
    notes: str | None = None  # User-authored documentation shown in the Properties sidebar


class BotOptionModel(BaseModel):
    name: str
    # The last two are declared by nodes only and never offered in the Add Option
    # dialog: they are a str / str_list underneath, with a richer control in Setup.
    type: Literal["bool", "int", "float", "str", "str_list", "player_list", "drop_keep_list", "friend_name"]
    # The author's recommended value -- *not* the user's current one, which lives in
    # settings.json (see bartlebot.bot_options). None means the author has no opinion,
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
    # indented under its parent in Setup and disabled while the parent is off --
    # ``Sell Pets`` does nothing whatever it says while ``Enable Quicksell`` is off,
    # and a live-looking control that cannot affect the run is the thing to avoid.
    depends_on: str | None = None


class SubgraphInputBinding(BaseModel):
    """One member param an input drives: ``state`` is a definition-local id."""

    state: str
    param: str


class SubgraphInput(BaseModel):
    """A knob a subgraph definition exposes, set per instance in ``SubgraphSource.inputs``.

    ``type`` is a ``ParamField`` type name, so the region page can build its form
    with the same widgets a node's params get. ``default`` is what a fresh
    instance is prefilled with; it is *not* applied during composition -- an input
    the instance has not set leaves the definition's own value in place.
    """

    name: str
    label: str | None = None
    type: str
    help: str | None = None
    default: object | None = None
    bindings: list[SubgraphInputBinding] = Field(default_factory=list)


class SubgraphSource(BaseModel):
    """The link that makes a region an instance of a subgraph definition.

    ``version`` is the definition version the stored members were composed from.
    ``inputs`` maps input name to value; ``None`` (or absence) means unset.
    ``param_overrides`` maps a definition-local state id to the params this
    instance pins on it, and wins over everything. ``anchor`` is where the
    definition's origin sits on this canvas, in the ``StateModel.position``
    convention -- layout is instance-owned, see docs/SUBGRAPHS.md.
    """

    subgraph_id: str
    version: int
    inputs: dict[str, object] = Field(default_factory=dict)
    param_overrides: dict[str, dict[str, object]] = Field(default_factory=dict)
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
    # No ``start_location``: nothing in the engine ever read it, ``go_to_world`` and the
    # bot templates make it moot, and it read as a promise the app would take you there.
    # Pydantic ignores it in an older file, and ``save()`` drops it.
    drops: list[DropModel] = Field(default_factory=list)
    authors: str | None = None
    # What the bot is *for* — see bartlebot/bot_tags.py. Filters the library panel.
    tags: list[str] = Field(default_factory=list)
    options: list[BotOptionModel] = Field(default_factory=list)
    states: list[StateModel]
    transitions: list[TransitionModel]
    regions: list[RegionModel] = Field(default_factory=list)

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

        Tolerant rather than strict — see :func:`bartlebot.bot_tags.normalize_tags`.
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
          - Single non-negated flag, AND: ``_flag_<option>`` (simple, backward-compat)
          - All other cases: ``_flag_compound_<idx>`` with an entry in conditions_registry
        """
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
                    "groups": [
                        {
                            "flags": [{"option": fc.option, "negate": fc.negate} for fc in g.flags],
                            "op": g.op,
                        }
                        for g in t.flag_groups
                    ],
                    "groups_op": t.flag_groups_op,
                }
            elif t.flags:
                if len(t.flags) == 1 and not t.flags[0].negate and t.flag_op == "AND":
                    # Simple case: keep the cheap _flag_<name> form so existing bots
                    # with one plain flag condition continue to work without a registry lookup.
                    d.setdefault("conditions", []).append(f"_flag_{t.flags[0].option}")
                else:
                    cond_name = f"_flag_compound_{idx}"
                    d.setdefault("conditions", []).append(cond_name)
                    conditions_registry[cond_name] = {
                        "flags": [{"option": fc.option, "negate": fc.negate} for fc in t.flags],
                        "op": t.flag_op,
                    }
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
            "states": states,
            "transitions": trans_list,
            "conditions_registry": conditions_registry,
        }

    @classmethod
    def load(cls, path: str | Path) -> "BotConfig":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls.model_validate(data)

    def _dump_for_file(self) -> dict:
        """The JSON body of this bot, with derived declarations left out.

        An option a node requires is composed at load time from its NodeSpec, so
        writing it back would put 80 copies of one description in the library and
        let a value the user edited ride out to everyone who opens the bot. The
        prune is in :mod:`bartlebot.bot_options`; it keeps only what is
        authorial -- a hand-added option, or a required one whose default the
        author deliberately changed.

        Done at dump time rather than on ``self``: the in-memory config is what
        the editor is showing, and a save is not an edit.
        """
        from bartlebot.bot_options import storable_options  # noqa: PLC0415 - avoids an import cycle

        data = self.model_dump(exclude_none=True)
        data["options"] = [o.model_dump(exclude_none=True) for o in storable_options(self)]
        return data

    def save(self, path: str | Path) -> None:
        _atomic_write_json(self._dump_for_file(), path)


def _atomic_write_json(data: dict, path: str | Path) -> None:
    """Write ``data`` to a sibling temp file, then atomically replace ``path``.

    Prevents corruption if the process crashes mid-write.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix=path.stem + "_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
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


class SubgraphDefinition(BaseModel):
    """A reusable subgraph, stored as one file in the subgraph library.

    Holds states, internal transitions and (for nesting) regions, every id local
    to this file and every position relative to ``(0, 0)``. It has no ``initial``
    or ``recovery_state``: roles are bot-owned. Composition into a bot is
    :mod:`bartlebot.subgraphs`; storage is :mod:`bartlebot.subgraph_store`.

    Validation stays registry-free, as ``BotConfig``'s does: a binding's ``param``
    is not checked against the node's ``ParamField``s here, for the same reason
    ``_validate_node`` lives in the scene rather than in this module.
    """

    id: str
    name: str
    description: str | None = None
    version: int = 1
    inputs: list[SubgraphInput] = Field(default_factory=list)
    states: list[StateModel]
    transitions: list[TransitionModel] = Field(default_factory=list)
    regions: list[RegionModel] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def id_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("id must not be empty")
        return value

    @field_validator("version")
    @classmethod
    def version_is_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("version must be >= 1")
        return value

    @model_validator(mode="after")
    def ids_are_local_and_consistent(self) -> "SubgraphDefinition":
        seen: set[str] = set()
        for s in self.states:
            if not SUBGRAPH_LOCAL_ID.match(s.id):
                raise ValueError(f"state id {s.id!r} is not a valid subgraph-local id")
            if s.id in seen:
                raise ValueError(f"duplicate state id {s.id!r}")
            seen.add(s.id)
        for t in self.transitions:
            if t.source not in seen or t.dest not in seen:
                raise ValueError(f"transition {t.source!r} -> {t.dest!r} leaves the definition")
        names: set[str] = set()
        for inp in self.inputs:
            if inp.name in names:
                raise ValueError(f"duplicate input name {inp.name!r}")
            names.add(inp.name)
            for b in inp.bindings:
                if b.state not in seen:
                    raise ValueError(f"input {inp.name!r} binds to unknown state {b.state!r}")
        region_ids: set[str] = set()
        for r in self.regions:
            if not SUBGRAPH_LOCAL_ID.match(r.id):
                raise ValueError(f"region id {r.id!r} is not a valid subgraph-local id")
            if r.id in region_ids:
                raise ValueError(f"duplicate region id {r.id!r}")
            region_ids.add(r.id)
            if r.source is None:
                raise ValueError(f"region {r.id!r} in a definition must be a nested instance (carry source)")
        return self

    @classmethod
    def load(cls, path: str | Path) -> "SubgraphDefinition":
        with open(path, encoding="utf-8") as f:
            return cls.model_validate(json.load(f))

    def save(self, path: str | Path) -> None:
        _atomic_write_json(self.model_dump(exclude_none=True), path)
