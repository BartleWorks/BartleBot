"""Settings tab: Theme customization."""

from __future__ import annotations

import json
import logging

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bartlebot.gui.theme import _COLOR_TOKENS, DARK, LIGHT, _Theme
from bartlebot.settings_manager import SettingsManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Section definitions — matches the comment blocks in theme.py
# ---------------------------------------------------------------------------

_SECTIONS: list[tuple[str, list[str]]] = [
    ("Core", ["bg", "surface", "text", "accent", "border"]),
    ("Semantic", ["error", "warning", "success"]),
    ("Text Variants", ["text_muted", "text_subtle"]),
    ("Node: Neutral", ["node_fill", "node_fill_hover", "node_fill_selected", "node_border"]),
    ("Node: Active", ["node_fill_active", "node_fill_active_dim", "node_border_active"]),
    ("Node: Error", ["node_fill_error", "node_fill_error_hover"]),
    ("Node: Warning", ["node_fill_warning", "node_fill_warning_hover"]),
    ("Edges & Ports", ["edge_color", "edge_hover", "edge_selected", "port_color"]),
    ("Status & Pills", ["status_paused", "pill_warning_hover", "pill_text"]),
    ("Search", ["search_match_current", "search_match_other"]),
    ("Minimap", ["minimap_viewport"]),
    ("Regions", ["region_1", "region_2", "region_3", "region_4", "region_5", "region_6"]),
    ("Icon", ["icon_tint"]),
]

# Human-readable label overrides (token_name → display label)
_LABELS: dict[str, str] = {
    "bg": "Background",
    "surface": "Surface / panels",
    "text": "Primary text",
    "accent": "Accent / links",
    "border": "Border / dividers",
    "error": "Error",
    "warning": "Warning",
    "success": "Success / running",
    "text_muted": "Muted text",
    "text_subtle": "Subtle text",
    "node_fill": "Fill",
    "node_fill_hover": "Fill (hover)",
    "node_fill_selected": "Fill (selected)",
    "node_border": "Border",
    "node_fill_active": "Fill (active)",
    "node_fill_active_dim": "Fill (active dim)",
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
    "pill_text": "Pill text",
    "search_match_current": "Current match",
    "search_match_other": "Other matches",
    "minimap_viewport": "Viewport rect",
    "region_1": "Region 1",
    "region_2": "Region 2",
    "region_3": "Region 3",
    "region_4": "Region 4",
    "region_5": "Region 5",
    "region_6": "Region 6",
    "icon_tint": "Icon tint",
}

# Contrast pairs to check: (text_token, background_token, display_name)
_CONTRAST_PAIRS: list[tuple[str, str, str]] = [
    ("text", "bg", "text on bg"),
    ("text", "surface", "text on surface"),
    ("text_muted", "bg", "muted text on bg"),
    ("text_subtle", "bg", "subtle text on bg"),
    ("text_muted", "surface", "muted text on surface"),
    ("pill_text", "error", "pill text on error"),
    ("pill_text", "warning", "pill text on warning"),
]

_WCAG_AA_NORMAL = 4.5  # minimum for normal-size text


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
    # Choose black or white label text based on which has better contrast
    label_color = (
        "#000000" if _wcag_contrast(hex_color, "#000000") >= _wcag_contrast(hex_color, "#ffffff") else "#ffffff"
    )
    return (
        f"QPushButton {{ background: {hex_color}; color: {label_color}; "
        f"font-family: Consolas; border: 1px solid #888; padding: 2px 6px; }}"
        f"QPushButton:hover {{ border: 1px solid #444; }}"
    )


def _section_label(title: str) -> QLabel:
    lbl = QLabel(title)
    font = lbl.font()
    font.setBold(True)
    font.setPointSize(font.pointSize() + 1)
    lbl.setFont(font)
    lbl.setContentsMargins(0, 10, 0, 2)
    return lbl


# ---------------------------------------------------------------------------
# ThemeTab
# ---------------------------------------------------------------------------


class ThemeTab(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        sm = SettingsManager.instance()
        saved_overrides: dict = sm.get("custom_theme", default={})

        # {token_name: current_hex} — the live form state
        self._colors: dict[str, str] = {}
        # {token_name: QPushButton}
        self._buttons: dict[str, QPushButton] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(6)

        # ------------------------------------------------------------------
        # Toolbar row
        # ------------------------------------------------------------------
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        toolbar.addWidget(QLabel("Reset base:"))

        self._base_combo = QComboBox()
        self._base_combo.addItem("Light", userData="light")
        self._base_combo.addItem("Dark", userData="dark")
        self._base_combo.setFixedWidth(80)
        toolbar.addWidget(self._base_combo)

        self._reset_btn = QPushButton("Reset to Base")
        # Hardcoded style so the button is always visible regardless of any
        # custom colors the user may have applied to the running theme.
        self._reset_btn.setStyleSheet(
            "QPushButton { background: #f0f0f0; color: #1a1e28; border: 1px solid #b0b8c8; padding: 4px 10px; }"
            "QPushButton:hover { background: #e0e0e0; }"
        )
        self._reset_btn.clicked.connect(self._on_reset)
        toolbar.addWidget(self._reset_btn)

        toolbar.addStretch()

        import_btn = QPushButton("Import…")
        import_btn.clicked.connect(self._on_import)
        toolbar.addWidget(import_btn)

        export_btn = QPushButton("Export…")
        export_btn.clicked.connect(self._on_export)
        toolbar.addWidget(export_btn)

        outer.addLayout(toolbar)

        # ------------------------------------------------------------------
        # Scrollable color form
        # ------------------------------------------------------------------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)

        form_widget = QWidget()
        form_layout = QVBoxLayout(form_widget)
        form_layout.setContentsMargins(0, 0, 8, 0)
        form_layout.setSpacing(0)

        for section_title, tokens in _SECTIONS:
            form_layout.addWidget(_section_label(section_title))
            row_form = QFormLayout()
            row_form.setSpacing(6)
            row_form.setContentsMargins(8, 0, 0, 4)
            for token in tokens:
                if token not in _COLOR_TOKENS:
                    continue
                # Initial color: saved override → LIGHT default
                hex_val = saved_overrides.get(token, getattr(LIGHT, token))
                self._colors[token] = hex_val

                btn = QPushButton(hex_val)
                btn.setFixedWidth(130)
                btn.setStyleSheet(_button_style(hex_val))
                btn.clicked.connect(lambda checked, t=token: self._pick_color(t))
                self._buttons[token] = btn

                row_form.addRow(_LABELS.get(token, token) + ":", btn)
            form_layout.addLayout(row_form)

        form_layout.addStretch()
        scroll.setWidget(form_widget)
        outer.addWidget(scroll, 1)

        # ------------------------------------------------------------------
        # Contrast info bar
        # ------------------------------------------------------------------
        self._contrast_label = QLabel()
        self._contrast_label.setWordWrap(True)
        self._contrast_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        outer.addWidget(self._contrast_label)

        self._refresh_contrast()

    # ------------------------------------------------------------------
    # Color picking
    # ------------------------------------------------------------------

    def _pick_color(self, token: str) -> None:
        current = QColor(self._colors[token])
        chosen = QColorDialog.getColor(current, self, f"Choose color for '{token}'")
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
        base: _Theme = DARK if self._base_combo.currentData() == "dark" else LIGHT
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
            line = f"{label}: {ratio:.1f}:1"
            tooltip_lines.append(line + (" ✓" if ratio >= _WCAG_AA_NORMAL else f" ✗ (needs {_WCAG_AA_NORMAL})"))
            if ratio < _WCAG_AA_NORMAL:
                failures.append(f"{label} ({ratio:.1f}:1)")

        if failures:
            self._contrast_label.setText(
                f"⚠ Low contrast: {', '.join(failures[:2])}" + (" (hover for details)" if len(failures) > 2 else "")
            )
            self._contrast_label.setStyleSheet("color: #cc6600; font-weight: bold;")
        else:
            self._contrast_label.setText("✓ All contrast ratios meet WCAG AA (4.5:1)")
            self._contrast_label.setStyleSheet("color: #2e7d32;")

        self._contrast_label.setToolTip("\n".join(tooltip_lines))

    # ------------------------------------------------------------------
    # Apply (called by SettingsDialog._on_accept)
    # ------------------------------------------------------------------

    def apply(self) -> None:
        SettingsManager.instance().set("custom_theme", value=dict(self._colors))
