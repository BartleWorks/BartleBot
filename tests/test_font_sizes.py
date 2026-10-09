"""Every font size in the app comes from a ``THEME`` token.

Sizes were written as literals in about eighty places once — ``font-size: 11px`` in
a style sheet, ``setPointSize(9)`` on a font, a ``_FONT_PX`` constant per module —
so the same decision was kept in dozens of places and drifted between them.  They
are tokens now, and this holds the line: the checks below fail on a literal size
anywhere under ``bartlebot/``, which is what stops the next one being added.

The reads are barred for a separate reason.  ``pointSize()`` returns -1 on a font
whose size was set in px, so ``font.setPointSize(font.pointSize() + 1)`` silently
resolves to 0, and a ``pointSizeF()`` read into a ``max(..., 6.0)`` floor silently
resolves to that floor.  Both shipped in this app before the conversion.  Size a
font from a token instead of from another font.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bartlebot.paths import project_root

_ROOT = project_root()
_PACKAGE = _ROOT / "bartlebot"

#: The one module allowed to spell a size out: it defines the tokens.
_TOKEN_MODULE = _PACKAGE / "gui" / "theme.py"

_RULES: list[tuple[str, str, str]] = [
    (
        "literal font-size in a style sheet",
        r"font-size:\s*\d",
        'use a token — f"font-size: {THEME.font_small_px}px"',
    ),
    (
        "font size set in points",
        r"\.setPointSizeF?\(",
        "sizes are px — use setPixelSize() with a token, or THEME.scaled_ui_font()",
    ),
    (
        "literal pixel size",
        r"\.setPixelSize\(\s*\d",
        "use a token, or an offset from one: THEME.font_body_px + 1",
    ),
    (
        "literal size in a QFont constructor",
        r"QFont\([^()]*,\s*\d",
        "use THEME.ui_font(px, bold=...)",
    ),
    (
        "font size read back off a font",
        r"\.(pointSizeF?|pixelSize)\(\)",
        "set the size from a token; a px font reports pointSize() as -1",
    ),
    (
        "per-module font size constant",
        r"^_?[A-Z_]*FONT_(PX|PT|SIZE)\b\s*=|^\s*[A-Z_]*FONT_(PX|PT|SIZE)\b\s*=\s*\d",
        "the token belongs in gui/theme.py",
    ),
]


def _sources() -> list[Path]:
    return [p for p in _PACKAGE.rglob("*.py") if p != _TOKEN_MODULE]


@pytest.mark.parametrize(("what", "pattern", "instead"), _RULES, ids=[r[0] for r in _RULES])
def test_no_literal_font_sizes(what: str, pattern: str, instead: str) -> None:
    rx = re.compile(pattern, re.MULTILINE)
    offenders: list[str] = []
    for path in _sources():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue  # prose about the rule, not a size
            if rx.search(line):
                offenders.append(f"{path.relative_to(_ROOT).as_posix()}:{lineno}: {line.strip()}")
    assert not offenders, f"{what} ({instead}):\n" + "\n".join(offenders)


def test_a_widget_pinned_to_a_height_still_fits_its_text(qapp) -> None:
    """Every fixed height that holds text is tall enough for the text it holds.

    These are the widgets sized by a number rather than by their own metrics, and each
    one here was a constant that happened to match the text at some earlier body size: a
    pill at 14px, a badge at 14, a chip at 20. Raising the body text cut into all of
    them at once, and nothing failed -- a three-pixel vertical clip on a short badge is
    not something a test suite notices on its own, and it is barely something a person
    notices either.

    ``sizeHint()`` is the right question because ``setFixedHeight`` does not change it:
    a QLabel goes on reporting the height its text wants while being held at the height
    it was given, so the gap between the two is exactly the clipping.
    """
    from bartlebot.gui.canvas.node_browser_dialog import _Chip
    from bartlebot.gui.widgets.player_pills import PlayerBadge, PlayerSlotPill
    from bartlebot.gui.widgets.tag_pill import ActionPill, NeutralPill, TagPill

    widgets = {
        "PlayerBadge": PlayerBadge(1),
        "PlayerSlotPill": PlayerSlotPill(1),
        "NeutralPill": NeutralPill("v1.0"),
        "ActionPill": ActionPill("Defeat the second Lost Soul", "state_1"),
        "TagPill": TagPill("Boss"),
        "_Chip": _Chip("FAILURE", "#333333", "#ffffff"),
    }
    short = {
        name: (w.minimumHeight(), w.sizeHint().height())
        for name, w in widgets.items()
        if w.sizeHint().height() > w.minimumHeight() > 0
    }
    assert not short, "pinned shorter than their own text (fixed, wants): " + repr(short)


@pytest.mark.parametrize("lines", [2, 3, 4])
def test_a_multi_line_value_field_shows_every_line_it_promises(qapp, lines: int) -> None:
    """``value_edit_multi(n)`` is n lines tall, at whatever size the body text is.

    It charged 18px a line, which was the body font's line spacing when it was written,
    so the last line went under the fold as soon as that moved. Asserted by asking the
    field whether it can scroll: if it can, there is text it is not showing.
    """
    from bartlebot.gui.sidebar.sidebar_section import value_edit_multi

    field = value_edit_multi(lines)
    field.setPlainText("\n".join(f"line {n}" for n in range(lines)))
    field.resize(200, field.height())
    field.show()
    qapp.processEvents()
    bar = field.verticalScrollBar()

    assert bar.maximum() <= bar.minimum(), f"{lines} lines do not fit {field.height()}px"


def test_the_derived_sizes_stay_in_their_order() -> None:
    """The four UI sizes are one ladder, so a body size change keeps their order."""
    from bartlebot.gui.theme import LIGHT

    assert LIGHT.font_micro_px < LIGHT.font_subtext_px < LIGHT.font_small_px
    assert LIGHT.font_small_px < LIGHT.font_body_px < LIGHT.font_heading_px
    assert LIGHT.font_node_sub_px < LIGHT.font_node_px


def test_no_font_token_varies_by_theme() -> None:
    """Sizes are the same in every theme, which is what lets a widget bake one in.

    ``test_theme_refresh_guard`` demands that a widget re-derive any ``THEME`` value it
    writes into its own stylesheet, because a color set under one theme is stale under
    the next. It excuses the font tokens on the strength of this: a size is the same
    whichever theme is active, so there is nothing to go stale. Give a theme a font size
    of its own and that exemption turns into a bug this test fails first.
    """
    from bartlebot.gui.theme import DARK, LIGHT, build_custom_theme

    sizes = lambda t: {  # noqa: E731
        name: getattr(t, name) for name in dir(t) if name.startswith("font_") and isinstance(getattr(t, name), int)
    }
    assert sizes(LIGHT) == sizes(DARK)
    assert sizes(build_custom_theme({"accent": "#ff0000"})) == sizes(LIGHT)


def test_the_font_helpers_size_in_pixels() -> None:
    """Not points: a font sized in points reports -1 for its pixel size, and every
    widget that measures its own text would measure the wrong thing."""
    from bartlebot.gui.theme import THEME

    assert THEME.ui_font().pixelSize() == THEME.font_body_px
    assert THEME.ui_font(THEME.font_micro_px, bold=True).pixelSize() == THEME.font_micro_px
    assert THEME.ui_font(bold=True).bold()
    assert THEME.font_mono().pixelSize() == THEME.font_body_px
    assert THEME.font_node_main().pixelSize() == THEME.font_node_px
    assert THEME.font_node_sub().pixelSize() == THEME.font_node_sub_px


def test_a_scaled_font_tracks_the_scale_it_is_given() -> None:
    """Scene text is drawn at a fractional scale, which whole pixels cannot express.

    Qt converts points at 96 logical DPI whatever the display scaling, so 0.75pt is
    one pixel and the px token the caller passes is the size at scale 1.
    """
    from bartlebot.gui.theme import THEME

    assert THEME.scaled_ui_font(12, 1.0).pointSizeF() == pytest.approx(9.0)
    assert THEME.scaled_ui_font(12, 2.0).pointSizeF() == pytest.approx(18.0)
    # Never zero or negative, however far the canvas is zoomed out.
    assert THEME.scaled_ui_font(12, 0.0001).pointSizeF() >= 1.0
