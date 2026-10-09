"""Tests for ``ThemedMixin``.

The mixin is four lines of logic and three rules that are easy to get wrong, so
what is tested here is mostly the rules: that the override actually wins the MRO
against Qt's own, that the re-entrancy guard holds when a subclass styles itself,
and that the base class's ``changeEvent`` still runs.
"""

import logging

import pytest
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QLabel, QPushButton, QWidget

import bartlebot.gui.theming.theme as theme_mod
from bartlebot.gui.theming.theme import THEME
from bartlebot.gui.theming.themed import ThemedMixin, set_style_sheet


@pytest.fixture
def theme():
    """Pin the active theme and restore it, as tests/test_picker_popup.py does."""
    before = theme_mod.active_theme()
    yield theme_mod.set_theme
    theme_mod.set_theme_instance(before)


class _Panel(ThemedMixin, QWidget):
    """The documented shape: mixin first, styles applied at end of construction."""

    def __init__(self) -> None:
        super().__init__()
        self.label = QLabel("hello", self)
        self.calls = 0
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        self.calls += 1
        self.label.setStyleSheet(f"color: {THEME.text};")


class _SelfStyling(ThemedMixin, QWidget):
    """Styles *itself*, so every apply feeds a StyleChange straight back in."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        self.calls += 1
        self.setStyleSheet(f"background: {THEME.surface};")


def _switch(qapp, widget) -> None:
    """What QApplication.setStyleSheet does to one widget on a theme change."""
    qapp.sendEvent(widget, QEvent(QEvent.Type.StyleChange))


# ---------------------------------------------------------------------------
# The success path
# ---------------------------------------------------------------------------


def test_a_style_change_reapplies_the_styles(qapp):
    panel = _Panel()
    assert panel.calls == 1  # construction
    _switch(qapp, panel)
    assert panel.calls == 2


def test_the_new_theme_is_what_gets_applied(qapp, theme):
    theme("light")
    panel = _Panel()
    light = panel.label.styleSheet()
    theme("dark")
    _switch(qapp, panel)
    assert panel.label.styleSheet() != light
    assert THEME.text in panel.label.styleSheet()


def test_unrelated_events_do_not_restyle(qapp):
    panel = _Panel()
    qapp.sendEvent(panel, QEvent(QEvent.Type.EnabledChange))
    qapp.sendEvent(panel, QEvent(QEvent.Type.FontChange))
    assert panel.calls == 1


# ---------------------------------------------------------------------------
# The three rules
# ---------------------------------------------------------------------------


def test_a_self_styling_widget_does_not_recurse(qapp):
    """setStyleSheet on self re-enters changeEvent; the guard is what stops it."""
    w = _SelfStyling()
    assert w.calls == 1  # not 2, and not a RecursionError
    _switch(qapp, w)
    assert w.calls == 2


def test_the_guard_is_released_after_applying(qapp):
    """A guard left set would silently ignore every later theme switch."""
    w = _SelfStyling()
    for expected in (2, 3, 4):
        _switch(qapp, w)
        assert w.calls == expected


def test_the_guard_is_released_even_when_applying_raises(qapp):
    class _Broken(ThemedMixin, QWidget):
        fail = True
        calls = 0

        def apply_theme_styles(self):
            type(self).calls += 1
            if self.fail:
                raise RuntimeError("boom")

    w = _Broken()
    # Called directly, not through changeEvent: an exception crossing PyQt6's
    # virtual-method boundary calls qFatal() and takes the process with it, which
    # is what the try/except in changeEvent exists to prevent (tested separately).
    with pytest.raises(RuntimeError):
        w.refresh_theme_styles()
    w.fail = False
    w.refresh_theme_styles()
    assert _Broken.calls == 2


def test_the_base_class_change_event_still_runs(qapp):
    """super().changeEvent is what keeps Qt's own handling — e.g. EnabledChange."""
    seen = []

    class _Watching(QWidget):
        def changeEvent(self, event):
            seen.append(event.type())
            super().changeEvent(event)

    class _Sub(ThemedMixin, _Watching):
        def apply_theme_styles(self):
            pass

    _switch(qapp, _Sub())
    assert QEvent.Type.StyleChange in seen


def test_it_works_on_bases_other_than_qwidget(qapp):
    """The 22 classes to convert span QWidget, QPushButton, QLabel, QScrollArea."""

    class _Btn(ThemedMixin, QPushButton):
        calls = 0

        def apply_theme_styles(self):
            type(self).calls += 1

    _switch(qapp, _Btn("ok"))
    assert _Btn.calls == 1


def test_forgetting_to_implement_it_says_so(qapp):
    class _Empty(ThemedMixin, QWidget):
        pass

    with pytest.raises(NotImplementedError, match="_Empty"):
        _Empty().refresh_theme_styles()


def test_calling_apply_directly_skips_the_guard(qapp):
    """Why there are two names.

    ``apply_theme_styles`` is unguarded by construction — it is the hook, not the
    entry point. A self-styling widget that calls it directly applies twice, which
    is the bug this split exists to make impossible for anyone following the
    documented shape.
    """

    class _Direct(ThemedMixin, QWidget):
        def __init__(self):
            super().__init__()
            self.calls = 0
            self.apply_theme_styles()  # the mistake

        def apply_theme_styles(self):
            self.calls += 1
            self.setStyleSheet(f"background: {THEME.surface};")

    assert _Direct().calls == 2
    assert _SelfStyling().calls == 1  # the documented shape, for contrast


def test_a_raising_restyle_does_not_kill_the_app(qapp, caplog):
    """PyQt6 turns an exception escaping changeEvent into qFatal() — a hard abort.

    A theme switch is cosmetic; it must not be able to take the process down. The
    boundary swallows and logs instead, and the widget keeps whatever styling it
    had.
    """

    class _Boom(ThemedMixin, QWidget):
        def apply_theme_styles(self):
            raise RuntimeError("boom")

    w = _Boom()
    with caplog.at_level(logging.ERROR, logger="bartlebot.gui.theming.themed"):
        _switch(qapp, w)  # must return normally
    assert "boom" in caplog.text


def test_the_guard_is_released_after_a_swallowed_failure(qapp):
    """A guard left set by a raising apply would freeze the widget's styling."""

    class _Flaky(ThemedMixin, QWidget):
        fail = True
        calls = 0

        def apply_theme_styles(self):
            type(self).calls += 1
            if self.fail:
                raise RuntimeError("boom")

    w = _Flaky()
    _switch(qapp, w)
    w.fail = False
    _switch(qapp, w)
    assert _Flaky.calls == 2


class TestSetStyleSheet:
    """``set_style_sheet`` — the skip that keeps a theme pass from going quadratic."""

    def test_a_new_sheet_is_written(self, qapp):
        w = QWidget()
        assert set_style_sheet(w, "color: red;") is True
        assert w.styleSheet() == "color: red;"

    def test_a_changed_sheet_is_written(self, qapp):
        w = QWidget()
        set_style_sheet(w, "color: red;")
        assert set_style_sheet(w, "color: blue;") is True
        assert w.styleSheet() == "color: blue;"

    def test_an_unchanged_sheet_is_not_written(self, qapp):
        """The point of the helper: an identical rule must not be re-assigned."""
        w = QWidget()
        set_style_sheet(w, "color: red;")
        assert set_style_sheet(w, "color: red;") is False
        assert w.styleSheet() == "color: red;"

    def test_skipping_sends_no_style_change(self, qapp):
        """Why the skip matters -- a re-assignment broadcasts to the whole subtree.

        Each StyleChange a themed child answers re-derives *its* rule and sets it,
        which broadcasts again. That is the cascade the helper exists to cut, so the
        absence of the event is the behavior under test, not an implementation detail.
        """
        seen: list[int] = []

        class _Counting(QWidget):
            def changeEvent(self, event):
                super().changeEvent(event)
                if event.type() == QEvent.Type.StyleChange:
                    seen.append(1)

        parent = QWidget()
        _Counting(parent)  # parented, so `parent` owns it
        set_style_sheet(parent, "color: red;")
        first = len(seen)
        assert first, "a genuine change must still reach the subtree"

        set_style_sheet(parent, "color: red;")
        assert len(seen) == first, "an unchanged sheet must not re-broadcast"

    def test_a_theme_switch_still_gets_through(self, qapp):
        """The skip must not defeat the thing it is guarding: a real switch writes."""
        w = QWidget()
        set_style_sheet(w, f"color: {THEME.text};")
        before = w.styleSheet()
        assert set_style_sheet(w, "color: #123456;") is True
        assert w.styleSheet() != before
