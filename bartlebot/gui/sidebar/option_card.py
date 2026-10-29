"""``OptionCard`` -- one folding group of bot options on Option Values and Options (#138).

The two option surfaces draw a bot's options as a column of these rather than one flat
list: *This dungeon*, *Team*, *Quicksell* and *General* (``bartlebot.options.option_groups``
decides which option goes where). Folding the groups nobody is working in is what puts
a thirteen-option bot on a 1080p screen.

ANATOMY
-------

A bordered card with a header band over a page-colored body::

    ┌──────────────────────────────────────────────────────────┐
    │▌▾ Quicksell ⚠  pets kept · keeping 2 of 58  App settings ◉│  <- header band
    ├──────────────────────────────────────────────────────────┤
    │  ...the ordinary option rows, gutter and all...          │  <- body
    └──────────────────────────────────────────────────────────┘

* The frame is ``CardFrame``'s border and radius, 1px and 4px.
* The band is the surface color, and a rule separates it from the body while the
  card is expanded. The band is what makes each group, and its title, easy to find.
* The stripe at the band's left edge is the override bar's color, lit when any value
  in the card is the user's -- folded or not, which is Unity's rule for a component
  header with prefab overrides. A folded card is the one place an override can be
  out of sight, so the stripe is what stops it being forgotten.
* The fold mark is the 10px disclosure triangle every folding sidebar header draws,
  tinted ``text_muted`` the way a nested header's is; the title is semibold under the
  bold section heading above. Depth is said by tint and weight, never by a different
  glyph.
* A ``⚠`` follows the title while anything in the card has a warning, whether the card
  is folded or not -- the assignment card's mark, and for its reason: a fold that could
  hide a problem would make folding unsafe.
* While folded, the band carries a one-line summary of the card, warning parts in the
  warning color. It is hidden while expanded, so nothing is on screen twice.
* At the right end, whatever the surface adds: Option Values puts Quicksell's source
  caption and switch there.

The header has **no option gutter**. The rows keep theirs, so every revert and ``?``
still sits in one column, but a header holding only a title would carry fifty pixels of
empty gutter before it.

WHAT IT DOES NOT DO
-------------------

It holds no option state and builds no rows: the surface fills ``body_layout`` and
drives ``set_marked``, ``set_summary`` and ``set_warning``. Nor does a card fold itself:
a click on the band asks, and ``CardFolds`` below -- one per surface -- answers. That
keeps one owner for the fold state, which is remembered across bots and shared by both
surfaces.

Painted from ``THEME`` at paint time -- the frame, the band, the stripe and the rule --
so a theme switch needs nothing re-derived but the fold button's glyph.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from bartleby.qt.theming.theme import THEME
from bartleby.qt.theming.themed import ThemedMixin, set_style_sheet
from bartleby.qt.widgets.labels.themed_label import ThemedTextLabel
from PyQt6.QtCore import QPoint, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QIcon, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from bartlebot.gui.menu_utils import popup_menu
from bartlebot.gui.sidebar.sidebar_section import DISCLOSURE_PX, HeadingAction, disclosure_pixmap
from bartlebot.options import option_groups
from bartlebot.options.option_groups import SummaryPart, summary_text

logger = logging.getLogger(__name__)

#: The card's outline and corner, ``CardFrame``'s.
CARD_RADIUS_PX = 4
#: The band's padding: the stripe plus a gap on the left, and the row inset elsewhere.
STRIPE_PX = 3
HEADER_MARGINS = (STRIPE_PX + 6, 4, 6, 4)
#: The body's inset on every side, and the gap between its rows.
BODY_MARGIN_PX = 6
BODY_SPACING_PX = 6
#: Between the fold mark and the title, and between the header's items.
FOLD_GAP_PX = 6
WARNING_MARK = "⚠"


class _FoldButton(ThemedMixin, QPushButton):
    """The fold mark and the title, as one button the keyboard can reach.

    Flat, so it reads as the band's heading rather than as a button on it. Focus comes
    from the keyboard only, and Enter or Space folds the card, as a click on the band
    does; the tooltip names what a press does next.
    """

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self._expanded = True
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setIconSize(QSize(DISCLOSURE_PX, DISCLOSURE_PX))
        font = THEME.ui_font(THEME.font_body_px)
        font.setWeight(font.Weight.DemiBold)
        self.setFont(font)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.refresh_theme_styles()

    def set_expanded(self, expanded: bool) -> None:
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self.refresh_theme_styles()

    def take_modifiers(self) -> Qt.KeyboardModifier:
        """The modifiers held for the click being handled, cleared once read.

        Read off the button's own event rather than ``QApplication.keyboardModifiers()``,
        which reports the last input event anywhere -- a Ctrl+click on another card a
        moment ago, say.
        """
        held, self._modifiers = self._modifiers, Qt.KeyboardModifier.NoModifier
        return held

    _modifiers = Qt.KeyboardModifier.NoModifier

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._modifiers = event.modifiers()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._modifiers = event.modifiers()
        super().keyPressEvent(event)

    def apply_theme_styles(self) -> None:
        # Nested: the muted tint is what says this fold sits inside the section's.
        self.setIcon(QIcon(disclosure_pixmap(self._expanded, tint=THEME.text_muted)))
        verb = "Collapse" if self._expanded else "Expand"
        self.setToolTip(f"{verb} {self.text()}")
        set_style_sheet(
            self,
            f"QPushButton {{ background: transparent; border: none; padding: 0; color: {THEME.text}; "
            f"text-align: left; }}",
        )


class _SummaryLine(QWidget):
    """The folded card's one-line summary: parts joined by ``·``, warnings in color.

    Painted rather than a ``QLabel`` because it has to do two things a label cannot do
    together: elide to whatever width is left, and color one part differently from the
    rest. Elided rich text is not something ``QLabel`` offers.

    Like ``ElidedLabel``, its minimum width is small and its size hint is the full text,
    so it takes what room the band has and degrades to an ellipsis when there is none.
    The whole line is the tooltip.
    """

    _MIN_WIDTH = 24
    _SEPARATOR = " · "

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._parts: list[SummaryPart] = []
        self._shown = False
        self.setFont(THEME.ui_font(THEME.font_subtext_px))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    @property
    def parts(self) -> list[SummaryPart]:
        return list(self._parts)

    @property
    def shown(self) -> bool:
        return self._shown

    def set_parts(self, parts: list[SummaryPart]) -> None:
        if parts == self._parts:
            return
        self._parts = list(parts)
        self._apply_tooltip()
        self.updateGeometry()
        self.update()

    def set_shown(self, shown: bool) -> None:
        """Draw the summary or leave its space blank.

        Never hidden outright: it is the band's stretch, and without it whatever the
        surface put at the right end would slide left to sit beside the title.
        """
        if shown == self._shown:
            return
        self._shown = shown
        self._apply_tooltip()
        self.update()

    def _apply_tooltip(self) -> None:
        self.setToolTip(summary_text(self._parts) if self._shown else "")

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        metrics = QFontMetrics(self.font())
        return QSize(metrics.horizontalAdvance(summary_text(self._parts)), metrics.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(self._MIN_WIDTH, QFontMetrics(self.font()).height())

    def paintEvent(self, event) -> None:  # noqa: ARG002, N802 - Qt override
        if not self._shown:
            return
        metrics = QFontMetrics(self.font())
        painter = QPainter(self)
        x, width = 0, self.width()
        baseline = (self.height() + metrics.ascent() - metrics.descent()) // 2
        muted, warning = QColor(THEME.text_muted), QColor(THEME.warning)
        for index, part in enumerate(self._parts):
            pieces = [(self._SEPARATOR, muted)] if index else []
            pieces.append((part.text, warning if part.warning else muted))
            for text, color in pieces:
                room = width - x
                if room <= 0:
                    painter.end()
                    return
                shown = metrics.elidedText(text, Qt.TextElideMode.ElideRight, room)
                painter.setPen(color)
                painter.drawText(x, baseline, shown)
                if shown != text:
                    painter.end()
                    return
                x += metrics.horizontalAdvance(text)
        painter.end()


class _CardHeader(QWidget):
    """The band: a click anywhere on it that no child control took folds the card.

    Activation is on release and only for a pointer that stayed put, as on every card
    in the app, so a press-and-sweep across the band does nothing.
    """

    def __init__(self, card: OptionCard) -> None:
        super().__init__(card)
        self._card = card
        self._press: QPoint | None = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        pressed, self._press = self._press, None
        if (
            event.button() == Qt.MouseButton.LeftButton
            and pressed is not None
            and self.rect().contains(event.position().toPoint())
            and (event.position().toPoint() - pressed).manhattanLength() < QApplication.startDragDistance()
        ):
            self._card.request_fold(event.modifiers())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt override
        # Reached by a right-click on the band, and by Shift+F10 or the Menu key while
        # the fold button has focus: a button does not take the event, so Qt hands it
        # up to here.
        self._card.menu_requested.emit(event.globalPos())
        event.accept()

    def paintEvent(self, event) -> None:  # noqa: ARG002, N802 - Qt override
        """The band, the stripe, and the rule under the band while the card is open.

        The band's top corners follow the card's, less its 1px border; folded, its
        bottom corners do too, since it is then the whole card.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        radius = CARD_RADIUS_PX - 1
        expanded = not self._card.is_collapsed

        band = _rounded(rect, radius, round_bottom=not expanded)
        painter.fillPath(band, QColor(THEME.surface))
        if self._card.is_marked:
            painter.save()
            painter.setClipPath(band)
            painter.fillRect(QRectF(rect.left(), rect.top(), STRIPE_PX, rect.height()), QColor(THEME.accent))
            painter.restore()
        if expanded:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setPen(QPen(QColor(THEME.border), 1))
            painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        painter.end()


def _rounded(rect: QRectF, radius: float, *, round_bottom: bool) -> QPainterPath:
    """*rect* with its top corners rounded, and its bottom corners too if asked."""
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    if not round_bottom:
        square = QPainterPath()
        square.addRect(QRectF(rect.left(), rect.center().y(), rect.width(), rect.height() / 2))
        path = path.united(square)
    return path


class OptionCard(QFrame):
    """A folding card of bot options: a header band and a body the surface fills.

    The card never folds itself. A click on the band, or on its title, emits
    ``fold_requested`` with whether the user held Ctrl, and the surface -- which owns
    the remembered fold state for every card -- calls ``set_collapsed`` back.
    """

    #: The user asked to fold or unfold this card; *solo* is True for Ctrl+click,
    #: which keeps this one open and collapses the rest (Blender's panels).
    fold_requested = pyqtSignal(bool)
    #: A right-click on the band, or the keyboard's menu key, at this global point.
    menu_requested = pyqtSignal(QPoint)

    def __init__(self, group_id: str, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._group_id = group_id
        self._title = title
        self._collapsed = False
        self._marked = False
        # Preferred, not Maximum: a folded-out description makes the body height-for-
        # width, and Maximum caps the card at a size hint computed at no particular
        # width -- the trap ``SidebarSection._expanded_v_policy`` records.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

        outer = QVBoxLayout(self)
        # Inside the 1px border the card paints, so neither the band nor the body
        # covers it.
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)

        self._header = _CardHeader(self)
        head = QHBoxLayout(self._header)
        head.setContentsMargins(*HEADER_MARGINS)
        head.setSpacing(FOLD_GAP_PX)

        self._fold_btn = _FoldButton(title)
        self._fold_btn.clicked.connect(lambda: self.request_fold(self._fold_btn.take_modifiers()))
        head.addWidget(self._fold_btn, 0)

        self._warning = ThemedTextLabel(WARNING_MARK, color="warning")
        head.addWidget(self._warning, 0)

        self._summary = _SummaryLine()
        head.addWidget(self._summary, 1)
        self._head_layout = head

        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(BODY_MARGIN_PX, BODY_MARGIN_PX, BODY_MARGIN_PX, BODY_MARGIN_PX)
        self._body_layout.setSpacing(BODY_SPACING_PX)

        outer.addWidget(self._header)
        outer.addWidget(self._body)

        # Visibility only once everything is parented: see "Never show a widget before
        # it has a parent" in gui/CLAUDE.md.
        self._warning.setVisible(False)

    # ------------------------------------------------------------------
    # What the surface reads
    # ------------------------------------------------------------------

    @property
    def group_id(self) -> str:
        return self._group_id

    @property
    def title(self) -> str:
        return self._title

    @property
    def is_collapsed(self) -> bool:
        return self._collapsed

    @property
    def is_marked(self) -> bool:
        """Whether the stripe is lit: a value somewhere in this card is the user's."""
        return self._marked

    @property
    def header(self) -> QWidget:
        """The band. Public so a test can click it as a user would."""
        return self._header

    @property
    def fold_button(self) -> QPushButton:
        return self._fold_btn

    @property
    def body(self) -> QWidget:
        return self._body

    @property
    def body_layout(self) -> QVBoxLayout:
        """Where the surface puts the card's rows."""
        return self._body_layout

    @property
    def summary_parts(self) -> list[SummaryPart]:
        return self._summary.parts

    @property
    def summary_shown(self) -> bool:
        """Whether the band is drawing its summary -- only while the card is folded."""
        return self._summary.shown

    @property
    def warning(self) -> str | None:
        """The warning the mark's tooltip carries, or None while there is none."""
        return self._warning.toolTip() if self._warning.isVisibleTo(self) else None

    # ------------------------------------------------------------------
    # What the surface sets
    # ------------------------------------------------------------------

    def add_header_widget(self, widget: QWidget, *, gap_before: int = 0) -> None:
        """Append *widget* to the right end of the band -- a caption, a switch."""
        if gap_before:
            self._head_layout.addSpacing(gap_before)
        self._head_layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_collapsed(self, collapsed: bool) -> None:
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self._body.setVisible(not collapsed)
        self._summary.set_shown(collapsed)
        self._fold_btn.set_expanded(not collapsed)
        self._header.update()

    def set_marked(self, marked: bool) -> None:
        if marked == self._marked:
            return
        self._marked = marked
        self._header.update()

    def set_summary(self, parts: list[SummaryPart]) -> None:
        self._summary.set_parts(parts)

    def set_warning(self, text: str | None) -> None:
        self._warning.setToolTip(text or "")
        self._warning.setVisible(bool(text))

    def request_fold(self, modifiers) -> None:
        """Ask the surface to fold or unfold this card, as a click on the band does."""
        self.fold_requested.emit(bool(modifiers & Qt.KeyboardModifier.ControlModifier))

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: ARG002, N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(QColor(THEME.border), 1))
        painter.setBrush(QColor(THEME.bg))
        painter.drawRoundedRect(rect, CARD_RADIUS_PX, CARD_RADIUS_PX)
        painter.end()


# ---------------------------------------------------------------------------
# Folding, for a whole surface of cards
# ---------------------------------------------------------------------------

#: The heading's two actions and the card menu's entries, so the menu, the tooltips and
#: the tests name them from one place. The verbs are Expand and Collapse -- the pair
#: VS Code, JetBrains, Visual Studio, Word and Unreal all use -- and each names what it
#: acts on, because an icon-only button has nothing else to say it.
COLLAPSE_ALL = "Collapse all groups"
EXPAND_ALL = "Expand all groups"
COLLAPSE_OTHERS = "Collapse others"
#: The menu's hint for the gesture that does the same thing (Blender's Ctrl+click).
COLLAPSE_OTHERS_HINT = "Ctrl+click"


class SettingsFoldStore:
    """The option surfaces' folds: one ``{group: collapsed}`` in ``settings.json``.

    #138 put them there before ``ui_state.json`` existed. They are four fixed keys, so
    they stayed rather than being moved.
    """

    def __init__(self, settings: Callable[[], Any]) -> None:
        self._settings = settings

    def read(self) -> dict[str, bool]:
        try:
            stored = self._settings().get("general", option_groups.COLLAPSED_SETTING, default=None)
        except Exception:
            logger.exception("Failed to read which option groups are collapsed")
            stored = None
        return option_groups.collapsed_groups(stored)

    def default(self, group: str) -> bool:
        return group in option_groups.FIRST_RUN_COLLAPSED

    def write(self, folds: dict[str, bool], changes: dict[str, bool]) -> None:  # noqa: ARG002
        try:
            self._settings().set("general", option_groups.COLLAPSED_SETTING, value=dict(folds))
        except Exception:
            logger.exception("Failed to remember which option groups are collapsed")


class UiStateFoldStore:
    """A surface's folds in ``ui_state.json``: only a fold that departs from its default.

    *defaults* names every group the surface draws, so a stored group it no longer has
    is pruned on read (``UiState.folds``). A default may depend on the page as it was
    built -- an incomplete card starting open -- and the reader's own answer, once given,
    is what is stored.
    """

    def __init__(self, surface: str, defaults: dict[str, bool], ui_state: Any = None) -> None:
        from bartlebot.gui.ui_state import UiState  # noqa: PLC0415 - keeps this module's imports light

        self._surface = surface
        self._defaults = dict(defaults)
        self._ui = ui_state if ui_state is not None else UiState()

    def read(self) -> dict[str, bool]:
        return self._ui.folds(self._surface, self._defaults)

    def default(self, group: str) -> bool:
        return self._defaults.get(group, False)

    def write(self, folds: dict[str, bool], changes: dict[str, bool]) -> None:  # noqa: ARG002
        for group, collapsed in changes.items():
            if group in self._defaults:
                self._ui.set_collapsed(self._surface, group, collapsed, default=self._defaults[group])


class CardHeadingActions(QWidget):
    """A row that holds a surface's Collapse all / Expand all where there is no section heading.

    The node page and the subgraph node page are pages of their own, not sections of
    one, so the two actions sit at the right end of the row above their cards. They are
    always drawn there: ``HeadingAction`` blanks its glyph until a section heading is
    reached for, and this row has no heading to reach for.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(2)

    def add_header_action(self, button: HeadingAction) -> None:
        self._row.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        button.reveal(True)


class CardFolds:
    """Which of a surface's cards are folded, and every way there is to change it.

    Option Values and Options each hold one, and so do the node page and the subgraph
    node page (#142). The answer is the *reader's*, and where it is kept is the
    *store*'s: the option surfaces keep one ``{group: collapsed}`` in ``settings.json``
    (``option_groups.COLLAPSED_SETTING``), the same for every bot and shared by both;
    the node pages keep theirs in ``ui_state.json`` (:class:`UiStateFoldStore`). Only
    one option surface is on screen at a time, so each re-reads it on the way in
    (``reload``) rather than the two being wired together.

    The ways to change it:

    * a click on a card's band, or its title from the keyboard -- that card;
    * Ctrl+click on a band -- that card open, every other one folded (Blender's panels);
    * the section heading's **Collapse all** and **Expand all**, Bar B's pair for
      regions with the same icons in the same order, drawn while the heading is hovered
      or focused and each enabled only while it has something to do;
    * the band's right-click menu, which holds all of the above and is the keyboard's
      route to the two heading actions.
    """

    def __init__(self, section, settings: Callable[[], Any] | None = None, *, store: Any = None) -> None:
        self._store = store if store is not None else SettingsFoldStore(settings)
        self._cards: dict[str, OptionCard] = {}
        self._folds = self._read()

        self.collapse_all_button = HeadingAction("region_collapse", tooltip=COLLAPSE_ALL)
        self.collapse_all_button.clicked.connect(self.collapse_all)
        self.expand_all_button = HeadingAction("region_expand", tooltip=EXPAND_ALL)
        self.expand_all_button.clicked.connect(self.expand_all)
        section.add_header_action(self.collapse_all_button)
        section.add_header_action(self.expand_all_button)
        self._refresh_buttons()

    @property
    def cards(self) -> dict[str, OptionCard]:
        return dict(self._cards)

    def set_cards(self, cards: dict[str, OptionCard]) -> None:
        """Take over a freshly built set of cards: wire them, and fold them as remembered."""
        self._cards = dict(cards)
        for group, card in self._cards.items():
            card.fold_requested.connect(lambda solo, g=group: self._on_fold_requested(g, solo))
            card.menu_requested.connect(lambda pos, g=group: self._show_menu(g, pos))
        self._apply()

    def reload(self) -> None:
        """Re-read the remembered answer -- the other surface may have changed it."""
        self._folds = self._read()
        self._apply()

    def set_store(self, store: Any) -> None:
        """Keep the folds in *store* from now on, and read them from it.

        A subgraph node page changes store with every definition it shows, and its
        defaults with every page it builds. Call it before ``set_cards``.
        """
        self._store = store
        self._folds = self._read()

    def set_group_collapsed(self, group: str, collapsed: bool) -> None:
        self._update({group: collapsed})

    def collapse_all(self) -> None:
        self._update(dict.fromkeys(self._cards, True))

    def expand_all(self) -> None:
        self._update(dict.fromkeys(self._cards, False))

    def collapse_others(self, keep: str) -> None:
        self._update({group: group != keep for group in self._cards})

    def card_menu(self, group: str) -> QMenu | None:
        """The menu a right-click on *group*'s band opens, built but not shown."""
        card = self._cards.get(group)
        if card is None:
            return None
        menu = popup_menu(card)
        verb = "Expand" if card.is_collapsed else "Collapse"
        menu.addAction(f"{verb} {card.title}").triggered.connect(
            lambda: self.set_group_collapsed(group, not card.is_collapsed)
        )
        menu.addAction(f"{COLLAPSE_OTHERS}\t{COLLAPSE_OTHERS_HINT}").triggered.connect(
            lambda: self.collapse_others(group)
        )
        menu.addSeparator()
        collapse = menu.addAction(COLLAPSE_ALL)
        collapse.setEnabled(self.collapse_all_button.isEnabled())
        collapse.triggered.connect(self.collapse_all)
        expand = menu.addAction(EXPAND_ALL)
        expand.setEnabled(self.expand_all_button.isEnabled())
        expand.triggered.connect(self.expand_all)
        return menu

    def _on_fold_requested(self, group: str, solo: bool) -> None:
        if solo:
            self.collapse_others(group)
            return
        card = self._cards.get(group)
        if card is not None:
            self.set_group_collapsed(group, not card.is_collapsed)

    def _show_menu(self, group: str, pos: QPoint) -> None:
        # A synchronous slot, so exec() is safe here -- see the qasync rule in CLAUDE.md.
        menu = self.card_menu(group)
        if menu is not None:
            menu.exec(pos)

    def _update(self, changes: dict[str, bool]) -> None:
        if not changes:
            return
        self._folds.update(changes)
        self._store.write(dict(self._folds), dict(changes))
        self._apply()

    def _apply(self) -> None:
        for group, card in self._cards.items():
            card.set_collapsed(self._folds.get(group, self._store.default(group)))
        self._refresh_buttons()

    def _read(self) -> dict[str, bool]:
        return self._store.read()

    def _refresh_buttons(self) -> None:
        """Enable each heading action only while it has a card to act on, as Bar B does."""
        cards = list(self._cards.values())
        self.collapse_all_button.setEnabled(any(not c.is_collapsed for c in cards))
        self.expand_all_button.setEnabled(any(c.is_collapsed for c in cards))
