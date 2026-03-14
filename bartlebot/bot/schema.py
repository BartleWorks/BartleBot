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


class BotOptionModel(BaseModel):
    name: str
    type: Literal["bool", "int", "float", "str"]
    default: bool | int | float | str = False
    description: str | None = None


class BotConfig(BaseModel):
    name: str
    description: str | None = None
    version: str = "1.0"
    clients: str | list[int] = "all"
    initial: str
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

    @model_validator(mode="after")
    def initial_state_exists(self) -> "BotConfig":
        ids = {s.id for s in self.states}
        if self.initial not in ids:
            raise ValueError(f"initial state '{self.initial}' is not in states list")
        return self

    def to_machine_config(self) -> dict:
        """Convert to the flat dict format BotMachine expects."""
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
        return {
            "initial": self.initial,
            "states": [s.model_dump() for s in self.states],
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
