"""Bot variables: what a value may be, how a condition reads one and a write changes it, and the rules a bot's use of them is held to.

A variable is a value the bot learns during a run and reads back on its edges to choose
the next node. The model and the rules are in ``docs/VARIABLES.md``; in short:

- Declared per bot (``BotConfig.variables``) and typed: ``bool``, ``int`` or ``enum``.
- Read by edge conditions only, as a row of variable, comparison and value.
- Written by a ``set_variables`` node only, as rows of set, add and toggle, which the
  machine applies through :meth:`BotMachine.set_variable` with no ``await`` between them.
- Fresh when the run starts, and changed after that only by a write.

:func:`check` returns a bot's errors and warnings. An error refuses the run
(:func:`run_refusal`); a warning is for the author. No Qt here: the canvas badge
(Stage 2) draws what :func:`check` returns.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from bartlebot.bot.schema import (
    SET_VARIABLES_NODE,
    AssignmentModel,
    BotConfig,
    FlagCondition,
    TransitionModel,
    VariableModel,
)

#: The node that does nothing and returns SUCCESS, to hang a multi-way switch on.
BRANCH_NODE = "branch"

#: Nodes that never touch the game. A run of them in a row can only be the graph going
#: round in circles, so the machine stops after this many (``MAX_VARIABLE_ONLY_STEPS``).
VARIABLE_ONLY_NODE_TYPES = frozenset({SET_VARIABLES_NODE, BRANCH_NODE})

#: The most variable-only nodes the machine runs in a row before it stops the run. A
#: switch hung on a branch, then a set_variables, then another branch is three; nothing a
#: bot means to do needs dozens, and a wrong condition between two of them loops forever
#: without the no-match or same-node counts ever seeing it.
MAX_VARIABLE_ONLY_STEPS = 50

#: The comparisons that order values, which only an int has.
ORDERING_COMPARES = frozenset({"<", "<=", ">", ">="})

#: The param a set_variables node keeps its rows in.
ASSIGNMENTS_PARAM = "assignments"


class VariableError(ValueError):
    """A write the machine refuses: an undeclared variable, or a value its type does not take."""


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def value_fits(decl: VariableModel, value: Any) -> bool:
    """Whether ``value`` is one ``decl`` can hold: a bool, an int, or one of an enum's values."""
    if decl.type == "bool":
        return isinstance(value, bool)
    if decl.type == "int":
        return _is_int(value)
    return isinstance(value, str) and value in (decl.values or [])


def compare(current: Any, op: str, value: Any) -> bool:
    """Whether a variable holding ``current`` passes the row ``op value``.

    Equality is by type as well as value, so ``True`` never equals ``1``. A variable
    that does not exist (``current`` is None) passes no row; validation refuses to run a
    bot whose condition names one, so that only reaches a test.
    """
    if current is None:
        return False
    same = type(current) is type(value) and current == value
    if op == "==":
        return same
    if op == "!=":
        return not same
    if not (_is_int(current) and _is_int(value)):
        return False
    if op == "<":
        return current < value
    if op == "<=":
        return current <= value
    if op == ">":
        return current > value
    if op == ">=":
        return current >= value
    return False


def format_value(value: Any) -> str:
    """A value as the log and the canvas show it: ``true``, ``3``, ``Sun Chamber``."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def describe_condition(fc: FlagCondition) -> str:
    """A variable condition row as text, ``swords == 2``; an option row as its option, ``NOT`` first if negated."""
    if fc.is_variable:
        return f"{fc.variable} {fc.compare} {format_value(fc.value)}"
    return f"NOT {fc.option}" if fc.negate else str(fc.option)


def describe_assignment(row: AssignmentModel) -> str:
    """One write as text: ``next_spot = 4``, ``swords + 1``, ``found toggled``."""
    if row.op == "toggle":
        return f"{row.variable} toggled"
    if row.op == "add":
        return f"{row.variable} + {format_value(row.value)}"
    return f"{row.variable} = {format_value(row.value)}"


def parse_assignments(raw: Any) -> list[AssignmentModel]:
    """A set_variables node's ``assignments`` param as rows. Raises ``ValueError`` on one that is not a row."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("assignments must be a list of rows")
    return [AssignmentModel.model_validate(row) for row in raw]


def apply_assignments(
    values: Mapping[str, Any], decls: Mapping[str, VariableModel], rows: Iterable[AssignmentModel]
) -> dict[str, Any]:
    """The new value of every variable ``rows`` write, applied in order to ``values``.

    Changes nothing itself, and raises :class:`VariableError` before returning anything,
    so a caller that commits the result commits all of it or none. A later row sees an
    earlier one's write: ``set swords 0`` then ``add swords 1`` leaves 1.
    """
    staged = dict(values)
    changed: dict[str, Any] = {}
    for row in rows:
        decl = decls.get(row.variable)
        if decl is None:
            raise VariableError(f"there is no variable named {row.variable!r}")
        if row.op == "toggle":
            if decl.type != "bool":
                raise VariableError(f"only a bool can be toggled, and {row.variable!r} is {decl.type}")
            new = not staged[row.variable]
        elif row.op == "add":
            if decl.type != "int":
                raise VariableError(f"only an int can be added to, and {row.variable!r} is {decl.type}")
            if not _is_int(row.value):
                raise VariableError(f"{row.variable!r} can only have a whole number added to it")
            new = staged[row.variable] + row.value
        else:
            if not value_fits(decl, row.value):
                raise VariableError(f"{row.variable!r} cannot hold {format_value(row.value)}")
            new = row.value
        staged[row.variable] = new
        changed[row.variable] = new
    return changed


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Problem:
    """One thing wrong with a bot's variables. ``states`` are the nodes it is about, for a badge."""

    text: str
    variable: str | None = None
    states: tuple[str, ...] = ()


@dataclass
class Report:
    """What :func:`check` found. Any error refuses the run."""

    errors: list[Problem] = field(default_factory=list)
    warnings: list[Problem] = field(default_factory=list)


def condition_rows(t: TransitionModel) -> list[FlagCondition]:
    """Every condition row on an edge, in both the flat and the grouped form."""
    return [*t.flags, *(fc for g in t.flag_groups for fc in g.flags)]


def _guard(t: TransitionModel) -> list[FlagCondition]:
    """The rows that decide whether an edge fires: the grouped form wins over a stale flat one, as at run time."""
    return [fc for g in t.flag_groups for fc in g.flags] if t.flag_groups else list(t.flags)


def _variable_rows(t: TransitionModel) -> list[FlagCondition]:
    return [fc for fc in condition_rows(t) if fc.is_variable]


def _domain(decl: VariableModel, rows: Sequence[FlagCondition]) -> list[Any]:
    """Enough of ``decl``'s values to find any value two of ``rows`` both pass.

    A bool's and an enum's are all of them. An int's are each row's value and its
    neighbours: every comparison here is true on a run of whole numbers that starts or
    ends beside its value, so two rows that share any value share one of those.
    """
    if decl.type == "bool":
        return [False, True]
    if decl.type == "enum":
        return list(decl.values or [])
    return sorted({v + d for fc in rows if _is_int(fc.value) for v in [fc.value] for d in (-1, 0, 1)})


def _single_variable_row(t: TransitionModel) -> FlagCondition | None:
    """The one row an edge's guard is, when that row reads a variable."""
    rows = _guard(t)
    return rows[0] if len(rows) == 1 and rows[0].is_variable else None


def _loops(nodes: Iterable[str], edges: Mapping[str, set[str]]) -> list[set[str]]:
    """The graph's loops: each strongly connected set of nodes that some cycle runs through (Tarjan).

    A loop with another inside it is one set, which is what lets a counter count inside a
    pass while the pass as a whole resets it.
    """
    nodes = set(nodes)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[set[str]] = []
    counter = 0

    for root in sorted(nodes):
        if root in index:
            continue
        # Iterative, so a long chain of nodes cannot exhaust the recursion limit.
        work = [(root, iter(sorted(edges.get(root, ()) & nodes)))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(edges.get(child, ()) & nodes))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1 or node in edges.get(node, ()):
                    found.append(set(component))
    return found


def _reachable_without(start: str, edges: Mapping[str, set[str]], blocked: set[str]) -> set[str]:
    """Every node a walk from ``start`` reaches without passing through one of ``blocked``."""
    if start in blocked:
        return set()
    seen = {start}
    todo = [start]
    while todo:
        for nxt in edges.get(todo.pop(), ()):
            if nxt not in seen and nxt not in blocked:
                seen.add(nxt)
                todo.append(nxt)
    return seen


def check(config: BotConfig, specs=None) -> Report:
    """Every error and warning in how ``config`` declares, reads and writes its variables.

    ``specs`` is the NodeSpec registry, for which options the nodes declare; the real one
    when None. A bot with no variables and no use of one has nothing to report.
    """
    from bartlebot.bot_options import compose_options  # noqa: PLC0415 - bot_options imports the registry late

    report = Report()
    error, warn = report.errors.append, report.warnings.append
    labels = {s.id: s.label or s.id for s in config.states}

    def where(state_id: str) -> str:
        return f"'{labels.get(state_id, state_id)}'"

    # --- Declarations ---
    decls: dict[str, VariableModel] = {}
    options = {o.name for o in compose_options(config, specs)}
    for decl in config.variables:
        name = decl.name
        if not name.strip():
            error(Problem("a variable has no name"))
            continue
        if name in decls:
            error(Problem(f"two variables are named {name!r}", name))
            continue
        decls[name] = decl
        if name in options:
            error(Problem(f"variable {name!r} has the name of an option; rename one of them", name))
        if decl.type == "enum":
            values = decl.values or []
            if not values:
                error(Problem(f"enum variable {name!r} lists no values", name))
            if len(set(values)) != len(values):
                error(Problem(f"enum variable {name!r} lists a value twice", name))
            if any(not v.strip() for v in values):
                error(Problem(f"enum variable {name!r} has a blank value", name))
        elif decl.values:
            error(Problem(f"variable {name!r} is {decl.type}, and only an enum lists values", name))
        start = decl.initial_value()
        if start is None or not value_fits(decl, start):
            error(Problem(f"variable {name!r} cannot start at {format_value(decl.initial)}", name))

    # --- Reads: edge conditions ---
    read: dict[str, set[str]] = {}
    for t in config.transitions:
        for fc in _variable_rows(t):
            name = fc.variable or ""
            read.setdefault(name, set()).add(t.source)
            decl = decls.get(name)
            edge = f"the edge from {where(t.source)}"
            if decl is None:
                error(Problem(f"{edge} reads {name!r}, which is not a declared variable", name, (t.source,)))
                continue
            if fc.compare in ORDERING_COMPARES and decl.type != "int":
                error(
                    Problem(f"{edge} compares {name!r} with {fc.compare}, which only an int can use", name, (t.source,))
                )
            if not value_fits(decl, fc.value):
                error(
                    Problem(
                        f"{edge} compares {name!r} with {format_value(fc.value)}, which a {decl.type} cannot hold",
                        name,
                        (t.source,),
                    )
                )

    # --- Writes: set_variables nodes ---
    written: dict[str, set[str]] = {}
    resets: dict[str, set[str]] = {}
    changes: dict[str, set[str]] = {}
    for s in config.states:
        if s.node_type != SET_VARIABLES_NODE:
            continue
        try:
            rows = parse_assignments(s.params.get(ASSIGNMENTS_PARAM))
        except ValueError:
            error(
                Problem(
                    f"{where(s.id)} has an assignment that is not a variable, an operation and a value", None, (s.id,)
                )
            )
            continue
        if not rows:
            warn(Problem(f"{where(s.id)} sets no variables", None, (s.id,)))
        for row in rows:
            name = row.variable
            written.setdefault(name, set()).add(s.id)
            (resets if row.op == "set" else changes).setdefault(name, set()).add(s.id)
            decl = decls.get(name)
            if decl is None:
                error(Problem(f"{where(s.id)} writes {name!r}, which is not a declared variable", name, (s.id,)))
            elif row.op == "add" and decl.type != "int":
                error(Problem(f"{where(s.id)} adds to {name!r}, and only an int can be added to", name, (s.id,)))
            elif row.op == "add" and not _is_int(row.value):
                error(
                    Problem(
                        f"{where(s.id)} adds {format_value(row.value)} to {name!r}: add a whole number", name, (s.id,)
                    )
                )
            elif row.op == "toggle" and decl.type != "bool":
                error(Problem(f"{where(s.id)} toggles {name!r}, and only a bool can be toggled", name, (s.id,)))
            elif row.op == "set" and not value_fits(decl, row.value):
                error(
                    Problem(
                        f"{where(s.id)} sets {name!r} to {format_value(row.value)}, which a {decl.type} cannot hold",
                        name,
                        (s.id,),
                    )
                )

    # --- Warnings: switches ---
    ports: dict[tuple[str, str | None], list[TransitionModel]] = {}
    for t in config.transitions:
        ports.setdefault((t.source, t.when), []).append(t)
    for (source, when), edges in ports.items():
        if not any(_variable_rows(t) for t in edges):
            continue
        # An edge with no ``when`` fires on any result, so it is every port's otherwise too.
        candidates = edges if when is None else [*edges, *ports.get((source, None), [])]
        port = f"{where(source)} on {when}" if when else f"{where(source)}"
        if all(_guard(t) for t in candidates):
            warn(
                Problem(
                    f"every edge from {port} has a condition: add one without, for when none of them is true",
                    None,
                    (source,),
                )
            )
        singles = [row for t in edges if (row := _single_variable_row(t)) is not None]
        for ra, rb in combinations(singles, 2):
            decl = decls.get(ra.variable or "")
            if decl is None or ra.variable != rb.variable:
                continue
            if any(
                compare(v, ra.compare or "==", ra.value) and compare(v, rb.compare or "==", rb.value)
                for v in _domain(decl, [ra, rb])
            ):
                warn(
                    Problem(
                        f"two edges from {port} can both be true ({describe_condition(ra)} and"
                        f" {describe_condition(rb)}), and which one is taken is not shown on the canvas",
                        ra.variable,
                        (source,),
                    )
                )

    # --- Warnings: reads and writes that do not meet ---
    for name in decls:
        if name in written and name not in read:
            warn(Problem(f"variable {name!r} is set and never read", name, tuple(sorted(written[name]))))
        if name in read and name not in written:
            warn(
                Problem(
                    f"variable {name!r} is read and never set, so it never changes: make it an option instead",
                    name,
                    tuple(sorted(read[name])),
                )
            )

    # --- Warnings: a loop that carries a value over ---
    edges_out: dict[str, set[str]] = {}
    for t in config.transitions:
        edges_out.setdefault(t.source, set()).add(t.dest)
    # Any node can end up at recovery: an unrouted failure falls through to it, and so does
    # an ambient fight that leaves the players out of place.
    if config.recovery_state:
        for s in config.states:
            edges_out.setdefault(s.id, set()).add(config.recovery_state)
    loops = _loops((s.id for s in config.states), edges_out)
    for name, changed_by in changes.items():
        if name not in decls:
            continue
        # A node that also sets the variable leaves it at a fixed value, whatever it adds after.
        setters = resets.get(name, set())
        changers = changed_by - setters
        # The loop as the strongly connected set, so a counter counting inside one pass of a
        # loop that resets it is fine: the shape of Heretical Academy's sword puzzle.
        stale = {c for c in changers for loop in loops if c in loop and not loop & setters}
        if stale:
            warn(
                Problem(
                    f"variable {name!r} is changed in a loop that never sets it back, so it carries over from one"
                    " trip round the loop to the next: set it to a fixed value where the loop begins",
                    name,
                    tuple(sorted(stale)),
                )
            )
            continue
        # A recovery jumps into the loop from outside it, so it has to come in through a reset too.
        if config.recovery_state:
            bypassed = changers & _reachable_without(config.recovery_state, edges_out, setters)
            if bypassed:
                warn(
                    Problem(
                        f"variable {name!r} is changed after a recovery without being set back first, so a value"
                        " from before the recovery carries into the run after it: set it on the way back in",
                        name,
                        tuple(sorted(bypassed)),
                    )
                )
    return report


def run_refusal(config: BotConfig, specs=None) -> str | None:
    """Why ``config`` must not run because of its variables, or None. The errors of :func:`check`."""
    errors = check(config, specs).errors
    if not errors:
        return None
    lines = "\n".join(f"• {p.text}" for p in errors)
    return f"This bot's variables have errors to fix before it can run:\n\n{lines}"
