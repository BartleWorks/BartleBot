"""The properties panel: a stack of pages, one per kind of selection.

``_ParamPanel`` picks the page and forwards its signals. Each page is its own class: the
node page (``node_page.py``, which owns Record), the edge page (``edge_page.py``), a plain
region's (``region_page.py``), a call node's (``call_page.py``) and a definition's
signature (``signature_page.py``), with an idle page when nothing is selected.
"""

from collections.abc import Callable
from typing import Any

from bartleby.qt.theming.theme import THEME
from bartleby.qt.theming.themed import ThemedMixin
from bartleby.qt.widgets.layout.scroll_gutter import reserve_scrollbar_gutter
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QLabel,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.schema import (
    RegionModel,
    StateModel,
    TransitionModel,
    VariableModel,
)
from bartlebot.gui.canvas.call_page import CallNodePage
from bartlebot.gui.canvas.edge_page import EdgePage
from bartlebot.gui.canvas.node_page import NodePage
from bartlebot.gui.canvas.region_page import RegionPage
from bartlebot.gui.canvas.signature_page import SignaturePage
from bartlebot.gui.widgets.buttons.record_button import RecordButton
from bartlebot.gui.widgets.pickers.player_list_widget import PlayerListWidget, PlayerSlotWidget

_PAGE_IDLE = 0
_PAGE_NODE = 1
_PAGE_EDGE = 2
_PAGE_REGION = 3
_PAGE_CALL = 4
_PAGE_SIGNATURE = 5


class _ParamPanel(ThemedMixin, QWidget):
    # Emitted BEFORE a typed edit mutates the model -- a param, label, notes, flag
    # conditions, or a region's label or inputs -- carrying the undo description.
    # The canvas opens its debounced undo step here, which captures a before-snapshot
    # only on the first edit of a burst; opened from properties_changed instead, that
    # snapshot already held the edit and undo restored it.
    edit_starting = pyqtSignal(str)
    properties_changed = pyqtSignal()
    label_committed = pyqtSignal()
    # Emitted for discrete structural property changes (node type switch, edge
    # rewire) that need an immediate undo snapshot rather than the debounced
    # param-edit path.  Emit it BEFORE mutating the model — UndoManager.push()
    # captures a before-snapshot.
    structural_property_changed = pyqtSignal(str)
    # Emitted after such a change has been applied to the model.  Unlike
    # properties_changed this does not start a debounced undo snapshot (the
    # immediate one above already covers the edit) and it asks the canvas for a
    # full structural refresh, which is what redraws node type sub-labels,
    # output ports and rewired edges.
    structural_property_applied = pyqtSignal()
    # Emitted when the user changes source or dest via the dropdowns.
    # Args: old_source, old_when (str|None), old_dest, new_source, new_dest
    edge_endpoints_changed = pyqtSignal(str, object, str, str, str)
    # Emitted when the user changes the When condition via the dropdown.
    # Args: source, old_when (str|None), dest, new_when (str|None)
    edge_when_changed = pyqtSignal(str, object, str, object)
    # Emitted when the user completes a wiki import from the handle_combat node panel.
    # Carries the list of selected DropModel entries to be added to the bot's drops.
    wiki_import_completed = pyqtSignal(list)
    # Emitted when the user clicks a connection row to navigate to an edge or state.
    select_edge_requested = pyqtSignal(object)  # TransitionModel
    select_state_requested = pyqtSignal(str)  # state ID
    # Emitted when the user gives the shown state one of the two bot-wide FSM roles.
    # The panel holds no BotConfig, so it asks rather than assigns -- the canvas owns
    # the mutation, which is also what gets it an undo entry and a badge refresh.
    # Both roles are exactly-one, so there is no "clear" counterpart: the request
    # always names the state taking the role.
    initial_state_requested = pyqtSignal(str)  # state ID
    recovery_state_requested = pyqtSignal(str)  # state ID
    # The region or call node page wrote into the shown RegionModel (label, color,
    # inputs). Its undo step is already open: a typed edit announced itself with
    # edit_starting, a discrete one pushed through structural_property_changed.
    region_property_changed = pyqtSignal()
    # (region id, collapse): the region page's Collapse / Expand button (#67).
    region_collapse_requested = pyqtSignal(str, bool)
    detach_requested = pyqtSignal(str)  # region ID
    open_definition_requested = pyqtSignal(str)  # region ID
    # (role field, state id): the call node's page moved a bot role onto the member
    # the chosen entry port leads to.
    role_requested = pyqtSignal(str, str)
    # (state id, param key): Link to subgraph input, and Unlink, on a definition node's page.
    link_input_requested = pyqtSignal(str, str)
    unlink_input_requested = pyqtSignal(str, str)
    # (region id, input name): the same two on a nested subgraph node's page, in a definition.
    link_nested_input_requested = pyqtSignal(str, str)
    unlink_nested_input_requested = pyqtSignal(str, str)
    # (state id, promoted): the node page's Promote to input group / Demote button.
    promote_node_requested = pyqtSignal(str, bool)
    # The signature page wrote into the definition document's signature.
    definition_changed = pyqtSignal()
    # (state id, param key), on a node of a template draft: make the param a setup step's
    # fill-in, or stop it being one. The panel only reports; the canvas edits the draft.
    add_setup_target_requested = pyqtSignal(str, str)
    remove_setup_target_requested = pyqtSignal(str, str)
    add_port_requested = pyqtSignal(str)  # SUBGRAPH_ENTRY / SUBGRAPH_EXIT
    delete_port_requested = pyqtSignal(str)  # state ID
    # The signature page reaching into the canvas without selecting anything (#155): the
    # nodes its selected item sets, to light (empty when it no longer shows), and one node
    # to bring into view. State ids, or a nested subgraph node's region id.
    link_highlight_requested = pyqtSignal(list)
    center_node_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._get_clients: Callable[[], list] = lambda: []

        self._stack = QStackedWidget()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._stack)

        # Page 0 — idle
        self._idle_lbl = QLabel("Select a node or edge to edit its properties.")
        self._idle_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stack.addWidget(self._idle_lbl)

        # Page 1 — node; 2 — edge; 3 — a plain region; 4 — a call node; 5 — a
        # definition's signature, nothing selected. Each is its own class.
        self._node_page = NodePage()
        self._edge_page = EdgePage()
        self._region_page = RegionPage()
        self._call_page = CallNodePage()
        self._signature_page = SignaturePage()
        # Every inspector page but the edge's scrolls, and reserves its gutter, so the
        # panel keeps one content width whichever page is up, and a fold does not jolt
        # every card sideways (#112).
        self._stack.addWidget(self._scrolled(self._node_page, horizontal=False))
        self._stack.addWidget(self._edge_page)
        for page in (self._region_page, self._call_page, self._signature_page):
            self._stack.addWidget(self._scrolled(page))

        node = self._node_page
        for name in (
            "edit_starting",
            "properties_changed",
            "label_committed",
            "structural_property_changed",
            "structural_property_applied",
            "wiki_import_completed",
            "select_edge_requested",
            "select_state_requested",
            "initial_state_requested",
            "recovery_state_requested",
            "link_input_requested",
            "unlink_input_requested",
            "promote_node_requested",
            "add_setup_target_requested",
            "remove_setup_target_requested",
        ):
            getattr(node, name).connect(getattr(self, name))
        for name in (
            "edit_starting",
            "properties_changed",
            "structural_property_changed",
            "structural_property_applied",
            "edge_endpoints_changed",
            "edge_when_changed",
        ):
            getattr(self._edge_page, name).connect(getattr(self, name))
        for name in (
            "edit_starting",
            "structural_property_changed",
            "region_property_changed",
            "region_collapse_requested",
        ):
            getattr(self._region_page, name).connect(getattr(self, name))
        self._call_page.edit_starting.connect(self.edit_starting)
        self._call_page.structural_change.connect(self.structural_property_changed)
        self._call_page.changed.connect(self.region_property_changed)
        self._call_page.role_requested.connect(self.role_requested)
        self._call_page.open_definition_requested.connect(self.open_definition_requested)
        self._call_page.detach_requested.connect(self.detach_requested)
        self._call_page.link_input_requested.connect(self.link_nested_input_requested)
        self._call_page.unlink_input_requested.connect(self.unlink_nested_input_requested)
        self._signature_page.edit_starting.connect(self.edit_starting)
        self._signature_page.structural_change.connect(self.structural_property_changed)
        self._signature_page.changed.connect(self.definition_changed)
        self._signature_page.add_port_requested.connect(self.add_port_requested)
        self._signature_page.delete_port_requested.connect(self.delete_port_requested)
        self._signature_page.highlight_requested.connect(self.link_highlight_requested)
        self._signature_page.center_requested.connect(self.center_node_requested)
        # The signature page is the only one that lights anything, so leaving it for any
        # other page puts the lights out; coming back, it names its selection again.
        self._stack.currentChanged.connect(
            lambda index: index != _PAGE_SIGNATURE and self.link_highlight_requested.emit([])
        )

        self.refresh_theme_styles()

    @staticmethod
    def _scrolled(page: QWidget, *, horizontal: bool = True) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        reserve_scrollbar_gutter(scroll)
        if not horizontal:
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        return scroll

    def apply_theme_styles(self) -> None:
        self._idle_lbl.setStyleSheet(f"color: {THEME.text_muted};")
        # A switch delivered to this panel alone reaches no page, so they are asked here.
        self._node_page.refresh_theme_styles()
        self._edge_page.refresh_theme_styles()

    @property
    def node_page(self) -> NodePage:
        return self._node_page

    @property
    def edge_page(self) -> EdgePage:
        return self._edge_page

    @property
    def region_page(self) -> RegionPage:
        return self._region_page

    @property
    def call_page(self) -> CallNodePage:
        return self._call_page

    @property
    def signature_page(self) -> SignaturePage:
        return self._signature_page

    def set_clients_getter(self, fn: Callable[[], list]) -> None:
        """Pass a callable returning the currently hooked clients."""
        self._get_clients = fn
        self._node_page.set_clients_getter(fn)
        self._call_page.set_clients_getter(fn)
        self._signature_page.set_clients_getter(fn)
        self.refresh_player_availability()

    def set_default_scales_getter(self, fn: Callable[[], Any]) -> None:
        """Pass a callable returning the open bot's default Scales, for the node form."""
        self._node_page.set_default_scales_getter(fn)

    def set_variables_getter(self, fn: Callable[[], list[VariableModel]]) -> None:
        """Pass a callable returning the open bot's variable declarations, for a set_variables node's rows."""
        self._node_page.set_variables_getter(fn)

    def set_zone_suggestions_getter(self, fn: Callable[[], list[str]]) -> None:
        """Pass a callable returning zone suggestions, for the node form and the region inputs form alike."""
        self._node_page.set_zone_suggestions_getter(fn)
        self._call_page.set_zone_suggestions_getter(fn)
        self._signature_page.set_zone_suggestions_getter(fn)

    def set_subgraph_library(self, library) -> None:
        """The library an input linked into a nested instance is looked up in, to draw its field."""
        self._call_page.set_subgraph_library(library)
        self._signature_page.set_subgraph_library(library)

    def refresh_player_availability(self) -> None:
        """Re-gray the player checkboxes — call when clients connect or disconnect.

        findChildren rather than a widget registry: player checkboxes appear at several
        depths inside the param form, and this needs no bookkeeping for any of them.
        """
        connected = len(self._get_clients())
        for w in self.findChildren(PlayerListWidget) + self.findChildren(PlayerSlotWidget):
            w.set_available_players(connected)
        for button in self.findChildren(RecordButton):
            button.refresh_players()

    def _leave_pages(self) -> None:
        """Every page lets go of what it showed, before one is shown: only one page holds anything."""
        self._node_page.clear()
        self._edge_page.clear()
        self._region_page.clear()

    def show_idle(self) -> None:
        self._leave_pages()
        self._stack.setCurrentIndex(_PAGE_IDLE)

    @property
    def shown_state(self) -> StateModel | None:
        """The state on the node page, or None when another page is up."""
        return self._node_page.state

    @property
    def shown_transition(self) -> TransitionModel | None:
        """The transition on the edge page, or None when another page is up."""
        return self._edge_page.transition

    @property
    def shown_region(self) -> RegionModel | None:
        """The region on the region page or the call node's page, or None when another page is up."""
        if self.showing_call:
            return self._call_page.region
        return self._region_page.region

    def show_state(self, state: StateModel, *args, **kwargs) -> None:
        """Show *state* on the node page: ``NodePage.show_state`` has the arguments."""
        self._leave_pages()
        self._node_page.show_state(state, *args, **kwargs)
        self._stack.setCurrentIndex(_PAGE_NODE)

    def show_required_errors(self) -> None:
        """Draw every empty required field on the shown page red: a save was refused for them."""
        if self._stack.currentIndex() == _PAGE_NODE:
            self._node_page.show_required_errors()
        elif self._stack.currentIndex() == _PAGE_CALL:
            self._call_page.show_required_errors()

    def show_region(self, region: RegionModel, **kwargs) -> None:
        """Show a plain region's page: ``RegionPage.show_region`` has the arguments."""
        self._leave_pages()
        self._region_page.show_region(region, **kwargs)
        self._stack.setCurrentIndex(_PAGE_REGION)

    def show_call(self, region: RegionModel, defn, status, config) -> None:
        """Show a call node's page for the subgraph instance *region*."""
        self._leave_pages()
        self._call_page.show_call(region, defn, status, config)
        self._stack.setCurrentIndex(_PAGE_CALL)

    def show_signature(self, config) -> None:
        """Show a definition document's signature: its page when nothing is selected."""
        self._leave_pages()
        self._signature_page.show_signature(config)
        self._stack.setCurrentIndex(_PAGE_SIGNATURE)

    @property
    def showing_call(self) -> bool:
        return self._stack.currentIndex() == _PAGE_CALL

    @property
    def showing_signature(self) -> bool:
        return self._stack.currentIndex() == _PAGE_SIGNATURE

    def show_transition(self, transition: TransitionModel, *args, **kwargs) -> None:
        """Show *transition* on the edge page: ``EdgePage.show_transition`` has the arguments."""
        self._leave_pages()
        self._edge_page.show_transition(transition, *args, **kwargs)
        self._stack.setCurrentIndex(_PAGE_EDGE)

    def refresh_role_checks(self, initial_id: str | None, recovery_id: str | None) -> None:
        """Re-sync the node page's role checkboxes after a role moved: ``NodePage.refresh_role_checks``."""
        self._node_page.refresh_role_checks(initial_id, recovery_id)

    # ------------------------------------------------------------------
    # Recording, on the node page
    # ------------------------------------------------------------------

    def set_preferred_player_getter(self, fn: Callable[[], int]) -> None:
        """Pass a callable naming the player index Ctrl+R records from."""
        self._node_page.set_preferred_player_getter(fn)

    def set_safety_delay_getter(self, fn: Callable[[], float]) -> None:
        """Pass a callable returning the open bot's Safety Delay, how long Record watches for dialogue."""
        self._node_page.set_safety_delay_getter(fn)
        self._call_page.set_safety_delay_getter(fn)

    def toggle_record(self) -> None:
        """Ctrl+R: start or cancel recording the shown node (``NodePage.toggle_record``)."""
        self._node_page.toggle_record()

    def start_record(self, client) -> bool:
        """Record the shown node from *client* (``NodePage.start_record``)."""
        return self._node_page.start_record(client)
