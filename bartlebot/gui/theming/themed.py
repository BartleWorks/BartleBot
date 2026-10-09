"""``ThemedMixin`` — one place for the re-style-on-theme-switch plumbing.

Qt does not recompute a stylesheet a widget set on itself, and ``svg_icon`` bakes
its tint in at render time, so anything built from ``THEME`` at construction is a
snapshot of whichever theme was active then. The widget has to re-derive it when
the theme changes, which means a ``changeEvent`` override on every such widget —
and that override is identical every time, apart from the body that does the work.

So the override lives here and the body becomes :meth:`apply_theme_styles`::

    class MyPanel(ThemedMixin, QWidget):
        def __init__(self) -> None:
            super().__init__()
            self._label = QLabel("hello")
            self.refresh_theme_styles()        # see "Call it yourself" below

        def apply_theme_styles(self) -> None:
            self._label.setStyleSheet(f"color: {THEME.text};")

``tests/test_theme_refresh_guard.py`` accepts this base in place of a hand-written
``changeEvent``, and fails any themed widget that has neither.

Three things about it are not obvious:

**Mixin first, and exactly one Qt base.** ``class MyPanel(ThemedMixin, QWidget)``,
never the reverse. Python resolves ``changeEvent`` left to right, so with the mixin
second Qt's own implementation wins and this never runs — but it is worse than
dead code: sending a ``StyleChange`` to a widget whose bases are in that order
**aborts the process** inside ``sendEvent``, with no exception and no traceback.
Nothing here can detect that at runtime, so ``test_theme_refresh_guard.py`` checks
the base order statically instead.

PyQt6 handles cooperative multiple inheritance only while a single base derives
from ``QObject``, which is why this one derives from ``object`` and defines no Qt
behavior of its own beyond the override.

**No ``__init__``.** The guard is a *class* attribute, so an instance that has
never assigned it still reads ``False``. That keeps the mixin out of the
``__init__`` chain entirely, where PyQt6's argument forwarding is at its most
delicate — a mixin that swallows or misorders ``parent`` is a crash in a
constructor with no useful traceback.

**Call it yourself at the end of construction, via**
:meth:`~ThemedMixin.refresh_theme_styles`. The mixin cannot do it for you: at the
time any ``__init__`` it could hook into runs, the child widgets
``apply_theme_styles`` is about to style do not exist yet. The guard test checks
that a class inheriting this calls it somewhere outside ``changeEvent``, because a
widget styled only on the *next* theme switch is unstyled until then.

Two names, and the split matters: ``apply_theme_styles`` is the one you *write*,
``refresh_theme_styles`` is the one you *call*. Calling the former directly skips
the re-entrancy guard, and a widget that styles itself then applies twice per
switch — or recurses, if what it applies is not identical each time.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QEvent

logger = logging.getLogger(__name__)


class ThemedMixin:
    """Re-applies a widget's ``THEME``-derived styling whenever the theme changes.

    Subclasses implement :meth:`apply_theme_styles`. See the module docstring for
    the three usage rules; the important one is that the mixin goes first in the
    base list.
    """

    # Class-level default, deliberately: see "No ``__init__``" in the module
    # docstring. Assigning it in changeEvent shadows this with an instance
    # attribute, which is the only state this mixin ever holds.
    _applying_theme = False

    def apply_theme_styles(self) -> None:
        """Re-derive every ``THEME``-dependent value this widget holds.

        **Implement this; do not call it** — call :meth:`refresh_theme_styles`,
        which is the same thing behind the re-entrancy guard.

        Runs once at construction and again on every theme switch, so it must be
        safe to call repeatedly, and must set *everything*: a partial
        implementation leaves the widget half-converted, which is harder to spot
        than one that never updates at all.
        """
        raise NotImplementedError(
            f"{type(self).__name__} inherits ThemedMixin but does not implement apply_theme_styles"
        )

    def refresh_theme_styles(self) -> None:
        """Apply the styles, at most once at a time.

        The guard is load-bearing rather than defensive. Nearly every
        ``apply_theme_styles`` calls ``setStyleSheet`` on ``self``, and Qt delivers
        the resulting ``StyleChange`` synchronously — straight back through
        :meth:`changeEvent` and into here. Unguarded, the construction-time call
        applies twice, and a widget whose styling is not byte-identical each time
        recurses until the app dies in its own constructor with no traceback. That
        is the bug ``TagPill`` and ``PickerButton`` each carry a private copy of
        this guard to avoid.
        """
        if self._applying_theme:
            return
        self._applying_theme = True
        try:
            self.apply_theme_styles()
        finally:
            self._applying_theme = False

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)  # type: ignore[misc]
        if event.type() != QEvent.Type.StyleChange:
            return
        # PyQt6 calls qFatal() when a Python exception escapes a virtual method
        # reimplementation, so an exception raised here does not fail a repaint —
        # it aborts the process, from inside a theme switch, with no traceback.
        # Restyling is cosmetic and must never be able to do that. Same reasoning
        # as the callback guards in BotMachine (CLAUDE.md).
        #
        # Only this boundary is guarded. A direct refresh_theme_styles() call from
        # a constructor is ordinary Python and should still raise: that one is a
        # bug in code the author is looking at right now.
        try:
            self.refresh_theme_styles()
        except Exception:
            logger.exception("apply_theme_styles raised for %s", type(self).__name__)


def set_style_sheet(widget, sheet: str) -> bool:
    """Assign *sheet* to *widget*, unless that is what it already says.

    ``setStyleSheet`` is never free and never local: Qt re-parses the rule, re-polishes
    the widget, and delivers a ``StyleChange`` to the whole subtree beneath it. Every
    themed widget in that subtree answers a ``StyleChange`` by re-deriving its own rule
    and setting it — which broadcasts again, to *its* subtree. The re-entrancy guards
    stop that recursing forever; they do not stop it being quadratic in the depth of a
    card.

    Re-deriving a rule from ``THEME`` almost always reproduces the string already
    there, so comparing first turns the whole cascade into one string compare per
    widget. It cost 132 tests in ``test_combat_tab.py`` 40,151 ``setStyleSheet`` calls
    and 288,522 ``changeEvent`` calls before this existed.

    Returns whether anything was written, for a caller that has follow-up work to skip.

    This is safe because ``styleSheet()`` returns verbatim what was last set, so an
    equal string means an identical rule. It is *not* a substitute for the guards: a
    genuine theme switch changes the string, so the cascade still runs then — which is
    the one time it has to.
    """
    if widget.styleSheet() == sheet:
        return False
    widget.setStyleSheet(sheet)
    return True
