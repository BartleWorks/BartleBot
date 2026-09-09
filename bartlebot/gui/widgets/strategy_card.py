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

        # Every line under the name is the same kind of thing — a muted caption at
        # THEME.font_card_sub_size — so they are held as one list rather than as three
        # named attributes each restyled by its own line of _apply_style. The three
        # copies were identical apart from the attribute they wrote to, and one of them
        # had already been given a colour the other two lacked.
        self._sub_lbls: list[QLabel] = []

        if summary.description:
            desc = self._add_sub_line(header, summary.description)
            desc.setWordWrap(True)

        subtitle = summary.subtitle_line()
        if subtitle:
            self._add_sub_line(header, subtitle)

        if summary.schools:
            self._add_sub_line(header, " · ".join(f"{count}× {school}" for school, count in summary.schools.items()))

        layout.addLayout(header)

        self._apply_style()

    def _add_sub_line(self, header: QVBoxLayout, text: str) -> QLabel:
        """One muted caption under the name, collected for the theme pass.

        No stylesheet is written here: ``_apply_style`` runs at the end of ``__init__``
        and again on every theme change, and a size set in both places is a size that
        can disagree with itself.
        """
        label = QLabel(text)
        header.addWidget(label)
        self._sub_lbls.append(label)
        return label

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
            for label in self._sub_lbls:
                label.setStyleSheet(
                    f"color: {THEME.text_muted}; font-size: {THEME.font_card_sub_size}px; background: transparent;"
                )
        finally:
            self._applying_style = False

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._apply_style()
