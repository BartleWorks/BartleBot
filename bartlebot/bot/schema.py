import json
import os
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
    type: Literal["bool", "int", "float", "str", "player_list"]
    # The author's recommended value -- *not* the user's current one, which lives in
    # settings.json (see bartlebot.bot_options). None means the author has no opinion,
    # which is what lets the app setting below it be reachable at all.
    default: bool | int | float | str | list[int] | None = None
    description: str | None = None


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
    start_location: str | None = None
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
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a sibling temp file then atomically replace the target.
        # Prevents corruption if the process crashes mid-write.
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix=path.stem + "_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._dump_for_file(), f, indent=2)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
