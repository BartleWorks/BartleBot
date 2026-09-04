"""The leading gutter both option surfaces hang their row chrome in.

Setup and Options are two views of the same list, so a row's chrome has to land
at the same x on both: ``[bar][action][?]``, then the row's own content.  The
placement argument is in ``CLAUDE.md`` and in ``setup_section``'s own docstring --
in short it is the only arrangement that yields one *column* per button, because
the head line's right edge sits at two different x values and a bool row has no
separate label to right-align against.

The middle slot is the one that differs, and deliberately:

============  ==================================  ============================
surface       middle slot                         bar
============  ==================================  ============================
Setup         revert -- discard the user's value   accent while overridden
Options       edit -- open the declaration dialog  reserved width, never painted
============  ==================================  ============================

Options has no bar to paint because it has nothing to paint it *about*: the bar
and revert are one affordance in Setup, set by the same two lines, and a lone
marker with no paired action would be a fact to correlate rather than an
affordance.  Its width is still reserved, so the two surfaces' ``?`` columns line
up across a mode switch.  If a meaning is ever found for it -- "this author
changed the node's recommendation" is the obvious candidate, and 26 declarations
in the shipped library would light up -- the slot is already there.

Every slot keeps its size when its button is hidden.  A gutter that closes up on
the rows with nothing in them is not a column any more, and which rows those are
varies with whatever mix of options a bot happens to declare: only 24 of the
library's 152 Options rows carry an Edit button at all, and 64 of 80 bots carry
none.  That rarity is the argument *for* the gutter rather than against it --
inline, a button on 16% of rows makes 16% of rows a different shape.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QWidget

from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin

BAR_WIDTH_PX = 3

# Both buttons are 20px targets around 16px glyphs.
GUTTER_BTN_PX = 20
GUTTER_GAP_PX = 2
GUTTER_TO_BODY_PX = 5


class OverrideBar(ThemedMixin, QFrame):
    """The gutter marking a row that departs from the level beneath it.

    Always present and always the same width: it changes *colour* rather than
    appearing, so a row does not shift sideways the moment a value is typed.
    Left inactive for good on a surface with nothing to mark.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(BAR_WIDTH_PX)
        self._active = False
        self.refresh_theme_styles()

    def set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        colour = THEME.accent if self._active else "transparent"
        self.setStyleSheet(f"background: {colour}; border: none;")


class HelpToggle(ThemedMixin, QPushButton):
    """The ``?`` beside an option's name, folding its description in and out.

    Descriptions are node-authored prose -- two or three lines each, and 41% of an
    ordinary row's height with the control itself taking only 42%.  Every settings
    surface that shows help inline keeps it to one line (Chrome, Windows, GitHub);
    the ones whose prose is this long put it behind an affordance instead (Xcode's
    Quick Help, Unreal's property tooltips).  This is that affordance, and it is a
    *button* rather than a hover tooltip because the prose is load-bearing -- the
    only thing that keeps a pet is a sentence in one of these.

    It sits in the row's leading gutter, against the name it explains, and keeps
    its slot on a row that has no description to show.
    """

    _SIZE_PX = GUTTER_BTN_PX

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("?", parent)
        self.setCheckable(True)
        self.setFixedSize(self._SIZE_PX, self._SIZE_PX)
        self.setToolTip("Show what this option does")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        # The node help button in ``param_panel`` is the same affordance one panel
        # over, so it is the same shape and the same accent, only smaller.
        self.setStyleSheet(
            f"QPushButton {{ background: {THEME.surface}; border: 1px solid {THEME.border}; "
            f"border-radius: 3px; color: {THEME.accent}; font-weight: bold; font-size: 11px; padding: 0; }}"
            f"QPushButton:hover, QPushButton:checked {{ background: {THEME.accent}; color: #ffffff; }}"
        )


def keep_slot_when_hidden(button: QPushButton) -> None:
    """Hide *button* without letting its column close up."""
    policy = button.sizePolicy()
    policy.setRetainSizeWhenHidden(True)
    button.setSizePolicy(policy)
    button.setVisible(False)


def build_gutter_row(bar: OverrideBar, action_btn: QPushButton, help_btn: HelpToggle, body: QWidget) -> QWidget:
    """Wrap *body* behind the ``[bar][action][?]`` gutter.

    Both buttons top-align, which is what lets one arrangement serve a 20px bool
    row and a 240px drop table alike -- it is what deleted the branch that used to
    put Reset beside a short control and on the head line otherwise.
    """
    outer = QWidget()
    layout = QHBoxLayout(outer)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    top = Qt.AlignmentFlag.AlignTop
    layout.addWidget(bar)
    layout.addSpacing(GUTTER_GAP_PX)
    layout.addWidget(action_btn, 0, top)
    layout.addWidget(help_btn, 0, top)
    layout.addSpacing(GUTTER_TO_BODY_PX)
    layout.addWidget(body, 1)
    return outer


class DescriptionSwitches(QWidget):
    """The ``Show descriptions · Hide descriptions`` pair above a list of options.

    Two commands for the reader who wants the whole tab as a document, rather than
    a row at a time.  The choice is remembered in ``settings.json`` and shared by
    both surfaces: it is a property of the person reading, not of the bot, so
    folding the prose away on Setup folds it away on Options too.

    Links rather than a check box, and at the top of the *body* rather than on the
    section header.  A tick box captioned "Show descriptions" sitting above a list
    of options reads as the first option; a link whose text names what a click will
    do states its effect without a state to be read first.  The header is a
    click-through row that toggles the section, so a control inside it would be a
    hit-testing problem for no gain -- and it would hide with the section.

    Both are present at once rather than one link that flips its caption, because
    the per-row ``?`` moves rows independently: after one of those the surface is
    *mixed*, and a single toggle then names whichever half it last set rather than
    what the rows are actually doing -- so the one click the reader wants ("just
    fold them all") can be the click that unfolds them all.  Two unconditional
    commands say what they do whatever state the rows are in.
    """

    #: Matched to ``LinkButton``'s own default so the dot sits on the links'
    #: baseline rather than above it.
    SEPARATOR_FONT_PX = 10

    def __init__(self, on_toggled, parent: QWidget | None = None) -> None:
        """*on_toggled* is called with ``True`` for show and ``False`` for hide."""
        from bartlebot.gui.widgets.link_button import LinkButton  # noqa: PLC0415 - import cycle
        from bartlebot.gui.widgets.themed_label import ThemedTextLabel  # noqa: PLC0415 - import cycle

        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        show = LinkButton("Show descriptions")
        show.setToolTip("Show every option's description, instead of one at a time")
        show.clicked.connect(lambda: on_toggled(True))
        hide = LinkButton("Hide descriptions")
        hide.setToolTip("Fold every option's description away")
        hide.clicked.connect(lambda: on_toggled(False))
        row.addWidget(show, 0)
        row.addWidget(ThemedTextLabel(" · ", css=f"font-size: {self.SEPARATOR_FONT_PX}px;"), 0)
        row.addWidget(hide, 0)
        row.addStretch(1)
