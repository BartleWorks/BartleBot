"""Central style token registry for BartleBot.

All colors, fonts, and application-level QSS rules live here.  Consumer
code imports the module-level ``THEME`` singleton and references tokens by
name:

    from bartlebot.gui.theme import THEME

    # In QSS f-strings:
    widget.setStyleSheet(f"color: {THEME.text_muted};")

    # In QPainter / QColor code:
    painter.setBrush(QBrush(THEME.qcolor("node_fill")))

    # As a QFont:
    widget.setFont(THEME.font_mono())

Tokens are organized in three tiers:

- **Tier 1 — Core (5):** ``bg``, ``surface``, ``text``, ``accent``,
  ``border``.  Changing just these produces a coherent custom theme.
- **Tier 2 — Semantic (3):** ``error``, ``warning``, ``success``.
  Status/state colors also used for log levels, badges, and pills.
- **Tier 3 — Derived:** Component-specific tokens that default to
  values derived from Tier 1+2 but can be individually overridden.

Swapping themes requires calling ``set_theme()`` which replaces the
module-level ``THEME`` singleton, rebuilds the QPalette, and reapplies
the global stylesheet.
"""

from __future__ import annotations

import winreg
from dataclasses import dataclass, fields

from PyQt6.QtGui import QColor, QFont, QPalette


@dataclass(frozen=True)
class _Theme:
    # ------------------------------------------------------------------
    # Tier 1 — Core palette
    # ------------------------------------------------------------------
    bg: str = "#ffffff"  # main window / canvas / input backgrounds
    surface: str = "#f0f2f5"  # panels, overlays, node fills
    text: str = "#1a1e28"  # primary text
    accent: str = "#3366bb"  # interactive: ports, selected edges, links
    border: str = "#b0b8c8"  # borders, dividers, splitter handles

    # ------------------------------------------------------------------
    # Tier 2 — Semantic colors
    # ------------------------------------------------------------------
    error: str = "#CC2222"  # errors, terminal badges, required indicators
    warning: str = "#9a6c00"  # warnings, paused-ish state, recovery badge
    success: str = "#33bb55"  # running, active, initial badges

    # ------------------------------------------------------------------
    # Text variants
    # ------------------------------------------------------------------
    text_muted: str = "#6b6b6b"  # secondary text: labels, hints, descriptions
    text_subtle: str = "#555555"  # sub-headers, third-level text
    # Disabled controls.  Deliberately dimmer than ``text_muted``: that token is
    # the colour of *enabled* secondary text, so reusing it for the disabled
    # palette group made a disabled button indistinguishable from an enabled one
    # carrying a hint label.  Pairing it with ``surface_disabled`` means disabled
    # reads as a change of shape (flatter, recessed), not just a change of shade.
    text_disabled: str = "#a8adb8"
    surface_disabled: str = "#e4e7ec"  # button/base fill for the Disabled palette group

    # ------------------------------------------------------------------
    # Node: Neutral (default / hover / selected)
    # ------------------------------------------------------------------
    node_fill: str = "#d5dce8"
    node_fill_hover: str = "#dce4f0"
    node_fill_selected: str = "#b8cce4"
    node_border: str = "#8a9ab8"

    # ------------------------------------------------------------------
    # Node: Active (currently executing)
    # ------------------------------------------------------------------
    node_fill_active: str = "#5cb85c"
    node_fill_active_dim: str = "#4aa84a"  # pulse animation low
    node_border_active: str = "#2e7d32"

    # ------------------------------------------------------------------
    # Node: Error (has validation errors)
    # ------------------------------------------------------------------
    node_fill_error: str = "#fce8e8"
    node_fill_error_hover: str = "#fdd4d4"
    # border → uses ``error`` directly

    # ------------------------------------------------------------------
    # Node: Warning (has validation warnings)
    # ------------------------------------------------------------------
    node_fill_warning: str = "#fff8e0"
    node_fill_warning_hover: str = "#ffedb3"
    # border → uses ``warning`` directly

    # ------------------------------------------------------------------
    # Edges and ports
    # ------------------------------------------------------------------
    edge_color: str = "#607090"
    edge_hover: str = "#4488cc"
    edge_selected: str = "#2255aa"
    port_color: str = "#3366bb"

    # ------------------------------------------------------------------
    # Status (idle → text_muted, running → success)
    # ------------------------------------------------------------------
    status_paused: str = "#cc9900"

    # ------------------------------------------------------------------
    # Validation pills
    # ------------------------------------------------------------------
    pill_warning_hover: str = "#9a6c00"  # matches warning — gold hues can't lighten and still pass WCAG AA
    pill_text: str = "#ffffff"
    # error bg → ``error``, warning bg → ``warning``

    # ------------------------------------------------------------------
    # Node-browser return-value chips
    # ------------------------------------------------------------------
    chip_success_bg: str = "#2e7d32"  # SUCCESS / TRUE — dark green, readable against white text
    chip_failure_bg: str = "#b71c1c"  # FAILURE / FALSE — dark red, readable against white text
    chip_failure_loop_bg: str = "#7f0000"  # FAILURE / FALSE self-loop — darker red for retry-in-place edges
    chip_neutral_bg: str = "#546e7a"  # any other return value — blue-grey
    chip_text: str = "#ffffff"  # label text on all chip variants

    # ------------------------------------------------------------------
    # Search match highlights
    # ------------------------------------------------------------------
    search_match_current: str = "#00ccee"
    search_match_other: str = "#0088aa"

    # ------------------------------------------------------------------
    # Minimap
    # ------------------------------------------------------------------
    minimap_bg: str = "rgba(240, 242, 245, 220)"
    minimap_border: str = "rgba(160, 168, 192, 200)"
    minimap_viewport: str = "#3c78d2"

    # ------------------------------------------------------------------
    # Canvas grid (dot grid shown when snap-to-grid is enabled)
    # ------------------------------------------------------------------
    grid_dot: str = "#c8cdd6"

    # ------------------------------------------------------------------
    # Region palette (numbered — users may customize to any color)
    # ------------------------------------------------------------------
    region_1: str = "#F59E0B"
    region_2: str = "#14B8A6"
    region_3: str = "#8B5CF6"
    region_4: str = "#EF4444"
    region_5: str = "#3B82F6"
    region_6: str = "#22C55E"

    REGION_PALETTE: tuple = (1, 2, 3, 4, 5, 6)

    # ------------------------------------------------------------------
    # Icon tint
    # ------------------------------------------------------------------
    icon_tint: str = "#404040"

    # ------------------------------------------------------------------
    # Game-embed client tile (helper methods below)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Font tokens
    # ------------------------------------------------------------------
    font_ui_family: str = "Segoe UI"
    font_mono_family: str = "Consolas"

    font_mono_size: int = 9
    font_node_main_size: int = 11
    font_node_sub_size: int = 8
    font_node_hdr_size: int = 7

    # ------------------------------------------------------------------
    # QColor helpers
    # ------------------------------------------------------------------

    def qcolor(self, token_name: str) -> QColor:
        """Return a QColor for the named token.

        Example::

            THEME.qcolor("node_fill")
        """
        return QColor(getattr(self, token_name))

    def region_color(self, key: int) -> QColor:
        """Return a QColor for the numbered region palette slot (1-6)."""
        return QColor(getattr(self, f"region_{key}"))

    def qcolor_client_pill_bg(self) -> QColor:
        """Semi-transparent black for the client-tile label pill background."""
        return QColor(0, 0, 0, 160)

    def qcolor_client_pill_text(self) -> QColor:
        """Near-opaque white for the client-tile label pill text."""
        return QColor(255, 255, 255, 230)

    # ------------------------------------------------------------------
    # QFont helpers
    # ------------------------------------------------------------------

    def font_mono(self) -> QFont:
        return QFont(self.font_mono_family, self.font_mono_size)

    def font_node_main(self) -> QFont:
        return QFont(self.font_ui_family, self.font_node_main_size)

    def font_node_sub(self) -> QFont:
        return QFont(self.font_ui_family, self.font_node_sub_size)

    def font_node_hdr(self) -> QFont:
        return QFont(self.font_ui_family, self.font_node_hdr_size)

    # ------------------------------------------------------------------
    # QPalette builder
    # ------------------------------------------------------------------

    def build_palette(self) -> QPalette:
        """Build a QPalette from the theme's core tokens.

        Used with ``QApplication.setStyle("Fusion")`` — Fusion fully
        respects palette colors for all widget types.
        """
        p = QPalette()

        # Active / Inactive groups (normal state)
        p.setColor(QPalette.ColorRole.Window, QColor(self.bg))
        p.setColor(QPalette.ColorRole.WindowText, QColor(self.text))
        p.setColor(QPalette.ColorRole.Base, QColor(self.surface))
        p.setColor(QPalette.ColorRole.AlternateBase, QColor(self.surface))
        p.setColor(QPalette.ColorRole.Text, QColor(self.text))
        p.setColor(QPalette.ColorRole.Button, QColor(self.surface))
        p.setColor(QPalette.ColorRole.ButtonText, QColor(self.text))
        p.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
        p.setColor(QPalette.ColorRole.Highlight, QColor(self.accent))
        p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        p.setColor(QPalette.ColorRole.ToolTipBase, QColor(self.surface))
        p.setColor(QPalette.ColorRole.ToolTipText, QColor(self.text))
        p.setColor(QPalette.ColorRole.PlaceholderText, QColor(self.text_muted))
        p.setColor(QPalette.ColorRole.Light, QColor(self.surface))
        p.setColor(QPalette.ColorRole.Mid, QColor(self.border))
        p.setColor(QPalette.ColorRole.Dark, QColor(self.border))
        p.setColor(QPalette.ColorRole.Shadow, QColor(self.border))
        p.setColor(QPalette.ColorRole.Link, QColor(self.accent))
        p.setColor(QPalette.ColorRole.LinkVisited, QColor(self.accent))

        # Disabled group.  Both the text *and* the fill shift, so a disabled
        # control reads as recessed rather than merely dimmer — text alone was
        # not a strong enough signal (text_disabled vs text is a shade apart,
        # and both are legible).  Done through the palette rather than QSS on
        # purpose: a QSS ``background`` rule on QPushButton makes
        # QStyleSheetStyle take over drawing the button in *every* state, which
        # would change how enabled buttons look across the whole app.
        disabled_text = QColor(self.text_disabled)
        disabled_fill = QColor(self.surface_disabled)
        p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, disabled_text)
        p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled_text)
        p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, disabled_text)
        p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.HighlightedText, disabled_text)
        p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button, disabled_fill)
        p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, disabled_fill)
        p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight, disabled_fill)

        return p

    # ------------------------------------------------------------------
    # Global QSS stylesheet
    # ------------------------------------------------------------------

    def global_stylesheet(self) -> str:
        """Return the application-level QSS string.

        Applied once to ``QApplication`` at startup (and re-applied on
        theme switch).  Contains rules that QPalette alone cannot express.
        """
        return f"""
            /* ── Splitter handles ── */
            QSplitter::handle {{
                background: {self.border};
            }}
            QSplitter::handle:horizontal {{
                width: 2px;
            }}
            QSplitter::handle:vertical {{
                height: 2px;
            }}

            /* ── Graphics view (canvas background) ── */
            QGraphicsView {{
                background: {self.bg};
                border: none;
            }}

            /* ── Scroll bars ── */
            QScrollBar:vertical {{
                background: {self.bg};
                width: 10px;
                margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {self.border};
                border-radius: 4px;
                min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            QScrollBar:horizontal {{
                background: {self.bg};
                height: 10px;
                margin: 0;
            }}
            QScrollBar::handle:horizontal {{
                background: {self.border};
                border-radius: 4px;
                min-width: 20px;
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
                width: 0;
            }}

            /* ── Tab bar ── */
            QTabWidget::pane {{
                border: 1px solid {self.border};
                background: {self.bg};
            }}
            QTabBar::tab {{
                background: {self.surface};
                color: {self.text_muted};
                border: 1px solid {self.border};
                border-bottom: none;
                padding: 4px 10px;
                margin-right: 1px;
            }}
            QTabBar::tab:selected {{
                background: {self.bg};
                color: {self.text};
            }}

            /* ── Menu bar ── */
            QMenuBar {{
                background: {self.surface};
                color: {self.text};
                border-bottom: 1px solid {self.border};
            }}
            QMenuBar::item:selected {{
                background: {self.accent};
                color: #ffffff;
            }}
            QMenu {{
                background: {self.surface};
                color: {self.text};
                border: 1px solid {self.border};
            }}
            QMenu::item:selected {{
                background: {self.accent};
                color: #ffffff;
            }}
            QMenu::separator {{
                height: 1px;
                background: {self.border};
                margin: 4px 8px;
            }}

            /* ── Tooltips ── */
            QToolTip {{
                background: {self.surface};
                color: {self.text};
                border: 1px solid {self.border};
                padding: 4px;
            }}
        """


# ---------------------------------------------------------------------------
# Light and Dark theme instances
# ---------------------------------------------------------------------------

LIGHT = _Theme()  # all defaults are the light-theme values

DARK = _Theme(
    # Core
    bg="#1e1e1e",
    surface="#2d2d30",
    text="#d4d4d4",
    accent="#4488cc",
    border="#474747",
    # Semantic
    error="#d93030",  # darkened from #f44747 — #ffffff pill text needs >=4.5:1 (was 3.60:1)
    warning="#9a6c00",  # darkened from #cca700 — #ffffff pill text needs >=4.5:1
    success="#4caf50",
    # Text variants
    text_muted="#9e9e9e",  # lightened from #808080 — was 3.5:1 on bg, now ~5.5:1
    text_subtle="#999999",
    text_disabled="#5a5a5a",  # ~2.4:1 on surface_disabled — legible as "off", not as content
    surface_disabled="#262629",  # between bg and surface: a disabled button sinks into the panel
    # Node: Neutral
    node_fill="#3c3c3c",
    node_fill_hover="#454545",
    node_fill_selected="#2a4a6b",
    node_border="#606060",
    # Node: Active
    node_fill_active="#2e7d32",
    node_fill_active_dim="#256b28",
    node_border_active="#4caf50",
    # Node: Error — darkened to restore 3:1 contrast with new error border (#d93030)
    node_fill_error="#3a1515",
    node_fill_error_hover="#4a2020",
    # Node: Warning
    node_fill_warning="#3d3520",
    node_fill_warning_hover="#4d4228",
    # Edges
    edge_color="#607090",
    edge_hover="#4488cc",
    edge_selected="#4488cc",
    port_color="#4488cc",
    # Status
    status_paused="#cca700",
    # Pills
    pill_warning_hover="#9a6c00",  # matches warning — gold hues can't lighten and still pass WCAG AA
    pill_text="#ffffff",
    # Node-browser chips (slightly lighter on dark bg for contrast)
    chip_success_bg="#388e3c",
    chip_failure_bg="#c62828",
    chip_failure_loop_bg="#610000",
    chip_neutral_bg="#607d8b",
    chip_text="#ffffff",
    # Search
    search_match_current="#00ccee",
    search_match_other="#0088aa",
    # Minimap
    minimap_bg="rgba(45, 45, 48, 220)",
    minimap_border="rgba(80, 80, 85, 200)",
    minimap_viewport="#4488cc",
    # Grid
    grid_dot="#404044",
    # Regions (same vivid palette — works on both light and dark)
    region_1="#F59E0B",
    region_2="#14B8A6",
    region_3="#8B5CF6",
    region_4="#EF4444",
    region_5="#3B82F6",
    region_6="#22C55E",
    # Icon
    icon_tint="#cccccc",
)


# ---------------------------------------------------------------------------
# Active theme singleton — proxy pattern
#
# All consumer files bind ``THEME`` once at import time:
#
#     from bartlebot.gui.theme import THEME
#
# ``_ThemeProxy`` forwards every attribute lookup to ``_current_theme`` at
# access time, so ``set_theme()`` only needs to update ``_current_theme``
# and every consumer automatically reads the new values without rebinding.
# ---------------------------------------------------------------------------


class _ThemeProxy:
    """Transparent proxy for the active ``_Theme`` instance.

    Attribute reads are forwarded to ``_current_theme`` at call time, not at
    import time, so theme switches are immediately visible everywhere.
    """

    __slots__ = ()

    def __getattr__(self, name: str):  # type: ignore[override]
        return getattr(_current_theme, name)


_current_theme: _Theme = LIGHT
THEME: _Theme = _ThemeProxy()  # type: ignore[assignment]


def active_theme() -> _Theme:
    """The ``_Theme`` instance currently in force.

    ``THEME`` is a proxy that forwards attribute reads, so it cannot be stashed and
    restored; anything that needs to put the theme back the way it found it — a test
    pinning a theme, a preview that swaps one in — needs the instance itself.
    """
    return _current_theme


def _detect_system_dark_mode() -> bool:
    """Return True if Windows is set to dark mode (AppsUseLightTheme=0)."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return value == 0
    except OSError:
        return False


def resolve_theme(mode: str) -> _Theme:
    """Return the ``_Theme`` instance for the given mode string.

    *mode* is one of ``"light"``, ``"dark"``, or ``"system"``.
    """
    if mode == "dark":
        return DARK
    if mode == "light":
        return LIGHT
    # "system" — follow Windows setting
    return DARK if _detect_system_dark_mode() else LIGHT


def set_theme(mode: str) -> None:
    """Update the active theme.

    ``THEME`` is a proxy, so all consumers pick up the change immediately on
    the next attribute access.  Call ``QApplication.setPalette`` and
    ``QApplication.setStyleSheet`` afterwards to push the new palette/QSS to
    all existing widgets.
    """
    global _current_theme  # noqa: PLW0603
    _current_theme = resolve_theme(mode)


def set_theme_instance(theme: _Theme) -> None:
    """Set the active theme to an arbitrary ``_Theme`` instance.

    Used for the custom theme mode where the caller has already constructed
    the instance (e.g. from user-defined color overrides).
    """
    global _current_theme  # noqa: PLW0603
    _current_theme = theme


# ---------------------------------------------------------------------------
# Custom theme support
# ---------------------------------------------------------------------------

# All color token names (string fields of _Theme, excluding font fields).
# This is the authoritative list for the Theme editor tab.
_COLOR_TOKENS: list[str] = [
    f.name
    for f in fields(_Theme)
    if f.name
    not in {
        "font_ui_family",
        "font_mono_family",
        "font_mono_size",
        "font_node_main_size",
        "font_node_sub_size",
        "font_node_hdr_size",
        "REGION_PALETTE",
        # rgba() strings that QColorDialog cannot round-trip
        "minimap_bg",
        "minimap_border",
        # node-browser chip colors — derived from semantic success/failure, not independently editable
        "chip_success_bg",
        "chip_failure_bg",
        "chip_failure_loop_bg",
        "chip_neutral_bg",
        "chip_text",
    }
]


def build_custom_theme(overrides: dict, base: _Theme | None = None) -> _Theme:
    """Build a ``_Theme`` from a flat ``{token_name: hex_color}`` dict.

    Missing tokens fall back to *base*.  When *base* is ``None`` (the default)
    it is auto-detected from the Windows system theme preference, so a first-
    run custom theme on a dark-mode machine starts from ``DARK`` rather than
    always defaulting to ``LIGHT``.
    """
    if base is None:
        base = DARK if _detect_system_dark_mode() else LIGHT
    kwargs = {token: overrides.get(token, getattr(base, token)) for token in _COLOR_TOKENS}
    # Keep the rgba tokens at their base-theme defaults (not user-editable)
    kwargs["minimap_bg"] = base.minimap_bg
    kwargs["minimap_border"] = base.minimap_border
    return _Theme(**kwargs)
