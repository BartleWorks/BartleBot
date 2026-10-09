"""The signature page's inputs tree, without Qt (#155).

A definition's signature page lists its inputs as one tree: the inputs no input group
holds, then each group with its members indented under it, in the order a subgraph node
shows them. A promoted node is a group too, drawn as its heading alone -- its params
follow the node type and can be neither reordered nor regrouped, so they are not lines
anything could be dragged onto.

The tree is *derived*, never stored. What the definition stores is ``inputs`` (one list,
each naming the panel it is listed under) and ``panels`` (one list), and the tree is the
one way of drawing the two together. So a reorder is worked out on the tree's rows and
then written back to those two lists by :func:`reorder`, and nothing keeps a second copy
of either the order or the membership.

**An input belongs to the nearest group heading above it.** That one rule is what lets
dragging -- or Alt+Up/Down -- move an input into or out of a group with no other control,
and it is why :func:`allowed` needs no case for "into a group": the question is only
which arrangements are legal.

- An input may not land under a promoted node, whose group holds only that node's params.
- A move must not change any *other* input's group. A heading dropped between two of
  another group's inputs would take the lower ones with it, and one dropped above the
  ungrouped inputs would take all of them.

A group heading carries its members with it (:func:`carried`), so a group moves whole.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

#: A row's kinds: an input, an input group's heading, a promoted node's heading.
INPUT = "input"
GROUP = "group"
NODE = "node"


@dataclass(frozen=True)
class TreeRow:
    """One line of the tree: an input by name, or a heading by panel id.

    *member* is True for an input listed under an input group, which is drawn indented.
    """

    kind: str
    key: str
    member: bool = False

    @property
    def is_heading(self) -> bool:
        return self.kind != INPUT


def tree_rows(inputs: Sequence[Any], panels: Sequence[Any]) -> list[TreeRow]:
    """The tree's rows, in the order a subgraph node's page shows them.

    An input naming a panel that is not an input group is drawn with the ungrouped ones,
    as the subgraph node page draws it.
    """
    groups = {p.id for p in panels if p.kind == "inputs"}
    rows = [TreeRow(INPUT, i.name) for i in inputs if i.panel not in groups]
    for panel in panels:
        if panel.kind == "node":
            rows.append(TreeRow(NODE, panel.id))
            continue
        rows.append(TreeRow(GROUP, panel.id))
        rows.extend(TreeRow(INPUT, i.name, member=True) for i in inputs if i.panel == panel.id)
    return rows


def groups_of(rows: Sequence[TreeRow]) -> dict[str, TreeRow | None]:
    """Each input's heading -- the nearest one above it -- or None for an ungrouped input."""
    heading: TreeRow | None = None
    found: dict[str, TreeRow | None] = {}
    for row in rows:
        if row.is_heading:
            heading = row
        else:
            found[row.key] = heading
    return found


def carried(rows: Sequence[TreeRow], index: int) -> int:
    """How many rows after *index* travel with it: an input group's members, else none."""
    if rows[index].kind != GROUP:
        return 0
    count = 0
    for row in rows[index + 1 :]:
        if row.is_heading:
            break
        count += 1
    return count


def allowed(before: Sequence[TreeRow], after: Sequence[TreeRow], moved: Sequence[TreeRow]) -> bool:
    """Whether *after* is a legal arrangement of *before*, *moved* being what was picked up.

    Every input not picked up keeps its group, and no input lands under a promoted node.
    """
    old, new = groups_of(before), groups_of(after)
    if set(old) != set(new):
        return False
    lifted = {row.key for row in moved if row.kind == INPUT}
    for name, heading in new.items():
        if heading is not None and heading.kind == NODE:
            return False
        if name not in lifted and _key(heading) != _key(old[name]):
            return False
    return True


def _key(heading: TreeRow | None) -> str | None:
    return None if heading is None else heading.key


def relaid(rows: Sequence[TreeRow]) -> list[TreeRow]:
    """*rows* with each input's indent recomputed from where it now stands."""
    out = []
    heading: TreeRow | None = None
    for row in rows:
        if row.is_heading:
            heading = row
            out.append(row)
        else:
            out.append(TreeRow(INPUT, row.key, member=heading is not None and heading.kind == GROUP))
    return out


def step(rows: Sequence[TreeRow], index: int, direction: int) -> list[TreeRow] | None:
    """The arrangement Alt+Up (*direction* -1) or Alt+Down (+1) gives the row at *index*.

    The row moves one slot, carrying a group's members, and on past any slot that is not
    allowed: an input at the top of a group steps out to the end of the section above,
    skipping a promoted node it cannot sit under, and a group steps over a whole group
    rather than splitting it. None when there is nowhere to go -- the same rule a drag
    follows, so the keyboard and the mouse can never disagree about what is legal.
    """
    span = 1 + carried(rows, index)
    block = list(rows[index : index + span])
    rest = list(rows[:index]) + list(rows[index + span :])
    target = index + direction
    while 0 <= target <= len(rest):
        candidate = rest[:target] + block + rest[target:]
        if allowed(rows, candidate, block):
            return relaid(candidate)
        target += direction
    return None


def move_to_group(rows: Sequence[TreeRow], name: str, panel_id: str | None) -> list[TreeRow]:
    """*rows* with the input *name* moved to the end of *panel_id*'s group, or of the ungrouped ones."""
    row = next(r for r in rows if r.kind == INPUT and r.key == name)
    rest = [r for r in rows if r is not row]
    if panel_id is None:
        at = next((i for i, r in enumerate(rest) if r.is_heading), len(rest))
    else:
        start = next(i for i, r in enumerate(rest) if r.kind == GROUP and r.key == panel_id)
        at = next((i for i in range(start + 1, len(rest)) if rest[i].is_heading), len(rest))
    return relaid(rest[:at] + [row] + rest[at:])


def reorder(meta: Any, rows: Sequence[TreeRow]) -> None:
    """Write *rows* back to ``meta.inputs`` and ``meta.panels``: order, and each input's group.

    The inputs are put in the order the tree lists them. That is one valid order among
    several -- only the order *within* each group is visible anywhere -- and it is the one
    that keeps the stored list readable next to the page.
    """
    by_name = {i.name: i for i in meta.inputs}
    by_id = {p.id: p for p in meta.panels}
    groups = groups_of(rows)
    inputs = []
    for row in rows:
        if row.kind == INPUT:
            inp = by_name[row.key]
            heading = groups[row.key]
            inp.panel = heading.key if heading is not None else None
            inputs.append(inp)
    meta.inputs = inputs + [i for i in meta.inputs if i.name not in groups]
    headings = [r.key for r in rows if r.is_heading]
    meta.panels = [by_id[k] for k in headings] + [p for p in meta.panels if p.id not in headings]
