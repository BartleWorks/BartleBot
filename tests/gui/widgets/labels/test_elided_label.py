"""Tests for ElidedLabel and WrappingElidedLabel — the labels that keep long text from widening a sidebar."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QLabel, QSizePolicy

from bartlebot.gui.widgets.labels.elided_label import (
    ElidedLabel,
    WrappingElidedLabel,
    apply_muted_color,
    elide_long_words,
)
from bartlebot.gui.widgets.labels.themed_label import ThemedWrappingElidedLabel

_LONG = "P1: {1} shatter@boss {2} empower… · P2–P3: {1} pass {2} pass · P4: {1} weakness@boss"
#: A zone path — the case that motivated left elision.  Every zone in a world
#: shares the leading segment, so the tail is the only part that identifies one.
_ZONE = "WizardCity/WC_Streets/Interiors/Golem_Court"


def _half_width(lbl: ElidedLabel) -> int:
    """Half the width the full text wants — wide enough to show several characters, narrow enough to force elision."""
    return QFontMetrics(lbl.font()).horizontalAdvance(lbl.full_text()) // 2


def test_does_not_demand_the_full_text_width(qapp):
    """The whole point: a plain QLabel's minimum width is its full text width."""
    plain = QLabel(_LONG)
    elided = ElidedLabel(_LONG)

    assert plain.minimumSizeHint().width() > 400  # guards the premise
    assert elided.minimumSizeHint().width() < 50


def test_policy_is_shrinkable_so_the_minimum_hint_is_what_counts(qapp):
    """qSmartMinSize uses minimumSizeHint alone only for ShrinkFlag policies.

    Without ShrinkFlag it would take max(sizeHint, minimumSizeHint) instead, and
    the full text width would come straight back.
    """
    policy = ElidedLabel("x").sizePolicy().horizontalPolicy()

    assert policy.value & QSizePolicy.PolicyFlag.ShrinkFlag.value


def test_size_hint_still_asks_for_the_full_width(qapp):
    """Shrink under pressure, but take the natural width when the layout has room.

    An Ignored policy would also zero the minimum, but it discards the size hint
    too — a label with no stretch factor would then collapse to nothing even in
    a wide panel.
    """
    lbl = ElidedLabel(_LONG)

    assert lbl.sizeHint().width() > 400
    assert lbl.minimumSizeHint().width() < 50


def test_keeps_the_full_text_available(qapp):
    lbl = ElidedLabel(_LONG)

    assert lbl.full_text() == _LONG
    assert lbl.text() == _LONG  # so callers reading .text() still see everything
    assert lbl.toolTip() == _LONG


def test_set_text_updates_full_text_and_tooltip(qapp):
    lbl = ElidedLabel("first")
    lbl.setText("second and rather longer")

    assert lbl.full_text() == "second and rather longer"
    assert lbl.toolTip() == "second and rather longer"


def test_empty_text_is_handled(qapp):
    lbl = ElidedLabel()

    assert lbl.full_text() == ""
    assert lbl.toolTip() == ""


def test_none_text_does_not_crash(qapp):
    lbl = ElidedLabel("x")
    lbl.setText(None)

    assert lbl.full_text() == ""


def test_paints_without_error_at_a_narrow_width(qapp):
    """Elision happens in paintEvent, so exercise it rather than trusting the hint."""
    lbl = ElidedLabel(_LONG)
    lbl.resize(40, 16)
    lbl.grab()  # forces a real paint cycle


def test_default_elide_mode_is_right(qapp):
    assert ElidedLabel("x").elide_mode() == Qt.TextElideMode.ElideRight


def test_nothing_is_elided_when_the_text_fits(qapp):
    lbl = ElidedLabel(_ZONE, elide_mode=Qt.TextElideMode.ElideLeft)

    assert lbl.elided_text(lbl.sizeHint().width() * 2) == _ZONE


def test_left_elision_keeps_the_end_of_the_text(qapp):
    """The point of ElideLeft: '…/Interiors/Golem_Court' is what names a zone."""
    lbl = ElidedLabel(_ZONE, elide_mode=Qt.TextElideMode.ElideLeft)

    out = lbl.elided_text(_half_width(lbl))

    assert out != _ZONE  # guards the premise: it really did elide
    assert out.startswith("…")
    assert _ZONE.endswith(out.lstrip("…"))  # what survives is a suffix of the original


def test_right_elision_keeps_the_start_of_the_text(qapp):
    """The mirror image, and the default — used for enemy names, where the head identifies."""
    lbl = ElidedLabel(_ZONE)

    out = lbl.elided_text(_half_width(lbl))

    assert out != _ZONE
    assert out.endswith("…")
    assert _ZONE.startswith(out.rstrip("…"))


def test_paints_without_error_in_left_elide_mode(qapp):
    lbl = ElidedLabel(_ZONE, elide_mode=Qt.TextElideMode.ElideLeft)
    lbl.resize(40, 16)
    lbl.grab()  # forces a real paint cycle through elided_text


def test_apply_muted_color_uses_the_palette_not_a_stylesheet(qapp):
    """ElidedLabel paints its own text, so a QSS color rule would never reach it."""
    from bartlebot.gui.theming.theme import THEME

    lbl = ElidedLabel("x")
    apply_muted_color(lbl)

    assert lbl.styleSheet() == ""
    assert lbl.palette().color(lbl.foregroundRole()).name().lower() == THEME.text_muted.lower()


# -- WrappingElidedLabel ----------------------------------------------------------

#: Wraps anywhere a line needs it: no word in it is wider than the narrowest test width.
_SENTENCE = "Duel strategy for a two to four player team boss duel with blades then traps"
#: One word, far wider than any card, between two that fit.
_UNBROKEN = "Poseidon " + "BossDuel" * 20 + " v2"


def _metrics() -> QFontMetrics:
    return QFontMetrics(WrappingElidedLabel().font())


def _at_width(lbl: WrappingElidedLabel, width: int) -> WrappingElidedLabel:
    """Lay *lbl* out *width* wide, as a visible label would be.

    Shown, offscreen: a hidden widget's resize is held as pending until it is shown,
    so a bare ``resize`` never reaches the ``resizeEvent`` that elides.
    """
    lbl.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    lbl.show()
    lbl.resize(width, 60)
    return lbl


def test_elide_long_words_leaves_text_that_wraps_untouched(qapp):
    assert elide_long_words(_SENTENCE, _metrics(), 120) == _SENTENCE


def test_elide_long_words_elides_only_the_word_that_cannot_fit(qapp):
    metrics = _metrics()

    out = elide_long_words(_UNBROKEN, metrics, 150)

    first, middle, last = out.split(" ")
    assert (first, last) == ("Poseidon", "v2")
    assert middle.endswith("…")
    assert metrics.horizontalAdvance(middle) <= 150


def test_elide_long_words_keeps_the_whitespace(qapp):
    """Line breaks and double spaces are part of what a caller wrote -- the DSL line uses "  ·  "."""
    text = "a  ·  " + "x" * 200 + "\nnext"

    out = elide_long_words(text, _metrics(), 100)

    assert out.startswith("a  ·  ")
    assert out.endswith("\nnext")


def test_elide_long_words_returns_the_text_whole_before_layout(qapp):
    """Width 0 is a label not laid out yet, not a label to reduce to ellipses."""
    assert elide_long_words(_UNBROKEN, _metrics(), 0) == _UNBROKEN


def test_elide_long_words_never_drops_a_word_entirely(qapp):
    assert elide_long_words("x" * 50, _metrics(), 1) == "…"


def test_wrapping_label_does_not_demand_its_longest_word(qapp):
    """The bug: a word-wrapped QLabel's minimum width is its longest word (#118)."""
    plain = QLabel(_UNBROKEN)
    plain.setWordWrap(True)

    assert plain.minimumSizeHint().width() > 400  # guards the premise
    assert WrappingElidedLabel(_UNBROKEN).minimumSizeHint().width() < 50


def test_wrapping_label_measures_like_a_qlabel_when_nothing_is_elided(qapp):
    """Same text, same height at every width -- only the too-long word may change anything."""
    ours = WrappingElidedLabel(_SENTENCE)
    plain = QLabel(_SENTENCE)
    plain.setWordWrap(True)

    for width in (120, 200, 320, 800):
        assert ours.heightForWidth(width) == plain.heightForWidth(width)


def test_wrapping_label_measures_the_text_it_would_show_at_that_width(qapp):
    """Not the text it shows now: a list sizes its rows before the card has its width."""
    ours = WrappingElidedLabel(_UNBROKEN)
    _at_width(ours, 2000)  # shown unelided at this width
    shown_at_150 = QLabel(ours.elided_text(150))
    shown_at_150.setWordWrap(True)

    assert ours.heightForWidth(150) == shown_at_150.heightForWidth(150)


def test_wrapping_label_elides_on_resize_and_restores_on_widening(qapp):
    lbl = WrappingElidedLabel(_UNBROKEN)

    _at_width(lbl, 150)
    assert lbl.is_elided()
    assert "…" in lbl.shown_text()

    _at_width(lbl, 4000)
    assert not lbl.is_elided()
    assert lbl.shown_text() == _UNBROKEN


def test_wrapping_label_reads_back_the_full_text(qapp):
    """A caller reading text() back -- the assignment card splits its DSL line -- never sees an ellipsis."""
    lbl = WrappingElidedLabel(_UNBROKEN)
    _at_width(lbl, 150)

    assert lbl.text() == _UNBROKEN
    assert lbl.full_text() == _UNBROKEN


def test_wrapping_label_tooltip_is_the_full_text_only_while_elided(qapp):
    lbl = WrappingElidedLabel(_UNBROKEN)
    _at_width(lbl, 4000)
    assert lbl.toolTip() == ""

    _at_width(lbl, 150)
    assert lbl.toolTip() == _UNBROKEN


def test_wrapping_label_keeps_the_callers_tooltip_beside_the_full_text(qapp):
    """An invalid strategy's name already says why; eliding it must not replace that."""
    lbl = WrappingElidedLabel(_UNBROKEN)
    lbl.setToolTip("Does not parse: nope")
    _at_width(lbl, 4000)
    assert lbl.toolTip() == "Does not parse: nope"

    _at_width(lbl, 150)
    assert lbl.toolTip() == f"{_UNBROKEN}\n\nDoes not parse: nope"


def test_wrapping_label_is_plain_text(qapp):
    """A strategy name is user text: one containing markup must not render as HTML."""
    assert WrappingElidedLabel("<b>x</b>").textFormat() == Qt.TextFormat.PlainText


def test_the_themed_wrapping_label_elides_as_the_plain_one_does(qapp):
    lbl = ThemedWrappingElidedLabel(_UNBROKEN)

    assert lbl.minimumSizeHint().width() < 50
    assert lbl.text() == _UNBROKEN


def test_the_themed_wrapping_label_reads_its_color_from_the_token(qapp):
    from bartlebot.gui.theming.theme import THEME

    lbl = ThemedWrappingElidedLabel("x", color="warning", css="font-weight: bold;")

    assert THEME.warning in lbl.styleSheet()
    assert "font-weight: bold;" in lbl.styleSheet()


# ---------------------------------------------------------------------------
# WrappingElidedLabel remembers what it measured
# ---------------------------------------------------------------------------


def _font_px(px: int):
    from PyQt6.QtGui import QFont

    font = QFont()
    font.setPixelSize(px)
    return font


def _counting_elision(monkeypatch) -> list[int]:
    """Every width ``elide_long_words`` was actually run at."""
    from bartlebot.gui.widgets.labels import elided_label

    widths: list[int] = []
    real = elided_label.elide_long_words

    def counting(text, metrics, width):
        widths.append(width)
        return real(text, metrics, width)

    monkeypatch.setattr(elided_label, "elide_long_words", counting)
    return widths


def test_the_text_at_a_width_is_measured_once(qapp, monkeypatch):
    """A list sizing its rows asks again at every layout pass: ~49,000 words to open the strategy dialog."""
    lbl = WrappingElidedLabel(_UNBROKEN)
    measured = _counting_elision(monkeypatch)

    first = [lbl.heightForWidth(w) for w in (80, 120, 80, 120, 80)]

    assert sorted(measured) == [80, 120]
    assert first[0] == first[2] == first[4]


def test_new_text_is_measured_again(qapp, monkeypatch):
    lbl = WrappingElidedLabel(_UNBROKEN)
    lbl.elided_text(80)
    measured = _counting_elision(monkeypatch)

    lbl.setText(_SENTENCE)

    assert lbl.elided_text(80) == elide_long_words(_SENTENCE, lbl.fontMetrics(), 80)
    assert 80 in measured


def test_a_font_change_that_changes_nothing_costs_nothing(qapp, monkeypatch):
    """Qt sends FontChange on every polish; most leave the font as it was."""
    lbl = WrappingElidedLabel(_UNBROKEN)
    lbl.setFont(_font_px(13))
    lbl.elided_text(80)
    measured = _counting_elision(monkeypatch)

    lbl.setFont(_font_px(13))  # a new QFont, equal to the old: still a FontChange to Qt
    lbl.elided_text(80)

    assert measured == []


def test_a_real_font_change_elides_again_at_the_new_size(qapp):
    lbl = WrappingElidedLabel(_UNBROKEN)
    lbl.setFont(_font_px(10))
    small = lbl.elided_text(80)

    lbl.setFont(_font_px(24))

    assert lbl.elided_text(80) == elide_long_words(_UNBROKEN, lbl.fontMetrics(), 80)
    assert lbl.elided_text(80) != small
