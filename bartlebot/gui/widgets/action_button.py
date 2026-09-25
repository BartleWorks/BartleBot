"""``ActionButton`` — a flat, icon-plus-text push button that fills the space it is given.

The Fusion style's default ``QPushButton`` chrome (a beveled 3D frame sized to its
own label) is what every icon-and-text row in this app has been fighting: SVG
icons drawn against that frame read as flattened and off-centre, and a row of
such buttons naturally sizes each one to its own text rather than sharing the
row evenly. ``ActionButton`` is the reusable replacement — flat, theme-tinted,
and set to expand — so a row of them (``buttons.addWidget(btn, 1)`` each) splits
the available width evenly instead of leaving buttons to size themselves and
leaving a stretch to eat the remainder.

FOUR STYLES, AND WHY THE DEFAULT IS THE QUIET ONE
-------------------------------------------------

:class:`ActionStyle` follows the model Bootstrap settled on, for the reason it settled
on it: a row of buttons is a row of *choices*, and the only way a surface can say which
one it expects is to draw them at different weights. Four is enough to say everything
this app needs to say — one commit, one other way forward, everything else, and one
that costs something.

``TERTIARY`` is the default, and that is deliberate rather than incidental. Most rows
here are equal-weight verbs sitting beside one another (``+ New`` / ``Duplicate`` /
``Delete``), and in such a row nothing should look more important than the destructive
button next to it. A style is opted *into*, at the one place a surface really does have
a primary action — a dialog's commit button.

Fills are lightened or darkened from the token rather than given hover tokens of their
own. ``accent`` and ``error`` are Tier 1/2 tokens a custom theme is expected to change,
and a hover colour stored beside them is a second thing to keep in step; derived, the
hover follows whatever the theme says the fill is.
"""

from __future__ import annotations

from enum import Enum

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import QPushButton, QSizePolicy, QWidget

from bartlebot.gui.icon_utils import svg_icon
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin

#: How far a filled button's background moves on hover and on press. Percentages for
#: ``QColor.lighter``/``darker``, which take 100 as "unchanged".
_HOVER_SHIFT = 118
_PRESS_SHIFT = 88

#: How much ``accent`` shows through ``SECONDARY``'s background, as a fraction of the way
#: from the page colour to the accent. A dark page needs more of it: the same 10% that
#: reads as a tint against white is all but invisible against ``#1e1e1e``.
_WASH_ON_LIGHT = 0.10
_WASH_ON_DARK = 0.18


class ActionStyle(Enum):
    """How much weight a button carries — see the module docstring.

    ``PRIMARY`` is the one thing a surface expects you to do: filled with the accent.
    At most one per surface — ``tests/test_button_guard.py`` holds that line.

    ``SECONDARY`` is a way forward that is not the surface's commit: outlined in the
    accent over a faint wash of it. The wash is not decoration — drawn unfilled, the
    outline alone was a thin blue line and blue text on a near-white field, which read
    as a tinted ``TERTIARY`` rather than as a rung of its own.

    **A ``SECONDARY`` does not imply a ``PRIMARY``**, and the phrasing above is why: it
    was once "a real alternative to it", which made every use on a surface with no
    commit of its own a misuse. Most uses are that — ``assignment_card``'s *Save as
    Player Strategy…* inside an expanded card, the canvas toolbar's *New Bot*. They are
    offered next steps, not alternatives to anything, and the accent is right on them
    because a ``TERTIARY`` there would read as one of the card's own quiet verbs. What
    ``SECONDARY`` actually says is "worth reaching for, but not the default thing to do
    here", which is as true beside a commit as it is with no commit in sight.

    ``TERTIARY`` is everything else, and the default: no accent at all.
    ``CAUTION`` is a verb that costs something to undo: filled with the error colour.
    """

    PRIMARY = "primary"
    SECONDARY = "secondary"
    TERTIARY = "tertiary"
    CAUTION = "caution"


def _shift(color: str, percent: int) -> str:
    """*color* lightened or darkened by *percent*, as a hex string.

    One call for both directions: ``QColor.lighter`` below 100 delegates to ``darker``,
    so a single helper covers the hover and the press without the caller choosing.
    """
    return QColor(color).lighter(percent).name()


def _mix(base: str, over: str, amount: float) -> str:
    """*base* blended *amount* of the way toward *over*, as a hex string.

    Kept beside :func:`_shift` and used for the same reason: a derived colour follows
    whatever a custom theme sets its tokens to, where a stored one is a second thing to
    keep in step.
    """
    a, b = QColor(base), QColor(over)
    return QColor(
        round(a.red() + (b.red() - a.red()) * amount),
        round(a.green() + (b.green() - a.green()) * amount),
        round(a.blue() + (b.blue() - a.blue()) * amount),
    ).name()


class ActionButton(ThemedMixin, QPushButton):
    """A flat button with an icon and a label, sized to expand and fill its row.

    Meant for a row of equal-weight actions (``+ New`` / ``Duplicate`` / ``Delete``
    and similar) as well as for a dialog's commit button; which of those it is is
    :class:`ActionStyle`'s to say, and the default is the quiet one.
    """

    def __init__(
        self,
        icon_name: str,
        text: str,
        *,
        icon_size: int = 16,
        style: ActionStyle = ActionStyle.TERTIARY,
        fill: bool = True,
        on_accent: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self._icon_name = icon_name
        self._icon_px = icon_size
        self._style = style
        # Sitting on a ground that is itself the accent (the canvas crumb bar), where a
        # PRIMARY filled with the accent would vanish into it: the fill and text swap.
        self._on_accent = on_accent
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(icon_size, icon_size))
        # *fill* is what makes a row of these share its width evenly, and it is wrong for
        # the other place these are used: a dialog's trailing ``[Cancel] [Save]`` pair
        # sits after a stretch, and an expanding button there eats the stretch and spreads
        # the pair across the whole footer.
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding if fill else QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )
        self.setMinimumHeight(28)
        self.refresh_theme_styles()

    @property
    def action_style(self) -> ActionStyle:
        """This button's weight. Public so a test can assert what a surface asked for."""
        return self._style

    def set_action_style(self, style: ActionStyle) -> None:
        """Change the weight — a Delete that has become a Revert is no longer a caution."""
        if style == self._style:
            return
        self._style = style
        self.refresh_theme_styles()

    def set_icon_name(self, icon_name: str) -> None:
        """Swap the glyph — e.g. delete becoming revert once an origin is known."""
        if icon_name == self._icon_name:
            return
        self._icon_name = icon_name
        self.refresh_theme_styles()

    def _icon_tint(self, text: str) -> str:
        """The glyph's colour, given the style's text colour: the same, unless a subclass
        has a glyph whose colour is its meaning — ``RecordButton``'s red dot."""
        return text

    def _palette(self) -> tuple[str, str, str, str, str]:
        """(background, text, border, hover background, pressed background) for the style.

        A tuple rather than five branches inside ``apply_theme_styles`` so that the rule
        each style follows is readable as one line, and so that the only thing varying
        between them is colour: every style shares the same metrics, and a style that
        also changed the padding would stop being a weight and start being a shape.
        """
        if self._style is ActionStyle.PRIMARY and self._on_accent:
            # Still the heaviest thing on its ground, which is what PRIMARY means: the
            # accent-on-accent fill would have drawn it weaker than a TERTIARY beside it.
            fill = THEME.pill_text
            return fill, THEME.accent, fill, _shift(fill, _PRESS_SHIFT), _shift(fill, _PRESS_SHIFT - 10)
        if self._style is ActionStyle.PRIMARY:
            fill = THEME.accent
            return fill, THEME.pill_text, fill, _shift(fill, _HOVER_SHIFT), _shift(fill, _PRESS_SHIFT)
        if self._style is ActionStyle.CAUTION:
            fill = THEME.error
            return fill, THEME.pill_text, fill, _shift(fill, _HOVER_SHIFT), _shift(fill, _PRESS_SHIFT)
        if self._style is ActionStyle.SECONDARY:
            # A faint wash of the accent over the page colour rather than a stored tint,
            # so a theme that changes ``accent`` moves the wash with it. How much of it
            # shows through is read off ``bg``'s own lightness, not off a comparison with
            # a literal — a custom light theme is not required to be ``#ffffff``.
            wash = _WASH_ON_LIGHT if QColor(THEME.bg).lightnessF() > 0.5 else _WASH_ON_DARK
            # The wash is faint enough that the hover still has to come from somewhere
            # other than the outline: ``node_fill`` is the same "this is live" tint every
            # card and list row in the app takes on hover, which is what keeps the
            # outlined button in that family.
            return (
                _mix(THEME.bg, THEME.accent, wash),
                THEME.accent,
                THEME.accent,
                THEME.node_fill,
                THEME.node_fill_selected,
            )
        return THEME.surface, THEME.text, THEME.border, THEME.surface_disabled, THEME.border

    def apply_theme_styles(self) -> None:
        background, text, border, hover, pressed = self._palette()
        # Tinted with the button's own text colour rather than the theme's default icon
        # tint: on a filled PRIMARY or CAUTION the default is drawn nearly invisible
        # against the accent, and on the other two it is the colour it would have picked
        # anyway. An empty name means a button with no glyph — a dialog's Cancel — and
        # clearing the icon is how it stays that way across a theme pass.
        self.setIcon(
            svg_icon(self._icon_name, self._icon_px, tint=self._icon_tint(text)) if self._icon_name else QIcon()
        )
        self.setStyleSheet(
            "QPushButton {"
            f"  background: {background};"
            f"  color: {text};"
            f"  border: 1px solid {border};"
            "  border-radius: 4px;"
            "  padding: 4px 10px;"
            "}"
            f"QPushButton:hover:enabled {{ background: {hover}; }}"
            f"QPushButton:pressed:enabled {{ background: {pressed}; }}"
            # One disabled look for all four: a disabled button is not a weaker version of
            # its style, it is the same "off" every disabled control on the screen wears.
            # The border is the fill colour, i.e. no visible outline at all — an enabled
            # ``TERTIARY`` sits only ~3% off ``surface_disabled`` in the light theme, so
            # losing the outline is what actually separates off from on.
            f"QPushButton:disabled {{ color: {THEME.text_disabled}; background: {THEME.surface_disabled};"
            f" border-color: {THEME.surface_disabled}; }}"
        )
