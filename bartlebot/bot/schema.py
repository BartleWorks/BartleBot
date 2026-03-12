import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class XYZModel(BaseModel):
    x: float
    y: float
    z: float


class ConditionModel(BaseModel):
    type: str
    params: dict = Field(default_factory=dict)


class TransitionModel(BaseModel):
    trigger: str
    source: str
    dest: str
    # Strings referencing condition methods on BotMachine (e.g. "last_result_is_success")
    conditions: list[str] = Field(default_factory=list)
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
    default_combat_config: str | None = None
    start_location: str | None = None
    drops: list[str] = Field(default_factory=list)
    credits: str | None = None
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
        return {
            "initial": self.initial,
            "states": [s.model_dump() for s in self.states],
            "transitions": [t.model_dump(exclude_none=True) for t in self.transitions],
        }

    @classmethod
    def load(cls, path: str | Path) -> "BotConfig":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls.model_validate(data)

    def save(self, path: str | Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.model_dump(exclude_none=True), f, indent=2)
