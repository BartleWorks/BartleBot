"""Setup steps: what a template asks the user to fill in, and how far a bot has got.

A template version lists its steps in ``template_meta.setup``
(:class:`~bartlebot.bot.schema.SetupStep`). Each step names its *targets*, and every
target is a **fill-in**: a field left blank in every released version, for the user to
fill. Nothing else marks one. A bot does not carry the steps; it reads them from its base
version through ``template`` (:func:`steps_for`), so a finished update review brings the
newer version's steps with it.

Blank is the type's own emptiness
    :func:`field_empty` is the one rule: an empty string, an empty list, an all-zero
    coordinate. The scene's required-param check, the library row and Release all ask it,
    so "blank" cannot mean two things.

Done is worked out, never stored
    A step is done when every target is filled and valid (:func:`progress`). A target
    whose node the user deleted is ignored, and a step left with no targets is not listed
    or counted: they removed it on purpose, which is what a template update will assume
    too. A ``drops`` target only keeps its step open; a bot runs without drops.

Where a fill-in's value is
    A node param is read from the node. A subgraph node's input is read from the
    *member* it drives, since the bot file stores the composed members: a promoted
    node's ``<member>.<key>`` is that member's param, and a named input is every param
    bound to it, which only the definition can say -- pass the ``library`` to resolve one.
    That is also where an unfilled input's error lands: on the member, which rolls up
    onto the subgraph node.

No Qt here: the library row counts a bot's steps from its file without building a scene.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from bartlebot import zone_nav
from bartlebot.bot.schema import BotConfig, RegionModel, SetupStep, SetupTarget, StateModel
from bartlebot.nodes.registry import NODE_PARAM_SCHEMAS
from bartlebot.subgraphs import member_id

logger = logging.getLogger(__name__)

#: The one bot-level field a step may target.
DROPS_FIELD = "drops"

#: What an unfilled fill-in's error starts with, on its node.
ERROR_PREFIX = "Setup: "


# ---------------------------------------------------------------------------
# Blank and valid
# ---------------------------------------------------------------------------


def value_empty(field_type: str, value: Any) -> bool:
    """Whether ``value`` is the blank of ``field_type``: what an unfilled field of that type holds."""
    if value is None:
        return True
    if field_type in ("str", "text", "zone", "nav_zone", "wiki_page", "str_choice"):
        # Both zone widgets store a zone path, and an unset one is the empty string.
        return not str(value).strip()
    if field_type in ("str_list", "drop_keep_list", "player_list"):
        return not value  # empty list -- for player_list, no player selected is a no-op
    if field_type == "xyz":
        # All-zero counts as empty: it is what an unset coordinate was saved as before the
        # field could be blank, and a node would teleport to the zone's origin on it.
        try:
            axes = [float(value[axis]) for axis in ("x", "y", "z")]
        except (KeyError, TypeError, ValueError):
            return True
        return not any(axes)
    return False  # int, float, bool always have a value


def field_empty(field, params: Mapping[str, Any]) -> bool:
    """Whether a ``ParamField`` has no meaningful value in ``params``."""
    if field.key not in params:
        return True
    return value_empty(field.type, params[field.key])


def nav_zone_reachable(value: Any) -> bool:
    """Whether a ``nav_zone`` value is somewhere Sync In Zone can go. An empty one, or an unloadable map, passes.

    The picker offers only destinations, but a value can arrive another way: a file,
    or Record writing a zone param a ``nav_zone`` one is linked to.
    """
    if not value or not isinstance(value, str):
        return True
    nav = zone_nav.load()
    return not nav.worlds or zone_nav.is_destination(nav, value)


def _field_of(state: StateModel, key: str):
    return next((f for f in NODE_PARAM_SCHEMAS.get(state.node_type, []) if f.key == key), None)


def _param_empty(state: StateModel, key: str) -> bool:
    field = _field_of(state, key)
    if field is not None:
        return field_empty(field, state.params)
    return state.params.get(key) in (None, "", [], {})


def _param_valid(state: StateModel, key: str) -> bool:
    """Whether a filled param holds something its node accepts: the checks the scene makes per param."""
    field = _field_of(state, key)
    if field is not None and field.type == "nav_zone":
        return nav_zone_reachable(state.params.get(key))
    return True


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------


def _instance(config: BotConfig, region_id: str | None) -> RegionModel | None:
    return next((r for r in config.regions if r.id == region_id and r.source is not None), None)


def target_exists(config: BotConfig, target: SetupTarget) -> bool:
    """Whether the node, subgraph node or field ``target`` names is still in ``config``."""
    if target.field is not None:
        return target.field == DROPS_FIELD
    if target.state is not None:
        return any(s.id == target.state for s in config.states)
    return _instance(config, target.region) is not None


def target_params(config: BotConfig, target: SetupTarget, library=None) -> list[tuple[StateModel, str]]:
    """The ``(node, param key)`` pairs holding ``target``'s value: none for ``drops`` or a target that is gone.

    A subgraph input is read where it lands, on the instance's members. A named input
    needs ``library`` (a ``SubgraphLibrary``) for its bindings; without one it has no
    pairs and is judged by the value the instance stores (:func:`target_filled`).
    """
    by_id = {s.id: s for s in config.states}
    if target.state is not None and target.param is not None:
        state = by_id.get(target.state)
        return [(state, target.param)] if state is not None else []
    region = _instance(config, target.region)
    if region is None or target.input is None:
        return []
    defn = library.get(region.source.subgraph_id) if library is not None else None
    named = next((i for i in defn.inputs if i.name == target.input), None) if defn is not None else None
    if named is not None:
        pairs = [(by_id.get(member_id(region.id, b.state)), b.param) for b in named.bindings]
        return [(state, key) for state, key in pairs if state is not None]
    member, dot, key = target.input.partition(".")
    state = by_id.get(member_id(region.id, member)) if dot else None
    return [(state, key)] if state is not None else []


def target_filled(config: BotConfig, target: SetupTarget, library=None) -> bool:
    """Whether ``target`` holds a value. False for one that is gone."""
    if target.field is not None:
        return bool(config.drops)
    pairs = target_params(config, target, library)
    if pairs:
        return not any(_param_empty(state, key) for state, key in pairs)
    region = _instance(config, target.region)
    if region is None:
        return False
    return region.source.inputs.get(target.input or "") not in (None, "", [], {})


def target_done(config: BotConfig, target: SetupTarget, library=None) -> bool:
    """Filled, and holding something its node accepts."""
    if not target_filled(config, target, library):
        return False
    return all(_param_valid(state, key) for state, key in target_params(config, target, library))


def _param_label(state: StateModel | None, key: str) -> str:
    """A param as its own form labels it, or its key for one the node type does not have."""
    field = _field_of(state, key) if state is not None else None
    return (field.label or key) if field is not None else key


def describe_target(config: BotConfig, target: SetupTarget) -> str:
    """ "Enter dungeon · From Zone": what a target is called in the Setup tab and in a refusal.

    By the labels the user sees on the node's own page, never the raw keys: a template's
    steps are read by whoever makes a bot from it, and may one day be written by them too.
    """
    if target.field is not None:
        return "Possible drops"
    by_id = {s.id: s for s in config.states}
    if target.state is not None:
        state = by_id.get(target.state)
        name = (state.label or state.id) if state is not None else target.state
        return f"{name} \u00b7 {_param_label(state, target.param or '')}"
    region = _instance(config, target.region)
    if region is None:
        return f"{target.region} \u00b7 {target.input}"
    member, dot, key = (target.input or "").partition(".")
    state = by_id.get(member_id(region.id, member)) if dot else None
    if state is not None:
        return f"{region.label} \u00b7 {state.label or member} \u203a {_param_label(state, key)}"
    return f"{region.label} \u00b7 {target.input}"


# ---------------------------------------------------------------------------
# A bot's steps and how far along they are
# ---------------------------------------------------------------------------


@lru_cache(maxsize=64)
def _released_steps(templates_dir: str, template_id: str, version: int) -> tuple[SetupStep, ...]:
    # Cached for good: a released version never changes. Keyed on the folder so a
    # test's store on a tmp_path never answers for the shipped one.
    from bartlebot import template_store  # noqa: PLC0415 - template_store imports this module

    template = next(
        (t for t in template_store.list_templates(Path(templates_dir).parent) if t.id == template_id),
        None,
    )
    if template is None:
        raise LookupError(f"no template {template_id}")
    meta = BotConfig.load(template.version_path(version)).template_meta
    return tuple(meta.setup) if meta is not None else ()


def steps_for(
    config: BotConfig | None, root: str | Path | None = None, user_root: str | Path | None = None
) -> list[SetupStep]:
    """The setup steps that apply to ``config``: a template's own, or a bot's base version's.

    Empty for a bot made from nothing, and for one whose template or base version the
    store does not have -- a bot shared without its template still opens and runs.
    """
    if config is None:
        return []
    if config.template_meta is not None:
        return list(config.template_meta.setup)
    if config.template is None:
        return []
    from bartlebot import template_store  # noqa: PLC0415 - template_store imports this module

    folder = str(template_store.templates_dir(root, user_root))
    try:
        return list(_released_steps(folder, config.template.id, config.template.version))
    except Exception:
        logger.debug(
            "No setup steps for %s: template %s v%d is not in the store",
            config.name,
            config.template.id,
            config.template.version,
        )
        return []


@dataclass(frozen=True)
class StepStatus:
    """One step as a bot stands against it."""

    step: SetupStep
    #: Its place in the template's own list, from 1.
    number: int
    #: The targets the bot still has; never empty.
    targets: tuple[SetupTarget, ...]
    done: bool


def progress(config: BotConfig, steps: Sequence[SetupStep], library=None) -> list[StepStatus]:
    """Each step ``config`` still has something to show for, in order, with whether it is done.

    A target whose node is gone is dropped, and a step with none left is left out: the
    user removed what it asked about.
    """
    statuses: list[StepStatus] = []
    for number, step in enumerate(steps, start=1):
        live = tuple(t for t in step.targets if target_exists(config, t))
        if not live:
            continue
        done = all(target_done(config, t, library) for t in live)
        statuses.append(StepStatus(step=step, number=number, targets=live, done=done))
    return statuses


def steps_left(config: BotConfig, steps: Sequence[SetupStep], library=None) -> int:
    """How many steps are not done yet."""
    return sum(1 for status in progress(config, steps, library) if not status.done)


def left_in_file(data: Mapping[str, Any], root: str | Path | None = None, user_root: str | Path | None = None) -> int:
    """Steps left in a bot file's raw ``data``; 0 for a bot made from no template, or one that does not parse.

    What the library row shows. A file with no ``template`` is answered without
    validating it, which is every bot not made by New Bot.
    """
    if not isinstance(data.get("template"), dict):
        return 0
    try:
        config = BotConfig.model_validate(data)
    except Exception:
        return 0
    return steps_left(config, steps_for(config, root, user_root))


@dataclass(frozen=True)
class FillIn:
    """A param that is a fill-in, and the step that asks for it."""

    step: SetupStep
    number: int


def fill_ins(config: BotConfig, steps: Sequence[SetupStep], library=None) -> dict[tuple[str, str], FillIn]:
    """``(state id, param key)`` -> the first step that asks for it, over every node-param fill-in."""
    found: dict[tuple[str, str], FillIn] = {}
    for number, step in enumerate(steps, start=1):
        for target in step.targets:
            for state, key in target_params(config, target, library):
                found.setdefault((state.id, key), FillIn(step=step, number=number))
    return found


def setup_error(fill: FillIn) -> str:
    """The error an unfilled fill-in raises on its node, in a bot."""
    return f"{ERROR_PREFIX}{fill.step.text}"


# ---------------------------------------------------------------------------
# A draft
# ---------------------------------------------------------------------------


def draft_problems(draft: BotConfig, library=None) -> list[str]:
    """Why a template's steps keep it from being released: empty when they do not.

    Every step says something and asks for something, every target is in the draft,
    and every fill-in is blank -- so "done" means "valid" in the bot made from it, and
    a placeholder can never run as if it were a real value.
    """
    problems: list[str] = []
    meta = draft.template_meta
    if meta is None:
        return problems
    for number, step in enumerate(meta.setup, start=1):
        if not step.text.strip():
            problems.append(f"Setup step {number} has no text.")
        if not step.targets:
            problems.append(f"Setup step {number} asks for nothing: a step the app can't check belongs in the blurb.")
        for target in step.targets:
            what = describe_target(draft, target)
            if not target_exists(draft, target):
                problems.append(f"Setup step {number} asks for {what}, which the draft does not have.")
            elif target.field is None and not target_params(draft, target, library) and "." in (target.input or ""):
                problems.append(f"Setup step {number} asks for {what}, which the draft does not have.")
            elif target.state is not None and _field_of(target_params(draft, target)[0][0], target.param or "") is None:
                problems.append(f"Setup step {number} asks for {what}, which is not a param of that node.")
            elif target_filled(draft, target, library):
                problems.append(f"Setup step {number}: {what} holds a value. A fill-in is blank in every release.")
    return problems
