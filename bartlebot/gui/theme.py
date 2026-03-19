"""Central style token registry for BartleBot.

All colors, fonts, and application-level QSS rules live here.  Consumer
code imports the module-level ``THEME`` singleton and references tokens by
name:

    from bartlebot.gui.theme import THEME

    # In QSS f-strings:
    widget.setStyleSheet(f"color: {THEME.muted_text};")

    # In QPainter / QColor code:
    painter.setBrush(QBrush(THEME.qcolor("node_fill_normal")))

    # As a QFont:
    widget.setFont(THEME.font_mono())

Swapping themes later requires only replacing the ``THEME`` assignment at the
bottom of this file (or patching it at startup) with a different ``_Theme``
subclass instance.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtGui import QColor, QFont


@dataclass(frozen=True)
class _Theme:
    # ------------------------------------------------------------------
    # Text colours
    # ------------------------------------------------------------------
    muted_text:   str = "#999999"   # sidebar field labels
    muted_text_alt: str = "#888888"  # placeholder / secondary muted text
    subtle_text:  str = "#555555"   # section sub-headers (returns, overrides)
    error_text:   str = "#cc4444"   # inline validation error messages
    error_border: str = "#CC2222"   # required-field empty border / badges

    # ------------------------------------------------------------------
    # Chrome
    # ------------------------------------------------------------------
    splitter_handle: str = "#888888"

    # ------------------------------------------------------------------
    # Bot run-state status dots
    # ------------------------------------------------------------------
    status_idle:    str = "#888888"
    status_running: str = "#33bb55"
    status_paused:  str = "#cc9900"

    # ------------------------------------------------------------------
    # Log level colours
    # ------------------------------------------------------------------
    log_debug:   str = "#5599ff"
    log_info:    str = "#55cc55"
    log_warning: str = "#ddcc00"
    log_error:   str = "#ff5555"

    # ------------------------------------------------------------------
    # Validation pills (error / warning banners on the canvas toolbar)
    # ------------------------------------------------------------------
    pill_error_bg:         str = "#cc2222"
    pill_warning_bg:       str = "#bb7700"
    pill_warning_bg_hover: str = "#cc8800"
    pill_text:             str = "#ffffff"

    # ------------------------------------------------------------------
    # Node canvas — fills
    # ------------------------------------------------------------------
    node_fill_normal:        str = "#d5dce8"
    node_fill_hover:         str = "#dce4f0"
    node_fill_active:        str = "#5cb85c"
    node_fill_active_dim:    str = "#4aa84a"
    node_fill_selected:      str = "#b8cce4"
    node_fill_error:         str = "#fce8e8"
    node_fill_error_hover:   str = "#fdd4d4"
    node_fill_warning:       str = "#fff8e0"
    node_fill_warning_hover: str = "#ffedb3"

    # ------------------------------------------------------------------
    # Node canvas — borders
    # ------------------------------------------------------------------
    node_border_normal:   str = "#8a9ab8"
    node_border_active:   str = "#2e7d32"
    node_border_selected: str = "#2255aa"
    node_border_error:    str = "#CC2222"
    node_border_warning:  str = "#BB7700"

    # ------------------------------------------------------------------
    # Node canvas — badges & labels
    # ------------------------------------------------------------------
    badge_initial:          str = "#22AA44"
    badge_terminal:         str = "#CC2222"
    node_sublabel_inactive: str = "#888888"

    # ------------------------------------------------------------------
    # Edges and ports
    # ------------------------------------------------------------------
    edge_color:      str = "#607090"
    edge_hover_color: str = "#4488cc"
    edge_sel_color:  str = "#2255aa"
    port_color:      str = "#3366bb"
    port_label_color: str = "#444444"

    # ------------------------------------------------------------------
    # Game-embed client tile
    # ------------------------------------------------------------------
    client_tile_focus_border: str = "#cc2222"
    placeholder_box_border:   str = "#555555"   # border of the no-client placeholder box

    # ------------------------------------------------------------------
    # Font tokens
    # ------------------------------------------------------------------
    font_ui_family:   str = "Segoe UI"
    font_mono_family: str = "Consolas"

    font_mono_size:       int = 9
    font_node_main_size:  int = 9
    font_node_sub_size:   int = 6
    font_node_hdr_size:   int = 7

    # ------------------------------------------------------------------
    # QColor helpers
    # ------------------------------------------------------------------

    def qcolor(self, token_name: str) -> QColor:
        """Return a QColor for the named hex token.

        Example::

            THEME.qcolor("node_fill_normal")
        """
        return QColor(getattr(self, token_name))

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
    # Global QSS stylesheet
    # ------------------------------------------------------------------

    def global_stylesheet(self) -> str:
        """Return the application-level QSS string.

        Applied once to ``QApplication`` at startup, before any windows are
        constructed.  Contains rules that apply app-wide and cannot reasonably
        be expressed per-widget.
        """
        return f"""
            QSplitter::handle {{
                background: {self.splitter_handle};
            }}
            QSplitter::handle:horizontal {{
                width: 2px;
            }}
            QSplitter::handle:vertical {{
                height: 2px;
            }}

        """


# ---------------------------------------------------------------------------
# Singleton — replace with a different _Theme subclass for alternate themes
# ---------------------------------------------------------------------------

THEME: _Theme = _Theme()
