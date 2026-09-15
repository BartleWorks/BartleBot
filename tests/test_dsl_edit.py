"""Tests for the DSL text boxes: colouring, the parse-error underline, and completion.

What each word *is* is tested in ``test_dsl_syntax.py``; these check that the widgets
draw it, follow a theme switch, never count colouring as an edit, and hand the keys
around the completion popup the way Qt's completer expects.
"""

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import QApplication

from bartlebot import dsl_syntax
from bartlebot.bot.strategy_models import DuelConfig, PlayerAssignment, PlayerCombatStrategy
from bartlebot.gui.combat_strategies.duel_editor import DuelConfigEditor
from bartlebot.gui.combat_strategies.player_editor import PlayerStrategyEditor
from bartlebot.gui.theme import DARK, LIGHT, active_theme, set_theme_instance
from bartlebot.gui.widgets.dsl_edit import DslGrowingTextEdit, DslPlainTextEdit
from bartlebot.nodes.combat_runner import dsl_error

_BROKEN = "any<damag> @ enemy"  # stops parsing at line 1, column 5


@pytest.fixture
def light():
    previous = active_theme()
    set_theme_instance(LIGHT)
    yield
    set_theme_instance(previous)


def _format_at(edit, column: int, line: int = 0) -> QTextCharFormat | None:
    block = edit.document().findBlockByNumber(line)
    for fmt_range in block.layout().formats():
        if fmt_range.start <= column < fmt_range.start + fmt_range.length:
            return fmt_range.format
    return None


def _colour_at(edit, column: int, line: int = 0) -> str | None:
    fmt = _format_at(edit, column, line)
    return fmt.foreground().color().name() if fmt is not None else None


def _underlined(edit, column: int, line: int = 0) -> bool:
    fmt = _format_at(edit, column, line)
    return fmt is not None and fmt.underlineStyle() == QTextCharFormat.UnderlineStyle.WaveUnderline


def _to_end(edit) -> None:
    cursor = edit.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    edit.setTextCursor(cursor)


def _offered(edit) -> list[str]:
    model = edit.completer.completionModel()
    return [model.index(row, 0).data() for row in range(model.rowCount())]


# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------


def _luminance(hex_colour: str) -> float:
    colour = QColor(hex_colour)

    def channel(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(colour.red()) + 0.7152 * channel(colour.green()) + 0.0722 * channel(colour.blue())


@pytest.mark.parametrize("palette", [LIGHT, DARK], ids=["light", "dark"])
@pytest.mark.parametrize("ground", ["bg", "surface"])
def test_dsl_syntax_colours_are_legible(palette, ground):
    """4.5:1 on both grounds — an editable DSL box is drawn on ``bg``, a read-only one on ``surface``."""
    for kind in dsl_syntax.Kind:
        lighter, darker = sorted((_luminance(getattr(palette, kind.value)), _luminance(getattr(palette, ground))))
        assert (darker + 0.05) / (lighter + 0.05) >= 4.5, kind


@pytest.mark.parametrize("editor_class", [DslPlainTextEdit, DslGrowingTextEdit])
def test_each_word_is_drawn_in_its_theme_colour(qapp, light, editor_class):
    edit = editor_class()
    edit.setPlainText("any<aoe> @ aoe")

    assert _colour_at(edit, 0) == LIGHT.syntax_keyword.lower()
    assert _colour_at(edit, 4) == LIGHT.syntax_type.lower()
    assert _colour_at(edit, 9) == LIGHT.syntax_punct.lower()
    assert _colour_at(edit, 11) == LIGHT.syntax_target.lower()


def test_a_theme_switch_recolours_the_text(qapp, light):
    edit = DslPlainTextEdit()
    edit.setPlainText("pass")
    assert _colour_at(edit, 0) == LIGHT.syntax_keyword.lower()

    set_theme_instance(DARK)
    QApplication.sendEvent(edit, QEvent(QEvent.Type.StyleChange))

    assert _colour_at(edit, 0) == DARK.syntax_keyword.lower()


def test_colouring_and_underlining_never_count_as_an_edit(qapp):
    """Both editors write the DSL back to the strategy on ``textChanged``."""
    edit = DslPlainTextEdit()
    edits = []
    edit.textChanged.connect(lambda: edits.append(edit.toPlainText()))
    edit.setPlainText(_BROKEN)
    assert len(edits) == 1

    edit.set_dsl_error(dsl_error(_BROKEN))
    edit.set_dsl_error(None)
    QApplication.sendEvent(edit, QEvent(QEvent.Type.StyleChange))

    assert len(edits) == 1


# ---------------------------------------------------------------------------
# The error underline
# ---------------------------------------------------------------------------


def test_the_parse_error_is_underlined_from_where_the_parser_stopped(qapp):
    edit = DslPlainTextEdit()
    edit.setPlainText(_BROKEN)

    edit.set_dsl_error(dsl_error(_BROKEN))

    assert not _underlined(edit, 3)
    assert all(_underlined(edit, column) for column in range(4, 10))  # "damag>"
    assert not _underlined(edit, 11)


def test_the_underline_lands_on_the_line_the_error_is_on(qapp):
    text = "pass @ self\n" + _BROKEN
    edit = DslPlainTextEdit()
    edit.setPlainText(text)

    edit.set_dsl_error(dsl_error(text))

    assert not _underlined(edit, 4, line=0)
    assert _underlined(edit, 4, line=1)


def test_clearing_the_error_removes_the_underline_but_keeps_the_colours(qapp, light):
    edit = DslPlainTextEdit()
    edit.setPlainText(_BROKEN)
    edit.set_dsl_error(dsl_error(_BROKEN))

    edit.set_dsl_error(None)

    assert not _underlined(edit, 4)
    assert _colour_at(edit, 0) == LIGHT.syntax_keyword.lower()


def test_the_player_editor_underlines_after_validating_and_clears_on_an_edit(qapp):
    editor = PlayerStrategyEditor()
    editor.load(PlayerCombatStrategy(name="A", dsl=_BROKEN))
    assert editor._dsl_edit.highlighter.error == dsl_error(_BROKEN)

    editor._dsl_edit.setPlainText("any<damage> @ ene")

    assert editor._dsl_edit.highlighter.error is None


def test_the_assignment_card_underlines_after_validating_and_clears_on_an_edit(qapp):
    editor = DuelConfigEditor()
    editor.load(DuelConfig(player_assignments=[PlayerAssignment(players=[1], dsl=_BROKEN)]), [])
    card = editor.cards[0]
    assert card._dsl_edit.highlighter.error == dsl_error(_BROKEN)

    card._dsl_edit.setPlainText("any<damage> @ ene")

    assert card._dsl_edit.highlighter.error is None


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------


def test_typing_any_lt_offers_the_spell_types(qapp, qtbot):
    edit = DslPlainTextEdit()

    qtbot.keyClicks(edit, "any<")

    assert edit.completer.popup().isVisible()
    assert _offered(edit)[: len(dsl_syntax.SPELL_TYPES)] == list(dsl_syntax.SPELL_TYPES)


def test_enter_on_the_popup_replaces_the_typed_prefix(qapp, qtbot):
    edit = DslPlainTextEdit()
    qtbot.keyClicks(edit, "any<BL")
    assert _offered(edit) == ["blade"]

    # While the popup is open it has the keyboard, so that is where Enter arrives.
    qtbot.keyClick(edit.completer.popup(), Qt.Key.Key_Return)

    assert edit.toPlainText() == "any<blade"
    assert not edit.completer.popup().isVisible()


def test_escape_closes_the_popup_and_keeps_the_text(qapp, qtbot):
    edit = DslPlainTextEdit()
    qtbot.keyClicks(edit, "pass @ ")
    assert edit.completer.popup().isVisible()

    qtbot.keyClick(edit.completer.popup(), Qt.Key.Key_Escape)

    assert edit.toPlainText() == "pass @ "
    assert not edit.completer.popup().isVisible()


def test_the_start_of_a_move_waits_for_ctrl_space(qapp, qtbot):
    edit = DslPlainTextEdit()
    qtbot.keyClicks(edit, "pass | ")
    assert not edit.completer.popup().isVisible()

    qtbot.keyClick(edit, Qt.Key.Key_Space, Qt.KeyboardModifier.ControlModifier)

    assert edit.completer.popup().isVisible()
    assert _offered(edit) == ["any<", "pass", "willcast", "draw"]
    assert edit.toPlainText() == "pass | "  # Ctrl+Space types nothing


def test_a_finished_keyword_closes_the_popup_so_enter_is_a_newline_again(qapp, qtbot):
    edit = DslPlainTextEdit()
    qtbot.keyClicks(edit, "pass @ sel")
    assert edit.completer.popup().isVisible()

    qtbot.keyClicks(edit, "f")

    assert not edit.completer.popup().isVisible()


def test_choosing_an_opener_queues_what_goes_inside_it(qapp, qtbot):
    edit = DslPlainTextEdit()
    qtbot.keyClicks(edit, "an")
    qtbot.keyClick(edit.completer.popup(), Qt.Key.Key_Return)
    assert edit.toPlainText() == "any<"
    # Deferred a turn, because a mouse pick hides the popup after reporting the choice.
    assert edit._reopen_timer.isActive()

    edit._reopen_timer.stop()
    edit.show_completions(forced=False, may_open=True)

    assert "damage" in _offered(edit)


def test_a_read_only_box_offers_nothing(qapp, qtbot):
    edit = DslGrowingTextEdit()
    edit.setPlainText("any<")
    edit.setReadOnly(True)
    _to_end(edit)

    qtbot.keyClick(edit, Qt.Key.Key_Space, Qt.KeyboardModifier.ControlModifier)

    assert not edit.completer.popup().isVisible()
