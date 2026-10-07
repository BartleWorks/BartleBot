"""What a save changes in a subgraph definition's signature, for the warning before it is written (#169).

A definition's signature is what a bot linking it depends on: its inputs, which an
instance stores values under by name, and its ports, the Entries and Exits the bot's
edges attach to. Changing either can break the bots that link it, and nothing used to
say so. Home Assistant's blueprint guidance -- never rename an input, give every new
input a default -- exists for the same reason.

So saving a definition warns, naming the bots that link it, when the save:

- **removes an input**, or stops exposing a node whose params were inputs. A value a
  bot set for it stops applying;
- **renames an input**: its ``name``, the key a bot's value is stored under. Told apart
  from a removal by an added input driving the same params. A new *label* is not a
  rename, since nothing is stored under it;
- **adds an input with no default**, which an instance placed before it has no value
  for;
- **removes a port**, which leaves the bot's edges to it with nowhere to go;
- **renames a port**: the label of an Entry or Exit, which is its name on the call
  node, so an edge the bot drew to "Done" now leaves "Failed".

Nothing else warns. It is a warning, not a refusal: a bot that cannot absorb the change
is ``held`` on the snapshot it has (``docs/SUBGRAPHS.md``), so nothing stops running.

No Qt here; the controller shows the warning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from bartlebot.bot.schema import PORT_NODE_TYPES, SUBGRAPH_ENTRY, SubgraphDefinition, SubgraphInput
from bartlebot.subgraphs import port_name

SignatureChangeKind = Literal["input_removed", "input_renamed", "input_without_default", "port_removed", "port_renamed"]


@dataclass(frozen=True)
class SignatureChange:
    """One change to a definition's signature that can break a bot linking it."""

    kind: SignatureChangeKind
    #: The sentence the warning lists, starting with its verb: "removes the input 'Zone'".
    text: str


def _input_name(inp: SubgraphInput) -> str:
    return inp.label or inp.name


def _bindings(inp: SubgraphInput) -> list[dict]:
    return [b.model_dump() for b in inp.bindings]


def _input_changes(old: SubgraphDefinition, new: SubgraphDefinition) -> list[SignatureChange]:
    new_names = {i.name for i in new.inputs}
    old_names = {i.name for i in old.inputs}
    removed = [i for i in old.inputs if i.name not in new_names]
    added = [i for i in new.inputs if i.name not in old_names]
    changes: list[SignatureChange] = []
    for gone in removed:
        # The same params driven under a new name: the author renamed it.
        twin = next((a for a in added if _bindings(a) == _bindings(gone)), None)
        if twin is not None:
            added.remove(twin)
            changes.append(
                SignatureChange("input_renamed", f"renames the input '{_input_name(gone)}' to '{_input_name(twin)}'")
            )
        else:
            changes.append(SignatureChange("input_removed", f"removes the input '{_input_name(gone)}'"))
    for new_input in added:
        if new_input.default is None:
            changes.append(
                SignatureChange("input_without_default", f"adds the input '{_input_name(new_input)}' with no default")
            )
    # An exposed node's params are inputs too, valued under "<member>.<key>".
    labels = {s.id: s.label or s.id for s in old.states}
    still = {p.member for p in new.panels if p.kind == "node"}
    for panel in old.panels:
        if panel.kind == "node" and panel.member not in still:
            name = labels.get(panel.member or "", panel.member)
            changes.append(SignatureChange("input_removed", f"stops exposing the node '{name}' and its inputs"))
    return changes


def _port_changes(old: SubgraphDefinition, new: SubgraphDefinition) -> list[SignatureChange]:
    new_ports = {s.id: s for s in new.states if s.node_type in PORT_NODE_TYPES}
    changes: list[SignatureChange] = []
    for port in (s for s in old.states if s.node_type in PORT_NODE_TYPES):
        what = "Entry" if port.node_type == SUBGRAPH_ENTRY else "Exit"
        now = new_ports.get(port.id)
        if now is None or now.node_type != port.node_type:
            changes.append(SignatureChange("port_removed", f"removes the {what} '{port_name(port)}'"))
        elif port_name(now) != port_name(port):
            changes.append(
                SignatureChange("port_renamed", f"renames the {what} '{port_name(port)}' to '{port_name(now)}'")
            )
    return changes


def signature_changes(old: SubgraphDefinition, new: SubgraphDefinition) -> list[SignatureChange]:
    """Every change saving ``new`` over ``old`` makes that can break a bot linking it, inputs first."""
    return [*_input_changes(old, new), *_port_changes(old, new)]


def warning_text(name: str, changes: list[SignatureChange], users: list[str]) -> str:
    """The warning shown before saving ``name`` with ``changes``, naming what links it.

    ``users`` names what links the definition, each already phrased (a bot's name, or
    "the subgraph 'X'"); the first eight are listed and the rest counted.
    """
    listed = "\n".join(f"• Saving {c.text}." for c in changes)
    shown = users[:8]
    more = len(users) - len(shown)
    named = ", ".join(shown) + (f" and {more} more" if more else "")
    return (
        f"This changes how '{name}' is used:\n\n{listed}\n\n"
        f"It is used by {named}. Each takes the change the next time it is opened or run. "
        "A value set for an input that is gone no longer applies, and a bot whose edges no "
        "longer fit keeps running the copy it has, flagged as held, until it is edited.\n\n"
        "Save anyway?"
    )
