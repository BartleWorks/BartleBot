"""Unified bot canvas — single QGraphicsScene with Run and Edit modes.

BotCanvas  — top-level widget; owns the scene, toolbars, and properties panel.
"""

import enum
import logging
import time
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QEvent, QPointF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QKeySequence,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.schema import BotConfig
from bartlebot.gui.canvas.edge_item import EdgeItem
from bartlebot.gui.canvas.edit_scene import (
    EditScene,
    _EditView,
    _inject_required_options,
    _node_returns,
    _reachable_states,
    _validate_node,
)
from bartlebot.gui.canvas.graph_view import _MinimapView
from bartlebot.gui.canvas.node_item import NODE_H, NODE_W, NodeItem
from bartlebot.gui.canvas.param_panel import _ParamPanel
from bartlebot.gui.icon_utils import svg_icon
from bartlebot.gui.theme import THEME
from bartlebot.nodes.registry import NODE_SPECS
from bartlebot.settings_manager import SettingsManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TB_RUN = 0  # toolbar stack index for Run mode
_TB_EDIT = 1  # toolbar stack index for Edit mode

_IDLE = "idle"
_RUNNING = "running"
_PAUSED = "paused"


def _status_dot_color(state: str) -> str:
    if state == _RUNNING:
        return THEME.success
    if state == _PAUSED:
        return THEME.status_paused
    return THEME.text_muted  # idle / fallback


_ICON_SIZE = QSize(20, 20)
_BTN_SIZE = QSize(32, 32)


def _icon_btn(icon_name: str, tooltip: str) -> QPushButton:
    """Create a fixed-size icon-only toolbar button."""
    btn = QPushButton()
    btn.setIcon(svg_icon(icon_name))
    btn.setIconSize(_ICON_SIZE)
    btn.setFixedSize(_BTN_SIZE)
    btn.setToolTip(tooltip)
    return btn


class BotMode(enum.Enum):
    RUN = "run"
    EDIT = "edit"


# ---------------------------------------------------------------------------
# _BotCanvasPlaceholder — shown when no bot file is open
# ---------------------------------------------------------------------------


class _BotCanvasPlaceholder(QWidget):
    """Placeholder shown in BotCanvas when no bot file is open.

    Centered layout: title, recent files list, New Bot / Open Bot links.
    """

    new_bot_requested = pyqtSignal()
    open_bot_requested = pyqtSignal()
    open_recent_requested = pyqtSignal(str)  # path

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 16, 24, 16)
        outer.setSpacing(12)

        # Centered content column
        content_row = QHBoxLayout()
        content_row.setSpacing(0)
        content_row.addStretch(1)

        content = QWidget()
        content.setMaximumWidth(400)
        inner = QVBoxLayout(content)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(6)

        recent_hdr = QLabel("Recent Files")
        recent_hdr_font = recent_hdr.font()
        recent_hdr_font.setBold(True)
        recent_hdr.setFont(recent_hdr_font)
        recent_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        recent_hdr.setStyleSheet(f"color: {THEME.text_muted};")
        inner.addWidget(recent_hdr)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        inner.addWidget(sep)

        self._recent_area = self._make_scroll_area()
        inner.addWidget(self._recent_area, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(16)
        btn_row.addStretch(1)

        self._new_btn = QPushButton("New Bot")
        self._new_btn.setFlat(True)
        self._new_btn.setStyleSheet(
            f"color: {THEME.text_muted}; text-decoration: underline; border: none; padding: 2px;"
        )
        self._new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._new_btn.clicked.connect(self.new_bot_requested)
        btn_row.addWidget(self._new_btn)

        self._open_btn = QPushButton("Open Bot")
        self._open_btn.setFlat(True)
        self._open_btn.setStyleSheet(
            f"color: {THEME.text_muted}; text-decoration: underline; border: none; padding: 2px;"
        )
        self._open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_btn.clicked.connect(self.open_bot_requested)
        btn_row.addWidget(self._open_btn)

        btn_row.addStretch(1)
        inner.addLayout(btn_row)

        content_row.addWidget(content, stretch=2)
        content_row.addStretch(1)
        outer.addLayout(content_row, stretch=1)

    @staticmethod
    def _make_scroll_area() -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addStretch()
        area.setWidget(container)
        return area

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Rebuild recent files list from current settings."""
        paths = SettingsManager.instance().recent_bot_files
        self._rebuild_list(
            self._recent_area,
            items=[(Path(p).name, p, lambda *_, path=p: self.open_recent_requested.emit(path)) for p in paths],
            empty_hint="(No recent files)",
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _rebuild_list(
        area: QScrollArea,
        items: list[tuple[str, str, object]],
        empty_hint: str,
    ) -> None:
        container = area.widget()
        layout = container.layout()

        # Remove all existing widgets
        while layout.count() > 0:
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not items:
            hint = QLabel(empty_hint)
            hint.setWordWrap(True)
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint.setStyleSheet(f"color: {THEME.text_muted}; font-size: 11px;")
            layout.addWidget(hint)
        else:
            for label, tooltip, callback in items:
                btn = QPushButton(label)
                btn.setToolTip(tooltip)
                btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                btn.clicked.connect(callback)
                layout.addWidget(btn)

        layout.addStretch()


# ---------------------------------------------------------------------------
# _SearchBar — floating quick-search overlay (Ctrl+F)
# ---------------------------------------------------------------------------


class _SearchBar(QWidget):
    """Floating search bar overlaid on the graph view (Ctrl+F to open)."""

    text_changed = pyqtSignal(str)
    next_match = pyqtSignal()
    prev_match = pyqtSignal()
    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"QWidget {{ background-color: {THEME.surface}; border: 1px solid {THEME.border}; border-radius: 6px; }}"
            f"QLineEdit {{ border: none; background: transparent; color: {THEME.text}; font-size: 13px; }}"
            f"QPushButton {{ border: none; background: transparent; color: {THEME.text_muted}; font-size: 14px; }}"
            f"QPushButton:hover {{ color: {THEME.text}; }}"
            f"QLabel {{ border: none; color: {THEME.text_muted}; font-size: 11px; }}"
        )

        self._input = QLineEdit()
        self._input.setPlaceholderText("Search states…")
        self._input.setFixedWidth(200)
        self._input.installEventFilter(self)
        self._input.textChanged.connect(self.text_changed)

        self._count_lbl = QLabel()

        close_btn = QPushButton("×")
        close_btn.setFixedSize(22, 22)
        close_btn.clicked.connect(self.closed)

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 6, 4)
        row.setSpacing(6)
        row.addWidget(self._input)
        row.addWidget(self._count_lbl)
        row.addWidget(close_btn)
        self.adjustSize()
        self.setVisible(False)

    def set_count(self, current: int, total: int) -> None:
        self._count_lbl.setText(
            "" if total == 0 and not self._input.text() else "no matches" if total == 0 else f"{current}/{total}"
        )
        self.adjustSize()

    def focus_input(self) -> None:
        self._input.setFocus()
        self._input.selectAll()

    def current_text(self) -> str:
        return self._input.text()

    def eventFilter(self, obj, event) -> bool:
        if obj is self._input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.closed.emit()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Down):
                self.next_match.emit()
                return True
            if key == Qt.Key.Key_Up:
                self.prev_match.emit()
                return True
        return super().eventFilter(obj, event)


# ---------------------------------------------------------------------------
# BotCanvas — unified widget
# ---------------------------------------------------------------------------


class BotCanvas(QWidget):
    """Single canvas widget covering both Run and Edit modes."""

    config_changed = pyqtSignal()
    mode_changed = pyqtSignal(BotMode)
    run_requested = pyqtSignal()
    pause_requested = pyqtSignal()
    resume_requested = pyqtSignal()
    save_done = pyqtSignal()
    # Emitted with "log", "properties", or "bot" to switch the sidebar tab
    tab_switch = pyqtSignal(str)
    # (css_color, status, state_label, elapsed_text) — consumed by MainWindow title bar
    bot_status_changed = pyqtSignal(str, str, str, str)
    # Placeholder quick-start signals
    new_bot_requested = pyqtSignal()
    open_bot_requested = pyqtSignal()
    open_recent_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config: BotConfig | None = None
        self._mode = BotMode.RUN
        self._run_state = _IDLE
        self._start_time: float | None = None
        self._active_state: str | None = None
        self._run_externally_enabled: bool = False
        self._n_validation_errors: int = 0

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._tick)

        self._tab_idx: int = 0
        self._search_matches: list[str] = []
        self._search_match_idx: int = 0
        self._focus_region_id: str | None = None

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._scene = EditScene()
        self._view = _EditView(self._scene)
        self._param_panel = _ParamPanel()

        # ── Buttons ───────────────────────────────────────────────────
        self._run_pause_btn = _icon_btn("play", "Run bot")
        self._run_pause_btn.setEnabled(False)
        self._run_pause_btn.clicked.connect(self._on_run_pause_clicked)

        self.stop_button = _icon_btn("stop", "Stop bot")
        self.stop_button.setEnabled(False)  # enabled only when running/paused

        self._edit_btn = _icon_btn("pencil", "Enter Edit Mode")
        self._edit_btn.setEnabled(False)
        self._edit_btn.clicked.connect(self._on_edit_clicked)

        self._fit_btn = _icon_btn("fit", "Fit to Canvas (Ctrl+0)")
        self._fit_btn.clicked.connect(self._view.fit_scene)

        self._add_state_btn = _icon_btn("plus", "Add State")
        self._add_state_btn.clicked.connect(self._on_add_state)

        self._layout_btn = _icon_btn("layout", "Auto-Layout states")
        self._layout_btn.clicked.connect(self._on_layout_clicked)

        self._done_btn = _icon_btn("checkmark", "Apply changes and return to Run mode (does not save to disk)")
        self._done_btn.clicked.connect(self._on_done_clicked)

        self._save_done_btn = _icon_btn("save_done", "Save to disk and return to Run mode")
        self._save_done_btn.clicked.connect(self._on_save_done_clicked)

        # Track (button → current icon name) so changeEvent can re-tint on theme switch.
        # _run_pause_btn entry is updated by _set_run_icon() whenever the icon changes.
        self._btn_icons: dict[QPushButton, str] = {
            self._run_pause_btn: "play",
            self.stop_button: "stop",
            self._edit_btn: "pencil",
            self._fit_btn: "fit",
            self._add_state_btn: "plus",
            self._layout_btn: "layout",
            self._done_btn: "checkmark",
            self._save_done_btn: "save_done",
        }

        # ── Swappable center zone ─────────────────────────────────────
        run_center = QWidget()
        run_center_row = QHBoxLayout(run_center)
        run_center_row.setContentsMargins(0, 0, 0, 0)
        run_center_row.addWidget(self._run_pause_btn)
        run_center_row.addSpacing(4)
        run_center_row.addWidget(self.stop_button)

        edit_center = QWidget()
        edit_center_row = QHBoxLayout(edit_center)
        edit_center_row.setContentsMargins(0, 0, 0, 0)
        edit_center_row.addWidget(self._add_state_btn)
        edit_center_row.addSpacing(4)
        edit_center_row.addWidget(self._layout_btn)

        self._center_stack = QStackedWidget()
        self._center_stack.addWidget(run_center)  # _TB_RUN  = 0
        self._center_stack.addWidget(edit_center)  # _TB_EDIT = 1

        # ── Left section (68 px fixed: fit button) ─
        left_section = QWidget()
        left_section.setFixedWidth(68)
        left_row = QHBoxLayout(left_section)
        left_row.setContentsMargins(0, 0, 0, 0)
        left_row.setSpacing(0)
        left_row.addWidget(self._fit_btn)
        left_row.addStretch(1)

        # ── Right mode-toggle zone (68 px fixed) ─────────────────────
        # Run mode page: single icon right-aligned inside 68 px
        run_right = QWidget()
        run_right_row = QHBoxLayout(run_right)
        run_right_row.setContentsMargins(0, 0, 0, 0)
        run_right_row.addStretch(1)
        run_right_row.addWidget(self._edit_btn)

        # Edit mode page: two icons, fills 68 px naturally (32+4+32)
        edit_right = QWidget()
        edit_right_row = QHBoxLayout(edit_right)
        edit_right_row.setContentsMargins(0, 0, 0, 0)
        edit_right_row.addWidget(self._done_btn)
        edit_right_row.addSpacing(4)
        edit_right_row.addWidget(self._save_done_btn)

        self._right_stack = QStackedWidget()
        self._right_stack.setFixedWidth(68)
        self._right_stack.addWidget(run_right)  # _TB_RUN  = 0
        self._right_stack.addWidget(edit_right)  # _TB_EDIT = 1

        # ── Validation badge — shown left of center when errors/warnings exist ──
        # ── Persistent toolbar row ────────────────────────────────────
        self._toolbar = QWidget()
        toolbar = self._toolbar
        toolbar_row = QHBoxLayout(toolbar)
        toolbar_row.setContentsMargins(4, 4, 4, 4)
        toolbar_row.addWidget(left_section)
        toolbar_row.addStretch(1)
        toolbar_row.addWidget(self._center_stack)
        toolbar_row.addStretch(1)
        toolbar_row.addWidget(self._right_stack)

        # ── Validation banner — right-aligned below toolbar ───────────
        # Hidden when valid (takes no space); appears under the edit/save
        # button whose right_stack sits at the 4 px right margin of the toolbar.
        _pill_base = "border-radius: 4px; padding: 1px 7px;"
        self._error_pill = QLabel()
        self._error_pill.setStyleSheet(
            f"QLabel {{ background-color: {THEME.error}; color: {THEME.pill_text}; {_pill_base} }}"
        )
        self._error_pill.setVisible(False)

        self._warning_pill = QPushButton()
        self._warning_pill.setStyleSheet(
            f"QPushButton {{ background-color: {THEME.warning}; color: {THEME.pill_text}; {_pill_base} border: none; }}"
            f"QPushButton:hover {{ background-color: {THEME.pill_warning_hover}; }}"
        )
        self._warning_pill.setVisible(False)
        self._warning_pill.clicked.connect(self._cleanup_orphans)

        self._validation_banner = QWidget(self._view)
        self._validation_banner.setStyleSheet("background: transparent;")
        _banner_row = QHBoxLayout(self._validation_banner)
        _banner_row.setContentsMargins(4, 4, 4, 4)
        _banner_row.setSpacing(4)
        _banner_row.addWidget(self._error_pill)
        _banner_row.addWidget(self._warning_pill)
        self._validation_banner.setVisible(False)

        # ── Minimap overlay — bottom-right corner of the graph view ──────
        self._minimap = _MinimapView(self._scene, self._view, parent=self._view)
        self._minimap.setVisible(SettingsManager.instance().get("canvas", "minimap_visible", default=True))

        # ── Main layout: toolbar + validation banner + graph view ─────
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(toolbar)
        outer.addWidget(self._view, 1)

        # ── Search bar overlay — top-center of the graph view ────────
        self._search_bar = _SearchBar(parent=self._view)
        self._search_bar.text_changed.connect(self._on_search_text_changed)
        self._search_bar.next_match.connect(self._on_search_next)
        self._search_bar.prev_match.connect(self._on_search_prev)
        self._search_bar.closed.connect(self._close_search)

        # ── Focus breadcrumb overlay — top-left of the graph view ────
        self._focus_crumb = QWidget(self._view)
        self._focus_crumb.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._focus_crumb.setStyleSheet(
            f"QWidget {{ background-color: {THEME.surface}; border: 1px solid {THEME.border}; border-radius: 6px; }}"
            f"QLabel {{ border: none; color: {THEME.text}; font-size: 12px; padding: 0; }}"
            f"QPushButton {{ border: none; background: transparent; color: {THEME.text_muted}; font-size: 14px; }}"
            f"QPushButton:hover {{ color: {THEME.text}; }}"
        )
        self._focus_crumb_label = QLabel("Viewing: ")
        _close_focus_btn = QPushButton("×")
        _close_focus_btn.setFixedSize(22, 22)
        _close_focus_btn.clicked.connect(self._exit_focus_mode)
        _crumb_row = QHBoxLayout(self._focus_crumb)
        _crumb_row.setContentsMargins(8, 4, 6, 4)
        _crumb_row.setSpacing(4)
        _crumb_row.addWidget(self._focus_crumb_label)
        _crumb_row.addWidget(_close_focus_btn)
        self._focus_crumb.adjustSize()
        self._focus_crumb.setVisible(False)

        # ── Keyboard shortcuts ────────────────────────────────────────
        fit_shortcut = QShortcut(QKeySequence("Ctrl+0"), self)
        fit_shortcut.activated.connect(self._view.fit_scene)

        search_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        search_shortcut.activated.connect(self._show_search_bar)

        # ── Reposition banner overlay when the view or its viewport resizes ─
        self._view.installEventFilter(self)
        self._view.viewport().installEventFilter(self)

        # ── Wire internal signals ─────────────────────────────────────
        self._scene.graph_changed.connect(self._on_scene_changed)
        self._param_panel.properties_changed.connect(self._on_scene_changed)
        self._param_panel.edge_endpoints_changed.connect(self._scene.change_edge_endpoints)
        self._scene.selectionChanged.connect(self._on_selection_changed)
        self._scene.focus_region_requested.connect(self._enter_focus_mode)

        # ── Placeholder — shown until the first bot is loaded ────────
        self._placeholder = _BotCanvasPlaceholder(self)
        self._placeholder.new_bot_requested.connect(self.new_bot_requested)
        self._placeholder.open_bot_requested.connect(self.open_bot_requested)
        self._placeholder.open_recent_requested.connect(self.open_recent_requested)
        self._placeholder.show()

    # ------------------------------------------------------------------
    # Public API — bot loading
    # ------------------------------------------------------------------

    def setup(self, config: BotConfig) -> None:
        """Load a BotConfig. Enters RUN mode with the graph ready."""
        self._placeholder.setVisible(False)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        sm = SettingsManager.instance()
        self._minimap.setVisible(sm.get("canvas", "minimap_visible", default=True))
        self._exit_focus_mode()
        self._close_search()
        self._tab_idx = 0
        _inject_required_options(config)
        self._config = config
        # Auto-layout when no positions are saved and the setting is enabled
        if (
            config.states
            and all(s.position is None for s in config.states)
            and SettingsManager.instance().get("canvas", "auto_layout_on_load", default=True)
        ):
            from bartlebot.gui.canvas.layout import auto_layout

            auto_layout(config)
        self._scene.setup(config)
        self._param_panel.show_idle()
        self._view.fit_scene()
        self._minimap.fit_to_scene()
        self._reposition_banner()
        self._apply_mode(BotMode.RUN)
        self._edit_btn.setEnabled(True)
        self._update_validation_badge()
        self.mode_changed.emit(BotMode.RUN)

    def close_bot(self) -> None:
        """Unload the current bot and return to the placeholder."""
        self._exit_focus_mode()
        self._close_search()
        self._scene.teardown()
        self._update_validation_badge()
        self._config = None
        self._active_state = None
        self._edit_btn.setEnabled(False)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._minimap.setVisible(False)
        self._placeholder.refresh()
        self._placeholder.setVisible(True)

    def refresh_placeholder(self) -> None:
        """Rebuild the recent files list in the placeholder."""
        self._placeholder.refresh()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        tb_h = self._toolbar.height()
        self._placeholder.setGeometry(0, tb_h, self.width(), self.height() - tb_h)

    # ------------------------------------------------------------------
    # Public API — run state management
    # ------------------------------------------------------------------

    def set_run_enabled(self, enabled: bool) -> None:
        """Enable/disable the Run button (only when idle and in RUN mode)."""
        self._run_externally_enabled = enabled
        self._apply_run_btn_state()

    def _apply_run_btn_state(self) -> None:
        """Sync Run button enabled/tooltip from external readiness + validation errors."""
        if self._run_state != _IDLE or self._mode != BotMode.RUN:
            return
        if self._n_validation_errors > 0:
            self._run_pause_btn.setEnabled(False)
            noun = "error" if self._n_validation_errors == 1 else "errors"
            self._run_pause_btn.setToolTip(f"Cannot run: fix {self._n_validation_errors} {noun} first")
        elif self._run_externally_enabled:
            self._run_pause_btn.setEnabled(True)
            self._run_pause_btn.setToolTip("Bot is valid — click to run")
        else:
            self._run_pause_btn.setEnabled(False)
            self._run_pause_btn.setToolTip("")

    def eventFilter(self, obj, event) -> bool:
        if obj is self._view.viewport() and event.type() == QEvent.Type.Resize:
            self._reposition_banner()
        elif obj is self._view:
            if event.type() == QEvent.Type.Resize:
                self._reposition_banner()
            elif event.type() == QEvent.Type.KeyPress:
                key = event.key()
                mods = event.modifiers()
                if key == Qt.Key.Key_Escape and self._focus_region_id is not None:
                    self._exit_focus_mode()
                    return True
                if key == Qt.Key.Key_Tab and not (mods & Qt.KeyboardModifier.ShiftModifier):
                    self._tab_navigate(1)
                    return True
                if key in (Qt.Key.Key_Backtab,) or (key == Qt.Key.Key_Tab and mods & Qt.KeyboardModifier.ShiftModifier):
                    self._tab_navigate(-1)
                    return True
        return super().eventFilter(obj, event)

    def _reposition_banner(self) -> None:
        """Pin overlays: validation banner top-right, minimap bottom-right, search bar top-center, focus crumb top-left."""
        vp = self._view.viewport()
        vx, vy, vw, vh = vp.x(), vp.y(), vp.width(), vp.height()
        self._validation_banner.adjustSize()
        self._validation_banner.move(vx + vw - self._validation_banner.width() - 4, vy + 4)
        self._minimap.move(vx + vw - self._minimap.width() - 8, vy + vh - self._minimap.height() - 8)
        if hasattr(self, "_search_bar"):
            self._search_bar.adjustSize()
            self._search_bar.move(vx + (vw - self._search_bar.width()) // 2, vy + 8)
        if hasattr(self, "_focus_crumb"):
            self._focus_crumb.adjustSize()
            self._focus_crumb.move(vx + 8, vy + 8)

    def _apply_canvas_settings(self) -> None:
        """Re-read canvas settings and update live widgets. Called after Settings dialog OK."""
        sm = SettingsManager.instance()
        self._minimap.setVisible(sm.get("canvas", "minimap_visible", default=True))
        self._reposition_banner()

    def _active_state_label(self) -> str:
        """Return the display label for the current active state, falling back to its id."""
        if not self._active_state:
            return ""
        if self._config:
            state = next((s for s in self._config.states if s.id == self._active_state), None)
            if state and state.label:
                return state.label
        return self._active_state

    def _emit_bot_status(self) -> None:
        """Emit bot_status_changed with current run/edit state."""
        if self._mode == BotMode.EDIT:
            self.bot_status_changed.emit(THEME.text_muted, "Editing", "", "")
            return
        color = _status_dot_color(self._run_state)
        if self._run_state == _IDLE:
            self.bot_status_changed.emit(color, "Idle", "", "")
        elif self._run_state == _RUNNING:
            self.bot_status_changed.emit(color, "Running", self._active_state_label(), self._elapsed_str())
        elif self._run_state == _PAUSED:
            self.bot_status_changed.emit(color, "Paused", self._active_state_label(), self._elapsed_str())

    @property
    def param_panel(self) -> QWidget:
        """The _ParamPanel widget — placed in the left sidebar Properties tab."""
        return self._param_panel

    def set_clients_getter(self, fn: Callable[[], list]) -> None:
        """Pass a callable that returns the current list of hooked clients."""
        self._param_panel._get_clients = fn

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            for btn, icon_name in self._btn_icons.items():
                btn.setIcon(svg_icon(icon_name))

    def _set_run_icon(self, icon_name: str) -> None:
        """Set the run/pause button icon and record it for live theme updates."""
        self._btn_icons[self._run_pause_btn] = icon_name
        self._run_pause_btn.setIcon(svg_icon(icon_name))

    def set_running(self) -> None:
        self._run_state = _RUNNING
        self._set_run_icon("pause")
        self._run_pause_btn.setEnabled(True)
        self.stop_button.setEnabled(True)
        self._edit_btn.setEnabled(False)
        self._start_time = time.monotonic()
        self._tick_timer.start()
        self._emit_bot_status()
        self.tab_switch.emit("log")
        logger.info("=== Bot started ===")

    def set_paused(self) -> None:
        self._run_state = _PAUSED
        self._set_run_icon("play")
        self._run_pause_btn.setEnabled(True)
        self._emit_bot_status()
        logger.info("=== Paused ===")

    def set_idle(self) -> None:
        self._run_state = _IDLE
        self._set_run_icon("play")
        self._run_pause_btn.setEnabled(False)  # re-enabled by set_run_enabled
        self.stop_button.setEnabled(False)
        self._edit_btn.setEnabled(self._config is not None)
        self._tick_timer.stop()
        if self._active_state and self._active_state in self._scene._node_items:
            self._scene._node_items[self._active_state].set_active(False)
        self._active_state = None
        self._emit_bot_status()
        logger.info("=== Bot stopped ===")

    def set_state(self, state_id: str) -> None:
        """Highlight the active FSM state node."""
        if self._active_state and self._active_state in self._scene._node_items:
            self._scene._node_items[self._active_state].set_active(False)
        self._active_state = state_id
        if state_id in self._scene._node_items:
            self._scene._node_items[state_id].set_active(True)
        self._emit_bot_status()
        logger.info("Entered: %s", state_id)

    def on_transition(self, prev_state: str, result: str, next_state: str) -> None:
        if prev_state == next_state:
            logger.info("Result: %s → (retry %s)", result, next_state)
        else:
            logger.info("Result: %s → %s", result, next_state)

    # ------------------------------------------------------------------
    # Toolbar button handlers
    # ------------------------------------------------------------------

    def _on_run_pause_clicked(self) -> None:
        if self._run_state == _IDLE:
            self.run_requested.emit()
        elif self._run_state == _RUNNING:
            self.set_paused()
            self.pause_requested.emit()
        elif self._run_state == _PAUSED:
            self._run_state = _RUNNING
            self._set_run_icon("pause")
            self._emit_bot_status()
            logger.info("=== Resumed ===")
            self.resume_requested.emit()

    def enter_edit_mode(self) -> None:
        """Switch to Edit mode (no-op if the bot is currently running)."""
        if self._run_state != _IDLE:
            return
        self._apply_mode(BotMode.EDIT)
        self.mode_changed.emit(BotMode.EDIT)

    def _on_edit_clicked(self) -> None:
        self.enter_edit_mode()

    def _on_done_clicked(self) -> None:
        self._apply_mode(BotMode.RUN)
        self.mode_changed.emit(BotMode.RUN)
        self.config_changed.emit()

    def _on_save_done_clicked(self) -> None:
        self._apply_mode(BotMode.RUN)
        self.mode_changed.emit(BotMode.RUN)
        self.save_done.emit()

    def _on_add_state(self) -> None:
        center = self._view.mapToScene(self._view.viewport().rect().center())
        self._scene.add_state(QPointF(center.x() - NODE_W / 2, center.y() - NODE_H / 2))

    def _on_layout_clicked(self) -> None:
        if self._config is None or self._focus_region_id is not None:
            return
        from bartlebot.gui.canvas.layout import auto_layout

        self._scene._flush_positions()
        auto_layout(self._config)
        self._scene.setup(self._config)
        self._view.fit_scene()
        self._minimap.fit_to_scene()
        self.config_changed.emit()

    # ------------------------------------------------------------------
    # Focus Mode
    # ------------------------------------------------------------------

    def _enter_focus_mode(self, region_id: str) -> None:
        """Isolate a region's nodes in the view; hide everything else."""
        if self._config is None:
            return
        region = next((r for r in self._config.regions if r.id == region_id), None)
        if region is None:
            return
        self._exit_focus_mode()  # clean up any existing focus first
        self._focus_region_id = region_id
        member_set = set(region.members)

        for sid, node in self._scene._node_items.items():
            node.setVisible(sid in member_set)
        for s, _, d, edge in self._scene._edge_items:
            edge.setVisible(s in member_set and d in member_set)
        for rid, ri in self._scene._region_items.items():
            ri.setVisible(rid == region_id)

        self._focus_crumb_label.setText(f"Viewing: {region.label}")
        self._focus_crumb.setVisible(True)
        self._reposition_banner()

    def _exit_focus_mode(self) -> None:
        """Restore all nodes/edges/regions to visible; hide the breadcrumb."""
        if self._focus_region_id is None:
            return
        self._focus_region_id = None
        for node in self._scene._node_items.values():
            node.setVisible(True)
        for *_, edge in self._scene._edge_items:
            edge.setVisible(True)
        for ri in self._scene._region_items.values():
            ri.setVisible(True)
        if hasattr(self, "_focus_crumb"):
            self._focus_crumb.setVisible(False)

    # ------------------------------------------------------------------
    # Tab navigation
    # ------------------------------------------------------------------

    def _tab_navigate(self, direction: int) -> None:
        """Cycle node selection by BFS order and center the view on the node."""
        order = self._scene._get_tab_order()
        if not order:
            return
        selected = [item for item in self._scene.selectedItems() if isinstance(item, NodeItem)]
        if selected and selected[0].state_id in order:
            cur_idx = order.index(selected[0].state_id)
        else:
            cur_idx = self._tab_idx - direction  # step will land on _tab_idx
        next_idx = (cur_idx + direction) % len(order)
        self._tab_idx = next_idx
        node = self._scene._node_items.get(order[next_idx])
        if node:
            self._scene.clearSelection()
            if self._mode == BotMode.EDIT:
                node.setSelected(True)
            self._view.centerOn(node)

    # ------------------------------------------------------------------
    # Quick search
    # ------------------------------------------------------------------

    def _show_search_bar(self) -> None:
        self._search_bar.setVisible(True)
        self._reposition_banner()
        self._search_bar.focus_input()
        self._on_search_text_changed(self._search_bar.current_text())

    def _close_search(self) -> None:
        if hasattr(self, "_search_bar"):
            self._search_bar.setVisible(False)
        self._search_matches.clear()
        self._search_match_idx = 0
        for node in self._scene._node_items.values():
            node.set_search_match(False)

    def _on_search_text_changed(self, text: str) -> None:
        config = self._scene._config
        if not config:
            self._search_matches.clear()
            self._search_bar.set_count(0, 0)
            return
        q = text.strip().lower()
        matches: list[str] = []
        if q:
            for state in config.states:
                if q in (state.label or "").lower() or q in state.id.lower() or q in state.node_type.lower():
                    matches.append(state.id)
        self._search_matches = matches
        self._search_match_idx = 0
        # Apply highlights
        for sid, node in self._scene._node_items.items():
            node.set_search_match(sid in matches, is_current=(bool(matches) and sid == matches[0]))
        if matches:
            first = self._scene._node_items.get(matches[0])
            if first:
                self._view.centerOn(first)
        self._search_bar.set_count(1 if matches else 0, len(matches))
        self._reposition_banner()

    def _on_search_next(self) -> None:
        if not self._search_matches:
            return
        old = self._search_matches[self._search_match_idx]
        self._search_match_idx = (self._search_match_idx + 1) % len(self._search_matches)
        new = self._search_matches[self._search_match_idx]
        if old != new:
            old_node = self._scene._node_items.get(old)
            if old_node:
                old_node.set_search_match(True, is_current=False)
        new_node = self._scene._node_items.get(new)
        if new_node:
            new_node.set_search_match(True, is_current=True)
            self._view.centerOn(new_node)
        self._search_bar.set_count(self._search_match_idx + 1, len(self._search_matches))
        self._reposition_banner()

    def _on_search_prev(self) -> None:
        if not self._search_matches:
            return
        old = self._search_matches[self._search_match_idx]
        self._search_match_idx = (self._search_match_idx - 1) % len(self._search_matches)
        new = self._search_matches[self._search_match_idx]
        if old != new:
            old_node = self._scene._node_items.get(old)
            if old_node:
                old_node.set_search_match(True, is_current=False)
        new_node = self._scene._node_items.get(new)
        if new_node:
            new_node.set_search_match(True, is_current=True)
            self._view.centerOn(new_node)
        self._search_bar.set_count(self._search_match_idx + 1, len(self._search_matches))
        self._reposition_banner()

    # ------------------------------------------------------------------
    # Mode switching
    # ------------------------------------------------------------------

    def _apply_mode(self, mode: BotMode) -> None:
        self._exit_focus_mode()
        self._mode = mode
        if mode == BotMode.RUN:
            self._center_stack.setCurrentIndex(_TB_RUN)
            self._right_stack.setCurrentIndex(_TB_RUN)
            self._scene.set_interactive(False)
            self._param_panel.show_idle()
            self._emit_bot_status()
        else:
            self._center_stack.setCurrentIndex(_TB_EDIT)
            self._right_stack.setCurrentIndex(_TB_EDIT)
            self._scene.set_interactive(True)
            self._emit_bot_status()

    # ------------------------------------------------------------------
    # Scene change callbacks
    # ------------------------------------------------------------------

    def refresh_initial(self, initial_id: str) -> None:
        """Propagate initial state change to the canvas node indicator."""
        self._scene.refresh_initial(initial_id)

    def _on_scene_changed(self) -> None:
        if self._scene._config is not None:
            _inject_required_options(self._scene._config)
        self._refresh_node_labels()
        self._refresh_node_returns()
        self._refresh_initial()
        self._refresh_terminal()
        self._refresh_errors()
        self._update_validation_badge()
        self._minimap.fit_to_scene()
        if self._search_bar.isVisible():
            self._on_search_text_changed(self._search_bar.current_text())
        self.config_changed.emit()

    def _update_validation_badge(self) -> None:
        config = self._scene._config
        if config is None:
            self._validation_banner.setVisible(False)
            self._n_validation_errors = 0
            self._apply_run_btn_state()
            return

        error_lines: list[str] = []
        warning_lines: list[str] = []
        for state in config.states:
            name = state.label or state.id
            _, _, err_reasons, warn_reasons = _validate_node(state, config)
            for r in err_reasons:
                error_lines.append(f"{name}: {r}")
            for r in warn_reasons:
                warning_lines.append(f"{name}: {r}")

        n_errors = len(error_lines)
        n_warnings = len(warning_lines)

        if error_lines:
            noun = "error" if n_errors == 1 else "errors"
            self._error_pill.setText(f"\u25cf {n_errors} {noun}")
            self._error_pill.setToolTip("\n".join(error_lines))
            self._error_pill.setVisible(True)
        else:
            self._error_pill.setVisible(False)

        if warning_lines:
            noun = "warning" if n_warnings == 1 else "warnings"
            self._warning_pill.setText(f"\u25b2 {n_warnings} {noun}")
            n_orphans = sum(1 for line in warning_lines if "Unreachable from initial state" in line)
            tip = "\n".join(warning_lines)
            if n_orphans:
                noun_o = "state" if n_orphans == 1 else "states"
                tip += f"\n\nClick to delete {n_orphans} orphaned {noun_o}"
            self._warning_pill.setToolTip(tip)
            self._warning_pill.setVisible(True)
        else:
            self._warning_pill.setVisible(False)

        self._validation_banner.setVisible(bool(error_lines or warning_lines))
        if error_lines or warning_lines:
            self._reposition_banner()
        self._n_validation_errors = n_errors
        self._apply_run_btn_state()

    def _refresh_initial(self) -> None:
        if self._scene._config is None:
            return
        self._scene.refresh_initial(self._scene._config.initial)

    def _refresh_terminal(self) -> None:
        self._scene.refresh_terminal()

    def _refresh_errors(self) -> None:
        self._scene.refresh_errors()

    def _refresh_node_labels(self) -> None:
        if self._scene._config is None:
            return
        for state in self._scene._config.states:
            node = self._scene._node_items.get(state.id)
            if node:
                node.set_label(state.label)
                node.set_node_type(state.node_type)

    def _refresh_node_returns(self) -> None:
        if self._scene._config is None:
            return
        for state in self._scene._config.states:
            node = self._scene._node_items.get(state.id)
            if node:
                returns = _node_returns(state.node_type)
                if node._returns != returns:
                    node.set_returns(returns)
                    for s, _w, _d, edge in self._scene._edge_items:
                        if s == state.id:
                            edge._rebuild()

    def _cleanup_orphans(self) -> None:
        config = self._scene._config
        if config is None:
            return
        reachable = _reachable_states(config)
        orphan_ids = [s.id for s in config.states if s.id not in reachable]
        for sid in orphan_ids:
            self._scene._delete_node(sid, confirm=False)

    def _on_selection_changed(self) -> None:
        try:
            selected = self._scene.selectedItems()
        except RuntimeError:
            return
        if not selected:
            self._param_panel.show_idle()
            return
        item = selected[0]
        if isinstance(item, NodeItem) and self._scene._config:
            state = next((s for s in self._scene._config.states if s.id == item.state_id), None)
            if state:
                self._param_panel.show_state(state)
                self.tab_switch.emit("properties")
        elif isinstance(item, EdgeItem) and self._scene._config:
            for s, w, d, ei in self._scene._edge_items:
                if ei is item:
                    trans = next(
                        (t for t in self._scene._config.transitions if t.source == s and t.dest == d and t.when == w),
                        None,
                    )
                    if trans:
                        src_state = next((st for st in self._scene._config.states if st.id == s), None)
                        src_returns: list[str] = []
                        if src_state:
                            spec = NODE_SPECS.get(src_state.node_type)
                            if spec:
                                src_returns = [r.name for r in spec.returns]
                        option_names = [opt.name for opt in self._scene._config.options]
                        state_ids = [st.id for st in self._scene._config.states]
                        self._param_panel.show_transition(
                            trans,
                            src_returns,
                            option_names,
                            state_ids=state_ids,
                            all_transitions=self._scene._config.transitions,
                        )
                        self.tab_switch.emit("properties")
                    break
        else:
            self._param_panel.show_idle()

    def _tick(self) -> None:
        self._emit_bot_status()

    def _elapsed_str(self) -> str:
        if self._start_time is None:
            return "00:00:00"
        s = int(time.monotonic() - self._start_time)
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{sec:02d}"
