import json
import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class XYZModel(BaseModel):
    x: float
    y: float
    z: float


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
    # Per-node overrides for bot-wide options (e.g. "Safety Delay").
    # Keys are option names; values override the bot-wide default for this node only.
    option_overrides: dict = Field(default_factory=dict)
    notes: str | None = None  # User-authored documentation shown in the Properties sidebar


class BotOptionModel(BaseModel):
    name: str
    type: Literal["bool", "int", "float", "str", "int_list"]
    default: bool | int | float | str | list[int] = False
    description: str | None = None

    @model_validator(mode="after")
    def coerce_int_list_default(self) -> "BotOptionModel":
        if self.type == "int_list" and isinstance(self.default, str):
            self.default = [int(x.strip()) for x in self.default.split(",") if x.strip().lstrip("-").isdigit()]
        return self


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
    name: str
    description: str | None = None
    version: str = "1.0"
    clients: str | list[int] = "all"
    initial: str
    # Optional state to jump to after a client reconnects. If None, the bot stops on disconnect.
    recovery_state: str | None = None
    # Inline WizSprinter combat DSL string. None = use BartleBot built-in default.
    # use_bartlebot_default_combat_config controls whether this field is active.
    default_combat_config: str | None = None
    use_bartlebot_default_combat_config: bool = True
    start_location: str | None = None
    drops: list[str] = Field(default_factory=list)
    authors: str | None = None
    options: list[BotOptionModel] = Field(default_factory=list)
    states: list[StateModel]
    transitions: list[TransitionModel]
    regions: list[RegionModel] = Field(default_factory=list)

    @model_validator(mode="after")
    def initial_state_exists(self) -> "BotConfig":
        ids = {s.id for s in self.states}
        if self.initial not in ids:
            raise ValueError(f"initial state '{self.initial}' is not in states list")
        if self.recovery_state is not None and self.recovery_state not in ids:
            raise ValueError(f"recovery_state '{self.recovery_state}' is not in states list")
        return self

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
        # Stable-sort so conditional transitions (those with conditions) are evaluated
        # before unconditional ones from the same source.  This makes unconditional edges
        # act as explicit fallbacks regardless of their position in the JSON file.
        trans_list.sort(key=lambda t: (t["source"], 0 if t.get("conditions") else 1))
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
            "default_combat_config": self.default_combat_config,
            "conditions_registry": conditions_registry,
        }

    @classmethod
    def load(cls, path: str | Path) -> "BotConfig":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls.model_validate(data)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a sibling temp file then atomically replace the target.
        # Prevents corruption if the process crashes mid-write.
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix=path.stem + "_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.model_dump(exclude_none=True), f, indent=2)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
