"""NodeBrowserDialog — searchable node reference and add-node dialog.

Serves two roles depending on the ``mode`` constructor argument:

- ``mode="add"``  (default): the primary "add a state" flow. Shows Add + Cancel
  buttons; double-clicking or pressing Enter on a selected node triggers Add.
- ``mode="view"``: read-only reference browser. Shows only a Close button. Used
  when the user right-clicks an existing node and chooses "Node Help", or clicks
  the "?" button in the properties panel.

The dialog is driven entirely by ``NODE_SPECS`` and ``NODE_CATEGORIES`` from the
node registry — no separate documentation source to maintain.
"""

from __future__ import annotations

from typing import Literal

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.schema import SubgraphDefinition
from bartlebot.gui.icon_utils import svg_pixmap
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin
from bartlebot.gui.widgets.search_field import SearchLineEdit
from bartlebot.nodes.param_schema import NodeSpec, ParamField
from bartlebot.nodes.registry import NODE_CATEGORIES, NODE_SPECS, NODE_TYPE_TO_CATEGORY
from bartlebot.paths import project_root
from bartlebot.settings_manager import SettingsManager
from bartlebot.store_roots import Origin
from bartlebot.subgraph_store import SUBGRAPH_KEY_PREFIX, SubgraphLibrary, count_subgraph_uses

_ORIGIN_CHIP = {
    Origin.SHIPPED: "Shipped",
    Origin.SHIPPED_OVERRIDE: "Your copy",
    Origin.USER: "Your library",
    Origin.MISSING: "Missing",
}


def subgraph_key(subgraph_id: str) -> str:
    return f"{SUBGRAPH_KEY_PREFIX}{subgraph_id}"


def is_subgraph_key(key: str) -> bool:
    return key.startswith(SUBGRAPH_KEY_PREFIX)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONDITION_TYPES = {"check_if_dropped", "check_level_scaling", "check_zone"}


def _humanize(node_type: str) -> str:
    """Convert snake_case node key to a readable label.

    Examples:
        enter_zone_via_list → "Enter Zone Via List"
        handle_combat       → "Handle Combat"
    """
    return node_type.replace("_", " ").title()


def _is_condition(node_type: str) -> bool:
    return node_type in _CONDITION_TYPES


def _chip_color(return_name: str) -> tuple[str, str]:
    """Return (background_hex, text_hex) for a return-value chip."""
    name_upper = return_name.upper()
    if name_upper in ("SUCCESS", "TRUE"):
        return THEME.chip_success_bg, THEME.chip_text
    if name_upper in ("FAILURE", "FALSE"):
        return THEME.chip_failure_bg, THEME.chip_text
    return THEME.chip_neutral_bg, THEME.chip_text


def _make_separator() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setFrameShadow(QFrame.Shadow.Sunken)
    sep.setStyleSheet(f"color: {THEME.border};")
    return sep


# ---------------------------------------------------------------------------
# Return-value chip widget
# ---------------------------------------------------------------------------


class _Chip(QLabel):
    """Small pill label with a colored rounded-rect background."""

    def __init__(self, text: str, bg: str, fg: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFont(THEME.ui_font(THEME.font_small_px, bold=True))
        self.setStyleSheet(f"background: {bg}; color: {fg}; border-radius: 4px; padding: 1px 4px;")
        self.setFixedHeight(20)


# ---------------------------------------------------------------------------
# Detail panel
# ---------------------------------------------------------------------------


class _DetailPanel(ThemedMixin, QScrollArea):
    """Scrollable right panel displaying full NodeSpec documentation."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)

        self._node_type: str = ""
        self._layout: QVBoxLayout | None = None
        # The last show_* call, replayed to restyle: ("node", args) or
        # ("subgraph", args). See apply_theme_styles.
        self._shown: tuple[str, tuple] | None = None
        self._pending_scroll = None
        self._theme_timer = QTimer(self)
        self._theme_timer.setSingleShot(True)
        self._theme_timer.timeout.connect(self._rebuild_for_theme)
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        """Rebuild the panel, rather than restyle it in place.

        Everything here is built by ``show_node``, which throws the whole container
        away and makes a new one on every selection — so there is nothing stable to
        hold a reference to, and holding the seventeen labels it can produce would
        mean maintaining that list forever. Replaying the last call is both shorter
        and automatically complete.

        The rebuild keeps the scroll position, which ``show_node`` deliberately does
        not: switching *node* should start at the top, switching *theme* should leave
        the reader where they were.

        It is also the one place in the app where a theme switch *deletes* widgets
        rather than restyling them, so it must not happen inside the ``StyleChange``
        handler: ``QScrollArea.setWidget`` destroys the old container immediately, and
        the switch arrives via ``QApplication.setStyleSheet``, which is mid-iteration
        over every widget when it does. Freeing a subtree there aborts the process with
        no traceback, so the rebuild is deferred to the next event-loop turn. The timer
        is a child of the panel rather than a bare ``singleShot``, so it dies with the
        panel instead of firing into a destroyed widget.

        A panel with no content yet destroys nothing, so it builds immediately — that
        is construction, where deferring would leave the dialog blank for a turn.
        """
        if self.widget() is None:
            self._rebuild_for_theme()
        else:
            self._theme_timer.start(0)

    def _rebuild_for_theme(self) -> None:
        """Replay the last ``show_node`` (or the placeholder) under the new theme."""
        if self._shown is None:
            placeholder = QLabel("Select a node to view its documentation.")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet(f"color: {THEME.text_muted}; font-size: {THEME.font_body_px + 1}px;")
            self.setWidget(placeholder)
            return
        offset = self.verticalScrollBar().value()
        kind, args = self._shown
        if kind == "subgraph":
            self._build_subgraph(*args)
        else:
            self._build(*args)
        self._restore_scroll(offset)

    def _restore_scroll(self, offset: int) -> None:
        """Put the scrollbar back once the rebuilt content has been measured.

        ``setWidget`` resets the range to zero, and the real range only arrives a
        layout pass later — so ``setValue`` immediately after the rebuild, or from a
        ``singleShot(0)``, is clamped to zero and the reader is dumped at the top.
        ``rangeChanged`` is the signal that says the new content has a size, and the
        ``maximum <= 0`` check skips the reset on the way there.
        """
        if not offset:
            return
        bar = self.verticalScrollBar()
        self._clear_pending_scroll()

        def restore(_minimum: int, maximum: int) -> None:
            if maximum <= 0:
                return
            self._clear_pending_scroll()
            bar.setValue(offset)

        self._pending_scroll = restore
        bar.rangeChanged.connect(restore)

    def _clear_pending_scroll(self) -> None:
        """Drop an unfired restore — content short enough never to scroll leaves one."""
        if self._pending_scroll is not None:
            self.verticalScrollBar().rangeChanged.disconnect(self._pending_scroll)
            self._pending_scroll = None

    def show_node(self, node_type: str, spec: NodeSpec, on_see_also=None, on_favorite_toggled=None) -> None:
        """Replace the scroll area content with documentation for node_type."""
        self._shown = ("node", (node_type, spec, on_see_also, on_favorite_toggled))
        self._clear_pending_scroll()
        self._build(node_type, spec, on_see_also, on_favorite_toggled)
        # Scroll to top when switching nodes
        QTimer.singleShot(0, lambda: self.verticalScrollBar().setValue(0))

    def show_subgraph(self, defn: SubgraphDefinition, origin: Origin, used_by: int) -> None:
        """Replace the scroll area content with a library subgraph's summary."""
        self._shown = ("subgraph", (defn, origin, used_by))
        self._clear_pending_scroll()
        self._build_subgraph(defn, origin, used_by)
        QTimer.singleShot(0, lambda: self.verticalScrollBar().setValue(0))

    def _build_subgraph(self, defn: SubgraphDefinition, origin: Origin, used_by: int) -> None:
        self._node_type = subgraph_key(defn.id)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(10)
        self._layout = layout
        self.setWidget(container)

        title_row = QHBoxLayout()
        title_label = QLabel(defn.name)
        title_label.setFont(THEME.ui_font(THEME.font_body_px + 9, bold=True))
        title_label.setStyleSheet(f"color: {THEME.text};")
        title_row.addWidget(title_label, 1)
        title_row.addWidget(_Chip("Subgraph", THEME.accent, THEME.chip_text))
        title_row.addWidget(_Chip(_ORIGIN_CHIP.get(origin, origin.value), THEME.border, THEME.text))
        layout.addLayout(title_row)

        key_label = QLabel(f"{defn.id} \u00b7 version {defn.version}")
        key_label.setStyleSheet(f"color: {THEME.text_muted}; font-size: {THEME.font_subtext_px}px;")
        layout.addWidget(key_label)

        if defn.description:
            desc = QLabel(defn.description)
            desc.setWordWrap(True)
            desc.setStyleSheet(f"color: {THEME.text}; font-size: {THEME.font_small_px}px;")
            layout.addWidget(desc)

        n = len(defn.states)
        used = f"Used by {used_by} bot{'s' if used_by != 1 else ''}"
        summary = QLabel(
            f"{n} state{'s' if n != 1 else ''}, {len(defn.transitions)} internal transitions \u00b7 {used}"
        )
        summary.setStyleSheet(f"color: {THEME.text_muted}; font-size: {THEME.font_subtext_px}px;")
        layout.addWidget(summary)

        layout.addWidget(_make_separator())
        inputs_hdr = QLabel("Inputs")
        inputs_hdr.setFont(THEME.ui_font(THEME.font_heading_px, bold=True))
        inputs_hdr.setStyleSheet(f"color: {THEME.text};")
        layout.addWidget(inputs_hdr)
        if defn.inputs:
            fields = [
                ParamField(key=i.name, type=i.type, label=i.label or i.name, help=i.help or "", default=i.default)
                for i in defn.inputs
            ]
            layout.addWidget(self._build_params_table(fields))
        else:
            none = QLabel("This subgraph declares no inputs; it works as shipped.")
            none.setStyleSheet(f"color: {THEME.text_muted}; font-size: {THEME.font_subtext_px}px;")
            layout.addWidget(none)

        layout.addWidget(_make_separator())
        members_hdr = QLabel("States")
        members_hdr.setFont(THEME.ui_font(THEME.font_heading_px, bold=True))
        members_hdr.setStyleSheet(f"color: {THEME.text};")
        layout.addWidget(members_hdr)
        for st in defn.states:
            row = QLabel(f"{st.label or st.id} \u2014 {_humanize(st.node_type)}")
            row.setStyleSheet(f"color: {THEME.text}; font-size: {THEME.font_subtext_px}px;")
            layout.addWidget(row)
        layout.addStretch()

    def _build(self, node_type: str, spec: NodeSpec, on_see_also=None, on_favorite_toggled=None) -> None:
        self._node_type = node_type

        # Build a fresh container each call — avoids the stale-widget problem that
        # occurs when QHBoxLayouts added via addLayout() leave child widgets parented
        # to the old container even after takeAt() clears the top-level layout items.
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(10)
        self._layout = layout

        self.setWidget(container)

        # ── Title row ──────────────────────────────────────────────
        title_row = QHBoxLayout()
        title_label = QLabel(_humanize(node_type))
        title_font = THEME.ui_font(THEME.font_body_px + 9, bold=True)
        title_label.setFont(title_font)
        title_label.setStyleSheet(f"color: {THEME.text};")
        title_row.addWidget(title_label, 1)

        if _is_condition(node_type):
            badge = _Chip("Condition", THEME.accent, THEME.chip_text)
        else:
            badge = _Chip("Action", THEME.border, THEME.text)
        title_row.addWidget(badge)

        # ── Favorite star button ────────────────────────────────────
        sm = SettingsManager.instance()
        is_fav = sm.is_favorite_node(node_type)
        star_btn = QPushButton()
        star_btn.setFixedSize(28, 28)
        star_btn.setFlat(True)
        star_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        star_btn.setStyleSheet("background: transparent; border: none; padding: 0;")
        star_btn.setToolTip("Remove from favorites" if is_fav else "Add to favorites")
        star_btn.setIcon(QIcon(svg_pixmap("star_filled" if is_fav else "star_outline", 18, tint=THEME.accent)))

        def _on_star_clicked() -> None:
            new_is_fav = SettingsManager.instance().toggle_favorite_node(node_type)
            icon_name = "star_filled" if new_is_fav else "star_outline"
            star_btn.setIcon(QIcon(svg_pixmap(icon_name, 18, tint=THEME.accent)))
            star_btn.setToolTip("Remove from favorites" if new_is_fav else "Add to favorites")
            if on_favorite_toggled:
                on_favorite_toggled(node_type, new_is_fav)

        star_btn.clicked.connect(_on_star_clicked)
        title_row.addWidget(star_btn)

        self._layout.addLayout(title_row)

        # ── Node type key (muted) ───────────────────────────────────
        key_label = QLabel(node_type)
        key_label.setStyleSheet(f"color: {THEME.text_muted}; font-size: {THEME.font_subtext_px}px;")
        self._layout.addWidget(key_label)

        # ── Description ────────────────────────────────────────────
        if spec.description:
            desc = QLabel(spec.description)
            desc.setWordWrap(True)
            desc.setStyleSheet(f"color: {THEME.text}; font-size: {THEME.font_small_px}px;")
            self._layout.addWidget(desc)

        # ── Max retries note ───────────────────────────────────────
        if spec.max_consecutive_retries is not None:
            retries_label = QLabel(
                f"⚠ The bot will stop after {spec.max_consecutive_retries} consecutive failures on this node."
            )
            retries_label.setWordWrap(True)
            retries_label.setStyleSheet(f"color: {THEME.warning}; font-size: {THEME.font_subtext_px}px;")
            self._layout.addWidget(retries_label)

        # ── Separator ──────────────────────────────────────────────
        self._layout.addWidget(_make_separator())

        # ── Parameters ─────────────────────────────────────────────
        if spec.params:
            params_hdr = QLabel("Parameters")
            params_hdr.setFont(THEME.ui_font(THEME.font_heading_px, bold=True))
            params_hdr.setStyleSheet(f"color: {THEME.text};")
            self._layout.addWidget(params_hdr)

            table = self._build_params_table(spec.params)
            self._layout.addWidget(table)
        else:
            no_params = QLabel("This node has no parameters.")
            no_params.setStyleSheet(f"color: {THEME.text_muted}; font-size: {THEME.font_subtext_px}px;")
            self._layout.addWidget(no_params)

        # ── Returns ────────────────────────────────────────────────
        if spec.returns:
            self._layout.addWidget(_make_separator())
            returns_hdr = QLabel("Returns")
            returns_hdr.setFont(THEME.ui_font(THEME.font_heading_px, bold=True))
            returns_hdr.setStyleSheet(f"color: {THEME.text};")
            self._layout.addWidget(returns_hdr)

            for ret in spec.returns:
                row = QHBoxLayout()
                bg, fg = _chip_color(ret.name)
                chip = _Chip(ret.name, bg, fg)
                chip.setFixedWidth(80)
                row.addWidget(chip)

                if ret.description:
                    desc_lbl = QLabel(ret.description)
                    desc_lbl.setWordWrap(True)
                    desc_lbl.setStyleSheet(f"color: {THEME.text}; font-size: {THEME.font_subtext_px}px;")
                    row.addWidget(desc_lbl, 1)
                row.addStretch()
                self._layout.addLayout(row)

        # ── Required bot options ───────────────────────────────────
        if spec.required_bot_options:
            self._layout.addWidget(_make_separator())
            opts_hdr = QLabel("Required Bot Options")
            opts_hdr.setFont(THEME.ui_font(THEME.font_heading_px, bold=True))
            opts_hdr.setStyleSheet(f"color: {THEME.text};")
            self._layout.addWidget(opts_hdr)

            opts_note = QLabel(
                "Adding this node to a bot automatically injects the following options into the Bot Options panel."
            )
            opts_note.setWordWrap(True)
            opts_note.setStyleSheet(f"color: {THEME.text_muted}; font-size: {THEME.font_subtext_px}px;")
            self._layout.addWidget(opts_note)

            for opt in spec.required_bot_options:
                row = QHBoxLayout()
                name_chip = _Chip(opt.name, THEME.surface, THEME.text_subtle)
                type_chip = _Chip(opt.type, THEME.border, THEME.text_muted)
                row.addWidget(name_chip)
                row.addWidget(type_chip)
                # A conditional option is not injected until the param turns it on, so the
                # note above ("automatically injects") would otherwise be wrong for it.
                if opt.required_when:
                    row.addWidget(_Chip(f"if {opt.required_when}", THEME.border, THEME.text_muted))
                if opt.required_unless:
                    row.addWidget(_Chip(f"unless {opt.required_unless}", THEME.border, THEME.text_muted))

                if opt.description:
                    opt_desc = QLabel(opt.description)
                    opt_desc.setWordWrap(True)
                    opt_desc.setStyleSheet(f"color: {THEME.text}; font-size: {THEME.font_subtext_px}px;")
                    row.addWidget(opt_desc, 1)
                row.addStretch()
                self._layout.addLayout(row)

        # ── Usage notes ────────────────────────────────────────────
        if spec.usage_notes:
            self._layout.addWidget(_make_separator())
            notes_hdr = QLabel("Usage Notes")
            notes_hdr.setFont(THEME.ui_font(THEME.font_heading_px, bold=True))
            notes_hdr.setStyleSheet(f"color: {THEME.text};")
            self._layout.addWidget(notes_hdr)

            notes_lbl = QLabel(spec.usage_notes)
            notes_lbl.setWordWrap(True)
            notes_lbl.setStyleSheet(f"color: {THEME.text}; font-size: {THEME.font_subtext_px}px;")
            self._layout.addWidget(notes_lbl)

        # ── See also ───────────────────────────────────────────────
        if spec.see_also:
            self._layout.addWidget(_make_separator())
            see_hdr = QLabel("See Also")
            see_hdr.setFont(THEME.ui_font(THEME.font_heading_px, bold=True))
            see_hdr.setStyleSheet(f"color: {THEME.text};")
            self._layout.addWidget(see_hdr)

            see_row = QHBoxLayout()
            for related_type in spec.see_also:
                btn = QPushButton(_humanize(related_type))
                btn.setStyleSheet(
                    f"color: {THEME.accent}; background: transparent; border: none; "
                    f"text-decoration: underline; font-size: {THEME.font_subtext_px}px; padding: 0;"
                )
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                if on_see_also:
                    btn.clicked.connect(lambda _checked, t=related_type: on_see_also(t))
                see_row.addWidget(btn)
            see_row.addStretch()
            self._layout.addLayout(see_row)

        self._layout.addStretch()

    def _build_params_table(self, params: list[ParamField]) -> QTableWidget:
        """Build a read-only table showing all parameters for this node."""
        headers = ["Name", "Type", "Req", "Default", "Description"]
        table = QTableWidget(len(params), len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setWordWrap(True)

        table.setStyleSheet(
            f"""
            QTableWidget {{
                background: {THEME.bg};
                alternate-background-color: {THEME.surface};
                border: 1px solid {THEME.border};
                font-size: {THEME.font_subtext_px}px;
            }}
            QHeaderView::section {{
                background: {THEME.surface};
                color: {THEME.text_muted};
                font-size: {THEME.font_micro_px}px;
                font-weight: bold;
                padding: 3px 6px;
                border: none;
                border-bottom: 1px solid {THEME.border};
            }}
            """
        )

        col_widths = [140, 60, 30, 80, -1]  # -1 = stretch
        for col, w in enumerate(col_widths):
            if w >= 0:
                table.setColumnWidth(col, w)
        table.horizontalHeader().setStretchLastSection(True)

        for row, pf in enumerate(params):
            # Name
            name_item = QTableWidgetItem(pf.label)
            name_item.setToolTip(pf.key)
            if pf.required:
                font = name_item.font()
                font.setBold(True)
                name_item.setFont(font)
            table.setItem(row, 0, name_item)

            # Type
            type_item = QTableWidgetItem(pf.type)
            type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            type_item.setForeground(QColor(THEME.text_muted))
            table.setItem(row, 1, type_item)

            # Required
            req_item = QTableWidgetItem("✓" if pf.required else "")
            req_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            if pf.required:
                req_item.setForeground(QColor(THEME.error))
            table.setItem(row, 2, req_item)

            # Default
            default_str = str(pf.default) if pf.default not in (None, "", [], {}) else "—"
            default_item = QTableWidgetItem(default_str)
            default_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            default_item.setForeground(QColor(THEME.text_muted))
            table.setItem(row, 3, default_item)

            # Description
            desc_item = QTableWidgetItem(pf.help)
            table.setItem(row, 4, desc_item)

            table.resizeRowToContents(row)

        return table


# ---------------------------------------------------------------------------
# Node list delegate (two-line items: humanized name + raw key)
# ---------------------------------------------------------------------------


class _NodeListItem(QListWidgetItem):
    """List item storing the raw node_type key with a two-line display."""

    def __init__(self, node_type: str) -> None:
        super().__init__()
        self._node_type = node_type
        self.setText(_humanize(node_type))
        self.setData(Qt.ItemDataRole.UserRole, node_type)
        self.setData(Qt.ItemDataRole.UserRole + 1, node_type)  # for key display
        self.setToolTip(node_type)

    @property
    def node_type(self) -> str:
        return self._node_type


class _SubgraphListItem(_NodeListItem):
    """A library subgraph in the list, keyed ``subgraph:<id>`` and shown by its name."""

    def __init__(self, defn: SubgraphDefinition) -> None:
        super().__init__(subgraph_key(defn.id))
        self.setText(defn.name)
        self.setToolTip(defn.description or defn.name)


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------


class NodeBrowserDialog(ThemedMixin, QDialog):
    """Node browser and reference dialog.

    Args:
        parent: Parent widget.
        mode: ``"add"`` shows Add+Cancel and records the chosen node in recent
              history. ``"view"`` shows only Close — used for reference lookups.
        initial_node_type: If provided, the dialog pre-selects this node and
              scrolls to it. Otherwise the first item in the current tab is
              selected.
        allowed_node_types: If provided, only these node types are offered, in
              every tab and in search results. Used by insert-at-output-port,
              where a node that cannot produce the port's return value would
              silently turn every rerouted edge unconditional.
        subgraph_library: When given (and no ``allowed_node_types`` filter is in
              force), a Subgraphs tab lists the library's definitions under
              ``subgraph:<id>`` keys, which ``selected_node_type`` returns as it
              would a node type. A port insert never offers them: an instance has
              no single output port to reroute through.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        mode: Literal["add", "view"] = "add",
        initial_node_type: str | None = None,
        allowed_node_types: set[str] | None = None,
        subgraph_library: SubgraphLibrary | None = None,
    ) -> None:
        super().__init__(parent)
        self._mode = mode
        self._selected_node_type: str | None = None
        self._allowed_node_types = allowed_node_types
        self._library = subgraph_library if allowed_node_types is None else None
        self._use_counts: dict[str, int] | None = None  # computed on first subgraph selection

        self.setWindowTitle("Node Browser" if mode == "view" else "Add Node")
        self.setMinimumSize(860, 520)
        self._restore_geometry()

        # ── Build all tab entries ──────────────────────────────────
        # Each entry: (tab_label, list[node_type]) — "All", "Recent", and "Favorites" are synthetic
        # Favorites is always at index 2.
        self._tabs_data: list[tuple[str, list[str]]] = [
            ("All", list(NODE_SPECS.keys())),
            ("Recent", SettingsManager.instance().recently_used_nodes),
            ("Favorites", SettingsManager.instance().favorite_nodes),
            *NODE_CATEGORIES,
        ]
        if self._library is not None:
            self._tabs_data.append(("Subgraphs", [subgraph_key(sid) for sid in self._library.ids()]))

        # ── Search bar ────────────────────────────────────────────
        self._search = SearchLineEdit("Search nodes…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search_changed)

        # ── Tab bar ───────────────────────────────────────────────
        self._tab_bar = QTabBar()
        self._tab_bar.setExpanding(False)
        for label, _ in self._tabs_data:
            self._tab_bar.addTab(label)
        self._tab_bar.currentChanged.connect(self._on_tab_changed)

        # ── Node list ─────────────────────────────────────────────
        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.setSpacing(1)
        self._list.currentItemChanged.connect(self._on_list_selection_changed)
        self._list.itemDoubleClicked.connect(self._on_double_click)

        # Empty-search label (shown when search has no results)
        self._empty_label = QLabel()
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.hide()

        # Stack: page 0 = list, page 1 = empty label
        self._list_stack = QStackedWidget()
        self._list_stack.addWidget(self._list)
        self._list_stack.addWidget(self._empty_label)

        # ── Left panel (list only — tab bar moves above the splitter) ──
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        left_layout.addWidget(self._list_stack, 1)
        left_widget.setMinimumWidth(200)

        # ── Detail panel ──────────────────────────────────────────
        self._detail = _DetailPanel()

        # ── Splitter ──────────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_widget)
        splitter.addWidget(self._detail)
        splitter.setSizes([240, 660])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)

        # ── Button row ────────────────────────────────────────────
        # Only built in "add" mode, so apply_theme_styles has to tolerate its absence.
        self._add_btn: QPushButton | None = None
        if mode == "add":
            self._add_btn = QPushButton("Add")
            self._add_btn.setDefault(True)
            self._add_btn.setEnabled(False)
            self._add_btn.clicked.connect(self._on_add)

            cancel_btn = QPushButton("Cancel")
            cancel_btn.clicked.connect(self.reject)

            btn_row = QHBoxLayout()
            btn_row.addStretch()
            btn_row.addWidget(cancel_btn)
            btn_row.addWidget(self._add_btn)
        else:
            close_btn = QPushButton("Close")
            close_btn.setDefault(True)
            close_btn.clicked.connect(self.accept)

            btn_row = QHBoxLayout()
            btn_row.addStretch()
            btn_row.addWidget(close_btn)

        # ── Root layout ───────────────────────────────────────────
        # Tab bar spans full width above the splitter so all category tabs
        # are visible without scrolling regardless of the list panel width.
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)
        root.addWidget(self._search)
        root.addWidget(self._tab_bar)
        root.addWidget(splitter, 1)
        root.addLayout(btn_row)

        # ── Initial population ────────────────────────────────────
        self._populate_list(self._tabs_data[0][1])  # "All" tab

        if initial_node_type and initial_node_type in NODE_SPECS:
            # Find and select the node; navigate to its category tab first
            cat = NODE_TYPE_TO_CATEGORY.get(initial_node_type)
            if cat:
                for i, (label, _) in enumerate(self._tabs_data):
                    if label == cat:
                        self._tab_bar.setCurrentIndex(i)
                        break
            self._select_node_type(initial_node_type)
        elif self._list.count() > 0:
            self._list.setCurrentRow(0)

        # Focus the search bar on open
        QTimer.singleShot(0, self._search.setFocus)

        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        """Restyle the four long-lived pieces of dialog chrome.

        The detail panel is not one of them — it is a ThemedMixin of its own and
        Qt delivers StyleChange to it directly.
        """
        self._search.setStyleSheet(
            f"padding: 4px 8px; border: 1px solid {THEME.border}; border-radius: 4px; font-size: {THEME.font_small_px}px;"
        )
        self._list.setStyleSheet(
            f"""
            QListWidget {{
                border: 1px solid {THEME.border};
                font-size: {THEME.font_small_px}px;
                background: {THEME.bg};
                alternate-background-color: {THEME.surface};
            }}
            QListWidget::item {{
                padding: 5px 8px;
            }}
            QListWidget::item:selected {{
                background: {THEME.accent};
                color: #ffffff;
            }}
            """
        )
        self._empty_label.setStyleSheet(f"color: {THEME.text_muted}; font-size: {THEME.font_small_px}px;")
        if self._add_btn is not None:
            self._add_btn.setStyleSheet(
                f"background: {THEME.accent}; color: #ffffff; padding: 5px 18px; border-radius: 4px; font-weight: bold;"
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def selected_node_type(self) -> str | None:
        """Returns the chosen node_type key, or None if nothing was selected."""
        return self._selected_node_type

    # ------------------------------------------------------------------
    # List population
    # ------------------------------------------------------------------

    def _populate_list(self, node_types: list[str]) -> None:
        """Fill the list widget with items for the given node_type keys.

        Every tab and the search results funnel through here, so the
        ``allowed_node_types`` filter only has to be applied once.
        """
        self._list.clear()
        for nt in node_types:
            if is_subgraph_key(nt):
                defn = self._library.get(nt[len(SUBGRAPH_KEY_PREFIX) :]) if self._library is not None else None
                if defn is not None:
                    self._list.addItem(_SubgraphListItem(defn))
                continue
            if nt not in NODE_SPECS:
                continue
            if self._allowed_node_types is not None and nt not in self._allowed_node_types:
                continue
            self._list.addItem(_NodeListItem(nt))

    @property
    def offered_node_types(self) -> list[str]:
        """Node types currently listed, after any ``allowed_node_types`` filtering."""
        items = (self._list.item(i) for i in range(self._list.count()))
        return [it.node_type for it in items if isinstance(it, _NodeListItem)]

    def _select_node_type(self, node_type: str) -> None:
        """Select the list item for node_type, scrolling to it."""
        for i in range(self._list.count()):
            item = self._list.item(i)
            if isinstance(item, _NodeListItem) and item.node_type == node_type:
                self._list.setCurrentItem(item)
                self._list.scrollToItem(item)
                return

    # ------------------------------------------------------------------
    # Slot: tab changed
    # ------------------------------------------------------------------

    def _on_tab_changed(self, index: int) -> None:
        if self._search.text():
            # While searching, tabs are visually inactive — ignore
            return
        _, node_types = self._tabs_data[index]
        self._populate_list(node_types)

        if self._list.count() == 0:
            if index == 1:
                self._empty_label.setText("Nodes you add will appear here.")
            elif index == 2:
                self._empty_label.setText(
                    "No favorites yet — click the star button in a node's detail panel to add one."
                )
            elif self._tabs_data[index][0] == "Subgraphs":
                self._empty_label.setText(
                    "No subgraphs in the library yet — right-click a region and choose "
                    "Save Region as Reusable Subgraph."
                )
            else:
                self._empty_label.setText("No nodes in this category.")
            self._list_stack.setCurrentIndex(1)
        else:
            self._list_stack.setCurrentIndex(0)
            self._list.setCurrentRow(0)

    # ------------------------------------------------------------------
    # Slot: search changed
    # ------------------------------------------------------------------

    def _on_search_changed(self, text: str) -> None:
        if not text:
            # Restore the current tab's contents
            self._tab_bar.setEnabled(True)
            self._on_tab_changed(self._tab_bar.currentIndex())
            return

        self._tab_bar.setEnabled(False)
        query = text.lower()
        matches: list[str] = []

        for node_type, spec in NODE_SPECS.items():
            if self._spec_matches(node_type, spec, query):
                matches.append(node_type)
        if self._library is not None:
            for sid in self._library.ids():
                defn = self._library.get(sid)
                if defn is not None and (query in defn.name.lower() or query in (defn.description or "").lower()):
                    matches.append(subgraph_key(sid))

        if matches:
            self._populate_list(matches)
            self._list.setCurrentRow(0)
            self._list_stack.setCurrentIndex(0)
        else:
            self._list.clear()
            self._empty_label.setText(f'No nodes match "{text}".')
            self._list_stack.setCurrentIndex(1)

        # If exactly one result, auto-select it
        if len(matches) == 1:
            self._list.setCurrentRow(0)

    @staticmethod
    def _spec_matches(node_type: str, spec: NodeSpec, query: str) -> bool:
        """Return True if any searchable field in the spec contains query."""
        if query in node_type:
            return True
        if query in _humanize(node_type).lower():
            return True
        if query in spec.description.lower():
            return True
        for pf in spec.params:
            if query in pf.label.lower() or query in pf.help.lower():
                return True
        if spec.usage_notes and query in spec.usage_notes.lower():
            return True
        return False

    # ------------------------------------------------------------------
    # Slot: list selection changed
    # ------------------------------------------------------------------

    def _on_list_selection_changed(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if not isinstance(current, _NodeListItem):
            self._selected_node_type = None
            if self._mode == "add":
                self._add_btn.setEnabled(False)
            return

        node_type = current.node_type
        self._selected_node_type = node_type

        if self._mode == "add":
            self._add_btn.setEnabled(True)

        if is_subgraph_key(node_type) and self._library is not None:
            sid = node_type[len(SUBGRAPH_KEY_PREFIX) :]
            defn = self._library.get(sid)
            if defn is not None:
                self._detail.show_subgraph(defn, self._library.origin(sid), self._used_by(sid))
            return

        spec = NODE_SPECS.get(node_type)
        if spec:
            self._detail.show_node(
                node_type,
                spec,
                on_see_also=self._on_see_also_clicked,
                on_favorite_toggled=self._on_favorite_toggled,
            )

    # ------------------------------------------------------------------
    # Slot: see-also link clicked
    # ------------------------------------------------------------------

    def _on_see_also_clicked(self, node_type: str) -> None:
        """Navigate the list to node_type when a see-also link is clicked."""
        # Switch to "All" tab so the target is always visible
        self._tab_bar.setCurrentIndex(0)
        self._select_node_type(node_type)

    # ------------------------------------------------------------------
    # Slot: favorite toggled
    # ------------------------------------------------------------------

    def _on_favorite_toggled(self, node_type: str, is_fav: bool) -> None:
        """Update the Favorites tab and list item stars when a node is toggled."""
        # Refresh Favorites tab data
        self._tabs_data[2] = ("Favorites", SettingsManager.instance().favorite_nodes)

        # If Favorites tab is active, surgically update the list without rebuilding
        if self._tab_bar.currentIndex() == 2:
            if not is_fav:
                # Remove the un-favorited item
                for i in range(self._list.count()):
                    item = self._list.item(i)
                    if isinstance(item, _NodeListItem) and item.node_type == node_type:
                        self._list.takeItem(i)
                        break
                if self._list.count() == 0:
                    self._empty_label.setText(
                        "No favorites yet — click the star button in a node's detail panel to add one."
                    )
                    self._list_stack.setCurrentIndex(1)
            else:
                # Add the newly-favorited item
                new_item = _NodeListItem(node_type)
                self._list.addItem(new_item)
                self._list_stack.setCurrentIndex(0)

    # ------------------------------------------------------------------
    # Slot: double-click (add mode only)
    # ------------------------------------------------------------------

    def _on_double_click(self, item: QListWidgetItem) -> None:
        if self._mode == "add" and isinstance(item, _NodeListItem):
            self._on_add()

    # ------------------------------------------------------------------
    # Slot: Add button
    # ------------------------------------------------------------------

    def _used_by(self, subgraph_id: str) -> int:
        """How many bot files in the library link ``subgraph_id``; the scan runs once per dialog."""
        if self._use_counts is None:
            from bartlebot.gui.canvas.bot_canvas import _resolve_bots_dir  # noqa: PLC0415

            configured = SettingsManager.instance().get("canvas", "bot_library_path", default="bots")
            self._use_counts = count_subgraph_uses(_resolve_bots_dir(configured, project_root()))
        return self._use_counts.get(subgraph_id, 0)

    def _on_add(self) -> None:
        if self._selected_node_type:
            # Recent stays node types only: a subgraph is found on its own tab.
            if not is_subgraph_key(self._selected_node_type):
                SettingsManager.instance().add_recently_used_node(self._selected_node_type)
            self.accept()

    # ------------------------------------------------------------------
    # Keyboard
    # ------------------------------------------------------------------

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        from PyQt6.QtCore import Qt as _Qt

        key = event.key()
        if key in (
            _Qt.Key.Key_Return,
            _Qt.Key.Key_Enter,
        ):
            if self._mode == "add" and self._selected_node_type:
                self._on_add()
            else:
                self.accept()
        elif key == _Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Geometry persistence
    # ------------------------------------------------------------------

    def _restore_geometry(self) -> None:
        geom = SettingsManager.instance().get("node_browser_geometry", default=None)
        if isinstance(geom, list) and len(geom) == 4:
            self.setGeometry(*geom)
        else:
            self.resize(980, 620)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        g = self.geometry()
        SettingsManager.instance().set("node_browser_geometry", value=[g.x(), g.y(), g.width(), g.height()])
        SettingsManager.instance().save()
        super().closeEvent(event)
