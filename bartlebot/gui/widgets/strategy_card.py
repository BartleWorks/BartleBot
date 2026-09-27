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

from PyQt6.QtCore import QEvent, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import set_style_sheet
from bartlebot.gui.widgets.icon_button import IconButton, IconTint
from bartlebot.gui.widgets.tag_picker import STRATEGY_VOCABULARY
from bartlebot.gui.widgets.tag_pill import AccentPill, NeutralPill, TagPill
from bartlebot.store_roots import SOURCE_LABELS, Origin
from bartlebot.strategy_summary import HealthStatus, StrategySummary

ADD_FAVORITE_TIP = "Add to favorites"
REMOVE_FAVORITE_TIP = "Remove from favorites"


class StrategyCard(QWidget):
    """Card widget rendered inside a QListWidget item via setItemWidget.

    Pass *favorite* (True or False) to give the card a star; leave it None on a surface
    with no favorites. A favorite's star is always drawn, filled. A non-favorite's
    outline star shows only while the card is hovered or selected (#125): a column of
    empty stars on every card is noise, and the star is the one thing on a card that
    is a control rather than a description.

    The card does not store the favorite itself. It reports the click with
    ``favorite_toggled``, and the list decides — a favorite moves the card into
    another section, which only the list can do.
    """

    #: The star was clicked; the argument is whether it now wants to be a favorite.
    favorite_toggled = pyqtSignal(bool)

    def __init__(self, summary: StrategySummary, parent: QWidget | None = None, *, favorite: bool | None = None):
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
        set_style_sheet(name_row, "background: transparent;")
        if self._invalid:
            name_row.setText(f"{summary.name}  ⚠")
            name_row.setToolTip(f"Does not parse: {summary.health.reason}")
        else:
            name_row.setText(summary.name)
        name_row.setWordWrap(True)
        self._name_lbl = name_row
        self._source_pill: QLabel | None = None
        self._star: IconButton | None = None
        if summary.source is None and favorite is None:
            header.addWidget(name_row)
        else:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            row.addWidget(name_row, 1)
            if summary.source is not None:
                # Always shown, and a pill of words rather than a glyph: no icon says
                # "ships with the app" unaided (#124). Grey for the ordinary cases; the
                # accent outline only for a modified built-in, the one that shadows updates.
                text = SOURCE_LABELS[summary.source]
                pill = AccentPill(text) if summary.source is Origin.SHIPPED_OVERRIDE else NeutralPill(text)
                row.addWidget(pill, 0, Qt.AlignmentFlag.AlignTop)
                self._source_pill = pill
            if favorite is not None:
                star = IconButton(
                    "star_outline",
                    checked_icon_name="star_filled",
                    tooltip=REMOVE_FAVORITE_TIP if favorite else ADD_FAVORITE_TIP,
                    tint=IconTint.ACCENT,
                )
                star.setChecked(favorite)
                # Hidden stars keep their space, so a card's name does not rewrap under
                # the pointer as the star appears.
                policy = star.sizePolicy()
                policy.setRetainSizeWhenHidden(True)
                star.setSizePolicy(policy)
                star.toggled.connect(self._on_star_toggled)
                star.clicked.connect(lambda checked: self.favorite_toggled.emit(checked))
                row.addWidget(star, 0, Qt.AlignmentFlag.AlignTop)
                self._star = star
            header.addLayout(row)

        # Every line under the name is the same kind of thing — a muted caption at
        # THEME.font_subtext_px — so they are held as one list rather than as three
        # named attributes each restyled by its own line of _apply_style. The three
        # copies were identical apart from the attribute they wrote to, and one of them
        # had already been given a colour the other two lacked.
        self._sub_lbls: list[QLabel] = []

        if summary.description:
            self._add_sub_line(header, summary.description)

        subtitle = summary.subtitle_line()
        if subtitle:
            self._add_sub_line(header, subtitle)

        if summary.schools:
            self._add_sub_line(header, " · ".join(f"{count}× {school}" for school, count in summary.schools.items()))

        # Stored tags — the roles — as pills under everything else, rather than beside
        # the name: the name row already carries the source pill and the star, and a
        # 350px card list cannot fit three pills there without wrapping every name.
        self._tag_pills: list[TagPill] = []
        if summary.tags:
            tag_row = QHBoxLayout()
            tag_row.setContentsMargins(0, 2, 0, 0)
            tag_row.setSpacing(4)
            for tag in summary.tags:
                pill = TagPill(tag, vocabulary=STRATEGY_VOCABULARY)
                tag_row.addWidget(pill)
                self._tag_pills.append(pill)
            tag_row.addStretch(1)
            header.addLayout(tag_row)

        layout.addLayout(header)
        self._update_star()

        self._apply_style()

    def _add_sub_line(self, header: QVBoxLayout, text: str) -> QLabel:
        """One muted caption under the name, collected for the theme pass.

        Word-wrapped so a long line — a Player Strategy's DSL preview, in
        particular — never forces the card, and the sidebar it sits in, to grow
        a horizontal scrollbar. See issue #44.

        No stylesheet is written here: ``_apply_style`` runs at the end of ``__init__``
        and again on every theme change, and a size set in both places is a size that
        can disagree with itself.
        """
        label = QLabel(text)
        label.setWordWrap(True)
        header.addWidget(label)
        self._sub_lbls.append(label)
        return label

    @property
    def source_text(self) -> str:
        """What the source pill says, or empty when the card has none."""
        return self._source_pill.text() if self._source_pill is not None else ""

    @property
    def tag_texts(self) -> list[str]:
        """What the tag pills say, in order."""
        return [p.tag for p in self._tag_pills]

    @property
    def star(self) -> IconButton | None:
        """The favorite star, or None on a card without one. Public so a test can click it."""
        return self._star

    def _on_star_toggled(self, checked: bool) -> None:
        self._star.setToolTip(REMOVE_FAVORITE_TIP if checked else ADD_FAVORITE_TIP)
        self._update_star()

    def _update_star(self) -> None:
        if self._star is not None:
            self._star.setVisible(self._star.isChecked() or self._hovered or self._selected)

    def set_selected(self, selected: bool) -> None:
        if self._selected == selected:
            return
        self._selected = selected
        self._update_star()
        self._apply_style()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self._update_star()
        self._apply_style()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self._update_star()
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
            set_style_sheet(
                self, f"#StrategyCard {{ background: {bg}; border: 1px solid {border}; border-radius: 4px; }}"
            )
            name_color = THEME.error if self._invalid else THEME.text
            set_style_sheet(self._name_lbl, f"color: {name_color}; background: transparent;")
            for label in self._sub_lbls:
                set_style_sheet(
                    label, f"color: {THEME.text_muted}; font-size: {THEME.font_subtext_px}px; background: transparent;"
                )
        finally:
            self._applying_style = False

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._apply_style()
