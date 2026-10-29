"""What the node page's and the subgraph node page's folding cards say, without Qt.

Both pages draw their sections as ``OptionCard``\\s (#142), the card #138 built for bot
options. This module is the half of that which needs no widget: which cards start open,
what a folded card's one-line summary reads, how many required fields a card still
needs, and what each of a node's returns does -- the Connections card lists routes by
return value, including the ones the canvas never draws. ``option_groups`` is the same
split for the option surfaces.

Fold state is the reader's and lives in ``ui_state.json`` (``bartleby.qt.app.ui_state``),
keyed by a surface id and a group id:

* **The node page** has four fixed sections, the same on every node, so it is one
  surface (:data:`NODE_PAGE_SURFACE`) with four groups -- #138's situation exactly.
* **A subgraph node page** has groups its definition's author chose, so each definition
  is a surface of its own (:func:`call_page_surface`), never each placed node.

A group the reader has never folded follows its default, worked out when the page is
built and never while the user is working in it: a card holding an empty required field
starts open, a complete one starts folded. Once the reader folds or opens a card, that
answer is stored and wins (:func:`call_page_fold_defaults`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from bartlebot.content.templates.setup_steps import field_empty
from bartlebot.options.option_groups import SummaryPart

# ---------------------------------------------------------------------------
# Surfaces and groups
# ---------------------------------------------------------------------------

#: ``ui_state.json``'s name for the node page.
NODE_PAGE_SURFACE = "node_page"

#: The node page's four sections, in page order.
PARAMS = "params"
CONNECTIONS = "connections"
NOTES = "notes"
ROLE = "role"
NODE_PAGE_GROUPS = (PARAMS, CONNECTIONS, NOTES, ROLE)

#: A subgraph node's inputs that belong to no input group, drawn as one card.
INPUTS = "inputs"

#: What the folded Notes card shows when there is no note.
NO_NOTES = "No notes"


def node_page_fold_defaults(*, holds_role: bool = False) -> dict[str, bool]:
    """Whether each node page section starts collapsed, for a reader who never folded it.

    Parameters and Connections open, Notes folded. Role is folded unless the node
    holds a role, so a node that is the initial or recovery state says so at a glance.
    """
    return {PARAMS: False, CONNECTIONS: False, NOTES: True, ROLE: not holds_role}


#: ``ui_state.json``'s name for a definition's signature page (#155): three fixed
#: cards, the same in every definition, so one surface -- as the node page is.
SIGNATURE_PAGE_SURFACE = "signature_page"

#: The signature page's cards, in page order. Its inputs card shares :data:`INPUTS`.
DESCRIPTION = "description"
PORTS = "ports"
SIGNATURE_PAGE_GROUPS = (DESCRIPTION, PORTS, INPUTS)


def signature_fold_defaults(*, has_description: bool) -> dict[str, bool]:
    """Whether each signature page card starts collapsed, for a reader who never folded it.

    Ports and Inputs open: they are what the page is for. Description folds to its
    first line once it has one, and opens while it is empty, since an empty
    description is the one thing on the card left to do.
    """
    return {DESCRIPTION: has_description, PORTS: False, INPUTS: False}


def description_summary(text: str | None) -> list[SummaryPart]:
    """A folded Description card's line: its first line, or that there is none."""
    for line in (text or "").splitlines():
        if line.strip():
            return [SummaryPart(line.strip())]
    return [SummaryPart("No description")]


def ports_summary(entries: Iterable[str], exits: Iterable[str]) -> list[SummaryPart]:
    """A folded Ports card's line: "Start, Rejoin → Done, Out of potions"."""
    ins, outs = ", ".join(entries), ", ".join(exits)
    return [SummaryPart(f"{ins or 'No entry'} → {outs or 'no exit'}")]


def inputs_summary(inputs: int, groups: int, nodes: int) -> list[SummaryPart]:
    """A folded Inputs card's line: "6 inputs · 2 input groups · 1 promoted node"."""

    def count(n: int, word: str) -> str:
        return f"{n} {word}{'' if n == 1 else 's'}"

    parts = [SummaryPart(count(inputs, "input"))]
    if groups:
        parts.append(SummaryPart(count(groups, "input group")))
    if nodes:
        parts.append(SummaryPart(count(nodes, "promoted node")))
    return parts


def call_page_surface(subgraph_id: str) -> str:
    """``ui_state.json``'s name for the pages of every subgraph node of one definition."""
    return f"subgraph:{subgraph_id}"


def call_page_fold_defaults(
    groups: Iterable[str],
    *,
    incomplete: Iterable[str] = (),
    holds_role: bool = False,
) -> dict[str, bool]:
    """Whether each card on a subgraph node's page starts collapsed.

    A card holding an empty required field opens; every other card folds to its
    summary. Role follows the node page's rule.

    There is no author setting to keep a card folded. Input groups used to carry
    "Starts closed", but a complete card folds anyway, so the setting only ever
    changed anything for a card with a field still to fill -- hiding exactly the
    field the user had to find. An author wanting tuning knobs out of the way puts
    them in a group of their own (an "Advanced" group of optional inputs is always
    complete, so it always starts folded).
    """
    incomplete = set(incomplete)
    defaults = {group: group not in incomplete for group in groups}
    defaults[ROLE] = not holds_role
    return defaults


# ---------------------------------------------------------------------------
# Counts and summaries
# ---------------------------------------------------------------------------


def to_fill(schema: Iterable[Any], params: Mapping[str, Any]) -> int:
    """How many of *schema*'s required fields are empty in *params*."""
    return sum(1 for field in schema if field.required and field_empty(field, params))


def to_fill_text(count: int) -> str:
    """The card band's count: "2 to fill", or nothing once everything is."""
    return f"{count} to fill" if count else ""


def _short(field: Any, value: Any) -> str | None:
    """One value as a summary names it, or None for a value the summary leaves out."""
    if field.type in ("zone", "nav_zone"):
        text = str(value or "").strip()
        return text.rsplit("/", 1)[-1] if text else None
    if field.type == "bool":
        return field.label if value else None
    if field.type in ("str", "wiki_page", "text"):
        text = str(value or "").strip().splitlines()
        return text[0] if text else None
    if field.type in ("int", "float", "ratio"):
        if value is None or value == field.default:
            return None
        return f"{field.label}: {value}"
    if field.type in ("player_list", "str_list") and isinstance(value, list):
        return f"{field.label}: {', '.join(str(v) for v in value)}" if value else None
    return None


def params_summary(schema: Iterable[Any], params: Mapping[str, Any]) -> list[SummaryPart]:
    """A folded parameter card's line: the values that say what this node does.

    Zones by their last segment, text as its first line, a ticked box by its name,
    a number only when it has moved off its default. Coordinates are left out -- six
    numbers say nothing at a glance -- and so is anything empty. Nothing set at all
    reads "Nothing set".
    """
    parts = []
    for field in schema:
        if field.key not in params or field_empty(field, params):
            continue
        shown = _short(field, params[field.key])
        if shown:
            parts.append(SummaryPart(shown))
    return parts or [SummaryPart("Nothing set")]


def unrecorded(schema: Iterable[Any], params: Mapping[str, Any], recorded_keys: Iterable[str]) -> bool:
    """Whether nothing Record fills is set yet.

    Asked of the fields that can be blank: a bool Record fills, like ``interact``'s Has
    Dialogue, always holds a value, so it cannot say whether Record has run.
    """
    keys = set(recorded_keys)
    blankable = [f for f in schema if f.key in keys and f.type != "bool"]
    return bool(blankable) and all(field_empty(f, params) for f in blankable)


def recording_summary(
    schema: Iterable[Any], params: Mapping[str, Any], recorded_keys: Iterable[str]
) -> list[SummaryPart]:
    """A recordable node's folded line: "Not recorded" while nothing Record fills is set."""
    fields = list(schema)
    if unrecorded(fields, params, recorded_keys):
        return [SummaryPart("Not recorded")]
    return params_summary(fields, params)


def notes_summary(notes: str | None) -> list[SummaryPart]:
    """A folded Notes card's line: the note's first line, or that there is none."""
    for line in (notes or "").splitlines():
        if line.strip():
            return [SummaryPart(line.strip())]
    return [SummaryPart(NO_NOTES)]


def role_summary(*, initial: bool, recovery: bool) -> list[SummaryPart]:
    """A folded Role card's line."""
    if initial and recovery:
        return [SummaryPart("Initial"), SummaryPart("Recovery")]
    if initial:
        return [SummaryPart("Initial"), SummaryPart("not recovery")]
    if recovery:
        return [SummaryPart("Not initial"), SummaryPart("Recovery")]
    return [SummaryPart("Not initial"), SummaryPart("not recovery")]


# ---------------------------------------------------------------------------
# Routes: what each return does
# ---------------------------------------------------------------------------

#: How a route is taken, which decides how its row is drawn.
EDGE = "edge"  # a transition drawn on the canvas
RECOVERY = "recovery"  # an unrouted FAILURE/FALSE, to the recovery state (not drawn)
STOP = "stop"  # the author made this return stop the bot
END = "end"  # the node has no outgoing transitions: the run ends here
UNROUTED = "unrouted"  # nothing happens; validation warns about it


@dataclass(frozen=True)
class Route:
    """One outgoing route off a node: a return value and where it goes.

    *when* is the return name, or None for an edge that fires on any return.
    *dest* is a state id for an edge or the recovery route, else None. *transition*
    is the edge itself, for a row that selects it. *priority* is the 1-based order the
    engine tries edges in -- conditional ones first -- and None for a route that is not
    an edge. *reason* is an explicit stop's reason ("" when the author gave none).
    """

    when: str | None
    kind: str
    dest: str | None = None
    transition: Any = None
    priority: int | None = None
    reason: str | None = None


def outgoing_routes(
    state: Any,
    returns: Iterable[str],
    transitions: Iterable[Any],
    *,
    recovery_state: str | None,
    state_ids: Iterable[str] = (),
    fallthrough: Iterable[str] = ("FAILURE", "FALSE"),
) -> list[Route]:
    """Every route off *state*, one per edge and one per return nothing routes.

    The rules are the engine's (``docs/RETURN_ROUTING.md``): a stop outranks
    everything; a node with no outgoing transitions ends the run; an unrouted
    FAILURE or FALSE falls through to the recovery state, except on the recovery
    state itself; an edge with no ``when`` covers every return nothing else routes.

    Rows come in the node's return order, then any edge on a return the node does
    not declare, then the unconditional edges. Each edge keeps the priority the
    engine evaluates it at, so reordering for display hides nothing about which
    fires first.
    """
    returns = list(returns)
    fallthrough = set(fallthrough)
    known = set(state_ids)
    outgoing = [t for t in transitions if t.source == state.id]
    # The engine's order: conditional edges first, then unconditional ones.
    ordered = sorted(outgoing, key=lambda t: 0 if t.when else 1)
    priority = {id(t): i for i, t in enumerate(ordered, 1)}

    def edge(t) -> Route:
        return Route(t.when, EDGE, t.dest, t, priority[id(t)])

    routes: list[Route] = []
    always = [t for t in ordered if not t.when]
    for name in returns:
        reason = state.stop_reason(name)
        if reason is not None:
            routes.append(Route(name, STOP, reason=reason))
            continue
        routed = [t for t in ordered if t.when == name]
        if routed:
            routes.extend(edge(t) for t in routed)
        elif not outgoing:
            routes.append(Route(name, END))
        elif always:
            continue  # the unconditional edges below take it
        elif (
            name in fallthrough
            and recovery_state is not None
            and recovery_state != state.id
            and (not known or recovery_state in known)
        ):
            routes.append(Route(name, RECOVERY, recovery_state))
        else:
            routes.append(Route(name, UNROUTED))
    declared = set(returns)
    routes.extend(edge(t) for t in ordered if t.when and t.when not in declared)
    routes.extend(edge(t) for t in always)
    return routes


def routes_summary(routes: Iterable[Route], labels: Mapping[str, str]) -> list[SummaryPart]:
    """A folded Connections card's line: "SUCCESS → Sync players · FAILURE → recovery"."""
    parts = []
    for route in routes:
        when = route.when or "always"
        if route.kind == EDGE:
            parts.append(SummaryPart(f"{when} → {labels.get(route.dest, route.dest)}"))
        elif route.kind == RECOVERY:
            parts.append(SummaryPart(f"{when} → recovery"))
        elif route.kind == STOP:
            parts.append(SummaryPart(f"{when} stops the bot"))
        elif route.kind == END:
            parts.append(SummaryPart(f"{when} ends the run"))
        else:
            parts.append(SummaryPart(f"{when} not routed", warning=True))
    return parts or [SummaryPart("No routes")]
