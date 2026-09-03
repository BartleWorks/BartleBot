"""The app stylesheet's frame for text inputs and dropdowns.

Fusion derives an input's frame colour itself, as ``palette.window().darker(140)``,
and no ``QPalette`` role feeds it.  So until the rule in ``global_stylesheet`` existed,
``THEME.border`` was a token no field in the app drew: in the light theme Fusion's
derivation lands within a shade of it by luck, and in the dark theme it lands on
near-black beside a ``PickerButton`` drawing the real one.

These are pixel assertions rather than substring assertions on the QSS, because the
failure being guarded against is a rule that is present and has no effect — QSS on a
complex control is a takeover, and which parts of the control it claims is not
something the text of the rule says.
"""

import sys

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import bartlebot.gui.theme as theme_mod


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(params=["light", "dark"])
def themed_app(request, qapp):
    """A Fusion app wearing one theme, restored on the way out.

    Style, palette and stylesheet are all process-global and outlive the test in the
    xdist worker that ran it, so each is put back — see ``_restore_app_stylesheet``.
    """
    style_before = qapp.style().objectName()
    palette_before = qapp.palette()
    theme_before = theme_mod.active_theme()

    qapp.setStyle("Fusion")
    theme_mod.set_theme(request.param)
    theme = theme_mod.active_theme()
    qapp.setPalette(theme.build_palette())
    qapp.setStyleSheet(theme.global_stylesheet())
    yield theme

    theme_mod.set_theme_instance(theme_before)
    qapp.setPalette(palette_before)
    qapp.setStyle(style_before)


def _render(widget: QWidget) -> QImage:
    """Grab *widget* laid out in a shown window, as RGB32 so pixel names compare."""
    host = QWidget()
    host.resize(320, 120)
    layout = QVBoxLayout(host)
    # Focus draws a different frame on purpose, and an offscreen window hands focus to
    # whichever child will take it, so nothing under test is allowed to.
    widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    layout.addWidget(widget)
    host.show()
    QApplication.processEvents()
    return widget.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)


def _left_edge(image: QImage) -> str:
    return image.pixelColor(0, image.height() // 2).name()


def _fill(image: QImage) -> str:
    return image.pixelColor(image.width() // 2, image.height() // 2).name()


def _right_edge_px(image: QImage) -> int:
    """Device pixels of non-fill at the right edge of the middle row.

    Counted against the fill rather than matched to a colour name: the tests run on
    the real platform plugin, so at a display scale of 125% a one-pixel border is
    1.25 device pixels and its outer column is a blend rather than the token.  Device
    pixels, not logical ones, because the caller compares two controls rendered at the
    same scale — which is the claim being made — and converting would only add a
    rounding step to both sides of it.
    """
    y = image.height() // 2
    fill = _fill(image)
    n = 0
    while n < 12 and image.pixelColor(image.width() - 1 - n, y).name() != fill:
        n += 1
    return n


def _arrow_pixels(widget: QWidget, colour: str) -> int:
    """Pixels of exactly *colour* in the right-hand end of *widget*.

    The arrow is a solid triangle in a colour nothing else in the control uses, so it
    is counted by matching that colour rather than by subtracting a background. Most
    of the triangle is a blend — it is a scaled image, and the tests run on the real
    platform plugin, so a display scale of 125% antialiases it — but its interior
    survives, and the count is a clean handful against a flat zero.
    """
    image = _render(widget)
    return sum(
        1
        for y in range(image.height())
        for x in range(image.width() - 30, image.width())
        if image.pixelColor(x, y).name() == colour
    )


def _combo(*, enabled: bool = True) -> QComboBox:
    combo = QComboBox()
    combo.addItem("go_to_world")
    combo.setEnabled(enabled)
    return combo


def test_a_text_field_and_a_dropdown_draw_the_same_border(themed_app):
    line = _left_edge(_render(QLineEdit()))
    combo = _render(QComboBox())
    assert line == themed_app.border
    assert _left_edge(combo) == themed_app.border


def test_a_focused_field_is_still_marked_out(themed_app):
    """The frame is ours now, so Fusion's focus outline is ours to redraw."""
    host = QWidget()
    layout = QVBoxLayout(host)
    field = QLineEdit()
    layout.addWidget(field)
    host.show()
    field.setFocus()
    QApplication.processEvents()

    image = field.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
    assert _left_edge(image) == themed_app.accent


def test_a_disabled_field_still_reads_as_recessed(themed_app):
    """Claiming the frame reaches the fill under :disabled, so the rule restates it.

    Left unsaid, a disabled field keeps the enabled ``Base`` colour and the Disabled
    palette group's recessed look is lost for every input in the app.
    """
    field = QLineEdit("Sample")
    field.setEnabled(False)
    image = _render(field)
    assert _fill(image) == themed_app.surface_disabled
    assert _left_edge(image) == themed_app.surface_disabled


def test_a_spin_box_keeps_its_step_buttons(themed_app):
    """``QAbstractSpinBox`` is excluded from the rule, and this is why.

    A ``border`` on it makes QStyleSheetStyle draw the whole control, and the up/down
    buttons stop being painted at all.  Nothing about the QSS text says so, which is
    what makes this a pixel test: the guard is that the right-hand chrome is still
    there and still distinct from the field's own fill.
    """
    image = _render(QSpinBox())
    y = image.height() // 2
    right = {image.pixelColor(image.width() - 1 - i, y).name() for i in range(2, 10)}
    assert right - {_fill(image)}, "spin buttons are gone — is QAbstractSpinBox in the rule?"


# ---------------------------------------------------------------------------
# The combo box drop-down
# ---------------------------------------------------------------------------


def test_the_dropdown_edge_is_one_pixel_like_every_other(themed_app):
    """The artifact ``_combo_arrow_qss`` exists to remove.

    Claiming a combo box's frame makes QStyleSheetStyle draw the drop-down subcontrol
    with a rounded box of its own, which stacks onto the frame as a three-pixel right
    edge — plainly visible against the field fill, and unlike every other control.
    """
    combo = _right_edge_px(_render(QComboBox()))
    field = _right_edge_px(_render(QLineEdit()))
    assert combo == field


def test_a_dropdown_still_has_an_arrow(themed_app):
    """Any ``::drop-down`` rule stops the style painting one, so we paint our own.

    Worth its own test because the rule that removes the edge and the rule that puts
    the arrow back are separate, and dropping the second leaves a combo box that is
    indistinguishable from a text field.
    """
    assert _arrow_pixels(_combo(), themed_app.icon_tint) >= 3
    assert _arrow_pixels(QLineEdit(), themed_app.icon_tint) == 0


def test_a_disabled_dropdown_keeps_its_arrow_dimmed(themed_app):
    """Dimmed, not removed — an arrowless box reads as a different control.

    ``image: none`` is what QSS offers for the disabled state and it is the wrong
    answer, so both halves are asserted: an arrow is there, and it is not the enabled
    one merely inherited.
    """
    assert _arrow_pixels(_combo(enabled=False), themed_app.text_disabled) >= 3
    assert _arrow_pixels(_combo(enabled=False), themed_app.icon_tint) == 0


def test_an_unwritable_icon_cache_drops_the_rules_rather_than_raising(themed_app, monkeypatch):
    """The stylesheet is built at startup and on every theme switch.

    A cache directory that cannot be written is a cosmetic problem — the style's own
    arrow and thick edge come back — and must not be a fatal one.
    """
    import bartlebot.gui.icon_utils as icon_utils

    monkeypatch.setattr(icon_utils, "svg_qss_url", lambda *a, **kw: None)
    qss = themed_app.global_stylesheet()

    assert "QComboBox::down-arrow" not in qss
    assert "QLineEdit, QComboBox" in qss
