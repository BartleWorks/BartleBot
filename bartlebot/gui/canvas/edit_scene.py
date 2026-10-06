"""Interactive QGraphicsScene for the bot canvas editor.

EditScene        — manages interactive nodes, edge dragging, deletion, and positions.
_EditView        — GraphView subclass with interactive=True.
_ColorPickerDialog — six-swatch region palette picker used by EditScene.

Module-level helpers (_node_returns, _validate_node, _dist, _unique_id, etc.) are
also exported here so BotCanvas can use them without circular imports.
"""

import logging
import math
import re
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import chain

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QBrush, QCursor, QKeyEvent, QMouseEvent, QPainter, QPen
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsScene,
    QInputDialog,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QWidget,
)

from bartlebot import player_realms, scales, setup_steps
from bartlebot.bot.schema import (
    FALLTHROUGH_RETURNS,
    PORT_NODE_TYPES,
    SUBGRAPH_ENTRY,
    SUBGRAPH_EXIT,
    BotConfig,
    RegionModel,
    SetupStep,
    StateModel,
    SubgraphSource,
    TransitionModel,
)
from bartlebot.gui.canvas.call_node_item import CallNodeItem, CollapsedRegionItem, FoldedNodeItem, PortNodeItem
from bartlebot.gui.canvas.edge_item import (
    EdgeItem,
    bridge_y_above,
    bridge_y_below,
    is_above_routed,
)
from bartlebot.gui.canvas.fold import FoldMap
from bartlebot.gui.canvas.graph_view import GraphView
from bartlebot.gui.canvas.node_item import (
    GRID_H,
    GRID_W,
    NODE_H,
    NODE_W,
    PORT_MARK_RECOVERY,
    PORT_MARK_STOP,
    NodeItem,
    set_scene_rect_and_repaint,
)
from bartlebot.gui.canvas.region_item import RegionItem
from bartlebot.gui.canvas.region_manager import RegionManager
from bartlebot.gui.menu_utils import popup_menu
from bartlebot.gui.theme import THEME
from bartlebot.nodes.registry import NODE_PARAM_SCHEMAS, NODE_REGISTRY, NODE_SPECS, node_display_name
from bartlebot.subgraph_store import SUBGRAPH_KEY_PREFIX, SubgraphLibrary, source_label
from bartlebot.subgraphs import (
    ComposeReport,
    SubgraphCycleError,
    boundary_ports,
    collapse_blocker,
    folded_regions,
    input_targets,
    is_collapsed,
    is_nested_in,
    linked_region_of,
    linked_regions,
    local_id,
    materialize_region,
    member_id,
    mint_instance_id,
    nested_regions,
    port_name,
    region_anchor,
    taken_ids,
)
from bartlebot.subgraphs import detach as detach_subgraph
from bartlebot.template_store import TEMPLATE_ID_PREFIX

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants used only by the editor
# ---------------------------------------------------------------------------

_PORT_HIT_RADIUS = 16
_AUTO_SPACING = GRID_W  # auto-layout column stride matches grid width

_SCROLL_MARGIN = 40  # px from viewport edge at which auto-scroll activates during drag
_SCROLL_SPEED = 10  # max px to scroll per mouseMoveEvent while in the margin zone


def _spiral_ring(r: int):
    """Yield (dx, dy) offsets at Chebyshev distance r from (0, 0).

    Order: right column top-to-bottom, bottom row right-to-left,
    left column bottom-to-top, top row left-to-right.  This biases
    new nodes toward the right/below the origin, matching natural
    canvas reading order.
    """
    if r == 0:
        yield (0, 0)
        return
    for dy in range(-r, r + 1):
        yield (r, dy)
    for dx in range(r - 1, -r - 1, -1):
        yield (dx, r)
    for dy in range(r - 1, -r - 1, -1):
        yield (-r, dy)
    for dx in range(-r + 1, r):
        yield (dx, -r)


def _auto_scroll_view(view, scene_pos: QPointF) -> None:
    """Scroll the viewport when the drag cursor enters the margin zone near an edge."""
    vp_pos = view.mapFromScene(scene_pos)
    rect = view.viewport().rect()
    dx = dy = 0
    if vp_pos.x() < _SCROLL_MARGIN:
        dx = -((_SCROLL_MARGIN - vp_pos.x()) * _SCROLL_SPEED // _SCROLL_MARGIN + 1)
    elif vp_pos.x() > rect.width() - _SCROLL_MARGIN:
        dx = (vp_pos.x() - (rect.width() - _SCROLL_MARGIN)) * _SCROLL_SPEED // _SCROLL_MARGIN + 1
    if vp_pos.y() < _SCROLL_MARGIN:
        dy = -((_SCROLL_MARGIN - vp_pos.y()) * _SCROLL_SPEED // _SCROLL_MARGIN + 1)
    elif vp_pos.y() > rect.height() - _SCROLL_MARGIN:
        dy = (vp_pos.y() - (rect.height() - _SCROLL_MARGIN)) * _SCROLL_SPEED // _SCROLL_MARGIN + 1
    if dx:
        view.horizontalScrollBar().setValue(view.horizontalScrollBar().value() + dx)
    if dy:
        view.verticalScrollBar().setValue(view.verticalScrollBar().value() + dy)


# ---------------------------------------------------------------------------
# Module-level helpers (also imported by BotCanvas)
# ---------------------------------------------------------------------------


def _node_returns(node_type: str) -> list[str]:
    spec = NODE_SPECS.get(node_type)
    return [r.name for r in spec.returns] if spec else []


def driven_params(config: BotConfig, library: SubgraphLibrary | None = None) -> set[tuple[str, str]]:
    """``(state id, param key)`` a definition document's signature sets from outside.

    A param bound to an input, or belonging to an exposed node, is the consumer's to
    fill in, so an empty one is not the definition's error. Empty for a bot. With
    *library*, so is a nested subgraph node's member that an input reaches through
    one of that node's own inputs.
    """
    meta = config.definition
    if meta is None:
        return set()
    driven = {pair for inp in meta.inputs for pair in input_targets(config, inp, library)}
    for panel in meta.panels:
        if panel.kind != "node" or panel.member is None:
            continue
        member = next((s for s in config.states if s.id == panel.member), None)
        if member is not None:
            driven.update((member.id, f.key) for f in NODE_PARAM_SCHEMAS.get(member.node_type, []))
        driven.update((b.state, b.param) for extra in panel.bindings.values() for b in extra)
    return driven


# The return an inserted node should carry onward. An edge with no ``when`` fires
# whatever the node did, so a failure flows straight down the success path -- almost
# never what an author means. Every shipped node type returns SUCCESS/FAILURE or
# TRUE/FALSE, so there is always a sensible answer.
_HAPPY_PATH_RETURNS = ("SUCCESS", "TRUE")


def _happy_path_return(returns: list[str]) -> str | None:
    """The return value an inserted node should be wired onward from.

    Falls back to the first declared return for a node type that names neither of the
    conventional pair, and to None only for one with no returns at all.
    """
    for name in _HAPPY_PATH_RETURNS:
        if name in returns:
            return name
    return returns[0] if returns else None


# How far the insertion search will look before giving up and using the raw seed.
_PLACEMENT_SEARCH_RADIUS = 31


def _placement_candidates(x_dir: int):
    """Grid offsets to try for an inserted node, its own column exhausted first.

    Every insert tries the spot its node belongs in first and moves columns over when
    it is taken (``EditScene._open_column_at``), so this search is what is left for the
    inserts with no such spot: an edge running backward or down, and a node a template
    update adds beside something it does not lead on to.

    An inserted node belongs to the layer it was inserted into, so the column is held
    and only the row moves; widening in x is the fallback for a column with no free
    row at all, and it widens *away* from the node the insert hangs off so the new
    node never lands on the far side of it.

    Ordering by plain proximity instead -- the spiral behind "Add State Here" -- drifts
    sideways: across the shipped bots it put 82% of input-port inserts level with or to
    the right of the node they feed into, which reads backwards. Holding the column
    costs almost nothing in practice, because the free row is nearly always adjacent:
    over all 1264 insert sites in the library, 281 need no move, 982 move one row, and
    a single one moves two.
    """
    for dx_mag in range(_PLACEMENT_SEARCH_RADIUS):
        for dy_mag in range(_PLACEMENT_SEARCH_RADIUS):
            if dy_mag == 0:
                yield (x_dir * dx_mag, 0)
            else:
                yield (x_dir * dx_mag, -dy_mag)
                yield (x_dir * dx_mag, dy_mag)


def node_types_producing(return_name: str) -> set[str]:
    """Node types that can return *return_name*.

    The set an insert-at-output-port picker may offer. Membership, not an identical
    return list: a rerouted edge only needs its ``when`` to still be producible, and
    requiring the whole signature to match would rule out ``sleep`` -- which returns
    SUCCESS and nothing else -- on exactly the SUCCESS ports it is most wanted on.
    """
    return {nt for nt in NODE_SPECS if return_name in _node_returns(nt)}


def authoring_anchor(config: BotConfig) -> tuple[str, str] | None:
    """Where speed-authoring (#85) builds on with nothing selected: ``(state id, return)``.

    The first happy-path port with nothing leaving it, following happy paths from the
    initial state. That is the gap a template leaves between entering a dungeon and an
    Exit node already sitting unconnected at the far right, which is why the walk goes
    by edges rather than by where nodes are drawn: the Exit is reachable by none.

    An edge with no ``when`` fires on every return, so it routes the happy port too.
    Of several edges on the port (flag-guarded branches), the walk follows the first.

    None when there is no gap: the path loops back on itself, or ends on a port the
    author marked as a stop -- a node inserted there could never be reached.
    """
    by_id = {s.id: s for s in config.states}
    seen: set[str] = set()
    sid = config.initial
    while sid and sid not in seen:
        seen.add(sid)
        state = by_id.get(sid)
        if state is None:
            return None
        happy = _happy_path_return(_node_returns(state.node_type))
        if happy is None or state.stop_reason(happy) is not None:
            return None
        onward = next((t.dest for t in config.transitions if t.source == sid and t.when in (happy, None)), None)
        if onward is None:
            return sid, happy
        sid = onward
    return None


#: What "blank" means for a param, and whether a ``nav_zone`` is a destination: one rule
#: each, in ``setup_steps``, shared with the library row and Release.
_required_field_empty = setup_steps.field_empty
_nav_zone_reachable = setup_steps.nav_zone_reachable


def port_marks_for(state: StateModel, config: BotConfig) -> dict[str, str]:
    """Which of *state*'s output ports act without an edge, and as what.

    Return name -> ``PORT_MARK_STOP`` for one the author marked fatal, or
    ``PORT_MARK_RECOVERY`` for an unrouted failure the engine will send to the
    recovery node. Mirrors BotMachine: a state with no outgoing transitions at all
    is terminal and stops there, so none of its ports is marked.
    """
    outgoing = [t for t in config.transitions if t.source == state.id]
    marks: dict[str, str] = {}
    for name in _node_returns(state.node_type):
        if state.stop_reason(name) is not None:
            marks[name] = PORT_MARK_STOP
        elif outgoing and not any(t.when == name or t.when is None for t in outgoing):
            if name in FALLTHROUGH_RETURNS and state.id != config.recovery_state:
                marks[name] = PORT_MARK_RECOVERY
    return marks


def _return_is_handled_without_an_edge(state, config, return_name: str) -> bool:
    """Whether *return_name* has somewhere to go despite having no outgoing edge.

    Two ways: the author marked it an explicit stop, or it is one of
    FALLTHROUGH_RETURNS and the engine will send it to the recovery state. The
    recovery state's own failure is excluded, matching BotMachine._fallthrough_dest
    -- defaulting that one would loop it onto itself, so it is still worth a warning.
    """
    if state.stop_reason(return_name) is not None:
        return True
    return return_name in FALLTHROUGH_RETURNS and state.id != config.recovery_state


def _lacks_default_scales(config, fill_ins: Mapping[tuple[str, str], setup_steps.FillIn] | None) -> bool:
    """Whether an empty ``scales`` param has no world default to fall back on, as far as can be told yet.

    The world is read from the bot's zones (``scales.bot_world``), so while it is not known
    the answer waits for them where they are still to come: a draft, whose zones its bots
    fill in, and a bot with setup fill-ins left blank, which those steps already keep from
    running. A world that is known and has no default is a fault everywhere.
    """
    if not scales.load() or scales.default_for(config) is not None:
        return False
    if scales.bot_world(config) is not None:
        return True
    return not (config.is_template or setup_steps.any_blank(config, fill_ins or {}))


def _validate_node(
    state,
    config,
    reachable: set[str] | None = None,
    fill_ins: Mapping[tuple[str, str], setup_steps.FillIn] | None = None,
    library: SubgraphLibrary | None = None,
) -> tuple[bool, bool, list[str], list[str]]:
    """Return (is_error, is_warning, error_reasons, warning_reasons) for a state.

    *library* lets a definition document see through its nested subgraph nodes: see
    :func:`driven_params`.

    Error conditions:
      - node_type not in NODE_REGISTRY (unknown type)
      - any required param key absent from state.params
      - in a bot, any setup fill-in (*fill_ins*) left blank, required on its own or not
      - any outgoing transition points to a non-existent destination state

    Warning conditions:
      - node has outgoing transitions but some return values are not covered
        by any t.when (and no unconditional when=None transition exists)
    """
    error_reasons: list[str] = []
    warning_reasons: list[str] = []

    if state.node_type in PORT_NODE_TYPES:
        # Its own rules -- one edge in for an Exit is optional, one out for an Entry
        # is not -- are the definition's, listed with its structure; this is the
        # node-level half, so the port that breaks one is the one marked.
        if state.node_type == SUBGRAPH_ENTRY and sum(1 for t in config.transitions if t.source == state.id) != 1:
            error_reasons.append("An Entry needs exactly one edge into the subgraph")
        if state.node_type == SUBGRAPH_EXIT and not any(t.dest == state.id for t in config.transitions):
            # An error, as an Entry with no edge is: a call node would offer a way out
            # the subgraph can never take.
            error_reasons.append("Nothing leads into this Exit")
        return bool(error_reasons), bool(warning_reasons), error_reasons, warning_reasons

    if state.node_type not in NODE_REGISTRY:
        error_reasons.append(f"Unknown node type: '{state.node_type}'")

    schema = NODE_PARAM_SCHEMAS.get(state.node_type, [])
    driven = driven_params(config, library) if config.definition is not None else set()
    for field in schema:
        if (state.id, field.key) in driven:
            continue
        fill = fill_ins.get((state.id, field.key)) if fill_ins else None
        empty = _required_field_empty(field, state.params)
        if fill is not None:
            # A template leaves its fill-ins blank -- a released version must -- so a
            # blank one is the expected state there, and a filled one is the fault. In
            # the bot made from it, blank is the step not done: an error whether or not
            # the param is required on its own, so no placeholder ever runs as a value.
            if config.is_template and not empty:
                warning_reasons.append(
                    f"'{field.key}' is setup step {fill.number}'s to fill in, so a release needs it blank"
                )
            elif not config.is_template and empty and setup_steps.setup_error(fill) not in error_reasons:
                # Once per step, however many of its fill-ins on this node are blank:
                # Record fills an entrance's four in one go, and the step is one thing to do.
                error_reasons.append(setup_steps.setup_error(fill))
        if fill is not None and empty:
            continue
        if field.required and empty:
            # In a draft too: nothing would ever ask the user of a bot made from it to fill this.
            error_reasons.append(f"Required param '{field.key}' is empty")
        elif field.type == "nav_zone" and not _nav_zone_reachable(state.params.get(field.key)):
            error_reasons.append(f"'{field.key}' is not a destination on the zone map")
        elif field.type == "scales" and not scales.is_known(state.params.get(field.key)):
            error_reasons.append(f"'{field.key}' is not a Scales of Knowledge this build knows")
        elif field.type == "scales" and empty and _lacks_default_scales(config, fill_ins):
            # Empty means the bot's world's Scales, so it is only a fault when there is none.
            error_reasons.append(f"'{field.key}' is empty, and this bot's world has no default Scales of Knowledge")

    merging = NODE_SPECS.get(state.node_type)
    if merging is not None and merging.merges_players and player_realms.declares_separate(config):
        error_reasons.append(
            f"This node puts every player on one realm, and this bot's {player_realms.OPTION} is "
            f"{player_realms.SEPARATE}"
        )

    state_ids = {s.id for s in config.states}
    outgoing = [t for t in config.transitions if t.source == state.id]
    for t in outgoing:
        if t.dest not in state_ids:
            error_reasons.append(f"Transition to unknown state '{t.dest}'")

    if outgoing:
        spec = NODE_SPECS.get(state.node_type)
        if spec and spec.returns:
            outgoing_whens = {t.when for t in outgoing}
            if None not in outgoing_whens:
                uncovered = [
                    r.name
                    for r in spec.returns
                    if r.name not in outgoing_whens and not _return_is_handled_without_an_edge(state, config, r.name)
                ]
                if uncovered:
                    label = "return value" if len(uncovered) == 1 else "return values"
                    warning_reasons.append(f"Uncovered {label}: {', '.join(repr(r) for r in uncovered)}")

    if config.initial and state.id != config.initial:
        r = reachable if reachable is not None else _reachable_states(config)
        if state.id not in r:
            warning_reasons.append("Unreachable from initial state")

    return bool(error_reasons), bool(warning_reasons), error_reasons, warning_reasons


def _reachable_states(config: BotConfig) -> set[str]:
    """Every state the run can get to from the initial one.

    An unrouted failure port is an edge the engine takes and the file does not
    record, so the walk has to follow those too. Without it the recovery node of a
    bot whose failures all default is unreachable by transitions alone -- which
    would warn on the one node in the graph that is reachable from everywhere, and
    put it on ``_cleanup_orphans``' deletion list.
    """
    if not config.initial:
        return set()
    by_id = {s.id: s for s in config.states}
    reachable: set[str] = set()
    # In a definition every Entry is a way in, not just the one holding the roles.
    queue = [config.initial, *(s.id for s in config.states if s.node_type == SUBGRAPH_ENTRY)]
    while queue:
        sid = queue.pop()
        if sid in reachable:
            continue
        reachable.add(sid)
        for t in config.transitions:
            if t.source == sid:
                queue.append(t.dest)
        state = by_id.get(sid)
        if state is not None and PORT_MARK_RECOVERY in port_marks_for(state, config).values():
            queue.append(config.recovery_state)
    return reachable


def _dist(a: QPointF, b: QPointF) -> float:
    return math.hypot(a.x() - b.x(), a.y() - b.y())


#: What a bot's nodes are numbered with; a template draft's are :data:`TEMPLATE_ID_PREFIX`.
STATE_ID_PREFIX = "state_"


def _unique_id(existing: Iterable[str], prefix: str = STATE_ID_PREFIX, reserved: Iterable[str] = ()) -> str:
    """Return a fresh ``<prefix>N`` id, numbered above every id already in use or reserved.

    Deliberately *not* the lowest free number.  Combat configuration binds to
    state ids — ``BotCombatStrategy.state_assignments`` stores raw ``state_N``
    strings, and ``resolve_combat`` looks the running state up by id at fight
    time.  Recycling the id of a deleted node therefore hands that node's duel
    strategy to whatever unrelated node is created next, with nothing in the UI
    to show it happened; the wrong strategy simply runs, minutes into a bot run.

    ``prefix`` is ``state_`` in a bot and ``t_`` in a template draft, and only ids
    carrying it are counted, so a bot's template-owned ``t_N`` nodes never move its own
    numbering. ``reserved`` is what a draft's earlier releases used: an id a version
    retired still names a node in every bot based on that version, so it is counted
    as if it were still in use.

    Ids that don't fit the pattern (hand-authored bot files may use anything) are
    ignored when picking the number but still can't be collided with, since the
    result always carries the prefix and a number above every numbered id present.
    """
    pattern = _STATE_NUMBER if prefix == STATE_ID_PREFIX else re.compile(rf"(?:^|_){re.escape(prefix)}(\d+)$")
    highest = 0
    for state_id in chain(existing, reserved):
        match = pattern.search(state_id)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{prefix}{highest + 1}"


#: ``state_N`` on its own, or as the tail of a subgraph member id
#: (``sync_players_1_state_7``). The tail counts because a member's local id is
#: ``state_N`` in its definition: a state added while editing that instance takes
#: its number from here, and becomes local ``state_N`` on push, so a number the
#: definition already uses must not be handed out again.
_STATE_NUMBER = re.compile(r"(?:^|_)state_(\d+)$")


#: Compose-report kinds → call node link badge states. ``held`` is an update the bot
#: could not take; the other three are a definition the library could not supply.
_LINK_STATE_FOR_KIND = {
    "linked": "linked",
    "held": "outdated",
    "missing": "missing",
    "invalid": "missing",
    "cycle": "missing",
}


def _moved(old: dict, new: dict) -> bool:
    """Whether a stored position really changed, not just by the fold's round trip."""
    return abs(float(old["x"]) - new["x"]) > 1e-6 or abs(float(old["y"]) - new["y"]) > 1e-6


def member_warnings(reasons: list[str]) -> list[str]:
    """A member's warnings worth rolling up onto its call node.

    A member unreachable on its own is reachable through the call node, and an
    uncovered outcome is where its instance's exit is, not a gap the bot author
    left -- both are the definition's business.
    """
    return [r for r in reasons if r != "Unreachable from initial state" and not r.startswith("Uncovered")]


@dataclass(frozen=True)
class FoldPorts:
    """A folded node's ports, in bot ids: what its edges attach to and the flat states behind them.

    One shape for both kinds of folded node. An instance's comes from its snapshot's
    port map through the derived member ids, named by its definition; a collapsed
    plain region's is inferred from the edges crossing its boundary
    (:func:`~bartlebot.subgraphs.boundary_ports`). Nothing that reads one needs to
    know which it has.
    """

    #: ``(port id, name, member)``: the member an entry leads into, None if it leads nowhere.
    entries: tuple[tuple[str, str, str | None], ...] = ()
    #: ``(port id, name, feeds)``: the ``(member, outcome)`` pairs an exit is fed by.
    exits: tuple[tuple[str, str, tuple[tuple[str, str | None], ...]], ...] = ()
    #: Port id -> the one destination an inferred exit stands for. An instance's exits
    #: have none: which way they lead is the bot's edge, not the port.
    exit_dests: tuple[tuple[str, str], ...] = ()

    def entry_port(self, state_id: str) -> str | None:
        return next((pid for pid, _n, m in self.entries if m == state_id), None)

    def exit_port(self, state_id: str, when: str | None, dest: str | None = None) -> str | None:
        """The exit ``(state_id, when)`` feeds -- toward ``dest``, when exits are per destination."""
        dests = dict(self.exit_dests)
        return next(
            (
                pid
                for pid, _n, feeds in self.exits
                if (state_id, when) in feeds and (pid not in dests or dest is None or dests[pid] == dest)
            ),
            None,
        )

    def entry_member(self, port_id: str | None) -> str | None:
        return next((m for pid, _n, m in self.entries if pid == port_id), None)

    def exit_feeds(self, port_id: str | None) -> list[tuple[str, str | None]]:
        return next((list(feeds) for pid, _n, feeds in self.exits if pid == port_id), [])


class _DisplayLookup:
    """``get(state id)`` answering a folded region's member with the node it is drawn as.

    What a ``RegionItem`` reads its members through, so a plain region holding an
    instance or a collapsed region wraps the node the members are drawn as.
    """

    def __init__(self, scene: "EditScene") -> None:
        self._scene = scene

    def get(self, state_id: str):
        items = self._scene.node_items
        return items.get(state_id) or items.get(self._scene.display_id(state_id))


# ---------------------------------------------------------------------------
# EditScene — interactive QGraphicsScene
# ---------------------------------------------------------------------------


class EditScene(QGraphicsScene):
    """Manages interactive nodes, edge dragging, deletion, and positions."""

    graph_changed = pyqtSignal()
    about_to_change = pyqtSignal(str)  # emitted BEFORE config is mutated; carries description
    focus_region_requested = pyqtSignal(str)
    node_type_chosen = pyqtSignal(str, object)  # (node_type, QPointF canvas position)
    # Subgraph verbs the scene's menus offer but the canvas performs, since the library
    # and the document stack live there: (region id) for both.
    save_region_as_subgraph_requested = pyqtSignal(str)
    open_definition_requested = pyqtSignal(str)
    # A subgraph node double-clicked in Run mode: (region id) of the instance whose body to view.
    view_instance_requested = pyqtSignal(str)
    # (region id, collapse): a region label's collapse button or a collapsed node's
    # expand button was clicked. The canvas decides what that means in its mode.
    collapse_toggle_requested = pyqtSignal(str, bool)
    # (collapse): the canvas menu's Collapse All Regions / Expand All Regions.
    collapse_all_requested = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config: BotConfig | None = None
        # Keyed by *display* id: a state's own id, or for a subgraph instance the
        # region id its call node carries. An instance's members have no item.
        self._node_items: dict[str, NodeItem] = {}
        # (source, when, dest) in display ids: an edge out of a call node carries the
        # exit port id as its ``when``, and one into it names the entry on the item.
        self._edge_items: list[tuple[str, str | None, str, EdgeItem]] = []
        # The flat transitions each drawn edge stands for -- one, or for a call node's
        # exit, one per (member, outcome) feeding it.
        self._edge_models: dict[EdgeItem, list[TransitionModel]] = {}
        # Member id -> the region id of the folded node it is drawn as: an instance's
        # call node, or a collapsed region's node (#67).
        self._call_of: dict[str, str] = {}
        # Region id -> the ports its folded node shows, for every folded node drawn.
        self._fold_ports: dict[str, FoldPorts] = {}
        # Run mode's collapses and expands, region id -> collapsed: a view over the
        # saved flags, never written to the file. Kept across setup(); cleared by the
        # canvas on entering Edit mode, where the saved state is decided.
        self._collapse_overrides: dict[str, bool] = {}
        # (region id, members) of each folded region as last drawn: see _after_region_edit.
        self._folded_sig: tuple = ()
        # Where stored positions are drawn: identity until a region is collapsed, and
        # then the columns and rows only its members used are closed up (fold.py).
        self._fold = FoldMap(GRID_W, GRID_H)
        # Folded regions opened up, outermost first: an instance whose body is on show
        # in Run mode, or a collapsed region stepped into. Their members are drawn as
        # nodes of their own, and a folded region inside the innermost one as its
        # folded node. Kept across setup(); see set_expanded.
        self._expanded: list[str] = []
        self._region_manager: RegionManager | None = None
        self._drag_src: NodeItem | None = None
        self._drag_when: str | None = None
        self._temp_edge: QGraphicsLineItem | None = None
        self._drag_snap_edge: EdgeItem | None = None
        self._auto_scrolling: bool = False
        self._node_drag_scroll_pos: QPointF | None = None
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setInterval(16)
        self._scroll_timer.timeout.connect(self._on_scroll_timer)
        self._interactive_mode: bool = True
        # (states, transitions, regions) as model dumps. Regions are the linked ones
        # copied whole; ordinary regions do not travel with a copy.
        self._clipboard: tuple[list[dict], list[dict], list[dict]] | None = None
        self._subgraph_library: SubgraphLibrary | None = None
        # (document id, ids): see set_reserved_ids.
        self._reserved_ids: tuple[str | None, frozenset[str]] = (None, frozenset())
        # (document id, steps): see set_base_setup_steps.
        self._base_setup_steps: tuple[str | None, tuple[SetupStep, ...]] = (None, ())
        self._in_structural_edit: bool = False  # re-entrancy guard; suppresses nested about_to_change
        self._snap_enabled: bool = False
        self._grid_style: str = "dots"  # "dots" | "lines" | "none"
        self._cycle_pos: QPointF = QPointF()
        self._cycle_edges: list[EdgeItem] = []
        self._cycle_idx: int = -1
        # Connected first, so every other listener sees a collapsed region's ports and the
        # fold already brought up to date by whatever edit emitted it.
        self.graph_changed.connect(self._refresh_folds)

    # ------------------------------------------------------------------
    # Grid / snap
    # ------------------------------------------------------------------

    def set_snap_enabled(self, enabled: bool) -> None:
        """Toggle snap-to-grid for all nodes and repaint the background grid."""
        self._snap_enabled = enabled
        for node in self._node_items.values():
            node.set_snap_enabled(enabled)
        self.update()

    def set_grid_style(self, style: str) -> None:
        """Set background grid rendering style ("dots", "lines", or "none")."""
        self._grid_style = style
        self.update()

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        super().drawBackground(painter, rect)
        if not self._snap_enabled or self._grid_style == "none":
            return
        grid_color = THEME.qcolor("grid_dot")
        x_off = 0.0
        y_off = 0.0
        if self._grid_style == "lines":
            painter.setPen(QPen(grid_color, 1.0))
            x = math.floor((rect.left() - x_off) / GRID_W) * GRID_W + x_off
            while x <= rect.right():
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
                x += GRID_W
            y = math.floor((rect.top() - y_off) / GRID_H) * GRID_H + y_off
            while y <= rect.bottom():
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
                y += GRID_H
        else:
            # Dots sit at (GRID_W*i, GRID_H*j) — node left edges and vertical centers.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(grid_color))
            dot_r = 2.0
            x = math.floor((rect.left() - x_off) / GRID_W) * GRID_W + x_off
            while x <= rect.right():
                y = math.floor((rect.top() - y_off) / GRID_H) * GRID_H + y_off
                while y <= rect.bottom():
                    painter.drawEllipse(QPointF(x, y), dot_r, dot_r)
                    y += GRID_H
                x += GRID_W

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def teardown(self) -> None:
        """Remove all items and reset internal state without loading a new config."""
        if self._region_manager is not None:
            self._region_manager.teardown()
            self._region_manager = None
        # The maps are emptied before the items are deleted, not after: clearing a
        # scene with a selection emits selectionChanged, and whatever answers it --
        # the properties panel changing page, which puts out the link rings (#155) --
        # must not find the maps still naming items Qt has just freed.
        self._node_items.clear()
        self._edge_items.clear()
        self._edge_models.clear()
        self.clear()
        self._call_of = {}
        self._fold_ports = {}
        self._fold = FoldMap(GRID_W, GRID_H)
        self._drag_src = None
        self._drag_when = None
        self._temp_edge = None
        self._drag_snap_edge = None
        self._config = None

    def _pre_edit(self, desc: str) -> None:
        """Emit about_to_change unless we are already inside a compound structural edit."""
        if not self._in_structural_edit:
            self.about_to_change.emit(desc)

    def setup(self, config: BotConfig) -> None:
        """Rebuild the scene from a BotConfig.

        Every subgraph instance is drawn as one call node, and every collapsed plain
        region as one node of its own (#67); their members get no item. An edge into or
        out of either is drawn to the folded node's port, however many flat transitions
        it stands for (``transitions_of``). Stored positions are drawn through the fold,
        which closes up the columns and rows only collapsed members used.
        """
        self.teardown()
        self._config = config
        region_ids = {r.id for r in config.regions}
        self._collapse_overrides = {rid: v for rid, v in self._collapse_overrides.items() if rid in region_ids}
        foldable = {r.id for r in config.regions if r.source is not None or is_collapsed(r, self._collapse_overrides)}
        self._expanded = [rid for rid in self._expanded if rid in foldable]
        folded = folded_regions(config, self._expanded, self._collapse_overrides)
        self._folded_sig = tuple((r.id, frozenset(r.members)) for r in folded)
        self._call_of = {m: region.id for region in folded for m in region.members}
        self._fold_ports = {region.id: self._ports_for(region) for region in folded}

        states = config.states
        sources = {self.display_id(t.source) for t in config.transitions}
        # (item, stored left x, stored center y) for everything placed through the fold.
        placed: list[tuple[NodeItem, float, float]] = []
        for i, state in enumerate(states):
            if state.id in self._call_of:
                continue
            node = self._make_node(state, is_terminal=state.id not in sources)
            node.set_port_marks(port_marks_for(state, config) if state.node_type not in PORT_NODE_TYPES else {})
            pos = state.position
            if pos and isinstance(pos, dict):
                placed.append((node, float(pos.get("x", 0)), float(pos.get("y", 0))))
            else:
                total_w = (len(states) - 1) * _AUTO_SPACING
                node.setPos(i * _AUTO_SPACING - total_w / 2 - NODE_W / 2, -NODE_H / 2)
            self.addItem(node)
            self._node_items[state.id] = node
        for region in folded:
            node = self._make_call_node(region) if region.source is not None else self._make_collapsed_node(region)
            anchor = region_anchor(config, region)
            placed.append((node, float(anchor["x"]), float(anchor["y"])))
            self.addItem(node)
            self._node_items[region.id] = node
        self._fold = self._build_fold(placed, folded)
        for node, x, y in placed:
            dx, dy = self._fold.to_display(x, y)
            node.setPos(dx, dy - node.body_height / 2)

        # Pre-compute shared bridge_y for same-destination backward-edge groups.
        # Backward edges from same-Y nodes that share a (dest, when) pair are snapped
        # to a common corridor so they overlap cleanly instead of forming a spaghetti
        # bundle of nearly-identical slightly-offset paths.
        #
        # Two separate corridors are computed:
        #   below — all backward edges except topmost-port edges (FAILURE, FALSE, etc.)
        #   above — topmost-port (index-0) backward edges on nodes with ≥2 returns
        #           (SUCCESS, TRUE, etc.) — routed above the node row so they occupy a
        #           separate vertical band and do not cross the failure-path edges below.
        #
        # Y-band outliers (source Y differs from destination Y by ≥ one node height)
        # are excluded from corridor grouping and route independently.
        # bridge_y_below / bridge_y_above / is_above_routed are imported from
        # edge_item so the corridor geometry stays in one place.
        grouped: dict[tuple[str, str | None, str, str | None], list[TransitionModel]] = {}
        for t in config.transitions:
            key = self.display_key(t)
            if key is not None:
                grouped.setdefault(key, []).append(t)

        # Group by (when, y_row) so all backward edges in the same horizontal band
        # with the same when-label share a corridor, regardless of destination.
        # Using destination as the grouping key caused edges to unique destinations
        # to route independently via a port-midpoint heuristic, producing a different
        # bridge Y than the node-bounds formula used for multi-destination groups.
        below_groups: dict[tuple[str | None, int], list[tuple]] = defaultdict(list)
        above_groups: dict[tuple[str | None, int], list[tuple]] = defaultdict(list)
        for key in grouped:
            s, w, d, _port = key
            if s == d or s not in self._node_items or d not in self._node_items:
                continue
            sn, dn = self._node_items[s], self._node_items[d]
            if dn.pos().x() >= sn.pos().x():
                continue  # forward edge
            if abs(sn.pos().y() - dn.pos().y()) >= sn.body_height:
                continue  # Y-band outlier — routes independently
            y_row = round(sn.pos().y() / GRID_H)
            if is_above_routed(sn, w):
                above_groups[(w, y_row)].append(key)
            else:
                below_groups[(w, y_row)].append(key)

        bridge_y_map: dict[tuple, float] = {}
        for group_edges in below_groups.values():
            shared_y = max(bridge_y_below(self._node_items[k[0]], self._node_items[k[2]]) for k in group_edges)
            for key in group_edges:
                bridge_y_map[key] = shared_y
        for group_edges in above_groups.values():
            shared_y = min(bridge_y_above(self._node_items[k[0]], self._node_items[k[2]]) for k in group_edges)
            for key in group_edges:
                bridge_y_map[key] = shared_y

        for key, models in grouped.items():
            s, w, d, port = key
            if s not in self._node_items or d not in self._node_items:
                continue
            self._add_edge_item(s, w, d, port, models, bridge_y=bridge_y_map.get(key))

        # Compute forward-edge offsets and fan arrival ports now that all edges
        # are registered with their nodes (port_in_for needs the full edge list).
        self._recompute_offsets()

        # Add region items behind nodes (z=-1)
        self._region_manager = RegionManager(
            self, config, _DisplayLookup(self), self._pre_edit, self._after_region_edit, self.is_region_linked
        )
        folded_members = [set(r.members) for r in folded]
        folded_away = {
            r.id
            for r in config.regions
            if r.source is None and r.members and any(set(r.members) <= fm for fm in folded_members)
        }
        self._region_manager.setup(self._interactive_mode, folded_away)
        self._sync_region_buttons()
        self._refresh_roles()
        self.refresh_errors()
        # Every call node reads as linked until the canvas hands over a report.
        self.refresh_region_states(None)

        self._update_scene_rect()

    def _make_node(self, state: StateModel, *, is_terminal: bool) -> NodeItem:
        """The item for one ordinary state: a node, or in a definition an Entry/Exit pill."""
        if state.node_type in PORT_NODE_TYPES:
            node: NodeItem = PortNodeItem(
                state.id, state.label, is_entry=state.node_type == SUBGRAPH_ENTRY, interactive=self._interactive_mode
            )
        else:
            node = NodeItem(
                state.id,
                state.label,
                interactive=self._interactive_mode,
                returns=_node_returns(state.node_type),
                is_terminal=is_terminal,
                node_type=state.node_type,
            )
        node.set_snap_enabled(self._snap_enabled)
        return node

    def _make_call_node(self, region: RegionModel) -> CallNodeItem:
        """The one node a subgraph instance is drawn as, ports from its definition. Placed by ``setup``."""
        assert region.source is not None
        defn = self._definition(region)
        source = region.source
        ports = self._fold_ports[region.id]
        name = defn.name if defn is not None else source.subgraph_id
        origin = ""
        if self._subgraph_library is not None:
            origin = source_label(self._subgraph_library.origin(source.subgraph_id))
        parts = ["subgraph", name if name != region.label else "", origin, f"v{source.version}"]
        node = CallNodeItem(
            region.id,
            region.label,
            subline=" · ".join(p for p in parts if p),
            entries=[(pid, name) for pid, name, _m in ports.entries],
            exits=[(pid, name) for pid, name, _f in ports.exits],
            interactive=self._interactive_mode,
        )
        node.set_snap_enabled(self._snap_enabled)
        return node

    def _make_collapsed_node(self, region: RegionModel) -> CollapsedRegionItem:
        """The one node a collapsed plain region is drawn as, its ports inferred. Placed by ``setup``."""
        ports = self._fold_ports[region.id]
        n = len(region.members)
        node = CollapsedRegionItem(
            region.id,
            region.label,
            subline=f"region · {n} node{'' if n == 1 else 's'}",
            entries=[(pid, name) for pid, name, _m in ports.entries],
            exits=[(pid, name) for pid, name, _f in ports.exits],
            interactive=self._interactive_mode,
            color=region.color,
        )
        node.set_snap_enabled(self._snap_enabled)
        rid = region.id
        node.set_expand_action(lambda: self._request_collapse(rid, False))
        return node

    def _ports_for(self, region: RegionModel) -> FoldPorts:
        """What a folded region's node shows as ports, and the member each one reaches, in bot ids."""
        assert self._config is not None
        if region.source is None:
            entries, exits = boundary_ports(self._config, region.members)
            return FoldPorts(
                tuple((e.port_id, e.name, e.member) for e in entries),
                tuple((x.port_id, x.name, x.feeds) for x in exits),
                tuple((x.port_id, x.dest) for x in exits),
            )
        source = region.source
        defn = self._definition(region)
        if defn is not None:
            entry_ids = [(s.id, port_name(s)) for s in defn.states if s.node_type == SUBGRAPH_ENTRY]
            exit_ids = [(s.id, port_name(s)) for s in defn.states if s.node_type == SUBGRAPH_EXIT]
        else:
            # Missing from the library: the snapshot's own port map still says what the
            # ports are, which is what keeps the bot's edges drawable.
            entry_ids = [(pid, pid) for pid, p in source.ports.items() if p.member is not None]
            exit_ids = [(pid, pid) for pid, p in source.ports.items() if p.from_ is not None]
        rid = region.id

        def member_of(pid: str) -> str | None:
            port = source.ports.get(pid)
            return member_id(rid, port.member) if port is not None and port.member is not None else None

        def feeds_of(pid: str) -> tuple[tuple[str, str | None], ...]:
            port = source.ports.get(pid)
            return tuple((member_id(rid, m), w) for m, w in (port.from_ or ())) if port is not None else ()

        return FoldPorts(
            tuple((pid, name, member_of(pid)) for pid, name in entry_ids),
            tuple((pid, name, feeds_of(pid)) for pid, name in exit_ids),
        )

    def _build_fold(self, placed: list[tuple[NodeItem, float, float]], folded: list[RegionModel]) -> FoldMap:
        """The fold for what is drawn: every collapsed plain region's span, closed up around what is visible."""
        assert self._config is not None
        base = FoldMap(GRID_W, GRID_H)
        by_id = {s.id: s for s in self._config.states}
        spans = []
        for region in folded:
            if region.source is not None:
                continue
            positions = [
                (float(p["x"]), float(p["y"]))
                for m in region.members
                if (s := by_id.get(m)) is not None and (p := s.position) is not None
            ]
            span = base.span_of(positions)
            if span is not None:
                spans.append(span)
        if not spans:
            return base
        occupied = [(x, y, node.body_height) for node, x, y in placed]
        return FoldMap.build(spans, occupied, col_w=GRID_W, row_h=GRID_H)

    # ------------------------------------------------------------------
    # Stored positions and where they are drawn
    # ------------------------------------------------------------------

    @property
    def fold(self) -> FoldMap:
        """How stored positions map to where they are drawn; the identity when nothing is collapsed."""
        return self._fold

    def stored_position(self, top_left: QPointF, body_height: float = NODE_H) -> dict:
        """The ``StateModel.position`` an item drawn with its top-left at ``top_left`` stands for."""
        x, y = self._fold.to_stored(top_left.x(), top_left.y() + body_height / 2)
        return {"x": x, "y": y}

    def drawn_position(self, position: dict, body_height: float = NODE_H) -> QPointF:
        """Where an item whose stored position is ``position`` has its top-left drawn."""
        x, y = self._fold.to_display(float(position["x"]), float(position["y"]))
        return QPointF(x, y - body_height / 2)

    def free_position(self, position: dict) -> dict:
        """Where a node wanting stored ``position`` can go: there, or the nearest empty grid cell.

        What a template update asks before it adds a node: the spot beside the node it
        is wired from may already hold something the bot's author put there.
        """
        top_left = self.drawn_position(position)
        taken = any(
            abs(item.pos().x() - top_left.x()) < NODE_W and abs(item.pos().y() - top_left.y()) < NODE_H
            for item in self._node_items.values()
        )
        if not taken:
            return dict(position)
        center = QPointF(top_left.x() + NODE_W / 2, top_left.y() + NODE_H / 2)
        return self.stored_position(self.find_empty_grid_cell(center))

    # ------------------------------------------------------------------
    # A subgraph node's body, shown in Run mode
    # ------------------------------------------------------------------

    @property
    def expanded(self) -> list[str]:
        """The folded regions whose members are drawn as nodes, outermost first."""
        return list(self._expanded)

    def set_expanded(self, region_ids: list[str]) -> None:
        """Draw these folded regions' members as nodes of their own, and rebuild if that changed.

        What the Run-mode body view stands on: a running bot's config is already
        composed, so the members are real states at ``anchor + definition position``,
        and once they have items the run highlight lands on them by id. An instance is
        only ever opened in Run mode, where nothing can be edited; a collapsed region is
        opened by stepping into it in either mode, and its members are then visible, so
        the fold opens its columns back up with nothing more.
        """
        region_ids = list(region_ids)
        if region_ids == self._expanded:
            return
        self._expanded = region_ids
        if self._config is not None:
            self.setup(self._config)

    def _expanded_members(self) -> set[str]:
        return {m for rid in self._expanded if (r := self._region_by_id(rid)) is not None for m in r.members}

    def _expanded_instance_members(self) -> set[str]:
        """Members of opened instances: a collapsed region stepped into is the bot's own graph."""
        return {
            m
            for rid in self._expanded
            if (r := self._region_by_id(rid)) is not None and r.source is not None
            for m in r.members
        }

    def members_shown_for(self, region_id: str) -> set[str]:
        """The display ids an expanded region's members are drawn as: a folded region inside is its node."""
        region = self._region_by_id(region_id)
        if region is None:
            return set()
        return {self.display_id(m) for m in region.members if self.display_id(m) in self._node_items}

    def _definition(self, region: RegionModel):
        if self._subgraph_library is None or region.source is None:
            return None
        return self._subgraph_library.get(region.source.subgraph_id)

    def _add_edge_item(
        self,
        s: str,
        w: str | None,
        d: str,
        port: str | None,
        models: list[TransitionModel],
        *,
        bridge_y: float | None = None,
    ) -> EdgeItem:
        sn, dn = self._node_items[s], self._node_items[d]
        has_flags = any(t.flags or t.flag_groups for t in models)
        edge = EdgeItem(sn, dn, when=w, bridge_y_override=bridge_y, has_flags=has_flags, dst_port=port)
        self.addItem(edge)
        sn.add_edge(edge)
        if dn is not sn:
            dn.add_edge(edge)
        self._edge_items.append((s, w, d, edge))
        self._edge_models[edge] = list(models)
        return edge

    # ------------------------------------------------------------------
    # Display ids: what an instance's members are drawn as
    # ------------------------------------------------------------------

    def display_id(self, state_id: str) -> str:
        """The id of the item ``state_id`` is drawn as: its own, or its call node's region id."""
        return self._call_of.get(state_id, state_id)

    def display_key(self, t: TransitionModel) -> tuple[str, str | None, str, str | None] | None:
        """``(source, when, dest, entry port)`` a transition is drawn as, or None when it is inside an instance.

        An edge out of an instance leaves from the exit port its ``(member, outcome)``
        feeds, and one into it lands on the entry port its member is reached by. A
        crossing on no port (a hand-edited file) is drawn from the node itself.
        """
        s, d = self.display_id(t.source), self.display_id(t.dest)
        if s == d and t.source in self._call_of:
            return None
        w = t.when
        if t.source in self._call_of and s in self._fold_ports:
            pid = self._fold_ports[s].exit_port(t.source, t.when, t.dest)
            w = pid if pid is not None else t.when
        port = None
        if t.dest in self._call_of and d in self._fold_ports:
            port = self._fold_ports[d].entry_port(t.dest)
        return s, w, d, port

    def _region_by_id(self, region_id: str) -> RegionModel | None:
        if self._config is None:
            return None
        return next((r for r in self._config.regions if r.id == region_id), None)

    def transitions_of(self, edge: EdgeItem) -> list[TransitionModel]:
        """The flat transitions a drawn edge stands for, in file order."""
        return list(self._edge_models.get(edge, []))

    def edge_for(self, transition: TransitionModel) -> EdgeItem | None:
        """The drawn edge a flat transition is part of, or None (one inside an instance)."""
        for edge, models in self._edge_models.items():
            if any(t is transition for t in models):
                return edge
        return None

    def is_call_node(self, display_id: str) -> bool:
        """Whether ``display_id`` is a subgraph instance's call node -- linked, not a collapsed region."""
        return isinstance(self._node_items.get(display_id), CallNodeItem)

    def is_folded(self, display_id: str) -> bool:
        """Whether ``display_id`` is drawn as a folded node: a call node or a collapsed region."""
        return isinstance(self._node_items.get(display_id), FoldedNodeItem)

    def folded_nodes(self) -> dict[str, FoldedNodeItem]:
        """Region id -> folded node, for every call node and collapsed region on the canvas."""
        return {rid: n for rid, n in self._node_items.items() if isinstance(n, FoldedNodeItem)}

    def folded_node_of(self, state_id: str) -> FoldedNodeItem | None:
        """The folded node ``state_id`` is drawn as, when it is a member of one."""
        rid = self._call_of.get(state_id)
        node = self._node_items.get(rid) if rid is not None else None
        return node if isinstance(node, FoldedNodeItem) else None

    def item_for(self, some_id: str) -> NodeItem | None:
        """The node *some_id* is drawn as: a state id, a member's folded node, or a region's."""
        return self._node_items.get(some_id) or self._node_items.get(self.display_id(some_id))

    def set_link_lit(self, ids) -> None:
        """Light exactly the nodes *ids* are drawn as, and no others (#155).

        *ids* are state ids or a nested subgraph node's region id, as the signature
        page's selected input names what it sets.
        """
        lit = {item for i in ids if (item := self.item_for(i)) is not None}
        for node in self._node_items.values():
            node.set_link_lit(node in lit)

    def ports_of(self, region_id: str) -> FoldPorts | None:
        """The ports a folded region's node shows, or None when it is not drawn folded."""
        return self._fold_ports.get(region_id)

    def is_collapsed(self, display_id: str) -> bool:
        """Whether ``display_id`` is a collapsed plain region's node."""
        return isinstance(self._node_items.get(display_id), CollapsedRegionItem)

    def call_nodes(self) -> dict[str, CallNodeItem]:
        """Region id -> call node, for every subgraph instance on the canvas."""
        return {rid: n for rid, n in self._node_items.items() if isinstance(n, CallNodeItem)}

    def call_node_of(self, state_id: str) -> CallNodeItem | None:
        """The call node ``state_id`` is drawn as, when it is a member of an instance."""
        rid = self._call_of.get(state_id)
        node = self._node_items.get(rid) if rid is not None else None
        return node if isinstance(node, CallNodeItem) else None

    def state_ids_of(self, display_ids) -> list[str]:
        """Expand display ids to state ids: a folded node stands for every member of its region."""
        out: list[str] = []
        for did in display_ids:
            region = self._region_by_id(did) if self.is_folded(did) else None
            out.extend(region.members if region is not None else [did])
        return list(dict.fromkeys(out))

    @property
    def config(self) -> BotConfig | None:
        """The BotConfig this scene was set up from, or None before setup()."""
        return self._config

    @property
    def node_items(self) -> dict[str, NodeItem]:
        """Display id -> NodeItem for every node currently on the scene (call nodes by region id)."""
        return self._node_items

    @property
    def edge_items(self) -> list[tuple[str, str | None, str, EdgeItem]]:
        """(source, when, dest, EdgeItem) in display ids for every edge currently on the scene."""
        return self._edge_items

    def refresh_initial(self, initial_id: str) -> None:
        """Move the initial badge -- onto an entry port, when the role sits inside an instance."""
        self._refresh_roles()

    def _refresh_roles(self) -> None:
        if self._config is None:
            return
        initial, recovery = self._config.initial, self._config.recovery_state
        for node_id, node in self._node_items.items():
            if isinstance(node, FoldedNodeItem):
                ports = self._fold_ports.get(node_id)
                ip = ports.entry_port(initial) if ports is not None else None
                rp = ports.entry_port(recovery) if ports is not None else None
                node.set_role_ports(ip, rp)
            elif isinstance(node, PortNodeItem):
                continue  # a definition's roles are a formality: every Entry is a way in
            else:
                node.set_initial(node_id == initial)
                node.set_recovery(node_id == recovery)

    def refresh_edge_flags(self, transition) -> None:
        """Refresh the dotted-pen flag indicator on the edge *transition* is drawn as."""
        edge = self.edge_for(transition)
        if edge is not None:
            edge.set_has_flags(any(t.flags or t.flag_groups for t in self._edge_models.get(edge, [])))

    def refresh_all_edge_flags(self) -> None:
        """Re-derive every edge's dotted-pen indicator from the config.

        Deleting an option strips its conditions off the transitions that used it,
        which can leave an edge unconditional -- but the edge item is a snapshot,
        and nothing on the Options surface can reach the canvas to say so.  A whole
        sweep rather than a per-transition call for the same reason: the deletion
        can touch any number of edges, and ``set_has_flags`` is a no-op unless the
        flag actually moved.
        """
        for edge, models in self._edge_models.items():
            edge.set_has_flags(any(t.flags or t.flag_groups for t in models))

    def refresh_terminal(self) -> None:
        """Update the terminal-state badge based on current transitions."""
        if self._config is None:
            return
        sources = {self.display_id(t.source) for t in self._config.transitions}
        for node_id, node in self._node_items.items():
            node.set_terminal(node_id not in sources and not isinstance(node, (FoldedNodeItem, PortNodeItem)))

    def refresh_recovery(self, recovery_state_id: str | None) -> None:
        """Update the recovery-state badge across all nodes."""
        self._refresh_roles()
        # Moving the role moves every mark that points at it, including the one the
        # new recovery node loses on its own failure port.
        self.refresh_port_marks()

    def refresh_port_marks(self) -> None:
        """Redraw the ports that route without an edge on them.

        Recomputed from the config rather than tracked incrementally: a mark depends
        on what edges a port *lacks*, so every edge added, deleted or rerouted
        anywhere can change one, and a missed update leaves a port claiming a route
        the engine will not take.
        """
        if self._config is None:
            return
        for state in self._config.states:
            node = self._node_items.get(state.id)
            if node is not None and not isinstance(node, (FoldedNodeItem, PortNodeItem)):
                node.set_port_marks(port_marks_for(state, self._config))

    def refresh_errors(self) -> None:
        """Recompute and apply error/warning indicators for all nodes.

        An instance's members are checked like any state, and what they find rolls up
        onto the call node: an input left empty flags the call node, and the banner and
        the Run button see the member's own error.
        """
        if self._config is None:
            return
        reachable = _reachable_states(self._config)
        fills = self.fill_ins()
        rolled: dict[str, tuple[list[str], list[str]]] = {}
        for state in self._config.states:
            is_error, is_warning, err_reasons, warn_reasons = _validate_node(
                state, self._config, reachable, fills, self._subgraph_library
            )
            node = self._node_items.get(state.id)
            if node is not None:
                if self._expanded and state.id in self._expanded_instance_members():
                    # A member drawn in the Run-mode body view: the same rule as rolling up.
                    warn_reasons = member_warnings(warn_reasons)
                    is_warning = bool(warn_reasons)
                reasons = err_reasons + warn_reasons
                node.set_error(is_error, reasons)
                node.set_warning(is_warning, reasons)
                continue
            rid = self._call_of.get(state.id)
            if rid is None:
                continue
            errs, warns = rolled.setdefault(rid, ([], []))
            name = state.label or state.id
            errs.extend(f"{name}: {r}" for r in err_reasons)
            # A collapsed region's gaps are the bot author's own, so nothing is filtered.
            warns.extend(
                f"{name}: {r}" for r in (warn_reasons if self.is_collapsed(rid) else member_warnings(warn_reasons))
            )
        for rid, node in self.folded_nodes().items():
            errs, warns = rolled.get(rid, ([], []))
            node.set_error(bool(errs), errs + warns)
            node.set_warning(bool(warns), errs + warns)

    # ------------------------------------------------------------------
    # Subgraph instances
    # ------------------------------------------------------------------

    def set_subgraph_library(self, library: SubgraphLibrary | None) -> None:
        """The library a pasted instance composes from and a call node reads its ports from. May be None."""
        self._subgraph_library = library

    @property
    def subgraph_library(self) -> SubgraphLibrary | None:
        return self._subgraph_library

    # ------------------------------------------------------------------
    # Setup steps
    # ------------------------------------------------------------------

    def set_base_setup_steps(self, document_id: str | None, steps: Sequence[SetupStep] = ()) -> None:
        """The setup steps of the bot ``document_id``: its template's base version's (``setup_steps.steps_for``).

        Set by the controller when it opens a bot made from a template, since reading
        them takes the template store. Tied to the bot's id, as the reserved ids are, so
        a definition opened over it or the next bot never inherits them. A template
        draft needs none handed in: its steps are in the document.
        """
        self._base_setup_steps = (document_id, tuple(steps)) if document_id is not None else (None, ())

    @property
    def step_list(self) -> list[SetupStep]:
        """The steps that apply to the open document: a draft's own, or its bot's base version's."""
        if self._config is None:
            return []
        if self._config.template_meta is not None:
            return list(self._config.template_meta.setup)
        document_id, steps = self._base_setup_steps
        return list(steps) if self._config.template is not None and self._config.id == document_id else []

    def fill_ins(self) -> dict[tuple[str, str], setup_steps.FillIn]:
        """``(state id, param)`` -> the setup step asking for it, for the open document."""
        steps = self.step_list
        if self._config is None or not steps:
            return {}
        return setup_steps.fill_ins(self._config, steps, self._subgraph_library)

    def setup_progress(self) -> list[setup_steps.StepStatus]:
        """How the open document stands against its steps, in order."""
        if self._config is None:
            return []
        return setup_steps.progress(self._config, self.step_list, self._subgraph_library)

    # ------------------------------------------------------------------
    # Minting ids
    # ------------------------------------------------------------------

    def set_reserved_ids(self, document_id: str | None, ids: Iterable[str] = ()) -> None:
        """Ids the document ``document_id`` must never mint, however its own ids change.

        For a template draft, every id its released versions used (``template_store.used_ids``);
        set by the controller when it opens one. Tied to the document's id so a definition
        opened over the draft, or the next bot, never inherits them. ``None`` clears it.
        """
        self._reserved_ids = (document_id, frozenset(ids)) if document_id is not None else (None, frozenset())

    @property
    def reserved_ids(self) -> frozenset[str]:
        """The reserved ids that apply to the open document: empty unless it is the one they were set for."""
        document_id, ids = self._reserved_ids
        return ids if self._config is not None and self._config.id == document_id else frozenset()

    @property
    def state_id_prefix(self) -> str:
        """``t_`` while a template draft is open, else ``state_``: a bot never mints a template-owned id."""
        return TEMPLATE_ID_PREFIX if self._config is not None and self._config.is_template else STATE_ID_PREFIX

    @property
    def instance_id_prefix(self) -> str:
        """``t_`` while a template draft is open, else nothing (``sync_players_1``)."""
        return TEMPLATE_ID_PREFIX if self._config is not None and self._config.is_template else ""

    def mint_state_id(self, existing: Iterable[str]) -> str:
        """A fresh node id for the open document, clear of ``existing`` and the reserved ids."""
        return _unique_id(existing, self.state_id_prefix, self.reserved_ids)

    def mint_instance_id(self, name: str, config: BotConfig | None = None) -> str:
        """A fresh subgraph instance id for a definition called ``name``, in ``config`` (default: the open one).

        Public because the canvas mints one on a working copy of the config when it turns
        a region into an instance.
        """
        config = config if config is not None else self._config
        taken = taken_ids(config) if config is not None else set()
        return mint_instance_id(name, taken | self.reserved_ids, self.instance_id_prefix)

    def linked_region_of(self, state_id: str) -> RegionModel | None:
        """The outermost linked region ``state_id`` belongs to, or None."""
        if self._config is None:
            return None
        return linked_region_of(self._config, state_id)

    def is_region_linked(self, region_id: str) -> bool:
        """Whether the region is a subgraph instance, whose membership is the library's to decide."""
        region = self._region_by_id(region_id)
        return region is not None and region.source is not None

    def refresh_region_states(self, report: ComposeReport | None) -> None:
        """Push each call node's link badge state from the last compose report.

        An instance the report does not mention (one pasted since) reads as ``linked``.
        """
        if self._config is None:
            return
        for rid, node in self.call_nodes().items():
            status = report.regions.get(rid) if report is not None else None
            kind = status.kind if status is not None else "linked"
            node.set_link_state(_LINK_STATE_FOR_KIND.get(kind, "linked"))

    def refresh_call_nodes(self) -> None:
        """Re-read every folded node's title (and a collapsed region's color) from its region, after a page changed it."""
        for rid, node in self.folded_nodes().items():
            region = self._region_by_id(rid)
            if region is None:
                continue
            node.set_label(region.label)
            if isinstance(node, CollapsedRegionItem):
                node.set_color(region.color)

    def insert_subgraph_instance(self, subgraph_id: str, pos: QPointF, into_region: str | None = None) -> str | None:
        """Place a new instance of ``subgraph_id`` with its call node at ``pos``. Returns the region id.

        ``pos`` is the item position the other insert paths use (top-left of a node),
        so the anchor is stored in the model convention: left edge, vertical center.
        Inputs start unset, following the definition's defaults. With ``into_region`` the members also
        join that (plain) region.

        The scene is rebuilt from the config, as paste does, since a call node and its
        composed edges are simpler to build once than to grow.
        """
        if self._config is None or self._subgraph_library is None:
            return None
        defn = self._subgraph_library.get(subgraph_id)
        if defn is None:
            logger.warning("Cannot insert subgraph %s: not in the library", subgraph_id)
            return None
        config = self._config
        region_id = self.mint_instance_id(defn.name, config)
        source = SubgraphSource(
            subgraph_id=subgraph_id,
            version=defn.version,
            # Nothing set: every input follows the definition's default until changed here.
            inputs={},
            anchor=self.stored_position(pos),
        )
        try:
            mat = materialize_region(region_id, source, defn, self._subgraph_library)
        except (SubgraphCycleError, LookupError) as exc:
            logger.warning("Cannot insert subgraph %s: %s", subgraph_id, exc)
            return None
        source.ports = mat.ports
        self._pre_edit("Insert Subgraph")
        self._in_structural_edit = True
        try:
            config.states.extend(mat.states)
            config.transitions.extend(mat.transitions)
            color = (len(config.regions) % len(THEME.REGION_PALETTE)) + 1
            config.regions.append(
                RegionModel(id=region_id, label=defn.name, members=list(mat.member_ids), color=color, source=source)
            )
            config.regions.extend(mat.regions)
            if into_region is not None:
                outer = self._region_by_id(into_region)
                if outer is not None and outer.source is None:
                    outer.members.extend(m for m in mat.member_ids if m not in outer.members)
            self.setup(config)
            self.clearSelection()
            node = self._node_items.get(region_id)
            if node is not None:
                node.setSelected(True)
                view = self.views()[0] if self.views() else None
                if view is not None:
                    view.smooth_pan_if_offscreen(node)
        finally:
            self._in_structural_edit = False
        self.graph_changed.emit()
        return region_id

    def remove_instance(self, region_id: str, *, confirm: bool = True) -> bool:
        """Delete a folded node with every member, nested instances included.

        A subgraph instance -- its call node -- or a collapsed region, which is deleted
        as what it is drawn as: the region and every node in it. *Remove Region* is the
        way to drop only the grouping. Roles are reassigned as on any delete. Returns
        False if the user cancels, or if ``region_id`` is not drawn folded.
        """
        if self._config is None:
            return False
        region = self._region_by_id(region_id)
        if region is None or not (region.source is not None or self.is_collapsed(region_id)):
            return False
        is_instance = region.source is not None
        if confirm:
            n = len(region.members)
            question = (
                f"Delete subgraph '{region.label}' and its connections?"
                if is_instance
                else f"Delete region '{region.label}' and the {n} node{'' if n == 1 else 's'} in it?"
            )
            answer = QMessageBox.question(
                self.views()[0] if self.views() else None,
                "Delete Subgraph" if is_instance else "Delete Region",
                question,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        self._pre_edit("Delete Subgraph" if is_instance else "Delete Region")
        config = self._config
        members = set(region.members)
        gone = [region, *nested_regions(config, region)]
        config.states = [s for s in config.states if s.id not in members]
        config.transitions = [t for t in config.transitions if t.source not in members and t.dest not in members]
        config.regions = [r for r in config.regions if not any(r is g for g in gone)]
        for r in config.regions:
            r.members = [m for m in r.members if m not in members]
        config.regions = [r for r in config.regions if r.source is not None or len(r.members) >= 2]
        self._reassign_orphaned_roles()
        prev = self._in_structural_edit
        self._in_structural_edit = True
        try:
            self.setup(config)
        finally:
            self._in_structural_edit = prev
        self.graph_changed.emit()
        return True

    def collapse_blocker(self, region_id: str) -> str | None:
        """Why ``region_id`` cannot be collapsed, or None if it can."""
        if self._config is None:
            return "No bot is open."
        return collapse_blocker(self._config, region_id, self._collapse_overrides)

    def is_region_collapsed(self, region_id: str) -> bool:
        """Whether a plain region is drawn collapsed: Run mode's override, else its saved flag."""
        region = self._region_by_id(region_id)
        return region is not None and region.source is None and is_collapsed(region, self._collapse_overrides)

    def _request_collapse(self, region_id: str, collapsed: bool) -> None:
        """A button asked: pass it on once the click is over, since acting rebuilds the item clicked."""
        QTimer.singleShot(0, lambda: self.collapse_toggle_requested.emit(region_id, collapsed))

    def note_node_moved(self) -> None:
        """A node's position changed: fit the regions to it. ``NodeItem.itemChange`` calls this.

        The moves that need it are a drag and ``_refresh_folds``, which calls it itself;
        every other reposition goes through ``setup``, which builds the regions last.
        """
        if self._region_manager is not None:
            self._region_manager.recompute_all()

    def _sync_region_buttons(self) -> None:
        """Give every drawn region's label its collapse button, faded with the reason when refused."""
        if self._region_manager is None:
            return
        for rid, item in self._region_manager.items.items():
            item.set_collapse_action(lambda rid=rid: self._request_collapse(rid, True), self.collapse_blocker(rid))

    def bulk_collapse_targets(self, collapsed: bool) -> list[str]:
        """The plain regions *Collapse all* or *Expand all* would change, in the order it would.

        Collapse all goes largest first, and skips a region its own earlier collapses
        would block, so of two partly overlapping regions the larger folds. A region
        inside one that collapses is still marked, and shows when stepped into.
        """
        if self._config is None:
            return []
        plain = [r for r in self._config.regions if r.source is None and r.members]
        if not collapsed:
            return [r.id for r in plain if is_collapsed(r, self._collapse_overrides)]
        order = {r.id: i for i, r in enumerate(self._config.regions)}
        sim = dict(self._collapse_overrides)
        targets: list[str] = []
        for r in sorted(plain, key=lambda r: (-len(r.members), order[r.id])):
            if is_collapsed(r, sim) or collapse_blocker(self._config, r.id, sim) is not None:
                continue
            sim[r.id] = True
            targets.append(r.id)
        return targets

    def set_regions_collapsed(self, region_ids: list[str], collapsed: bool) -> bool:
        """Collapse or expand several plain regions as one undoable step (Edit mode's *all*)."""
        if self._config is None or not region_ids:
            return False
        self._pre_edit("Collapse All Regions" if collapsed else "Expand All Regions")
        for rid in region_ids:
            region = self._region_by_id(rid)
            if region is not None and region.source is None:
                region.collapsed = True if collapsed else None
        if not collapsed:
            self._expanded = [rid for rid in self._expanded if rid not in set(region_ids)]
        prev = self._in_structural_edit
        self._in_structural_edit = True
        try:
            self.setup(self._config)
        finally:
            self._in_structural_edit = prev
        self.graph_changed.emit()
        return True

    def set_collapse_overrides(self, region_ids: list[str], collapsed: bool) -> bool:
        """Run mode's collapse or expand: a view over the saved flags, not an edit. False if nothing changed.

        An override equal to the saved flag is dropped rather than kept, so what is
        held is only where the view differs from the file.
        """
        if self._config is None:
            return False
        changed = False
        for rid in region_ids:
            region = self._region_by_id(rid)
            if (
                region is None
                or region.source is not None
                or is_collapsed(region, self._collapse_overrides) == collapsed
            ):
                continue
            if collapsed and collapse_blocker(self._config, rid, self._collapse_overrides) is not None:
                continue
            if bool(region.collapsed) == collapsed:
                self._collapse_overrides.pop(rid, None)
            else:
                self._collapse_overrides[rid] = collapsed
            changed = True
        if not changed:
            return False
        if not collapsed:
            self._expanded = [rid for rid in self._expanded if rid not in set(region_ids)]
        self.setup(self._config)
        return True

    def forget_collapse_overrides(self) -> None:
        """Drop Run mode's view without rebuilding: for a caller about to set up a new document."""
        self._collapse_overrides = {}

    def clear_collapse_overrides(self) -> bool:
        """Drop Run mode's view, back to the saved flags. True, having rebuilt, if there was one."""
        if not self._collapse_overrides:
            return False
        self._collapse_overrides = {}
        if self._config is not None:
            self.setup(self._config)
        return True

    def set_region_collapsed(self, region_id: str, collapsed: bool) -> bool:
        """Draw a plain region as one node, or open it back up. One undoable step; False if refused.

        Only a flag changes: stored positions stay the expanded layout, and the fold
        closes up the space the members used (fold.py), so expanding is exact.
        """
        if self._config is None:
            return False
        region = self._region_by_id(region_id)
        if region is None or region.source is not None or bool(region.collapsed) == collapsed:
            return False
        if collapsed and collapse_blocker(self._config, region_id) is not None:
            return False
        self._pre_edit("Collapse Region" if collapsed else "Expand Region")
        region.collapsed = True if collapsed else None
        if not collapsed:
            self._expanded = [rid for rid in self._expanded if rid != region_id]
        prev = self._in_structural_edit
        self._in_structural_edit = True
        try:
            self.setup(self._config)
        finally:
            self._in_structural_edit = prev
        self.graph_changed.emit()
        return True

    def detach_region(self, region_id: str) -> None:
        """Drop the instance's link to the library: its members become ordinary states on the canvas.

        Ids stay exactly as they are; the region stays, as an ordinary one around them.
        """
        if self._config is None:
            return
        region = self._region_by_id(region_id)
        if region is None or region.source is None:
            return
        self._pre_edit("Detach from Library")
        detach_subgraph(self._config, region_id)
        prev = self._in_structural_edit
        self._in_structural_edit = True
        try:
            self.setup(self._config)
        finally:
            self._in_structural_edit = prev
        self.graph_changed.emit()

    @property
    def interactive(self) -> bool:
        """Whether nodes and edges take clicks and drags: Edit mode's state."""
        return self._interactive_mode

    def set_interactive(self, interactive: bool) -> None:
        """Toggle node and edge interactivity for mode switching."""
        self._interactive_mode = interactive
        for node in self._node_items.values():
            node.set_interactive(interactive)
        for _s, _w, _d, edge in self._edge_items:
            edge.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, interactive)
        if self._region_manager is not None:
            self._region_manager.set_interactive(interactive)
        if not interactive:
            self.clearSelection()

    def find_empty_grid_cell(self, viewport_center: QPointF) -> QPointF:
        """Return the top-left scene position of the nearest unoccupied grid cell to viewport_center.

        Searches outward in a spiral so new nodes never land on top of an existing one.
        Falls back to viewport_center (minus half node dims) if the canvas is pathologically full.
        """
        occupied: set[tuple[int, int]] = set()
        for item in self._node_items.values():
            p = item.pos()
            occupied.add((round(p.x() / GRID_W), round((p.y() + NODE_H / 2) / GRID_H)))

        # Grid cell whose center is nearest to viewport_center
        start_cx = round((viewport_center.x() - NODE_W / 2) / GRID_W)
        start_cy = round(viewport_center.y() / GRID_H)

        for radius in range(31):
            for dx, dy in _spiral_ring(radius):
                cell = (start_cx + dx, start_cy + dy)
                if cell not in occupied:
                    return QPointF(cell[0] * GRID_W, cell[1] * GRID_H - NODE_H / 2)

        return QPointF(viewport_center.x() - NODE_W / 2, viewport_center.y() - NODE_H / 2)

    def add_state(self, pos: QPointF) -> None:
        """Open the node browser dialog and emit node_type_chosen on confirmation."""
        if self._config is None:
            return
        from bartlebot.gui.canvas.node_browser_dialog import NodeBrowserDialog

        view = self.views()[0] if self.views() else None
        dlg = NodeBrowserDialog(view, mode="add", subgraph_library=self._subgraph_library)
        if dlg.exec() and dlg.selected_node_type:
            self.node_type_chosen.emit(dlg.selected_node_type, pos)

    def create_state_at(self, node_type: str, pos: QPointF) -> str | None:
        """Instantiate a state of the given node_type at pos. Called after dialog confirmation.

        A ``subgraph:<id>`` key from the node browser's Subgraphs tab inserts an
        instance instead, and returns its region id.
        """
        if self._config is None:
            return None
        if node_type.startswith(SUBGRAPH_KEY_PREFIX):
            return self.insert_subgraph_instance(node_type[len(SUBGRAPH_KEY_PREFIX) :], pos)
        self._pre_edit("Create Node")
        state_id = self.mint_state_id(s.id for s in self._config.states)
        default_params = {f.key: f.default for f in NODE_PARAM_SCHEMAS.get(node_type, []) if f.default is not None}
        state = StateModel(
            id=state_id,
            node_type=node_type,
            label=node_display_name(node_type),
            position=self.stored_position(pos),
            params=default_params,
        )
        self._config.states.append(state)
        returns = _node_returns(node_type)
        node = NodeItem(state_id, state.label, interactive=True, returns=returns, is_terminal=True, node_type=node_type)
        node.set_snap_enabled(self._snap_enabled)
        node.setPos(pos)
        self.addItem(node)
        self._node_items[state_id] = node
        self._update_scene_rect()
        view = self.views()[0] if self.views() else None
        if view is not None:
            view.smooth_pan_if_offscreen(node)
        self.graph_changed.emit()
        return state_id

    def add_port_node(self, node_type: str, pos: QPointF) -> str | None:
        """Place a new Entry or Exit in a definition document, named after its kind. Returns its id.

        A port's id is its node's id, stable for life; its name is the label, which
        the author changes freely. Only a definition document has ports.
        """
        if self._config is None or not self._config.is_definition or node_type not in PORT_NODE_TYPES:
            return None
        is_entry = node_type == SUBGRAPH_ENTRY
        kind = "entry" if is_entry else "exit"
        taken = {s.id for s in self._config.states}
        labels = {s.label for s in self._config.states if s.node_type == node_type}
        n = 1
        while f"{'Entry' if is_entry else 'Exit'} {n}" in labels:
            n += 1
        label = f"{'Entry' if is_entry else 'Exit'} {n}"
        pid = f"{kind}_{n}"
        while pid in taken:
            n += 1
            pid = f"{kind}_{n}"
        self._pre_edit("Add Entry" if is_entry else "Add Exit")
        state = StateModel(id=pid, node_type=node_type, label=label, position=self.stored_position(pos))
        self._config.states.append(state)
        node = self._make_node(state, is_terminal=False)
        node.set_interactive(True)
        node.setPos(pos)
        self.addItem(node)
        self._node_items[pid] = node
        self._update_scene_rect()
        self.refresh_errors()
        self.graph_changed.emit()
        return pid

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._interactive_mode:
            scene_pos = event.scenePos()
            result = self._node_near_port_out(scene_pos)
            if result is not None:
                src, when = result
                self._drag_src = src
                self._drag_when = when
                src_pt = src.port_out_for(when)
                line = QGraphicsLineItem(src_pt.x(), src_pt.y(), src_pt.x(), src_pt.y())
                line.setPen(QPen(THEME.qcolor("port_color"), 2, Qt.PenStyle.DashLine))
                line.setZValue(10)
                self._temp_edge = line
                self.addItem(line)
                event.accept()
                return

            # Click-cycle: when multiple edges overlap at a point, repeated clicks
            # at the same location cycle through them for individual selection.
            hit = list(self.items(scene_pos))
            hit_nodes = [i for i in hit if isinstance(i, NodeItem)]
            hit_edges = [i for i in hit if isinstance(i, EdgeItem)]
            if not hit_nodes and len(hit_edges) > 1:
                # Prune stale references from previous cycles.
                self._cycle_edges = [e for e in self._cycle_edges if e.scene() is self]
                near = (
                    self._cycle_edges
                    and abs(scene_pos.x() - self._cycle_pos.x()) < 10
                    and abs(scene_pos.y() - self._cycle_pos.y()) < 10
                )
                if near:
                    self._cycle_idx = (self._cycle_idx + 1) % len(self._cycle_edges)
                else:
                    self._cycle_pos = scene_pos
                    self._cycle_edges = hit_edges
                    self._cycle_idx = 0
                self.clearSelection()
                self._cycle_edges[self._cycle_idx].setSelected(True)
                event.accept()
                return

        self._cycle_edges = []
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_src is not None and self._temp_edge is not None:
            src_pt = self._drag_src.port_out_for(self._drag_when)
            cur = event.scenePos()

            snap_edge = self._edge_near(cur)
            if snap_edge is not None and snap_edge._src is self._drag_src and snap_edge._when == self._drag_when:
                snap_edge = None
            if snap_edge is not self._drag_snap_edge:
                if self._drag_snap_edge is not None:
                    self._drag_snap_edge.set_snap_target(False)
                self._drag_snap_edge = snap_edge
                if self._drag_snap_edge is not None:
                    self._drag_snap_edge.set_snap_target(True)

            snap = self._drag_snap_edge
            end_pt = snap.dst_node.port_in_at(snap.dst_port) if snap is not None else cur
            self._temp_edge.setLine(src_pt.x(), src_pt.y(), end_pt.x(), end_pt.y())

            views = self.views()
            if views and not self._auto_scrolling:
                self._auto_scrolling = True
                _auto_scroll_view(views[0], cur)
                self._auto_scrolling = False
            event.accept()
            return
        if isinstance(self.mouseGrabberItem(), NodeItem):
            self._node_drag_scroll_pos = event.scenePos()
            if not self._scroll_timer.isActive():
                self._scroll_timer.start()
        super().mouseMoveEvent(event)

    def _on_scroll_timer(self) -> None:
        if self._node_drag_scroll_pos is None:
            self._scroll_timer.stop()
            return
        views = self.views()
        if not views:
            self._scroll_timer.stop()
            return
        view = views[0]
        vp_pos = view.mapFromScene(self._node_drag_scroll_pos)
        rect = view.viewport().rect()
        in_margin = (
            vp_pos.x() < _SCROLL_MARGIN
            or vp_pos.x() > rect.width() - _SCROLL_MARGIN
            or vp_pos.y() < _SCROLL_MARGIN
            or vp_pos.y() > rect.height() - _SCROLL_MARGIN
        )
        if in_margin:
            _auto_scroll_view(view, self._node_drag_scroll_pos)
        else:
            self._scroll_timer.stop()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._scroll_timer.stop()
        self._node_drag_scroll_pos = None
        if self._drag_src is not None:
            if self._temp_edge:
                self.removeItem(self._temp_edge)
                self._temp_edge = None
            if event.button() == Qt.MouseButton.LeftButton:
                if self._drag_snap_edge is not None:
                    self._drag_snap_edge.set_snap_target(False)
                snap = self._drag_snap_edge
                self._drag_snap_edge = None

                # For #73, which asks whether a lost repaint followed a snap or a port drop.
                hit = None if snap is not None else self._node_near_port_in(event.scenePos())
                logger.debug(
                    "Edge drop from %s (%s): %s",
                    self._drag_src.state_id,
                    self._drag_when,
                    "snap onto an incoming edge" if snap is not None else "onto a port" if hit else "into empty space",
                )
                if snap is not None:
                    self._create_transition(self._drag_src, snap.dst_node, self._drag_when, dst_port=snap.dst_port)
                else:
                    if hit is not None:
                        self._create_transition(self._drag_src, hit[0], self._drag_when, dst_port=hit[1])
                    elif (
                        self._config is not None
                        and self._config.is_definition
                        and not isinstance(self._drag_src, (FoldedNodeItem, PortNodeItem))
                        and self._drag_when is not None
                    ):
                        self._offer_new_exit(self._drag_src, self._drag_when, event.scenePos())
            else:
                if self._drag_snap_edge is not None:
                    self._drag_snap_edge.set_snap_target(False)
                self._drag_snap_edge = None
            self._drag_src = None
            self._drag_when = None
            event.accept()
            return
        super().mouseReleaseEvent(event)
        grabber = self.mouseGrabberItem()
        if grabber is not None and event.buttons() == Qt.MouseButton.NoButton:
            # #126: a grab outliving its release takes every later press.
            logger.debug(
                "Mouse grab survived release: %s (%s)",
                getattr(grabber, "state_id", None) or type(grabber).__name__,
                "in this scene" if grabber.scene() is self else "not in this scene",
            )
        self._flush_positions()

    def _offer_new_exit(self, src: NodeItem, when: str, scene_pos: QPointF) -> None:
        """A member's outcome dragged into empty space in a definition: offer to end it at a new Exit."""
        from PyQt6.QtWidgets import QMenu

        view = self.views()[0] if self.views() else None
        menu = popup_menu(view) if view is not None else QMenu()
        act = QAction("New exit port", menu)
        menu.addAction(act)
        chosen = menu.exec(view.mapToGlobal(view.mapFromScene(scene_pos))) if view is not None else None
        menu.deleteLater()
        if chosen is not act:
            return
        self._pre_edit("New Exit Port")
        self._in_structural_edit = True
        try:
            pid = self.add_port_node(SUBGRAPH_EXIT, QPointF(scene_pos.x(), scene_pos.y() - NODE_H / 2))
            node = self._node_items.get(pid) if pid else None
            if node is not None:
                self._create_transition(src, node, when)
        finally:
            self._in_structural_edit = False
        self.graph_changed.emit()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._interactive_mode:
            super().keyPressEvent(event)
            return
        ctrl = event.modifiers() == Qt.KeyboardModifier.ControlModifier
        if event.key() == Qt.Key.Key_Delete:
            self._delete_selected()
            return
        if ctrl and event.key() == Qt.Key.Key_C:
            self._copy_selected()
            return
        if ctrl and event.key() == Qt.Key.Key_X:
            self._copy_selected()
            self._delete_selected()
            return
        if ctrl and event.key() == Qt.Key.Key_V:
            self._paste()
            return
        super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        from PyQt6.QtGui import QTransform

        item = self.itemAt(event.scenePos(), self.views()[0].transform() if self.views() else QTransform())
        if isinstance(item, RegionItem):
            self.focus_region_requested.emit(item.region_id)
            return
        if isinstance(item, CollapsedRegionItem):
            # Stepping in, in either mode: the region stays collapsed behind the level.
            self.focus_region_requested.emit(item.state_id)
            return
        if isinstance(item, CallNodeItem):
            if self._interactive_mode:
                self.open_definition_requested.emit(item.state_id)
            else:
                self.view_instance_requested.emit(item.state_id)
            return
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def contextMenuEvent(self, event) -> None:
        if not self._interactive_mode:
            return
        from PyQt6.QtGui import QTransform
        from PyQt6.QtWidgets import QMenu

        view = self.views()[0] if self.views() else None
        item = self.itemAt(event.scenePos(), view.transform() if view is not None else QTransform())
        menu = popup_menu(view) if view is not None else QMenu()
        if isinstance(item, CallNodeItem):
            self._build_call_menu(menu, item)
        elif isinstance(item, CollapsedRegionItem):
            self._build_collapsed_menu(menu, item)
        elif isinstance(item, NodeItem):
            self._build_node_menu(menu, item, event.scenePos())
        elif isinstance(item, RegionItem):
            self._build_region_menu(menu, item, event.scenePos())
        elif isinstance(item, EdgeItem):
            act = QAction("Delete Transition", menu)
            act.triggered.connect(lambda: self._delete_edge_item(item))
            menu.addAction(act)
            if not isinstance(item.src_node, (FoldedNodeItem, PortNodeItem)) and not isinstance(
                item.dst_node, (FoldedNodeItem, PortNodeItem)
            ):
                act_ins = QAction("Insert State Here", menu)
                act_ins.triggered.connect(lambda: self._insert_state_on_edge(item))
                menu.addAction(act_ins)
        else:
            scene_pos = event.scenePos()
            at = QPointF(scene_pos.x() - NODE_W / 2, scene_pos.y() - NODE_H / 2)
            act = QAction("Add State Here", menu)
            act.triggered.connect(lambda: self.add_state(at))
            menu.addAction(act)
            if self._config is not None and self._config.is_definition:
                menu.addSeparator()
                act_in = QAction("Add Entry", menu)
                act_in.triggered.connect(lambda: self.add_port_node(SUBGRAPH_ENTRY, at))
                menu.addAction(act_in)
                act_out = QAction("Add Exit", menu)
                act_out.triggered.connect(lambda: self.add_port_node(SUBGRAPH_EXIT, at))
                menu.addAction(act_out)
            self._add_create_region_action(menu)
            to_collapse, to_expand = self.bulk_collapse_targets(True), self.bulk_collapse_targets(False)
            if to_collapse or to_expand:
                menu.addSeparator()
                act_ca = QAction("Collapse All Regions", menu)
                act_ca.setEnabled(bool(to_collapse))
                act_ca.triggered.connect(lambda: self.collapse_all_requested.emit(True))
                menu.addAction(act_ca)
                act_ea = QAction("Expand All Regions", menu)
                act_ea.setEnabled(bool(to_expand))
                act_ea.triggered.connect(lambda: self.collapse_all_requested.emit(False))
                menu.addAction(act_ea)
        if not menu.isEmpty():
            menu.exec(event.screenPos())
        menu.deleteLater()

    def _add_create_region_action(self, menu) -> None:
        selected_nodes = [i for i in self.selectedItems() if isinstance(i, NodeItem)]
        if len(selected_nodes) >= 2:
            menu.addSeparator()
            n = len(selected_nodes)
            ids = self.state_ids_of(nd.state_id for nd in selected_nodes)
            act_cr = QAction(f"Create Region from {n} selected states", menu)
            act_cr.triggered.connect(lambda: self._region_manager.create_region(ids))
            menu.addAction(act_cr)

    def _build_call_menu(self, menu, item: CallNodeItem) -> None:
        """A subgraph instance's verbs: open its definition, detach it, delete it -- plus region membership."""
        rid = item.state_id
        act_open = QAction("Open Definition", menu)
        act_open.triggered.connect(lambda: self.open_definition_requested.emit(rid))
        menu.addAction(act_open)
        act_detach = QAction("Detach from Library", menu)
        act_detach.triggered.connect(lambda: self.detach_region(rid))
        menu.addAction(act_detach)
        act_del = QAction("Delete Subgraph", menu)
        act_del.triggered.connect(lambda: self.remove_instance(rid))
        menu.addAction(act_del)
        self._add_region_membership_actions(menu, item)

    def _build_collapsed_menu(self, menu, item: CollapsedRegionItem) -> None:
        """A collapsed region's verbs: the region's own, plus expanding it and deleting what it holds."""
        rid = item.state_id
        region = self._region_by_id(rid)
        n = len(region.members) if region is not None else 0
        act_expand = QAction("Expand Region", menu)
        act_expand.triggered.connect(lambda: self.set_region_collapsed(rid, False))
        menu.addAction(act_expand)
        menu.addSeparator()
        act_rn = QAction("Rename Region", menu)
        act_rn.triggered.connect(lambda: self._rename_region(rid))
        menu.addAction(act_rn)
        act_rc = QAction("Change Color", menu)
        act_rc.triggered.connect(lambda: self._recolor_region(rid))
        menu.addAction(act_rc)
        menu.addSeparator()
        act_save = QAction("Save Region as Reusable Subgraph…", menu)
        act_save.triggered.connect(lambda: self.save_region_as_subgraph_requested.emit(rid))
        menu.addAction(act_save)
        act_rm = QAction("Remove Region", menu)
        act_rm.triggered.connect(lambda: self._remove_region(rid))
        menu.addAction(act_rm)
        act_del = QAction(f"Delete Region and {n} Node{'' if n == 1 else 's'}", menu)
        act_del.triggered.connect(lambda: self.remove_instance(rid))
        menu.addAction(act_del)
        self._add_region_membership_actions(menu, item)

    def _rename_region(self, region_id: str) -> None:
        self._region_manager.rename_region(region_id)
        self.refresh_call_nodes()

    def _recolor_region(self, region_id: str) -> None:
        self._region_manager.recolor_region(region_id)
        self.refresh_call_nodes()

    def _remove_region(self, region_id: str) -> None:
        """Drop the grouping only; a collapsed region's members come back into view (``_after_region_edit``)."""
        self._region_manager.delete_region(region_id)

    def _folded_signature(self) -> tuple:
        if self._config is None:
            return ()
        return tuple(
            (r.id, frozenset(r.members)) for r in folded_regions(self._config, self._expanded, self._collapse_overrides)
        )

    def _after_region_edit(self) -> None:
        """What a region edit reports: rebuilt first when it changed what is drawn folded.

        Adding a node to a collapsed region folds it away, and removing the region or
        a member brings nodes back; the region manager edits the model only.
        """
        if self._config is not None and self._folded_signature() != self._folded_sig:
            prev = self._in_structural_edit
            self._in_structural_edit = True
            try:
                self.setup(self._config)
            finally:
                self._in_structural_edit = prev
        else:
            self._sync_region_buttons()  # a new region gets its button; a changed one its blocker
        self.graph_changed.emit()

    def _build_node_menu(self, menu, item: NodeItem, scene_pos: QPointF) -> None:
        sid = item.state_id
        is_port = isinstance(item, PortNodeItem)
        # Ports are painted on the node, so itemAt() has already resolved to the
        # NodeItem -- the port under the cursor has to be hit-tested separately,
        # with the same helpers and radius the drag-to-connect path uses so the
        # target areas agree.  Output first, matching mousePressEvent.
        port_out_hit = self._node_near_port_out(scene_pos)
        port_in_hit = self._node_near_port_in(scene_pos)
        if not is_port and port_out_hit is not None and not isinstance(port_out_hit[0], FoldedNodeItem):
            port_node, port_return = port_out_hit
            label = f"Insert State at {port_return} Port" if port_return else "Insert State at Output Port"
            act_port = QAction(label, menu)
            act_port.triggered.connect(
                lambda _=None, s=port_node.state_id, r=port_return: self._prompt_insert_at_output_port(s, r)
            )
            menu.addAction(act_port)
            self._add_stop_actions(menu, port_node.state_id, port_return)
            if not menu.isEmpty():
                menu.addSeparator()
        elif not is_port and port_in_hit is not None and not isinstance(port_in_hit[0], FoldedNodeItem):
            act_port = QAction("Insert State at Input Port", menu)
            act_port.triggered.connect(lambda _=None, s=port_in_hit[0].state_id: self._prompt_insert_at_input_port(s))
            menu.addAction(act_port)
            menu.addSeparator()
        act = QAction(
            "Delete Entry" if is_port and item.is_entry else ("Delete Exit" if is_port else "Delete State"), menu
        )
        act.triggered.connect(lambda: self._delete_node(item.state_id))
        menu.addAction(act)
        if not is_port:
            act_help = QAction("Node Help", menu)
            act_help.triggered.connect(lambda _=None, s=sid: self._open_node_help(s))
            menu.addAction(act_help)
        self._add_region_membership_actions(menu, item)
        self._add_create_region_action(menu)

    def _add_region_membership_actions(self, menu, item: NodeItem) -> None:
        """Add to / Remove from each plain region, for the clicked node or the multi-selection it is in."""
        selected_nodes = [i for i in self.selectedItems() if isinstance(i, NodeItem)]
        if len(selected_nodes) >= 2 and item in selected_nodes:
            display_ids = [n.state_id for n in selected_nodes]
        else:
            display_ids = [item.state_id]
        target_ids = self.state_ids_of(display_ids)
        is_multi = len(display_ids) > 1
        # A linked region's membership is the library's, so it is offered on neither side.
        regions = [r for r in (self._config.regions if self._config else []) if r.source is None]
        if not regions:
            return
        menu.addSeparator()
        for region in regions:
            not_members = [i for i in target_ids if i not in region.members]
            in_members = [i for i in target_ids if i in region.members]
            rid = region.id
            rlabel = region.label
            if not_members:
                label = f"Add {len(display_ids)} nodes to Region: {rlabel}" if is_multi else f"Add to Region: {rlabel}"
                act_add = QAction(label, menu)
                act_add.triggered.connect(
                    lambda _=None, ids=not_members, r=rid: self._region_manager.add_to_region(ids, r)
                )
                menu.addAction(act_add)
            if in_members:
                label = (
                    f"Remove {len(display_ids)} nodes from Region: {rlabel}"
                    if is_multi
                    else f"Remove from Region: {rlabel}"
                )
                act_rem = QAction(label, menu)
                act_rem.triggered.connect(
                    lambda _=None, ids=in_members, r=rid: self._region_manager.remove_from_region(ids, r)
                )
                menu.addAction(act_rem)

    def _add_stop_actions(self, menu, state_id: str, return_name: str | None) -> None:
        """Offer to make this output port end the run, or to undo that.

        Only on a port with no edge on it. A stop outranks every transition the
        engine might otherwise fire, so offering it on a routed port would be a way
        to disable an edge that is still drawn -- the confusion this whole feature
        exists to remove.
        """
        if self._config is None or return_name is None:
            return
        state = next((st for st in self._config.states if st.id == state_id), None)
        if state is None:
            return
        if state.stop_reason(return_name) is not None:
            act_clear = QAction(f"Clear Stop on {return_name} Port", menu)
            act_clear.triggered.connect(lambda _=None: self._set_port_stop(state_id, return_name, None))
            menu.addAction(act_clear)
            return
        if any(t.source == state_id and (t.when == return_name or t.when is None) for t in self._config.transitions):
            return
        act_stop = QAction(f"Stop Bot on {return_name}…", menu)
        act_stop.triggered.connect(lambda _=None: self._prompt_port_stop(state_id, return_name))
        menu.addAction(act_stop)

    def _prompt_port_stop(self, state_id: str, return_name: str) -> None:
        """Ask for the reason shown to the user, then mark the port a stop.

        Cancel means cancel; an empty reason is accepted, since a port can be fatal
        for a reason too obvious to write down and refusing the OK would only teach
        authors to type a space.
        """
        parent = self.views()[0] if self.views() else None
        reason, ok = QInputDialog.getText(
            parent,
            "Stop Bot",
            f"Why does {return_name} end the run?\nThis is shown to the user when it does.",
            QLineEdit.EchoMode.Normal,
            "",
        )
        if not ok:
            return
        self._set_port_stop(state_id, return_name, reason.strip())

    def _set_port_stop(self, state_id: str, return_name: str, reason: str | None) -> None:
        """Add or remove *return_name* from a state's stops. None removes it."""
        if self._config is None:
            return
        state = next((st for st in self._config.states if st.id == state_id), None)
        if state is None:
            return
        self._pre_edit("Clear Stop" if reason is None else "Set Stop")
        stops = dict(state.stops or {})
        if reason is None:
            stops.pop(return_name, None)
        else:
            stops[return_name] = reason
        # Back to None when empty so the key stays out of the saved file, matching
        # what StateModel.stops documents.
        state.stops = stops or None
        self.refresh_port_marks()
        self.refresh_errors()
        self.graph_changed.emit()

    def _build_region_menu(self, menu, item: RegionItem, scene_pos: QPointF) -> None:
        rid = item.region_id
        add_pos = QPointF(scene_pos.x() - NODE_W / 2, scene_pos.y() - NODE_H / 2)
        act_add = QAction("Add State Here", menu)

        def _add_state_in_region(_=None, rid=rid, pos=add_pos):
            from bartlebot.gui.canvas.node_browser_dialog import NodeBrowserDialog

            if self._config is None:
                return
            view = self.views()[0] if self.views() else None
            dlg = NodeBrowserDialog(view, mode="add", subgraph_library=self._subgraph_library)
            if dlg.exec() and dlg.selected_node_type:
                chosen = dlg.selected_node_type
                if chosen.startswith(SUBGRAPH_KEY_PREFIX):
                    self.insert_subgraph_instance(chosen[len(SUBGRAPH_KEY_PREFIX) :], pos, into_region=rid)
                    return
                state_id = self.create_state_at(chosen, pos)
                if state_id:
                    self._region_manager.add_to_region([state_id], rid)

        act_add.triggered.connect(_add_state_in_region)
        menu.addAction(act_add)
        menu.addSeparator()
        act_rn = QAction("Rename Region", menu)
        act_rn.triggered.connect(lambda: self._region_manager.rename_region(rid))
        menu.addAction(act_rn)
        act_rc = QAction("Change Color", menu)
        act_rc.triggered.connect(lambda: self._region_manager.recolor_region(rid))
        menu.addAction(act_rc)
        act_col = QAction("Collapse Region", menu)
        blocker = self.collapse_blocker(rid)
        if blocker is not None:
            act_col.setEnabled(False)
            act_col.setToolTip(blocker)
            menu.setToolTipsVisible(True)
        act_col.triggered.connect(lambda: self.set_region_collapsed(rid, True))
        menu.addAction(act_col)
        menu.addSeparator()
        act_save = QAction("Save Region as Reusable Subgraph…", menu)
        act_save.triggered.connect(lambda: self.save_region_as_subgraph_requested.emit(rid))
        menu.addAction(act_save)
        act_del = QAction("Remove Region", menu)
        act_del.triggered.connect(lambda: self._region_manager.delete_region(rid))
        menu.addAction(act_del)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _open_node_help(self, state_id: str) -> None:
        """Open the node browser in view mode pre-selected on the given state's node type."""
        from bartlebot.gui.canvas.node_browser_dialog import NodeBrowserDialog

        if self._config is None:
            return
        state = next((s for s in self._config.states if s.id == state_id), None)
        if state is None:
            return
        view = self.views()[0] if self.views() else None
        NodeBrowserDialog(view, mode="view", initial_node_type=state.node_type).exec()

    def _node_near_port_out(self, pos: QPointF) -> tuple[NodeItem, str | None] | None:
        for node in self._node_items.values():
            if not node.has_output_port:
                continue
            if node._returns:
                for name in node._returns:
                    if _dist(pos, node.port_out_for(name)) <= _PORT_HIT_RADIUS:
                        return (node, name)
            else:
                if _dist(pos, node.port_out()) <= _PORT_HIT_RADIUS:
                    return (node, None)
        return None

    def _node_near_port_in(self, pos: QPointF) -> tuple[NodeItem, str | None] | None:
        """The node and input port nearest ``pos`` within reach: ``(node, None)`` for an ordinary node."""
        for node in self._node_items.values():
            for port in node.input_ports():
                if _dist(pos, node.port_in_at(port)) <= _PORT_HIT_RADIUS:
                    return (node, port)
        return None

    def _edge_near(self, pos: QPointF) -> EdgeItem | None:
        """Return the first EdgeItem whose hit-shape (12 px stroke) contains pos."""
        for item in self.items(pos):
            if isinstance(item, EdgeItem):
                return item
        return None

    def _recompute_offsets(self) -> None:
        """Recompute forward-edge mid_x offsets for all (source, dest) groups.

        Handles bidirectional separation and N-parallel-edge spreading.  Also
        calls _rebuild() on every edge so fan arrival-port positions (option A) and
        Z-channel positions (option C) are both updated in one pass.  Self-loops
        take no offset, but they still need the rebuild: an edge rewired into a
        loop keeps drawing its old two-node path otherwise.
        """
        PARALLEL_STEP = 22.0
        sd_groups: dict[tuple[str, str], list] = defaultdict(list)
        for s, w, d, edge in self._edge_items:
            if s != d:
                sd_groups[(s, d)].append((s, w, d, edge))
            else:
                edge._rebuild()
        non_loop_pairs = {(s, d) for s, _w, d, _ in self._edge_items if s != d}
        for (s, d), group in sd_groups.items():
            group_sorted = sorted(group, key=lambda x: x[1] or "")
            n = len(group_sorted)
            is_bidi = (d, s) in non_loop_pairs
            bidi_base = (PARALLEL_STEP if s < d else -PARALLEL_STEP) if is_bidi else 0.0
            for i, (_s, _w, _d, edge) in enumerate(group_sorted):
                edge._offset = bidi_base + (i - (n - 1) / 2) * PARALLEL_STEP
                edge._rebuild()

    def _flat_endpoints(
        self, src_node: NodeItem, dst_node: NodeItem, when: str | None, dst_port: str | None
    ) -> tuple[list[tuple[str, str | None]], str] | None:
        """The flat ``[(source, when)]`` and destination a drawn edge between two items stands for.

        From a folded node, the pairs feeding the exit ``when`` names; into one, the
        member its entry ``dst_port`` is reached by. None when there is nothing to wire.
        """
        if isinstance(dst_node, FoldedNodeItem):
            ports = self._fold_ports.get(dst_node.state_id)
            member = ports.entry_member(dst_port) if ports is not None else None
            if member is None:
                return None
            dest = member
        else:
            dest = dst_node.state_id
        if isinstance(src_node, FoldedNodeItem):
            ports = self._fold_ports.get(src_node.state_id)
            pairs = ports.exit_feeds(when) if ports is not None else []
            if not pairs:
                return None
        else:
            pairs = [(src_node.state_id, when)]
        return pairs, dest

    def _create_transition(
        self, src_node: NodeItem, dst_node: NodeItem, when: str | None = None, *, dst_port: str | None = None
    ) -> None:
        """Draw an edge between two items, and add the flat transitions it stands for.

        An edge from a call node's exit is one flat transition per ``(member, outcome)``
        feeding the exit; into a call node, it lands on the member its entry leads to.
        An Entry has exactly one edge, so drawing another from it replaces the first;
        nothing leads into an Entry or out of an Exit.
        """
        if self._config is None:
            return
        if not src_node.has_output_port or not dst_node.input_ports():
            return
        if isinstance(dst_node, FoldedNodeItem) and dst_port not in dst_node.input_ports():
            dst_port = dst_node.input_ports()[0] if dst_node.input_ports() else None
        if isinstance(dst_node, PortNodeItem) and isinstance(src_node, PortNodeItem):
            return  # an Entry straight into an Exit wires nothing
        flat = self._flat_endpoints(src_node, dst_node, when, dst_port)
        if flat is None:
            logger.info("Nothing to wire from %s (%s): the port has nothing leading to it", src_node.state_id, when)
            return
        pairs, dest = flat
        new = [
            (s, w)
            for s, w in pairs
            if not any(t.source == s and t.dest == dest and t.when == w for t in self._config.transitions)
        ]
        if not new:
            return
        self._pre_edit("Create Edge")
        prev = self._in_structural_edit
        self._in_structural_edit = True
        try:
            if isinstance(src_node, PortNodeItem):
                for _s, _w, _d, ei in [r for r in self._edge_items if r[0] == src_node.state_id]:
                    self._delete_edge_item(ei, confirm=False)
            models = [TransitionModel(trigger="advance", source=s, dest=dest, when=w) for s, w in new]
            self._config.transitions.extend(models)
            existing = next(
                (
                    ei
                    for s, w, d, ei in self._edge_items
                    if s == src_node.state_id and w == when and d == dst_node.state_id and ei.dst_port == dst_port
                ),
                None,
            )
            if existing is not None:
                self._edge_models[existing].extend(models)
            else:
                self._add_edge_item(src_node.state_id, when, dst_node.state_id, dst_port, models)
        finally:
            self._in_structural_edit = prev
        self._recompute_offsets()
        self._update_scene_rect()
        self.refresh_terminal()
        self.refresh_port_marks()
        self.refresh_errors()
        self._refresh_folds()
        self.graph_changed.emit()

    def _refresh_folds(self) -> None:
        """Redraw when an edit changed what a collapsed region's node shows.

        Its ports are inferred from the edges crossing it, so an edge added, deleted or
        rewired there can add, drop or rename one; and a node moved can open or close a
        column of the fold. Either rebuilds the scene from the config, as undo does.
        Nothing is done, cheaply, while no region is collapsed.
        """
        self._sync_region_buttons()
        if self._config is None or not any(self.is_collapsed(rid) for rid in self._fold_ports):
            return
        ports = {rid: self._ports_for(r) for rid in self._fold_ports if (r := self._region_by_id(rid)) is not None}
        if ports != self._fold_ports:
            self.setup(self._config)
            return
        placed = []
        for did, node in self._node_items.items():
            region = self._region_by_id(did) if self.is_folded(did) else None
            if region is not None:
                anchor = region_anchor(self._config, region)
                placed.append((node, float(anchor["x"]), float(anchor["y"])))
                continue
            state = next((s for s in self._config.states if s.id == did), None)
            if state is not None and state.position is not None:
                placed.append((node, float(state.position["x"]), float(state.position["y"])))
        folded = [r for rid in self._fold_ports if (r := self._region_by_id(rid)) is not None]
        fold = self._build_fold(placed, folded)
        if fold == self._fold:
            return
        self._fold = fold
        for node, x, y in placed:
            dx, dy = fold.to_display(x, y)
            node.setPos(dx, dy - node.body_height / 2)
        self.note_node_moved()  # a node without ItemSendsGeometryChanges never reports its own move
        self._update_scene_rect()

    def _copy_selected(self) -> None:
        if self._config is None:
            return
        display_ids = [item.state_id for item in self.selectedItems() if isinstance(item, NodeItem)]
        if not display_ids:
            return
        selected_ids = set(self.state_ids_of(display_ids))
        states = [s.model_dump() for s in self._config.states if s.id in selected_ids]
        transitions = [
            t.model_dump() for t in self._config.transitions if t.source in selected_ids and t.dest in selected_ids
        ]
        # A selected call node carries its whole instance -- the outermost ones only,
        # since a nested instance composes through its outer.
        whole = [r for r in linked_regions(self._config) if r.members and set(r.members) <= selected_ids]
        regions = [r.model_dump() for r in whole if not any(is_nested_in(r, o) for o in whole)]
        # So does a selected collapsed region, which pastes collapsed; an expanded plain
        # region still does not travel.
        regions += [
            r.model_dump()
            for r in folded_regions(self._config, self._expanded, self._collapse_overrides)
            if r.source is None and set(r.members) <= selected_ids
        ]
        self._clipboard = (states, transitions, regions)

    def _paste(self) -> None:
        if self._config is None or self._clipboard is None:
            return
        self._pre_edit("Paste")
        self._in_structural_edit = True
        states_data, transitions_data, regions_data = self._clipboard

        # Determine (offset_x, offset_y) to apply to every pasted node's stored position.
        # Clipboard positions: p["x"] = node left edge, p["y"] = node center y (StateModel convention).
        # A copied instance is placed by its anchor -- its call node -- not by its members.
        # Everything here is in stored positions; the cursor and the items are drawn
        # through the fold, and are mapped back before they are compared.
        linked_data = [rd for rd in regions_data if rd.get("source")]
        member_ids = {m for rd in linked_data for m in rd["members"]}
        positions = [sd["position"] for sd in states_data if sd.get("position") and sd["id"] not in member_ids]
        positions += [rd["source"].get("anchor") for rd in linked_data if rd["source"].get("anchor")]

        offset_x = 0.0
        offset_y = 0.0
        if positions:
            view = self.views()[0] if self.views() else None
            cursor_in_vp = False
            if view:
                cursor_local = view.viewport().mapFromGlobal(QCursor.pos())
                cursor_in_vp = view.viewport().rect().contains(cursor_local)
                if cursor_in_vp:
                    cursor_scene = view.mapToScene(view.mapFromGlobal(QCursor.pos()))
                    cursor_x, cursor_y = self._fold.to_stored(cursor_scene.x(), cursor_scene.y())
                    centroid_x = sum(p["x"] for p in positions) / len(positions)
                    centroid_y = sum(p["y"] for p in positions) / len(positions)
                    offset_x = cursor_x - centroid_x
                    offset_y = cursor_y - centroid_y

            if not cursor_in_vp:
                # Fallback: start one grid row below the source nodes, preserving X.
                offset_y = GRID_H

            # Shift offset_y downward until no pasted node collides with an existing one.
            # Grid cell key: (col based on left edge, row based on center y) — same as snap formula.
            occupied = {
                (round(p["x"] / GRID_W), round(p["y"] / GRID_H))
                for item in self._node_items.values()
                if (p := self.stored_position(item.pos(), item.body_height))
            }
            # A collapsed region's members are out of sight but not out of the layout:
            # a node pasted onto one would sit on it once the region is expanded.
            for s in self._config.states:
                if s.position is not None and self.is_collapsed(self.display_id(s.id)):
                    occupied.add((round(float(s.position["x"]) / GRID_W), round(float(s.position["y"]) / GRID_H)))
            for _ in range(50):
                if not any(
                    (round((p["x"] + offset_x) / GRID_W), round((p["y"] + offset_y) / GRID_H)) in occupied
                    for p in positions
                ):
                    break
                offset_y += GRID_H

        if regions_data:
            self._paste_with_instances(states_data, transitions_data, regions_data, offset_x, offset_y)
            return

        # Build old_id → new_id mapping
        id_map: dict[str, str] = {}
        existing_ids = [s.id for s in self._config.states]
        for sd in states_data:
            new_id = self.mint_state_id(existing_ids)
            id_map[sd["id"]] = new_id
            existing_ids.append(new_id)

        # Create new StateModels and NodeItems
        for sd in states_data:
            new_sd = dict(sd)
            new_sd["id"] = id_map[sd["id"]]
            if new_sd.get("position"):
                new_sd["position"] = {
                    "x": new_sd["position"]["x"] + offset_x,
                    "y": new_sd["position"]["y"] + offset_y,
                }
            state = StateModel.model_validate(new_sd)
            if state.node_type in PORT_NODE_TYPES and not self._config.is_definition:
                continue  # a bot has no ports: pasting one into it drops it
            self._config.states.append(state)
            node = self._make_node(state, is_terminal=True)
            node.set_interactive(True)
            if new_sd.get("position"):
                node.setPos(self.drawn_position(new_sd["position"]))
            self.addItem(node)
            self._node_items[state.id] = node

        # Re-create internal transitions (_create_transition calls refresh_terminal/errors)
        for td in transitions_data:
            src_node = self._node_items.get(id_map[td["source"]])
            dst_node = self._node_items.get(id_map[td["dest"]])
            if src_node is not None and dst_node is not None:
                self._create_transition(src_node, dst_node, when=td.get("when"))

        # Ensure terminal/error badges are correct even with no internal transitions
        self.refresh_terminal()
        self.refresh_port_marks()
        self.refresh_errors()

        # Select the pasted nodes
        self.clearSelection()
        for new_id in id_map.values():
            if new_id in self._node_items:
                self._node_items[new_id].setSelected(True)

        self._refresh_folds()
        self._update_scene_rect()
        view = self.views()[0] if self.views() else None
        if view is not None:
            new_nodes = [self._node_items[nid] for nid in id_map.values() if nid in self._node_items]
            if new_nodes:
                view.smooth_pan_if_offscreen(new_nodes[0])
        self._in_structural_edit = False
        self.graph_changed.emit()

    def _paste_with_instances(
        self,
        states_data: list[dict],
        transitions_data: list[dict],
        regions_data: list[dict],
        offset_x: float,
        offset_y: float,
    ) -> None:
        """Paste a clipboard that carries at least one whole subgraph instance or collapsed region.

        Each copied instance becomes a *new* instance: a fresh region id, the same
        inputs, the anchor translated with the paste, and members composed from the
        library so their ids derive from the new region id and never from
        ``_unique_id``. When the library cannot supply the definition, the clipboard's
        own snapshot is renamed onto the new region id instead, so the paste still
        works and the new call node flags ``missing`` at the next compose like any other.

        States outside every copied instance paste as ordinary states, and a copied
        collapsed region comes back around its pasted members, still collapsed, under a
        new id. The scene is rebuilt from the config afterwards rather than grown item
        by item -- the same path undo takes.
        """
        assert self._config is not None
        config = self._config
        library = self._subgraph_library
        id_map: dict[str, str] = {}
        instance_of: dict[str, str] = {}  # new member id -> new region id
        new_states: list[StateModel] = []
        new_transitions: list[TransitionModel] = []
        new_regions: list[RegionModel] = []
        new_rids: list[str] = []

        def moved(pos: dict | None) -> dict | None:
            return {"x": float(pos["x"]) + offset_x, "y": float(pos["y"]) + offset_y} if pos else None

        taken = taken_ids(config)
        for rd in regions_data:
            if not rd.get("source"):
                continue  # a collapsed plain region: rebuilt around its members below
            old_rid = rd["id"]
            source = SubgraphSource.model_validate(rd["source"])
            defn = library.get(source.subgraph_id) if library is not None else None
            new_rid = mint_instance_id(
                defn.name if defn is not None else rd["label"], taken | self.reserved_ids, self.instance_id_prefix
            )
            taken.add(new_rid)
            new_rids.append(new_rid)
            source.anchor = moved(source.anchor) or {"x": offset_x, "y": offset_y}
            member_data = [sd for sd in states_data if sd["id"] in set(rd["members"])]
            locals_ = {sd["id"]: (local_id(old_rid, sd["id"]) or sd["id"]) for sd in member_data}
            for sd in member_data:
                id_map[sd["id"]] = member_id(new_rid, locals_[sd["id"]])
            mat = None
            if defn is not None:
                try:
                    mat = materialize_region(new_rid, source, defn, library)
                    source.ports = mat.ports
                    source.version = defn.version
                    new_states.extend(mat.states)
                    new_transitions.extend(mat.transitions)
                    new_regions.append(
                        RegionModel(
                            id=new_rid,
                            label=rd["label"],
                            members=list(mat.member_ids),
                            color=rd.get("color", 1),
                            source=source,
                        )
                    )
                    new_regions.extend(mat.regions)
                    for mid in mat.member_ids:
                        instance_of[mid] = new_rid
                except (SubgraphCycleError, LookupError) as exc:
                    logger.warning("Pasting subgraph %s from its clipboard snapshot: %s", source.subgraph_id, exc)
                    mat = None
            if mat is None:
                ids = set(locals_)
                for sd in member_data:
                    new_states.append(
                        StateModel.model_validate({**sd, "id": id_map[sd["id"]], "position": moved(sd.get("position"))})
                    )
                    instance_of[id_map[sd["id"]]] = new_rid
                for td in transitions_data:
                    if td["source"] in ids and td["dest"] in ids:
                        new_transitions.append(
                            TransitionModel.model_validate(
                                {**td, "source": id_map[td["source"]], "dest": id_map[td["dest"]]}
                            )
                        )
                new_regions.append(
                    RegionModel(
                        id=new_rid,
                        label=rd["label"],
                        members=[id_map[sd["id"]] for sd in member_data],
                        color=rd.get("color", 1),
                        source=source,
                    )
                )

        # Everything outside a copied instance is an ordinary paste.
        existing_ids = [st.id for st in config.states] + [st.id for st in new_states]
        for sd in states_data:
            if sd["id"] in id_map:
                continue
            if sd.get("node_type") in PORT_NODE_TYPES and not config.is_definition:
                continue
            new_id = self.mint_state_id(existing_ids)
            existing_ids.append(new_id)
            id_map[sd["id"]] = new_id
            new_states.append(StateModel.model_validate({**sd, "id": new_id, "position": moved(sd.get("position"))}))

        new_ids = {st.id for st in new_states}
        for td in transitions_data:
            src, dst = id_map.get(td["source"]), id_map.get(td["dest"])
            if src is None or dst is None or src not in new_ids or dst not in new_ids:
                continue  # a member the library version no longer has
            if instance_of.get(src) is not None and instance_of.get(src) == instance_of.get(dst):
                continue  # internal to an instance: the composition supplied it
            new_transitions.append(TransitionModel.model_validate({**td, "source": src, "dest": dst}))

        taken_rids = {r.id for r in config.regions} | {r.id for r in new_regions}
        for rd in regions_data:
            if rd.get("source"):
                continue
            members = [id_map[m] for m in rd["members"] if id_map.get(m) in new_ids]
            if len(members) < 2:
                continue
            rid = uuid.uuid4().hex[:8]
            while rid in taken_rids:
                rid = uuid.uuid4().hex[:8]
            taken_rids.add(rid)
            new_rids.append(rid)
            new_regions.append(
                RegionModel(id=rid, label=rd["label"], members=members, color=rd.get("color", 1), collapsed=True)
            )

        config.states.extend(new_states)
        config.transitions.extend(new_transitions)
        config.regions.extend(new_regions)

        self.setup(config)
        self.clearSelection()
        for did in dict.fromkeys(self.display_id(nid) for nid in new_ids):
            node = self._node_items.get(did)
            if node is not None:
                node.setSelected(True)
        view = self.views()[0] if self.views() else None
        first = self._node_items.get(new_rids[0]) if new_rids else None
        if view is not None and first is not None:
            view.smooth_pan_if_offscreen(first)
        self._in_structural_edit = False
        self.graph_changed.emit()

    def _delete_selected(self) -> None:
        if self._config is None:
            return
        selected = list(self.selectedItems())
        if not selected:
            return
        nodes = [item for item in selected if isinstance(item, NodeItem) and not isinstance(item, FoldedNodeItem)]
        calls = [item for item in selected if isinstance(item, FoldedNodeItem)]
        edges = [item for item in selected if isinstance(item, EdgeItem)]
        if not nodes and not calls and not edges:
            return

        # Single item — keep the original per-item dialogs (show node label / edge endpoints).
        if len(nodes) + len(calls) + len(edges) == 1:
            if calls:
                self.remove_instance(calls[0].state_id)
            elif nodes:
                self._delete_node(nodes[0].state_id)
            elif edges:
                self._delete_edge_item(edges[0])
            return

        # Multiple items — one bulk confirmation.
        node_ids = {n.state_id for n in nodes}
        display_ids = node_ids | {c.state_id for c in calls}
        deleted = set(self.state_ids_of(display_ids))
        # Every edge on a deleted node, and every edge crossing into a deleted instance --
        # never the edges inside one, which are not the user's to count.
        implicit_transition_count = sum(
            1
            for t in self._config.transitions
            if t.source in node_ids or t.dest in node_ids or (t.source in deleted) != (t.dest in deleted)
        )
        # Edges whose endpoints are not being deleted need separate confirmation in the count.
        standalone_edges = [
            e
            for e in edges
            if not any(s in display_ids or d in display_ids for s, _w, d, ei in self._edge_items if ei is e)
        ]
        total_transitions = implicit_transition_count + len(standalone_edges)

        parts = []
        n_states = len(nodes)
        if n_states:
            parts.append(f"{n_states} state{'s' if n_states != 1 else ''}")
        instances = [c for c in calls if isinstance(c, CallNodeItem)]
        collapsed = [c for c in calls if isinstance(c, CollapsedRegionItem)]
        if instances:
            parts.append(f"{len(instances)} subgraph{'s' if len(instances) != 1 else ''}")
        if collapsed:
            held = sum(len(r.members) for c in collapsed if (r := self._region_by_id(c.state_id)) is not None)
            parts.append(
                f"{len(collapsed)} collapsed region{'s' if len(collapsed) != 1 else ''} "
                f"({held} node{'s' if held != 1 else ''})"
            )
        if total_transitions:
            parts.append(f"{total_transitions} transition{'s' if total_transitions != 1 else ''}")
        summary = " and ".join(parts) if parts else "selection"

        answer = QMessageBox.question(
            self.views()[0] if self.views() else None,
            "Delete Selection",
            f"Delete {summary}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._pre_edit("Delete Selection")
        self._in_structural_edit = True
        try:
            for edge in standalone_edges:
                self._delete_edge_item(edge, confirm=False)
            for node in nodes:
                self._delete_node(node.state_id, confirm=False)
            for call in calls:
                self.remove_instance(call.state_id, confirm=False)
        finally:
            self._in_structural_edit = False
        self.graph_changed.emit()

    def delete_state(self, state_id: str, *, confirm: bool = True) -> bool:
        """Delete a state and its edges. Returns False if the user cancels the prompt."""
        return self._delete_node(state_id, confirm=confirm)

    def _reassign_orphaned_roles(self) -> None:
        """Move ``initial`` / ``recovery_state`` off a state that no longer exists.

        Both are required and validated against the states list, so a dangling one
        produces a config that saves happily and then refuses to load.  Deleting the
        initial state has always been able to do this; the state-role checkboxes made the recovery state
        as easy to delete, which is what brought it to the surface.

        The first remaining state is an arbitrary choice, but it is a *valid* one, and
        the badge moving to it is visible -- unlike the corrupted file, which is not.
        An empty graph holds neither role, and both are cleared: that is a blank bot,
        the one config that loads without them. The same pass is what gives both to the
        first node put on a blank bot's canvas (``settle_roles``).

        When the first state is inside a subgraph node, the roles go to that node's
        first entry port, since a role on a subgraph node is always on a port.
        """
        if self._config is None:
            return
        if not self._config.states:
            if not self._config.is_definition:
                self._config.initial = self._config.recovery_state = ""
            return
        ids = {s.id for s in self._config.states}
        fallback = self._config.states[0].id
        if self._config.is_definition:
            fallback = next((s.id for s in self._config.states if s.node_type == SUBGRAPH_ENTRY), fallback)
        else:
            region = self.linked_region_of(fallback)
            entries = [p.member for p in region.source.ports.values() if p.member] if region is not None else []
            if entries and member_id(region.id, entries[0]) in ids:
                fallback = member_id(region.id, entries[0])
        if self._config.initial not in ids:
            self._config.initial = fallback
        if self._config.recovery_state not in ids:
            self._config.recovery_state = fallback

    def settle_roles(self) -> None:
        """Give ``initial`` and ``recovery_state`` a state to name whenever they name none.

        Called on every change to the graph: the first node added to a blank bot takes
        both, however it was added.
        """
        self._reassign_orphaned_roles()

    def _delete_node(self, state_id: str, *, confirm: bool = True) -> bool:
        """Delete a state -- or, for a call node's id, the whole instance -- and all its edges.

        Returns False if the user cancels. Deleting an Entry or Exit in a definition
        deletes the port, and with it whatever of the node panels named it.
        """
        if self._config is None:
            return False
        if self.is_folded(state_id):
            return self.remove_instance(state_id, confirm=confirm)
        if state_id in self._call_of:
            return False  # a member goes only with the whole node it is drawn as
        if confirm:
            state = next((s for s in self._config.states if s.id == state_id), None)
            label = (state.label or state_id) if state else state_id
            edges_count = sum(1 for t in self._config.transitions if t.source == state_id or t.dest == state_id)
            detail = f" All {edges_count} connected transition(s) will also be deleted." if edges_count else ""
            answer = QMessageBox.question(
                self.views()[0] if self.views() else None,
                "Delete State",
                f"Delete state '{label}'?{detail}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        self._pre_edit("Delete Node")
        prev_in_structural = self._in_structural_edit
        self._in_structural_edit = True
        self._config.transitions = [t for t in self._config.transitions if t.source != state_id and t.dest != state_id]
        remaining = []
        for s, w, d, edge_item in self._edge_items:
            if s == state_id or d == state_id:
                self._unregister_edge(s, d, edge_item)
            else:
                remaining.append((s, w, d, edge_item))
        self._edge_items = remaining
        self._recompute_offsets()
        self._config.states = [s for s in self._config.states if s.id != state_id]
        meta = self._config.definition
        if meta is not None:
            meta.panels = [p for p in meta.panels if not (p.kind == "node" and p.member == state_id)]
            # An input this node was the only thing setting goes with it: it would sit on
            # every subgraph node setting nothing. One already unbound is the author's
            # to keep, so only an input this delete emptied is dropped.
            emptied = set()
            for inp in meta.inputs:
                kept = [b for b in inp.bindings if b.state != state_id]
                if inp.bindings and not kept:
                    emptied.add(inp.name)
                inp.bindings = kept
            meta.inputs = [i for i in meta.inputs if i.name not in emptied]
            for panel in meta.panels:
                panel.bindings = {
                    k: kept for k, v in panel.bindings.items() if (kept := [b for b in v if b.state != state_id])
                }
        self._reassign_orphaned_roles()
        if state_id in self._node_items:
            self.removeItem(self._node_items.pop(state_id))
        self.refresh_terminal()
        self.refresh_port_marks()
        self.refresh_errors()
        self._region_manager.flush_regions()
        self._in_structural_edit = prev_in_structural
        self._update_scene_rect()
        self.graph_changed.emit()
        return True

    def _unregister_edge(self, s: str, d: str, edge_item: EdgeItem) -> None:
        s_node = self._node_items.get(s)
        d_node = self._node_items.get(d)
        if s_node:
            s_node.remove_edge(edge_item)
        if d_node and d_node is not s_node:
            d_node.remove_edge(edge_item)
        self._edge_models.pop(edge_item, None)
        self.removeItem(edge_item)

    def _delete_edge_item(self, edge_item: EdgeItem, *, confirm: bool = True) -> bool:
        """Delete a drawn edge and every flat transition it stands for.  Returns False if the user cancels."""
        if self._config is None:
            return False
        if confirm:
            src = dst = ""
            for s, _w, d, ei in self._edge_items:
                if ei is edge_item:
                    src, dst = self._node_label(s), self._node_label(d)
                    break
            answer = QMessageBox.question(
                self.views()[0] if self.views() else None,
                "Delete Transition",
                f"Delete transition from '{src}' to '{dst}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        self._pre_edit("Delete Edge")
        models = self._edge_models.get(edge_item, [])
        self._config.transitions = [t for t in self._config.transitions if not any(t is m for m in models)]
        remaining = []
        for s, w, d, ei in self._edge_items:
            if ei is edge_item:
                self._unregister_edge(s, d, ei)
            else:
                remaining.append((s, w, d, ei))
        self._edge_items = remaining
        self._recompute_offsets()
        self.refresh_terminal()
        self.refresh_port_marks()
        self.refresh_errors()
        self.graph_changed.emit()
        return True

    def _node_label(self, display_id: str) -> str:
        node = self._node_items.get(display_id)
        return node.label if node is not None else display_id

    def _record_index(self, source: str, when: str | None, dest: str) -> int | None:
        """The ``_edge_items`` index of the drawn edge a flat ``(source, when, dest)`` belongs to."""
        probe = TransitionModel(trigger="advance", source=source, dest=dest, when=when)
        key = self.display_key(probe)
        if key is None:
            return None
        s, w, d, port = key
        for i, (rs, rw, rd, ei) in enumerate(self._edge_items):
            if rs == s and rw == w and rd == d and ei.dst_port == port:
                return i
        return None

    def change_edge_when(
        self,
        source: str,
        old_when: object,  # str | None
        dest: str,
        new_when: object,  # str | None
    ) -> None:
        """Reroute a scene edge after its ``when`` was changed via the properties panel.

        The TransitionModel is already updated by the param panel before this is
        called; the caller owns the undo snapshot, as for change_edge_endpoints.

        The ``when`` is part of the edge's identity in ``_edge_items``, so the record
        has to be re-keyed here too — every later lookup (flag refresh, selection,
        a subsequent endpoint rewire) matches on the (source, when, dest) triple and
        would silently miss the edge otherwise.
        """
        if self._config is None:
            return
        old_when_str: str | None = old_when  # type: ignore[assignment]
        new_when_str: str | None = new_when  # type: ignore[assignment]
        record_idx = self._record_index(source, old_when_str, dest)
        if record_idx is None:
            return
        s, _w, d, edge_item = self._edge_items[record_idx]
        edge_item.set_when(new_when_str)
        self._edge_items[record_idx] = (s, new_when_str, d, edge_item)

        # Parallel edges between the same pair are laned by `when`, so the whole
        # group's offsets shift when one of them is relabeled.
        self._recompute_offsets()

        self._update_scene_rect()
        self.refresh_errors()
        self.graph_changed.emit()

    def change_edge_endpoints(
        self,
        old_src: str,
        old_when: object,  # str | None
        old_dst: str,
        new_src: str,
        new_dst: str,
    ) -> None:
        """Rebuild a scene edge after its source/dest was changed via the properties panel.

        The TransitionModel itself is already updated by the param panel before this
        is called.  This method updates the visual EdgeItem and all data structures.
        An edge touching a call node is rebuilt from the model instead, since which
        port it lands on follows from the new endpoints.

        The caller owns the undo snapshot: it has to be taken *before* the model is
        mutated, which is earlier than this method can see (the param panel emits
        ``structural_property_changed`` for that).
        """
        if self._config is None:
            return
        old_when_str: str | None = old_when  # type: ignore[assignment]
        record_idx = self._record_index(old_src, old_when_str, old_dst)
        if record_idx is None:
            return
        s, w, d, edge_item = self._edge_items[record_idx]
        new_s, new_d = self.display_id(new_src), self.display_id(new_dst)
        involves_call = any(self.is_folded(x) for x in (s, d, new_s, new_d))
        if involves_call:
            models = self._edge_models.get(edge_item, [])
            self._unregister_edge(s, d, edge_item)
            del self._edge_items[record_idx]
            regroup: dict[tuple, list[TransitionModel]] = {}
            for t in models:
                key = self.display_key(t)
                if key is not None:
                    regroup.setdefault(key, []).append(t)
            for (ks, kw, kd, kport), ms in regroup.items():
                existing = next(
                    (ei for rs, rw, rd, ei in self._edge_items if (rs, rw, rd, ei.dst_port) == (ks, kw, kd, kport)),
                    None,
                )
                if existing is not None:
                    self._edge_models[existing].extend(ms)
                elif ks in self._node_items and kd in self._node_items:
                    self._add_edge_item(ks, kw, kd, kport, ms)
        else:
            old_src_node = self._node_items.get(s)
            old_dst_node = self._node_items.get(d)
            new_src_node = self._node_items.get(new_s)
            new_dst_node = self._node_items.get(new_d)
            if new_src_node is None or new_dst_node is None:
                return
            if old_src_node:
                old_src_node.remove_edge(edge_item)
            if old_dst_node and old_dst_node is not old_src_node:
                old_dst_node.remove_edge(edge_item)
            edge_item.set_endpoints(new_src_node, new_dst_node)
            new_src_node.add_edge(edge_item)
            if new_dst_node is not new_src_node:
                new_dst_node.add_edge(edge_item)
            self._edge_items[record_idx] = (new_s, w, new_d, edge_item)

        # Recompute all offsets and fan positions now that the topology has changed.
        self._recompute_offsets()

        self._update_scene_rect()
        self.refresh_terminal()
        self.refresh_port_marks()
        self.refresh_errors()
        self.graph_changed.emit()

    def _prompt_insert_at_input_port(self, state_id: str) -> None:
        """Ask which node type to insert, then insert it at *state_id*'s input port."""
        from bartlebot.gui.canvas.node_browser_dialog import NodeBrowserDialog

        view = self.views()[0] if self.views() else None
        dlg = NodeBrowserDialog(view, mode="add")
        if dlg.exec() and dlg.selected_node_type:
            self.insert_state_at_input_port(state_id, dlg.selected_node_type)

    def _prompt_insert_at_output_port(self, state_id: str, return_name: str | None) -> None:
        """Ask which node type to insert, then insert it at an output port.

        The picker is restricted to node types that can produce *return_name*.  A
        rerouted edge's ``when`` describes its source node's outcome, so a node that
        cannot produce that value would leave the edge unconditional -- firing on
        failure exactly as readily as on success.  Requiring the return rather than an
        identical return list keeps the restriction honest: ``sleep`` returns only
        SUCCESS, and is one of the more useful things to drop onto a SUCCESS port.
        """
        from bartlebot.gui.canvas.node_browser_dialog import NodeBrowserDialog

        allowed = node_types_producing(return_name) if return_name is not None else None
        view = self.views()[0] if self.views() else None
        dlg = NodeBrowserDialog(view, mode="add", allowed_node_types=allowed)
        if dlg.exec() and dlg.selected_node_type:
            self.insert_state_at_output_port(state_id, return_name, dlg.selected_node_type)

    def free_position_near(self, seed: QPointF, x_dir: int = 1) -> QPointF:
        """The nearest free on-grid position to *seed*, preferring *seed*'s own column.

        Every insert path needs this: the space an inserted node logically wants is
        the space its neighbors are already in. One column to the left of a node is
        exactly where that node's sources sit, and the midpoint of an edge between
        adjacent columns is 220 px from each -- less than the 300 px a node is wide.
        Left alone, 78% of port inserts and 90% of edge inserts landed on top of an
        existing node across the shipped bots. Seeding here takes all three to 0%.

        Overlap is tested against the nodes' actual rectangles rather than which grid
        cell they round to; cell-occupancy alone still left 7% overlapping.

        *seed* and the result are an item's top-left, as ``setPos`` takes it. Rows are
        snapped so the node's *center* sits on a whole multiple of GRID_H, because a
        stored position's y is the center and that is where the shipped bots put every
        one of their nodes -- the same rows ``find_empty_grid_cell`` returns. Snapping
        the top edge instead put every insert one row above its neighbors (#85).
        """
        boxes = [QRectF(n.pos().x(), n.pos().y(), NODE_W, n.body_height) for n in self._node_items.values()]
        cell_x = round(seed.x() / GRID_W)
        cell_y = round((seed.y() + NODE_H / 2) / GRID_H)
        for dx, dy in _placement_candidates(x_dir):
            pos = QPointF((cell_x + dx) * GRID_W, (cell_y + dy) * GRID_H - NODE_H / 2)
            if not any(QRectF(pos.x(), pos.y(), NODE_W, NODE_H).intersects(b) for b in boxes):
                return pos
        return seed

    def _spawn_state(self, node_type: str, pos: QPointF) -> tuple[str, NodeItem]:
        """Create a state of *node_type* at *pos*, in both the model and the scene.

        Shared by every insert path. The caller owns the undo snapshot and all the
        transition wiring; this only brings the state into existence.

        *pos* is the item's top-left; the stored y is its center, as ``_flush_positions``
        writes it and ``setup`` reads it. Storing the top-left put a node saved before
        anything flushed it back half a node higher than it was drawn.
        """
        new_id = self.mint_state_id(st.id for st in self._config.states)
        default_params = {f.key: f.default for f in NODE_PARAM_SCHEMAS.get(node_type, []) if f.default is not None}
        label = node_display_name(node_type)
        self._config.states.append(
            StateModel(
                id=new_id,
                node_type=node_type,
                label=label,
                position=self.stored_position(pos),
                params=default_params,
            )
        )
        node = NodeItem(
            new_id,
            label,
            interactive=True,
            returns=_node_returns(node_type),
            is_terminal=True,
            node_type=node_type,
        )
        node.set_snap_enabled(self._snap_enabled)
        node.setPos(pos)
        self.addItem(node)
        self._node_items[new_id] = node

        from bartlebot.settings_manager import SettingsManager

        SettingsManager.instance().add_recently_used_node(node_type)
        return new_id, node

    def insert_state_at_input_port(self, state_id: str, node_type: str) -> str | None:
        """Insert a state of *node_type* in front of everything entering *state_id*.

        Every transition that ended at *state_id* now ends at the new state instead,
        keeping its own ``when``, flags and label untouched -- those describe the
        *source* node's outcome, and are unaffected by what the edge now points at.
        The new state is then wired onward to *state_id*.

        Returns the new state's id, or None if *state_id* is not on the canvas.
        """
        if self._config is None:
            return None
        target = self._node_items.get(state_id)
        if target is None or isinstance(target, (FoldedNodeItem, PortNodeItem)):
            return None
        incoming = [t for t in self._config.transitions if t.dest == state_id]

        self._pre_edit("Insert State at Port")
        prev_in_structural = self._in_structural_edit
        self._in_structural_edit = True
        try:
            # In front of the target, on its row: the cell to its left when that is free,
            # else the target's own place, with the target's column and the rest moved over.
            before = self._grid_cell(QPointF(target.pos().x() - GRID_W, target.pos().y()))
            pos = before if self._cell_is_free(before) else self._open_column_at(QPointF(target.pos()))
            new_id, new_node = self._spawn_state(node_type, pos)
            for t in incoming:
                old_src, old_when = t.source, t.when
                t.dest = new_id
                self.change_edge_endpoints(old_src, old_when, state_id, old_src, new_id)
            self._create_transition(new_node, target, _happy_path_return(new_node.returns))
        finally:
            self._in_structural_edit = prev_in_structural
        self._update_scene_rect()
        self.graph_changed.emit()
        return new_id

    def insert_state_at_output_port(self, state_id: str, return_name: str | None, node_type: str) -> str | None:
        """Insert a state of *node_type* after *state_id*'s *return_name* port.

        Every transition leaving that port now leaves the new state instead, keeping
        its flags and label; *state_id* is then wired to the new state on the port
        that was clicked.

        ``when`` describes the *source* node's outcome, so it survives the move only
        when *node_type* can produce *return_name* too. When it cannot -- an action
        dropped on a condition's TRUE port -- the rerouted edges take the new node's
        happy-path return instead, the rule an insert on an edge follows. Left as it
        was, the value would never be produced and every rerouted edge would be dead.
        The port picker still offers only node types that keep the value, since that
        is the insert an author clicking a port means; speed-authoring (#85) is what
        relies on the remap.

        Returns the new state's id, or None if the insert was refused: the source is
        not an ordinary node, or *node_type* has no returns to route anything off.
        """
        if self._config is None:
            return None
        source = self._node_items.get(state_id)
        if source is None or isinstance(source, (FoldedNodeItem, PortNodeItem)):
            return None
        new_returns = _node_returns(node_type)
        onward_when = return_name
        if return_name is not None and return_name not in new_returns:
            onward_when = _happy_path_return(new_returns)
            if onward_when is None:
                logger.warning(
                    "Refusing to insert %r at the %s port of %s: it has no returns", node_type, return_name, state_id
                )
                return None

        outgoing = [t for t in self._config.transitions if t.source == state_id and t.when == return_name]
        port = source.port_out_for(return_name)

        self._pre_edit("Insert State at Port")
        prev_in_structural = self._in_structural_edit
        self._in_structural_edit = True
        try:
            # The next column, on the port's row: there when it is free, else there all
            # the same, with that column and the rest moved over to make it so.
            after = self._grid_cell(QPointF(source.pos().x() + GRID_W, port.y() - NODE_H / 2))
            pos = after if self._cell_is_free(after) else self._open_column_at(after)
            new_id, new_node = self._spawn_state(node_type, pos)
            for t in outgoing:
                old_dst = t.dest
                t.source = new_id
                t.when = onward_when
                # An edge into a call node is rebuilt from the model, which already
                # carries the new `when`; any other keeps its record and is re-keyed.
                self.change_edge_endpoints(state_id, return_name, old_dst, new_id, old_dst)
                if onward_when != return_name and not self.is_folded(self.display_id(old_dst)):
                    self.change_edge_when(new_id, return_name, old_dst, onward_when)
            self._create_transition(source, new_node, return_name)
        finally:
            self._in_structural_edit = prev_in_structural
        self._update_scene_rect()
        self.graph_changed.emit()
        return new_id

    def _insert_state_on_edge(self, edge_item: EdgeItem) -> None:
        """The edge menu's Insert: ask which node, then split the edge with it."""
        if self._config is None:
            return
        # Ask the user which node type to insert
        from bartlebot.gui.canvas.node_browser_dialog import NodeBrowserDialog

        view = self.views()[0] if self.views() else None
        dlg = NodeBrowserDialog(view, mode="add")
        if not (dlg.exec() and dlg.selected_node_type):
            return
        self.insert_state_on_edge(edge_item, dlg.selected_node_type)

    def insert_state_on_edge(self, edge_item: EdgeItem, node_type: str) -> str | None:
        """Split *edge_item* with a new state of *node_type*, making room for it. Returns the new state's id."""
        if self._config is None:
            return None
        for s, w, d, ei in list(self._edge_items):  # noqa: B007 — s, w, d used after loop via for-else leak
            if ei is edge_item:
                break
        else:
            return None
        src_node = self._node_items.get(s)
        dst_node = self._node_items.get(d)
        if src_node is None or dst_node is None:
            return None

        self._pre_edit("Insert State on Edge")
        self._in_structural_edit = True

        new_id, new_node = self._spawn_state(node_type, self._room_on_edge(edge_item, src_node, dst_node))

        # Remove original edge (src --w--> dst), then wire src --w--> new --w'--> dst.
        # Carry the original when value to the outgoing edge only if the new node actually
        # returns it; otherwise fall back to the new node's happy path rather than to an
        # unconditional edge, which would run the rest of the bot on a failure too.
        returns = new_node.returns
        outgoing_when = w if (w is not None and w in returns) else _happy_path_return(returns)
        self._delete_edge_item(edge_item, confirm=False)
        self._create_transition(src_node, new_node, w)
        self._create_transition(new_node, dst_node, outgoing_when)
        self._in_structural_edit = False
        self._update_scene_rect()
        return new_id

    def _room_on_edge(self, edge_item: EdgeItem, src_node: NodeItem, dst_node: NodeItem) -> QPointF:
        """Where a node inserted on the edge from *src_node* to *dst_node* goes, making room when there is none.

        Between two nodes in neighboring columns there is no cell to put it in: the
        midpoint is on top of one of them, and the nearest free row is above or below
        the line the edge ran along, which reads as a detour. So the new node takes the
        destination's place, and the destination's column and every column right of it
        move over by one -- all of each column, not only what is downstream, so rows
        that were level stay level.

        An edge with a whole column between its ends has a place for the node already,
        and one that runs backward or straight down has no "after" to move, so both
        keep the midpoint and the nearest free cell to it.
        """
        columns_apart = round(dst_node.pos().x() / GRID_W) - round(src_node.pos().x() / GRID_W)
        if columns_apart != 1:
            mid = edge_item.mapToScene(edge_item.path().pointAtPercent(0.5))
            return self.free_position_near(QPointF(mid.x() - NODE_W / 2, mid.y() - NODE_H / 2), x_dir=1)
        return self._open_column_at(QPointF(dst_node.pos()))

    @staticmethod
    def _grid_cell(seed: QPointF) -> QPointF:
        """The grid cell *seed* is in, as an item's top-left: the snap ``free_position_near`` starts from."""
        return QPointF(round(seed.x() / GRID_W) * GRID_W, round((seed.y() + NODE_H / 2) / GRID_H) * GRID_H - NODE_H / 2)

    def _cell_is_free(self, pos: QPointF) -> bool:
        """Whether a node put at *pos* would overlap none of the nodes drawn."""
        box = QRectF(pos.x(), pos.y(), NODE_W, NODE_H)
        return not any(
            box.intersects(QRectF(n.pos().x(), n.pos().y(), NODE_W, n.body_height)) for n in self._node_items.values()
        )

    def _open_column_at(self, place: QPointF) -> QPointF:
        """Move *place*'s column and every column right of it over by one, and return *place*, now free.

        What every insert does when the spot its node belongs in is taken: on an edge,
        at an input port and at an output port alike. All of each column moves, not only
        what is downstream of the insert, so rows that were level stay level. The caller
        has already opened the undo step, so the move and the insert are one.
        """
        for item in self._node_items.values():
            if item.pos().x() >= place.x() - 1:
                item.setPos(item.pos().x() + GRID_W, item.pos().y())
        self._flush_positions()
        return place

    def _flush_positions(self) -> None:
        """Write where every item now sits back into the model, through the fold.

        A call node's position is its instance's ``anchor``, and a collapsed region's is
        its members' top-left; either way the members follow it by the same delta --
        nested instances' anchors included -- since an instance's members sit at
        ``anchor + definition position`` and a collapsed region stores nothing else.
        """
        if self._config is None:
            return
        # Compute all position changes before mutating so about_to_change fires
        # against the unmodified config (snapshot captures current positions).
        updates: dict[str, dict] = {}
        for state in self._config.states:
            node = self._node_items.get(state.id)
            if node is None:
                continue
            new_pos = self.stored_position(node.scenePos(), node.body_height)
            if state.position is None or _moved(state.position, new_pos):
                updates[state.id] = new_pos
        anchor_moves: list[tuple[RegionModel, float, float]] = []
        for rid, node in self.folded_nodes().items():
            region = self._region_by_id(rid)
            if region is None:
                continue
            anchor = region_anchor(self._config, region)
            new_anchor = self.stored_position(node.scenePos(), node.body_height)
            dx = round(new_anchor["x"] - float(anchor["x"]), 3)
            dy = round(new_anchor["y"] - float(anchor["y"]), 3)
            if dx or dy:
                anchor_moves.append((region, dx, dy))
        if not updates and not anchor_moves:
            return
        self._pre_edit("Move Node")
        for state in self._config.states:
            if state.id in updates:
                state.position = updates[state.id]
        by_id = {s.id: s for s in self._config.states}
        for region, dx, dy in anchor_moves:
            for r in [region, *nested_regions(self._config, region)]:
                if r.source is None:
                    continue
                a = r.source.anchor or {"x": 0.0, "y": 0.0}
                r.source.anchor = {"x": float(a["x"]) + dx, "y": float(a["y"]) + dy}
            for m in region.members:
                st = by_id.get(m)
                if st is not None and st.position is not None:
                    st.position = {"x": float(st.position["x"]) + dx, "y": float(st.position["y"]) + dy}
        self._refresh_folds()
        self._update_scene_rect()
        self.graph_changed.emit()

    @property
    def region_items(self) -> dict[str, RegionItem]:
        """Public access to the region visual items (used by BotCanvas focus mode)."""
        return self._region_manager.items if self._region_manager is not None else {}

    def _update_scene_rect(self) -> None:
        bounds = self.itemsBoundingRect().adjusted(-500, -500, 500, 500)
        set_scene_rect_and_repaint(self, bounds)

    def flush_positions(self) -> None:
        """Write where every item now sits back into the model (anchors for call nodes)."""
        self._flush_positions()

    def tab_order(self) -> list[str]:
        """Display ids in the order Tab visits them."""
        return self._get_tab_order()

    def _get_tab_order(self) -> list[str]:
        """Display ids in BFS order from the initial state, orphans appended at the end."""
        if self._config is None:
            return list(self._node_items.keys())
        adj: dict[str, list[str]] = {}
        for t in self._config.transitions:
            adj.setdefault(self.display_id(t.source), []).append(self.display_id(t.dest))
        order: list[str] = []
        seen: set[str] = set()
        queue = [self.display_id(self._config.initial)]
        while queue:
            sid = queue.pop(0)
            if sid in seen:
                continue
            seen.add(sid)
            if sid in self._node_items:
                order.append(sid)
            for dest in adj.get(sid, []):
                if dest not in seen:
                    queue.append(dest)
        for sid in self._node_items:
            if sid not in seen:
                order.append(sid)
        return order


# ---------------------------------------------------------------------------
# _EditView
# ---------------------------------------------------------------------------


class _EditView(GraphView):
    def __init__(self, scene: QGraphicsScene, parent=None):
        super().__init__(scene, interactive=True, parent=parent)


# ---------------------------------------------------------------------------
# _ColorPickerDialog — six-swatch region palette picker
# ---------------------------------------------------------------------------


class _ColorPickerDialog(QWidget):
    """Minimal six-swatch palette dialog for region color selection."""

    def __init__(self, current_key: int, parent=None) -> None:
        from PyQt6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout

        from bartlebot.gui.widgets.dialog_footer import dialog_footer

        # Re-parent as QDialog so exec() works
        self._dialog = QDialog(parent)
        self._dialog.setWindowTitle("Choose Color")
        self._selected = current_key
        self._swatch_btns: dict[int, QPushButton] = {}

        swatch_row = QHBoxLayout()
        swatch_row.setSpacing(8)
        for key in THEME.REGION_PALETTE:
            btn = QPushButton()
            btn.setFixedSize(28, 28)
            color_hex = THEME.region_color(key).name()
            btn.setStyleSheet(
                f"QPushButton {{ background: {color_hex}; border: 2px solid transparent; border-radius: 14px; }}"
                f"QPushButton:hover {{ border-color: white; }}"
            )
            btn.clicked.connect(lambda _=None, k=key: self._select(k))
            self._swatch_btns[key] = btn
            swatch_row.addWidget(btn)

        # The swatches are the six ``QPushButton``s above, and the footer's sweep clears
        # ``autoDefault`` on every one of them — without it the first swatch built would
        # hold Enter, and picking a color with the keyboard would never close the dialog.
        footer = dialog_footer(self._dialog)

        layout = QVBoxLayout(self._dialog)
        layout.addLayout(swatch_row)
        layout.addLayout(footer.layout)
        self._dialog.setLayout(layout)
        self._highlight(current_key)

    def _select(self, key: int) -> None:
        self._selected = key
        self._highlight(key)

    def _highlight(self, key: int) -> None:
        for k, btn in self._swatch_btns.items():
            color_hex = THEME.region_color(k).name()
            border = "white" if k == key else "transparent"
            btn.setStyleSheet(
                f"QPushButton {{ background: {color_hex}; border: 2px solid {border}; border-radius: 14px; }}"
                f"QPushButton:hover {{ border-color: white; }}"
            )

    def exec(self):
        return self._dialog.exec()

    def selected_color(self) -> int:
        return self._selected
