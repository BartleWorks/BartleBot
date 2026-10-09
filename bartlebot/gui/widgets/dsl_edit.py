"""Text boxes for the combat DSL: syntax coloring, underlines, and completion.

Two kinds of underline: a parse error, in ``error``, and a spell name no card has, in
``syntax_warning`` — ``combat_runner.dsl_error`` and ``dsl_warnings`` decide both.

Two classes, because the DSL is edited in two widgets of different Qt types —
``DslPlainTextEdit`` for the Player Strategies tab and ``DslGrowingTextEdit`` for an
assignment card — and both get everything from ``_DslEditMixin``. What each word *is*
comes from :mod:`bartlebot.combat.dsl_syntax`; this module only draws and handles keys.

Completion follows Qt's own "Custom Completer" recipe, and its one non-obvious rule
is why this is a subclass rather than an event filter. While the popup is open it
has the keyboard, and ``QCompleter`` hands each key back to the editor by calling
``event()`` on it directly — which skips any event filter installed on the editor.
So the editor's own ``keyPressEvent`` has to decline Enter, Tab and Escape while the
popup is up, or Enter types a newline instead of choosing the suggestion.

When the popup opens on its own: after anything that can only be followed by a
keyword (``@``, ``any<``, ``?(``, ``.`` in a condition), and as soon as a typed word
matches a keyword. Never at the start of a move with nothing typed, where the
likeliest thing coming is a spell name. Ctrl+Space asks for it anywhere.

Colors are read from ``THEME`` each time a line is colored, so nothing is baked
in; a theme switch arrives as a ``StyleChange``, and the editor re-colors.
"""

from __future__ import annotations

import logging
import re

from PyQt6.QtCore import QEvent, QStringListModel, Qt, QTimer
from PyQt6.QtGui import QKeyEvent, QSyntaxHighlighter, QTextCharFormat, QTextCursor, QTextDocument
from PyQt6.QtWidgets import QCompleter, QPlainTextEdit, QWidget

from bartlebot.combat import dsl_syntax
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.growing_text_edit import GrowingTextEdit
from bartlebot.nodes.combat_runner import DslError, DslWarning

logger = logging.getLogger(__name__)

#: Keys the open popup owns. The editor declines them so ``QCompleter`` acts on them.
_POPUP_KEYS = frozenset({Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Escape, Qt.Key.Key_Tab, Qt.Key.Key_Backtab})
_NON_SPACE = re.compile(r"\S*")


class DslHighlighter(QSyntaxHighlighter):
    """Colors each line of a DSL document, and underlines where it stops parsing."""

    def __init__(self, document: QTextDocument) -> None:
        super().__init__(document)
        self._error: DslError | None = None
        self._warnings: tuple[DslWarning, ...] = ()

    @property
    def error(self) -> DslError | None:
        return self._error

    @property
    def warnings(self) -> tuple[DslWarning, ...]:
        return self._warnings

    def set_error(self, error: DslError | None) -> None:
        """Underline *error*'s position, or nothing. Re-colors only the lines involved."""
        if error == self._error:
            return
        previous, self._error = self._error, error
        self._rehighlight_lines(e.line for e in (previous, error) if e is not None)

    def set_warnings(self, warnings: tuple[DslWarning, ...]) -> None:
        """Underline each of *warnings* in place of the last set. Re-colors only the lines involved."""
        warnings = tuple(warnings)
        if warnings == self._warnings:
            return
        previous, self._warnings = self._warnings, warnings
        self._rehighlight_lines(w.line for w in (*previous, *warnings))

    def highlightBlock(self, text: str | None) -> None:  # noqa: N802 - Qt override
        # PyQt6 aborts the process when an exception escapes a virtual method, and
        # coloring is cosmetic: a line drawn plain is always better than no app.
        try:
            self._highlight(text or "")
        except Exception:
            logger.exception("DSL highlighting failed")

    def _highlight(self, text: str) -> None:
        stack = dsl_syntax.next_line_stack(dsl_syntax.decode_stack(self.previousBlockState()))
        scan = dsl_syntax.scan_line(text, stack)
        for span in scan.spans:
            fmt = QTextCharFormat()
            fmt.setForeground(THEME.qcolor(span.kind.value))
            self.setFormat(span.start, span.length, fmt)
        self.setCurrentBlockState(dsl_syntax.encode_stack(scan.stack))
        self._underline_warnings(text)
        self._underline_error(text)

    def _rehighlight_lines(self, linenos) -> None:  # noqa: ANN001
        document = self.document()
        for lineno in {n for n in linenos if n > 0}:
            block = document.findBlockByNumber(lineno - 1)
            if block.isValid():
                self.rehighlightBlock(block)

    def _underline_warnings(self, text: str) -> None:
        lineno = self.currentBlock().blockNumber() + 1
        for warning in self._warnings:
            if warning.line == lineno and warning.column > 0:
                start = warning.column - 1
                self._underline(start, min(len(text), start + max(1, warning.length)), "syntax_warning")

    def _underline_error(self, text: str) -> None:
        error = self._error
        if error is None or error.column <= 0 or not text:
            return
        if error.line != self.currentBlock().blockNumber() + 1:
            return
        # The parser names the first character it could not use; underline the run of
        # non-space from there, so a single character is not a squiggle too small to see.
        start = min(error.column - 1, len(text) - 1)
        end = max(start + 1, _NON_SPACE.match(text, start).end())
        self._underline(start, end, "error")

    def _underline(self, start: int, end: int, token: str) -> None:
        color = THEME.qcolor(token)
        for position in range(start, end):
            fmt = self.format(position)
            fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
            fmt.setUnderlineColor(color)
            self.setFormat(position, 1, fmt)


class _DslEditMixin:
    """Highlighting and completion for a Qt text editor.

    Goes first in the base list, and has no ``__init__`` — the same two rules as
    ``ThemedMixin``, for the same reason: PyQt6 only handles cooperative multiple
    inheritance with a single Qt base. The concrete class calls
    :meth:`_install_dsl_support` at the end of its own ``__init__``.
    """

    _dsl_highlighter: DslHighlighter | None = None
    _dsl_completer: QCompleter | None = None

    def _install_dsl_support(self) -> None:
        self._dsl_highlighter = DslHighlighter(self.document())
        self._dsl_model = QStringListModel(self)
        self._dsl_completion: dsl_syntax.Completion | None = None

        completer = QCompleter(self._dsl_model, self)
        completer.setWidget(self)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setMaxVisibleItems(10)
        completer.activated[str].connect(self._insert_completion)
        self._dsl_completer = completer

        # Choosing ``any<`` should offer the spell types straight away. Not from inside
        # ``activated``: a mouse click hides the popup *after* emitting it, which would
        # close the popup just reopened. Parented, so it dies with the editor.
        self._reopen_timer = QTimer(self)
        self._reopen_timer.setSingleShot(True)
        self._reopen_timer.setInterval(0)
        self._reopen_timer.timeout.connect(lambda: self.show_completions(forced=False, may_open=True))

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    @property
    def highlighter(self) -> DslHighlighter | None:
        return self._dsl_highlighter

    @property
    def completer(self) -> QCompleter | None:
        return self._dsl_completer

    def set_dsl_error(self, error: DslError | None) -> None:
        """Underline where *error* says the DSL stops parsing; ``None`` clears it."""
        highlighter = self._dsl_highlighter
        if highlighter is not None:
            self._recolor_quietly(lambda: highlighter.set_error(error))

    def set_dsl_warnings(self, warnings: tuple[DslWarning, ...]) -> None:
        """Underline each spell name in *warnings*; an empty tuple clears them."""
        highlighter = self._dsl_highlighter
        if highlighter is not None:
            self._recolor_quietly(lambda: highlighter.set_warnings(warnings))

    def show_completions(self, *, forced: bool, may_open: bool) -> bool:
        """Offer what can go at the cursor, and return whether the popup is showing.

        *forced* is Ctrl+Space: suggest even where the context would not on its own.
        *may_open* false only updates or closes a popup that is already open.
        """
        completer = self._dsl_completer
        if completer is None:
            return False
        popup = completer.popup()
        cursor = self.textCursor()
        if self.isReadOnly() or cursor.hasSelection():
            popup.hide()
            return False

        completion = dsl_syntax.complete(self._text_before_cursor(cursor))
        candidates = completion.candidates
        finished = len(candidates) == 1 and candidates[0].lower() == completion.prefix.lower()
        wanted = bool(candidates) and not (finished and not forced)
        if wanted and not (forced or popup.isVisible()):
            wanted = may_open and (bool(completion.prefix) or completion.eager)
        if not wanted:
            popup.hide()
            self._dsl_completion = None
            return False

        self._dsl_completion = completion
        self._dsl_model.setStringList(list(candidates))
        completer.setCompletionPrefix(completion.prefix)
        popup.setCurrentIndex(completer.completionModel().index(0, 0))
        rect = self.cursorRect()
        rect.translate(self.viewport().pos())
        rect.setWidth(popup.sizeHintForColumn(0) + popup.verticalScrollBar().sizeHint().width() + 8)
        completer.complete(rect)
        return True

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        completer = self._dsl_completer
        popup_open = completer is not None and completer.popup().isVisible()
        if popup_open and event.key() in _POPUP_KEYS:
            event.ignore()
            return

        forced = event.key() == Qt.Key.Key_Space and event.modifiers() == Qt.KeyboardModifier.ControlModifier
        if not forced:
            super().keyPressEvent(event)  # type: ignore[misc]
        if completer is None:
            return

        typed = event.text()
        if not (forced or popup_open or (typed and typed.isprintable())):
            return
        try:
            self.show_completions(forced=forced, may_open=forced or (bool(typed) and typed.isprintable()))
        except Exception:
            logger.exception("DSL completion failed")

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)  # type: ignore[misc]
        if event.type() != QEvent.Type.StyleChange or self._dsl_highlighter is None:
            return
        try:
            self._recolor_quietly(self._dsl_highlighter.rehighlight)
        except Exception:
            logger.exception("DSL re-highlight on theme change failed")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _recolor_quietly(self, recolor) -> None:  # noqa: ANN001
        """Run a re-coloring without it reaching ``textChanged``.

        Outside an edit, Qt reports a highlighter's format change as a change to the
        document's contents, so ``textChanged`` fires with the text untouched. Both
        editors write the DSL back to their strategy on that signal, and the assignment
        card clears the underline there too — so an underline it had just drawn erased
        itself, and every theme switch marked every open strategy as edited.
        """
        blocked = self.blockSignals(True)
        try:
            recolor()
        finally:
            self.blockSignals(blocked)

    @staticmethod
    def _text_before_cursor(cursor: QTextCursor) -> str:
        # Through a selection rather than slicing toPlainText() by position: Qt counts
        # positions in UTF-16 units, so anything astral earlier in the text would put a
        # Python slice in the wrong place.
        head = QTextCursor(cursor)
        head.movePosition(QTextCursor.MoveOperation.Start, QTextCursor.MoveMode.KeepAnchor)
        return head.selectedText().replace(" ", "\n")

    def _insert_completion(self, text: str) -> None:
        completion = self._dsl_completion
        if completion is None:
            return
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor, len(completion.prefix))
        cursor.insertText(text)
        self.setTextCursor(cursor)
        self._dsl_completion = None
        if text.endswith(("<", "(")):
            self._reopen_timer.start()


class DslPlainTextEdit(_DslEditMixin, QPlainTextEdit):
    """A ``QPlainTextEdit`` for the combat DSL."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._install_dsl_support()


class DslGrowingTextEdit(_DslEditMixin, GrowingTextEdit):
    """A :class:`GrowingTextEdit` for the combat DSL."""

    def __init__(self, *, min_lines: int = 2, max_lines: int = 8, parent: QWidget | None = None) -> None:
        super().__init__(min_lines=min_lines, max_lines=max_lines, parent=parent)
        self._install_dsl_support()
