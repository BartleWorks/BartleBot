"""``StrategyCard``: one row in a strategy list, rendered from a ``StrategySummary``.

Promoted out of the strategy picker's private ``_StrategyCard`` so the picker,
the Strategy Library window and any future strategy list can all draw the same
card from the same :class:`~bartlebot.strategy_summary.StrategySummary` — see
that module's docstring for why the summary itself is shared.

Sized by a real ``sizeHint()`` rather than a fixed height: a card with a
description and a school chip row is taller than a bare name, and the two
heights the picker hard-coded (``_CARD_H_WITH_SUB`` / ``_CARD_H_NO_SUB``) had
no way to express that.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QSize, Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from bartlebot.gui.theme import THEME
from bartlebot.strategy_summary import HealthStatus, StrategySummary


class StrategyCard(QWidget):
    """Card widget rendered inside a QListWidget item via setItemWidget."""

    def __init__(self, summary: StrategySummary, parent: QWidget | None = None):
        super().__init__(parent)
        self._selected = False
        self._hovered = False
        self._applying_style = False
        self._invalid = summary.health.status is HealthStatus.INVALID_DSL
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("StrategyCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(2)

        header = QVBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(2)

        name_row = QLabel()
        f = name_row.font()
        f.setBold(True)
        name_row.setFont(f)
        name_row.setStyleSheet("background: transparent;")
        if self._invalid:
            name_row.setText(f"{summary.name}  ⚠")
            name_row.setToolTip(f"Does not parse: {summary.health.reason}")
        else:
            name_row.setText(summary.name)
        header.addWidget(name_row)
        self._name_lbl = name_row

        self._desc_lbl: QLabel | None = None
        if summary.description:
            desc = QLabel(summary.description)
            desc.setWordWrap(True)
            desc.setStyleSheet("font-size: 10px; background: transparent;")
            header.addWidget(desc)
            self._desc_lbl = desc

        subtitle = summary.subtitle_line()
        self._sub_lbl: QLabel | None = None
        if subtitle:
            sub = QLabel(subtitle)
            sub.setStyleSheet("font-size: 10px; background: transparent;")
            header.addWidget(sub)
            self._sub_lbl = sub

        self._school_lbl: QLabel | None = None
        if summary.schools:
            schools_text = " · ".join(f"{count}× {school}" for school, count in summary.schools.items())
            school_lbl = QLabel(schools_text)
            school_lbl.setStyleSheet("font-size: 10px; background: transparent;")
            header.addWidget(school_lbl)
            self._school_lbl = school_lbl

        layout.addLayout(header)

        self._apply_style()

    def set_selected(self, selected: bool) -> None:
        if self._selected == selected:
            return
        self._selected = selected
        self._apply_style()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self._apply_style()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self._apply_style()
        super().leaveEvent(event)

    def sizeHint(self) -> QSize:
        # A real hint rather than a fixed constant: the header layout below
        # already knows its own height once every optional line is added, so
        # asking it is more honest than maintaining a second set of numbers.
        return self.layout().totalSizeHint() if self.layout() is not None else super().sizeHint()

    def _apply_style(self) -> None:
        if self._applying_style:
            return
        self._applying_style = True
        try:
            border = THEME.accent if self._selected else THEME.border
            bg = THEME.node_fill if (self._selected or self._hovered) else THEME.surface
            self.setStyleSheet(f"#StrategyCard {{ background: {bg}; border: 1px solid {border}; border-radius: 4px; }}")
            name_color = THEME.error if self._invalid else THEME.text
            self._name_lbl.setStyleSheet(f"color: {name_color}; background: transparent;")
            if self._desc_lbl:
                self._desc_lbl.setStyleSheet(f"color: {THEME.text_muted}; font-size: 10px; background: transparent;")
            if self._sub_lbl:
                self._sub_lbl.setStyleSheet(f"color: {THEME.text_muted}; font-size: 10px; background: transparent;")
            if self._school_lbl:
                self._school_lbl.setStyleSheet(f"color: {THEME.text_muted}; font-size: 10px; background: transparent;")
        finally:
            self._applying_style = False

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._apply_style()
