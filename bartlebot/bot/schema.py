import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class XYZModel(BaseModel):
    x: float
    y: float
    z: float


class TransitionModel(BaseModel):
    trigger: str
    source: str
    dest: str
    # Expected return value from the source node (e.g. "SUCCESS", "FAILURE").
    # BotMachine auto-generates a condition method from this at construction time.
    # None means the transition fires unconditionally (no condition check).
    when: str | None = None
    # Bot option name that must be truthy for this transition to fire.
    # BotMachine generates a _flag_<name> condition method for this.
    # Used to implement bot-level behavioural flags (e.g. "repeat").
    flag: str | None = None
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
    flipped: bool = False  # When True, input port is on the right and outputs on the left


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


class RegionModel(BaseModel):
    id: str
    label: str
    members: list[str]  # state IDs
    color: str  # palette key, e.g. "amber"


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
        """
        trans_list = []
        for t in self.transitions:
            d: dict = {"trigger": t.trigger, "source": t.source, "dest": t.dest}
            if t.when:
                d["conditions"] = [f"_result_is_{t.when}"]
            if t.flag:
                d.setdefault("conditions", []).append(f"_flag_{t.flag}")
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
            "default_combat_config": self.default_combat_config,
        }

    @classmethod
    def load(cls, path: str | Path) -> "BotConfig":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls.model_validate(data)

    def save(self, path: str | Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.model_dump(exclude_none=True), f, indent=2)
