"""Settings tab: Theme customization."""

from __future__ import annotations

import json
import logging

from bartleby.qt.theming.theme import _COLOR_TOKENS, DARK, LIGHT, _detect_system_dark_mode, _Theme, saved_color
from bartleby.qt.widgets.buttons.action_button import ActionButton
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from bartlebot.gui.settings.rows import SettingRow, SettingsForm
from bartlebot.gui.widgets.labels.rule_heading import RuleHeading
from bartlebot.settings_manager import SettingsManager, default_for

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Section definitions — matches the comment blocks in theme.py
# ---------------------------------------------------------------------------

_SECTIONS: list[tuple[str, list[str]]] = [
    ("Core", ["bg", "surface", "text", "accent", "border"]),
    ("Semantic", ["error", "warning", "success"]),
    ("Text variants", ["text_muted", "text_subtle"]),
    ("Node: neutral", ["node_fill", "node_fill_hover", "node_fill_selected", "node_border"]),
    ("Node: active", ["node_fill_active", "node_fill_active_dim", "node_border_active"]),
    ("Node: error", ["node_fill_error", "node_fill_error_hover"]),
    ("Node: warning", ["node_fill_warning", "node_fill_warning_hover"]),
    ("Edges and ports", ["edge_color", "edge_hover", "edge_selected", "port_color"]),
    (
        "Status and pills",
        ["status_paused", "pill_warning_hover", "pill_neutral_bg", "pill_activity_bg", "pill_this_bot_bg", "pill_text"],
    ),
    ("Search", ["search_match_current", "search_match_other"]),
    ("Minimap", ["minimap_viewport"]),
    ("Regions", ["region_1", "region_2", "region_3", "region_4", "region_5", "region_6"]),
    ("Icon", ["icon_tint"]),
    (
        "Combat DSL",
        [
            "syntax_keyword",
            "syntax_type",
            "syntax_target",
            "syntax_number",
            "syntax_string",
            "syntax_punct",
            "syntax_warning",
        ],
    ),
]

# Human-readable label overrides (token_name → display label)
_LABELS: dict[str, str] = {
    "bg": "Background",
    "surface": "Panels",
    "text": "Primary text",
    "accent": "Accent and links",
    "border": "Borders and dividers",
    "error": "Error",
    "warning": "Warning",
    "success": "Success and running",
    "text_muted": "Muted text",
    "text_subtle": "Subtle text",
    "node_fill": "Fill",
    "node_fill_hover": "Fill (hover)",
    "node_fill_selected": "Fill (selected)",
    "node_border": "Border",
    "node_fill_active": "Fill (active)",
    "node_fill_active_dim": "Fill (active, dimmed)",
    "node_border_active": "Border (active)",
    "node_fill_error": "Fill",
    "node_fill_error_hover": "Fill (hover)",
    "node_fill_warning": "Fill",
    "node_fill_warning_hover": "Fill (hover)",
    "edge_color": "Edge",
    "edge_hover": "Edge (hover)",
    "edge_selected": "Edge (selected)",
    "port_color": "Port",
    "status_paused": "Paused",
    "pill_warning_hover": "Warning pill (hover)",
    "pill_neutral_bg": "Neutral pill",
    "pill_activity_bg": "Activities tag pill",
    "pill_this_bot_bg": "This bot pill",
    "pill_text": "Pill text",
    "search_match_current": "Current match",
    "search_match_other": "Other matches",
    "minimap_viewport": "Visible area",
    "region_1": "Region 1",
    "region_2": "Region 2",
    "region_3": "Region 3",
    "region_4": "Region 4",
    "region_5": "Region 5",
    "region_6": "Region 6",
    "icon_tint": "Icon tint",
    "syntax_keyword": "Keywords",
    "syntax_type": "Spell types",
    "syntax_target": "Targets",
    "syntax_number": "Numbers",
    "syntax_string": "Quoted names",
    "syntax_punct": "Punctuation",
    "syntax_warning": "Unknown spell names",
}

# Contrast pairs to check: (text_token, background_token, display_name)
_CONTRAST_PAIRS: list[tuple[str, str, str]] = [
    ("text", "bg", "Text on background"),
    ("text", "surface", "Text on panels"),
    ("text_muted", "bg", "Muted text on background"),
    ("text_subtle", "bg", "Subtle text on background"),
    ("text_muted", "surface", "Muted text on panels"),
    ("pill_text", "error", "Pill text on error"),
    ("pill_text", "warning", "Pill text on warning"),
]

_WCAG_AA_NORMAL = 4.5  # minimum for normal-size text

# Width reserved for the label column in the color grid.
# Long enough for the widest label ("Fill (active dim):") at the UI font size.
_LABEL_COL_WIDTH = 150


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wcag_contrast(hex1: str, hex2: str) -> float:
    """Return the WCAG 2.1 contrast ratio between two hex colors."""

    def luminance(h: str) -> float:
        c = QColor(h)
        r, g, b = c.redF(), c.greenF(), c.blueF()

        def linearize(v: float) -> float:
            return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

        return 0.2126 * linearize(r) + 0.7152 * linearize(g) + 0.0722 * linearize(b)

    l1, l2 = sorted([luminance(hex1), luminance(hex2)], reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def _button_style(hex_color: str) -> str:
    """Return a QSS string for a color swatch button."""
    label_color = (
        "#000000" if _wcag_contrast(hex_color, "#000000") >= _wcag_contrast(hex_color, "#ffffff") else "#ffffff"
    )
    return (
        f"QPushButton {{ background: {hex_color}; color: {label_color}; "
        f"font-family: Consolas; border: 1px solid #888; padding: 2px 6px; }}"
        f"QPushButton:hover {{ border: 1px solid #444; }}"
    )


def _system_base() -> _Theme:
    """Return DARK or LIGHT based on the current Windows system preference."""
    return DARK if _detect_system_dark_mode() else LIGHT


# ---------------------------------------------------------------------------
# ThemeTab
# ---------------------------------------------------------------------------


class ThemeTab(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        sm = SettingsManager.instance()
        saved_overrides: dict = sm.get("custom_theme", default={})
        base = _system_base()

        # {token_name: current_hex} — the live form state
        self._colors: dict[str, str] = {}
        # {token_name: QPushButton}
        self._buttons: dict[str, QPushButton] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(6)

        # ------------------------------------------------------------------
        # Theme mode selector (moved here from General tab)
        # ------------------------------------------------------------------
        self._theme_combo = QComboBox()
        self._theme_combo.addItem("System (follows Windows)", userData="system")
        self._theme_combo.addItem("Light", userData="light")
        self._theme_combo.addItem("Dark", userData="dark")
        self._theme_combo.addItem("Custom", userData="custom")
        current_theme = sm.get_general("theme", default="system")
        for i in range(self._theme_combo.count()):
            if self._theme_combo.itemData(i) == current_theme:
                self._theme_combo.setCurrentIndex(i)
                break
        # A form for the one row; the color list below it is not a column of settings.
        mode_form = SettingsForm(margin=0)
        mode_form.add_heading("Mode")
        mode_form.add_row(
            SettingRow(
                "Theme",
                "Which colors BartleBot uses.",
                self._theme_combo,
                default=default_for("general", "theme"),
                details=(
                    "System changes along with Windows' light or dark setting. Custom starts from the current "
                    "colors and lets you edit each one."
                ),
            )
        )
        mode_form.finish(stretch=False)
        outer.addWidget(mode_form)

        # ------------------------------------------------------------------
        # Toolbar row (custom colors section)
        # ------------------------------------------------------------------
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        self._reset_btn = QPushButton("Reset to system default")
        # Hardcoded style so the button is always visible regardless of any
        # custom colors the user may have applied to the running theme.
        self._reset_btn.setStyleSheet(
            "QPushButton { background: #f0f0f0; color: #1a1e28; border: 1px solid #b0b8c8; padding: 4px 10px; }"
            "QPushButton:hover { background: #e0e0e0; }"
        )
        self._reset_btn.clicked.connect(self._on_reset)
        toolbar.addWidget(self._reset_btn)

        # The two beside it are ordinary ActionButtons, and the contrast is the point:
        # the escape hatch above keeps its literal styling, everything else on this tab
        # is drawn by the theme it edits.
        self._import_btn = ActionButton("download", "Import…", fill=False)
        self._import_btn.clicked.connect(self._on_import)
        toolbar.addWidget(self._import_btn)

        self._export_btn = ActionButton("upload", "Export…", fill=False)
        self._export_btn.clicked.connect(self._on_export)
        toolbar.addWidget(self._export_btn)

        outer.addLayout(toolbar)

        # ------------------------------------------------------------------
        # Scrollable color grid
        # ------------------------------------------------------------------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)

        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setSpacing(4)
        grid.setContentsMargins(0, 0, 8, 0)
        # Fixed label column; button column takes remaining space (left-aligned)
        grid.setColumnMinimumWidth(0, _LABEL_COL_WIDTH)
        grid.setColumnStretch(1, 1)

        grid_row = 0
        for section_title, tokens in _SECTIONS:
            hdr = RuleHeading(section_title)
            grid.addWidget(hdr, grid_row, 0, 1, 2)
            grid_row += 1

            for token in tokens:
                if token not in _COLOR_TOKENS:
                    continue

                hex_val = saved_color(saved_overrides, token, getattr(base, token))
                self._colors[token] = hex_val

                lbl = QLabel("  " + _LABELS.get(token, token) + ":")
                btn = QPushButton(hex_val)
                btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                btn.setStyleSheet(_button_style(hex_val))
                btn.clicked.connect(lambda checked, t=token: self._pick_color(t))
                self._buttons[token] = btn

                grid.addWidget(lbl, grid_row, 0, Qt.AlignmentFlag.AlignVCenter)
                grid.addWidget(btn, grid_row, 1, Qt.AlignmentFlag.AlignVCenter)
                grid_row += 1

        grid.setRowStretch(grid_row, 1)
        self._scroll = scroll
        scroll.setWidget(grid_widget)
        outer.addWidget(scroll, 1)

        # ------------------------------------------------------------------
        # Contrast info bar
        # ------------------------------------------------------------------
        self._contrast_label = QLabel()
        self._contrast_label.setWordWrap(True)
        self._contrast_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        outer.addWidget(self._contrast_label)
        # Takes the height the color list leaves while it is hidden, and none while it is
        # shown: the list's stretch outranks a factor of 0.
        outer.addStretch(0)

        self._refresh_contrast()

        # Enable/disable the custom-color section based on the selected mode
        self._theme_combo.currentIndexChanged.connect(self._on_mode_changed)
        self._set_custom_enabled(self._theme_combo.currentData() == "custom")

    # ------------------------------------------------------------------
    # Mode switching
    # ------------------------------------------------------------------

    def _on_mode_changed(self) -> None:
        self._set_custom_enabled(self._theme_combo.currentData() == "custom")

    def _set_custom_enabled(self, enabled: bool) -> None:
        self._reset_btn.setVisible(enabled)
        self._import_btn.setVisible(enabled)
        self._export_btn.setVisible(enabled)
        self._scroll.setVisible(enabled)
        self._contrast_label.setVisible(enabled)

    # ------------------------------------------------------------------
    # Color picking
    # ------------------------------------------------------------------

    def _pick_color(self, token: str) -> None:
        current = QColor(self._colors[token])
        chosen = QColorDialog.getColor(current, self, f"Choose Color for {_LABELS.get(token, token)}")
        if not chosen.isValid():
            return
        hex_val = chosen.name().lower()
        self._colors[token] = hex_val
        btn = self._buttons[token]
        btn.setText(hex_val)
        btn.setStyleSheet(_button_style(hex_val))
        self._refresh_contrast()

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def _on_reset(self) -> None:
        base = _system_base()
        for token, btn in self._buttons.items():
            hex_val: str = getattr(base, token)
            self._colors[token] = hex_val
            btn.setText(hex_val)
            btn.setStyleSheet(_button_style(hex_val))
        self._refresh_contrast()

    # ------------------------------------------------------------------
    # Import / Export
    # ------------------------------------------------------------------

    def _on_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Theme", "", "JSON files (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self._colors, fh, indent=2)
        except OSError as exc:
            logger.error("Failed to export theme: %s", exc)

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import Theme", "", "JSON files (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                data: dict = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Failed to import theme: %s", exc)
            return

        valid_tokens = set(_COLOR_TOKENS)
        for token, value in data.items():
            if token not in valid_tokens:
                continue
            if not isinstance(value, str) or not QColor(value).isValid():
                continue
            hex_val = QColor(value).name().lower()
            self._colors[token] = hex_val
            btn = self._buttons[token]
            btn.setText(hex_val)
            btn.setStyleSheet(_button_style(hex_val))

        self._refresh_contrast()

    # ------------------------------------------------------------------
    # Contrast checking
    # ------------------------------------------------------------------

    def _refresh_contrast(self) -> None:
        failures: list[str] = []
        tooltip_lines: list[str] = []

        for text_token, bg_token, label in _CONTRAST_PAIRS:
            text_hex = self._colors.get(text_token, getattr(LIGHT, text_token))
            bg_hex = self._colors.get(bg_token, getattr(LIGHT, bg_token))
            ratio = _wcag_contrast(text_hex, bg_hex)
            tooltip_lines.append(
                label + f": {ratio:.1f}:1" + (" ✓" if ratio >= _WCAG_AA_NORMAL else f" ✗ (needs {_WCAG_AA_NORMAL})")
            )
            if ratio < _WCAG_AA_NORMAL:
                failures.append(f"{label} ({ratio:.1f}:1)")

        if failures:
            self._contrast_label.setText(f"⚠ Low contrast: {', '.join(failures)}")
            self._contrast_label.setStyleSheet("color: #cc6600; font-weight: bold;")
        else:
            self._contrast_label.setText("✓ All contrast ratios meet WCAG AA (4.5:1)")
            self._contrast_label.setStyleSheet("color: #2e7d32;")

        self._contrast_label.setToolTip("\n".join(tooltip_lines))

    # ------------------------------------------------------------------
    # Apply (called by SettingsDialog._on_accept)
    # ------------------------------------------------------------------

    def apply(self) -> None:
        sm = SettingsManager.instance()
        sm.set_general("theme", self._theme_combo.currentData())
        sm.set("custom_theme", value=dict(self._colors))
