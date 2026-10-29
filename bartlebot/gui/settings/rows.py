"""The Settings dialog's rows, the cards they sit in, and the page that holds them (#148).

ANATOMY
-------

Every setting is a card of its own, a row in Windows 11's settings style::

    ┌────────────────────────────────────────────────────────────────┐
    │▌↺ ?  Default Safety Delay                       [ 3.0 sec  ⌃⌄]  │
    │      How long the game server gets before an action fails.      │
    └────────────────────────────────────────────────────────────────┘

* The gutter is the sidebar's, built by ``build_gutter_row``: the override bar, the
  revert button and the ``?``, in the same three columns on every row. Here the bar
  means "this setting has left the app's default", and revert puts the default back.
  Nothing is written until OK, so a reset is as pending as any other edit.
* The title, and under it one line saying what the setting does. That line is always
  on screen -- a row still explains itself on the page -- and anything longer, the
  *why*, folds behind the ``?``. The page's ``Show descriptions · Hide descriptions``
  open or fold every row at once.
* The control sits at the right in a slot of one fixed width, so every control on a
  tab lines up in one column however long its title is. A control too wide for the
  slot (the user's keep list) goes under the text instead, full width.
* A last 20px slot at the far right holds the fold chevron on a group's header row,
  and is kept empty on every other row so their controls still line up.

Borders go on each setting, not around a section: sections are ``RuleHeading``s, as
they were. The one card holding several rows is a :class:`SettingGroup` -- a setting
with settings that depend on it (Quicksell and its five rows) -- which folds,
the way Windows' ``SettingsExpander`` does and only where it does.

A group's fold is a reading preference, so it lives in ``ui_state.json`` rather than in
``settings.json`` (``bartlebot.gui.ui_state``), and :data:`SETTINGS_FOLD_DEFAULTS` names
every group the dialog has. Whether descriptions are shown is the dialog's own
``show_setting_descriptions``, apart from the sidebar's ``show_option_descriptions``.

The rule from before this rework still holds: **a description is a**
``ThemedTextLabel``, **not a styled** ``QLabel``. A factory hands its label to a caller
with nothing to re-apply on a theme switch, so a plain one keeps whichever palette was
live when the tab was built -- see the "GUI theming" section of CLAUDE.md.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from bartleby.qt.theming.theme import THEME
from bartleby.qt.theming.themed import ThemedMixin, set_style_sheet
from bartleby.qt.widgets.buttons.icon_button import IconButton
from bartleby.qt.widgets.labels.themed_label import ThemedTextLabel
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from bartlebot.gui.sidebar.option_gutter import (
    DescriptionSwitches,
    HelpToggle,
    OverrideBar,
    RevertButton,
    build_gutter_row,
    keep_slot_when_hidden,
)
from bartlebot.gui.ui_state import UiState
from bartlebot.gui.widgets.labels.rule_heading import RuleHeading
from bartlebot.gui.widgets.layout.scroll_gutter import reserve_scrollbar_gutter
from bartlebot.settings_manager import SettingsManager

logger = logging.getLogger(__name__)

#: The width every control is right-aligned in, so a tab's controls form one column.
CONTROL_SLOT_PX = 210
#: A number or key field's width, inside the slot. Combos and text fields fill it.
NARROW_CONTROL_PX = 150
#: The slot after the control: a group header's fold chevron, empty on other rows.
FOLD_SLOT_PX = 20
#: The chevron's glyph: the assignment card's, so both surfaces fold with one mark.
FOLD_ICON_PX = 14
#: Between the control slot and the fold slot.
FOLD_GAP_PX = 4
#: Between the text and the control slot.
TEXT_TO_CONTROL_PX = 12
#: A row's padding above and below, inside its card; the bar runs through it.
ROW_INSET_PX = 7
#: A row's padding on the right, inside its card.
ROW_RIGHT_PX = 8
#: Between one card and the next.
CARD_GAP_PX = 4
#: The widest a tab's column of cards grows. Past this a title and its control drift too
#: far apart to read as one row -- the complaint about macOS Ventura's System Settings.
PAGE_MAX_WIDTH_PX = 760

#: ``ui_state.json``'s name for this dialog.
FOLD_SURFACE = "settings_dialog"
#: Every group the dialog draws, and whether it starts collapsed. A group missing from
#: here would have its remembered fold pruned by every other group's read.
SETTINGS_FOLD_DEFAULTS: dict[str, bool] = {
    "autosave": True,
    "quicksell": True,
    "grid": True,
}

_SETTINGS_SECTION = "general"
_DESCRIPTIONS_KEY = "show_setting_descriptions"


def hint(text: str) -> QLabel:
    """Dimmed prose that is not a row's: a tab's note at the top, or under a heading."""
    lbl = ThemedTextLabel(text, css=f"font-size: {THEME.font_small_px}px; padding-bottom: 6px;")
    lbl.setWordWrap(True)
    return lbl


# ---------------------------------------------------------------------------
# Reading and writing a control
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Binding:
    """How a row reads, writes and hears about its control's value, and says one aloud."""

    read: Callable[[], Any]
    write: Callable[[Any], None]
    signals: Sequence[Any]
    describe: Callable[[Any], str]


def bind(widget: QWidget) -> Binding:
    """The :class:`Binding` for one of the controls the Settings tabs use."""
    if isinstance(widget, QCheckBox):
        return Binding(widget.isChecked, widget.setChecked, (widget.toggled,), lambda v: "on" if v else "off")
    if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
        return Binding(widget.value, widget.setValue, (widget.valueChanged,), lambda v: _spin_text(widget, v))
    if isinstance(widget, QComboBox):
        return Binding(
            widget.currentData,
            lambda v: widget.setCurrentIndex(max(_combo_index(widget, v), 0)),
            (widget.currentIndexChanged,),
            lambda v: widget.itemText(i) if (i := _combo_index(widget, v)) >= 0 else str(v),
        )
    if isinstance(widget, QLineEdit):
        return Binding(widget.text, widget.setText, (widget.textChanged,), lambda v: v or "empty")
    if isinstance(widget, QKeySequenceEdit):
        return Binding(
            lambda: widget.keySequence().toString(QKeySequence.SequenceFormat.PortableText),
            lambda v: widget.setKeySequence(QKeySequence(v)),
            (widget.keySequenceChanged,),
            lambda v: v or "no key",
        )
    raise TypeError(f"no settings binding for {type(widget).__name__}")


def _combo_index(combo: QComboBox, value: Any) -> int:
    """The index whose data equals *value*, compared in Python.

    Not ``findData``: that compares through ``QVariant``, which does not match the tuple
    a resolution is stored as -- nor the list its default is.
    """
    return next((i for i in range(combo.count()) if _same(combo.itemData(i), value)), -1)


def _spin_text(spin: QAbstractSpinBox, value: float) -> str:
    """*value* the way *spin* would print it, special text included."""
    if spin.specialValueText() and value == spin.minimum():
        return spin.specialValueText()
    return f"{spin.prefix()}{spin.textFromValue(value)}{spin.suffix()}"


def _size_control(control: QWidget) -> None:
    """Give a field one of two widths, so a tab's fields line up on both edges.

    A control that is not a single field -- the window size's pair -- is left at its own.
    """
    if isinstance(control, (QAbstractSpinBox, QKeySequenceEdit)):
        control.setFixedWidth(NARROW_CONTROL_PX)
    elif isinstance(control, (QComboBox, QLineEdit)):
        control.setFixedWidth(CONTROL_SLOT_PX)


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return math.isclose(float(a), float(b), abs_tol=1e-9)
        except (TypeError, ValueError):
            return False
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return list(a) == list(b)
    return a == b


# ---------------------------------------------------------------------------
# A row
# ---------------------------------------------------------------------------


class SettingRow(QWidget):
    """One setting: the gutter, the title and its line, and the control at the right."""

    #: The control's value changed, by an edit or a reset.
    state_changed = pyqtSignal()

    def __init__(
        self,
        title: str,
        summary: str,
        control: QWidget,
        *,
        default: Any,
        details: str = "",
        binding: Binding | None = None,
        wide: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        """*default* is the app's default in the control's own terms -- a keep list's text,
        not the list. *details* is the longer prose behind the ``?``; a row with none keeps
        the ``?``'s slot empty. *wide* puts the control under the text, full width."""
        super().__init__(parent)
        self._title_text = title
        self._control = control
        self._binding = binding if binding is not None else bind(control)
        if isinstance(control, (QSpinBox, QDoubleSpinBox)):
            # A default the field cannot hold is the nearest one it can: a sidebar floor
            # raised past the shipped width must not mark a fresh install as changed.
            default = min(max(default, control.minimum()), control.maximum())
        self._default = default
        self._rolled_up = False

        body = QWidget()
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 0, ROW_RIGHT_PX, 0)
        column.setSpacing(0)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(0)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(0)
        self._title = ThemedTextLabel(title, color="text", css=f"font-size: {THEME.font_body_px}px;")
        self._title.setWordWrap(True)
        self._summary = ThemedTextLabel(summary, css=f"font-size: {THEME.font_subtext_px}px;")
        self._summary.setWordWrap(True)
        text.addWidget(self._title)
        text.addWidget(self._summary)
        top.addLayout(text, 1)
        top.addSpacing(TEXT_TO_CONTROL_PX)

        # A minimum, not a fixed width: a control wider than the slot (the window size's
        # pair) grows it leftward and keeps its right edge in line with the rest.
        slot = QWidget()
        slot.setMinimumWidth(CONTROL_SLOT_PX)
        slot_layout = QHBoxLayout(slot)
        slot_layout.setContentsMargins(0, 0, 0, 0)
        slot_layout.setSpacing(0)
        slot_layout.addStretch(1)
        if not wide:
            _size_control(control)
            slot_layout.addWidget(control, 0)
        top.addWidget(slot, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addSpacing(FOLD_GAP_PX)

        self._trailing = QWidget()
        self._trailing.setFixedWidth(FOLD_SLOT_PX)
        self._trailing_layout = QHBoxLayout(self._trailing)
        self._trailing_layout.setContentsMargins(0, 0, 0, 0)
        self._trailing_layout.setSpacing(0)
        top.addWidget(self._trailing, 0, Qt.AlignmentFlag.AlignVCenter)
        column.addLayout(top)

        if wide:
            # Its right edge where every other control's is: past the fold slot.
            holder = QWidget()
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(0, 6, FOLD_GAP_PX + FOLD_SLOT_PX, 0)
            holder_layout.addWidget(control)
            column.addWidget(holder)

        self._details: ThemedTextLabel | None = None
        if details:
            self._details = ThemedTextLabel(
                details,
                css=f"font-size: {THEME.font_small_px}px; padding-top: 4px; padding-right: {FOLD_GAP_PX + FOLD_SLOT_PX}px;",
            )
            self._details.setWordWrap(True)
            column.addWidget(self._details)
            # The hover path stays: the ? is the discoverable route, not the only one.
            self._title.setToolTip(details)

        self._bar = OverrideBar()
        self._revert = RevertButton()
        self._revert.clicked.connect(self.reset)
        self._help = HelpToggle()
        self._help.setToolTip("Show what this setting does")

        self._body = body
        self._outer = build_gutter_row(self._bar, self._revert, self._help, body, inset=ROW_INSET_PX)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._outer)

        # Visibility only once parented: setVisible(True) on a parentless widget maps it as
        # a window of its own.
        if self._details is None:
            keep_slot_when_hidden(self._help)
        else:
            self._details.setVisible(False)
            self._help.toggled.connect(self._details.setVisible)

        for signal in self._binding.signals:
            signal.connect(self._on_edited)
        self.refresh()

    # ------------------------------------------------------------------

    @property
    def title(self) -> str:
        return self._title_text

    @property
    def summary(self) -> ThemedTextLabel:
        """The one line under the title, always on screen."""
        return self._summary

    @property
    def control(self) -> QWidget:
        return self._control

    @property
    def bar(self) -> OverrideBar:
        return self._bar

    @property
    def revert_button(self) -> RevertButton:
        return self._revert

    @property
    def help_button(self) -> HelpToggle:
        return self._help

    @property
    def details(self) -> ThemedTextLabel | None:
        return self._details

    @property
    def body(self) -> QWidget:
        return self._body

    @property
    def is_changed(self) -> bool:
        """Whether the control holds something other than the app's default."""
        return not _same(self._binding.read(), self._default)

    @property
    def details_shown(self) -> bool:
        return self._details is not None and self._help.isChecked()

    # ------------------------------------------------------------------

    def set_details_shown(self, shown: bool) -> None:
        """Open or fold the prose behind the ``?``; a row with none ignores this."""
        if self._details is not None:
            self._help.setChecked(shown)

    def pin_details(self) -> None:
        """Show the prose for good, with no ``?`` to fold it: setup's pages, where every
        description is part of the question. The ``?``'s slot stays, so rows line up."""
        if self._details is not None:
            self._help.setChecked(True)
            keep_slot_when_hidden(self._help)

    def set_available(self, available: bool, reason: str = "") -> None:
        """Gray the body, leaving the gutter live -- the sidebar's rule for a dependent row.

        Disabled rather than hidden, so nothing below it moves, and so a value the user
        set here stays in sight while the setting it hangs off is off.
        """
        self._body.setEnabled(available)
        self._outer.setToolTip("" if available else reason)
        # A themed label's color is its own stylesheet, which a disabled parent does not
        # reach, so the text is dimmed here with the control the palette already dims.
        self._title.set_color("text" if available else "text_disabled")
        self._summary.set_color("text_muted" if available else "text_disabled")
        if self._details is not None:
            self._details.set_color("text_muted" if available else "text_disabled")

    def set_rolled_up(self, rolled_up: bool) -> None:
        """Light the bar for a change out of sight: a collapsed group's rows."""
        if rolled_up != self._rolled_up:
            self._rolled_up = rolled_up
            self.refresh()

    def set_trailing(self, widget: QWidget) -> None:
        """Put *widget* -- a group's fold chevron -- in the slot after the control."""
        self._trailing_layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignCenter)

    def reset(self) -> None:
        """Put the app's default back into the control. Written, like any edit, on OK."""
        default = list(self._default) if isinstance(self._default, list) else self._default
        self._binding.write(default)
        self.refresh()

    def refresh(self) -> None:
        changed = self.is_changed
        self._bar.set_active(changed or self._rolled_up)
        self._revert.setEnabled(changed)
        described = self._binding.describe(self._default)
        self._revert.setToolTip(f"Reset to the default ({described})" if changed else f"At the default ({described})")
        if self._rolled_up and not changed:
            self._bar.setToolTip("Changed inside this group")
        else:
            self._bar.setToolTip("Changed from the default" if changed else "")

    def _on_edited(self, *_args) -> None:
        self.refresh()
        self.state_changed.emit()


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------


class _Divider(ThemedMixin, QFrame):
    """The hairline between two rows of one card."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFixedHeight(1)
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        set_style_sheet(self, f"background: {THEME.border}; border: none;")


class SettingCard(ThemedMixin, QFrame):
    """The bordered, page-colored frame around one setting, or one group of them."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Named, so the border and fill reach the frame alone and not every child in it.
        self.setObjectName("settingCard")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(1, 1, 1, 1)
        self._layout.setSpacing(0)
        self.refresh_theme_styles()

    def add_row(self, row: SettingRow) -> _Divider | None:
        """Append *row*, behind a hairline if it is not the first; returns the hairline."""
        divider = None
        if self._layout.count():
            divider = _Divider()
            self._layout.addWidget(divider)
        self._layout.addWidget(row)
        return divider

    def apply_theme_styles(self) -> None:
        set_style_sheet(
            self,
            f"QFrame#settingCard {{ background: {THEME.bg}; border: 1px solid {THEME.border}; border-radius: 4px; }}",
        )


class SettingGroup(SettingCard):
    """A setting with settings that depend on it: one card that folds.

    The header is the parent setting's own row, a check box, with the fold chevron in
    its last slot -- the combat tab's assignment card's: down while collapsed, up while
    open. While the check box is off the rows under it are grayed, not hidden.
    While the card is collapsed and one of them has left its default, the header's bar
    lights, so a fold never hides a change -- the rule #138's cards follow.

    With *foldable* False it has no chevron and is always open, and reads and writes no
    fold state: setup's Quicksell page shows the group whole, whatever the user folded in
    Settings.
    """

    def __init__(
        self, group_id: str, header: SettingRow, rows: Sequence[SettingRow], parent=None, *, foldable: bool = True
    ) -> None:
        if group_id not in SETTINGS_FOLD_DEFAULTS:
            raise KeyError(f"{group_id!r} is not in SETTINGS_FOLD_DEFAULTS")
        if not isinstance(header.control, QCheckBox):
            raise TypeError("a settings group is headed by a check box")
        super().__init__(parent)
        self._id = group_id
        self._header = header
        self._rows = list(rows)
        self._ui_state = UiState()
        self._collapsed = foldable and self._ui_state.folds(FOLD_SURFACE, SETTINGS_FOLD_DEFAULTS)[group_id]

        self.add_row(header)
        self._dividers = [self.add_row(row) for row in self._rows]

        self._fold: IconButton | None = None
        if foldable:
            self._fold = IconButton("chevron_down", tooltip="", size=FOLD_SLOT_PX, icon_size=FOLD_ICON_PX, parent=self)
            self._fold.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            self._fold.clicked.connect(lambda: self.set_collapsed(not self._collapsed))
            header.set_trailing(self._fold)

        header.control.toggled.connect(self._refresh_gate)
        for row in self._rows:
            row.state_changed.connect(self._refresh_rollup)
        self._refresh_gate()
        self._apply_fold()

    @property
    def group_id(self) -> str:
        return self._id

    @property
    def header(self) -> SettingRow:
        return self._header

    @property
    def rows(self) -> list[SettingRow]:
        return list(self._rows)

    @property
    def fold_button(self) -> IconButton | None:
        """The chevron; None for a group that does not fold."""
        return self._fold

    @property
    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool) -> None:
        """Fold or open the card, and remember it in ``ui_state.json``."""
        if collapsed == self._collapsed or self._fold is None:
            return
        self._collapsed = collapsed
        self._ui_state.set_collapsed(FOLD_SURFACE, self._id, collapsed, default=SETTINGS_FOLD_DEFAULTS[self._id])
        self._apply_fold()

    def _apply_fold(self) -> None:
        shown = not self._collapsed
        for row, divider in zip(self._rows, self._dividers):
            row.setVisible(shown)
            if divider is not None:
                divider.setVisible(shown)
        if self._fold is not None:
            self._fold.set_icon_name("chevron_down" if self._collapsed else "chevron_up")
            verb = "Expand" if self._collapsed else "Collapse"
            self._fold.setToolTip(f"{verb} {self._header.title}")
        self._refresh_rollup()

    def _refresh_gate(self, *_args) -> None:
        on = self._header.control.isChecked()
        reason = f"Turn on {self._header.title} to use this."
        for row in self._rows:
            row.set_available(on, reason)

    def _refresh_rollup(self) -> None:
        self._header.set_rolled_up(self._collapsed and any(row.is_changed for row in self._rows))


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


class SettingsForm(QWidget):
    """A column of headings and cards, under the page's ``Show · Hide descriptions``.

    Every form tab is one, and a tab that is not all form -- Theme, Game -- holds one for
    its rows. The dialog finds them all and relays one form's switch to the others, since
    whether descriptions are shown is one preference for the whole dialog.
    """

    #: The reader used Show or Hide descriptions on this form.
    descriptions_toggled = pyqtSignal(bool)

    def __init__(
        self, parent: QWidget | None = None, *, margin: int = 12, intro: str = "", always_describe: bool = False
    ) -> None:
        """With *always_describe*, every description is shown and none can be folded: no
        Show · Hide descriptions, and no ``?`` on a row. Setup's pages are built so."""
        super().__init__(parent)
        self._always_describe = always_describe
        self._rows: list[SettingRow] = []
        self._groups: list[SettingGroup] = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(margin, margin, margin, margin)
        self._layout.setSpacing(0)

        self._switches = DescriptionSwitches(self._on_switches)
        self._layout.addWidget(self._switches)
        if intro:
            self._layout.addSpacing(4)
            self._layout.addWidget(hint(intro))

    @property
    def rows(self) -> list[SettingRow]:
        return list(self._rows)

    @property
    def groups(self) -> list[SettingGroup]:
        return list(self._groups)

    @property
    def switches(self) -> DescriptionSwitches:
        return self._switches

    def row(self, title: str) -> SettingRow:
        """The row titled *title*."""
        for row in self._rows:
            if row.title == title:
                return row
        raise KeyError(title)

    def add_heading(self, title: str) -> RuleHeading:
        heading = RuleHeading(title)
        self._layout.addWidget(heading)
        self._layout.addSpacing(4)
        return heading

    def add_note(self, text: str) -> QLabel:
        label = hint(text)
        self._layout.addWidget(label)
        return label

    def add_widget(self, widget: QWidget) -> None:
        self._layout.addWidget(widget)

    def add_row(self, row: SettingRow) -> SettingRow:
        card = SettingCard()
        card.add_row(row)
        self._add_card(card)
        self._rows.append(row)
        return row

    def add_card(self, card: SettingCard) -> SettingCard:
        """A card that is not built from rows -- Settings › Combat's defaults -- in the column's spacing."""
        self._add_card(card)
        return card

    def add_group(self, group: SettingGroup) -> SettingGroup:
        self._add_card(group)
        self._groups.append(group)
        self._rows.extend([group.header, *group.rows])
        return group

    def finish(self, *, stretch: bool = True) -> None:
        """Close the column, and open every description if the reader last chose to.

        A form with no description to open hides the links rather than offering two
        commands that do nothing.
        """
        if self._always_describe:
            self._switches.setVisible(False)
            for row in self._rows:
                row.pin_details()
        else:
            self._switches.setVisible(any(row.details is not None for row in self._rows))
        if stretch:
            self._layout.addStretch(1)
        else:
            # Embedded above something else, it is as tall as its rows and no taller.
            self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        if not self._always_describe:
            self.set_descriptions_shown(_read_descriptions_shown())

    def set_descriptions_shown(self, shown: bool) -> None:
        if self._always_describe:
            return
        for row in self._rows:
            row.set_details_shown(shown)

    def _add_card(self, card: SettingCard) -> None:
        self._layout.addWidget(card)
        self._layout.addSpacing(CARD_GAP_PX)

    def _on_switches(self, shown: bool) -> None:
        _write_descriptions_shown(shown)
        self.set_descriptions_shown(shown)
        self.descriptions_toggled.emit(shown)


def _read_descriptions_shown() -> bool:
    try:
        return bool(SettingsManager.instance().get(_SETTINGS_SECTION, _DESCRIPTIONS_KEY, default=False))
    except Exception:
        logger.exception("Failed to read whether setting descriptions are shown")
        return False


def _write_descriptions_shown(shown: bool) -> None:
    """Remember it now, OK or Cancel: it is a reading preference, not a setting being edited."""
    try:
        sm = SettingsManager.instance()
        sm.set(_SETTINGS_SECTION, _DESCRIPTIONS_KEY, value=shown)
        sm.save()
    except Exception:
        logger.exception("Failed to remember whether setting descriptions are shown")


# ---------------------------------------------------------------------------
# The scroll wrapper
# ---------------------------------------------------------------------------


class ScrollableTab(QScrollArea):
    """A settings tab that scrolls, and still answers ``apply()`` for the tab inside.

    ``SettingsDialog.apply_all`` walks the tab bar and calls ``apply`` on whatever it
    finds there, precisely so a tab added later cannot be forgotten. Wrapping a tab in
    a plain ``QScrollArea`` would put a widget with no ``apply`` in that position and
    throw away everything the user typed, so the wrapper forwards instead of the dialog
    learning to unwrap.

    Only the tabs whose height is fixed by their content are wrapped. A tab built to
    fill the space it is given -- the Game tab, whose two lists carry the stretch --
    shrinks on its own, and inside a scroll area those lists would collapse to a couple
    of visible rows instead.

    The column is capped at :data:`PAGE_MAX_WIDTH_PX` and centered, and the scroll bar's
    space is reserved, or opening a group would make the column jump sideways as the bar
    appeared (#112).
    """

    def __init__(self, content: QWidget) -> None:
        super().__init__()
        self._content = content
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        content.setMaximumWidth(PAGE_MAX_WIDTH_PX)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.setWidget(content)
        reserve_scrollbar_gutter(self)

    @property
    def content(self) -> QWidget:
        """The tab itself, for anything that needs the real widget."""
        return self._content

    def apply(self) -> None:
        self._content.apply()
