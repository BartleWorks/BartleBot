"""Tests for the sub-pixel text guard behind the QPainter bug.

The numbers here are anchored to measurements against the real Windows font
engine at Qt 6.10: drawing text whose glyphs fall below ~0.5 device pixels
makes Qt's font engine build a 0x0 offscreen glyph image, and QPainter then
emits four warnings per distinct glyph, per item, per repaint.
"""

from bartlebot.gui.canvas.lod import MIN_TEXT_DEVICE_PX, text_is_legible

#: Line height of the compact-LOD node label (14pt Segoe UI at 96 dpi).
COMPACT_LABEL_HEIGHT = 25.0

#: Largest scale at which warnings were observed for that label.
OBSERVED_FAILING_LOD = 0.02

#: Smallest scale at which it rendered cleanly.
OBSERVED_CLEAN_LOD = 0.03


def test_text_at_normal_zoom_is_drawn():
    assert text_is_legible(COMPACT_LABEL_HEIGHT, lod=1.0)


def test_text_at_the_compact_lod_threshold_is_still_drawn():
    """The compact branch starts at 0.6; the guard must not interfere there."""
    assert text_is_legible(COMPACT_LABEL_HEIGHT, lod=0.6)


def test_the_measured_failure_case_is_blocked():
    assert not text_is_legible(COMPACT_LABEL_HEIGHT, OBSERVED_FAILING_LOD)


def test_the_guard_engages_before_qt_starts_failing():
    """A scale Qt still survives is already blocked — the margin is deliberate.

    Text this small carries no information, so erring toward blocking costs
    nothing visually and keeps the guard robust across fonts and DPI settings
    where the exact failure point moves.
    """
    assert not text_is_legible(COMPACT_LABEL_HEIGHT, OBSERVED_CLEAN_LOD)


def test_a_minimap_scale_label_is_blocked():
    """A 160x110 minimap fitting a wide scene lands around this scale."""
    assert not text_is_legible(COMPACT_LABEL_HEIGHT, lod=0.05)


def test_the_boundary_is_inclusive():
    assert text_is_legible(MIN_TEXT_DEVICE_PX, lod=1.0)
    assert not text_is_legible(MIN_TEXT_DEVICE_PX * 0.99, lod=1.0)


def test_a_larger_font_survives_a_smaller_lod():
    """The test is on rendered size, not on zoom alone."""
    small, large = 10.0, 40.0
    lod = MIN_TEXT_DEVICE_PX / 20.0
    assert not text_is_legible(small, lod)
    assert text_is_legible(large, lod)


def test_a_degenerate_lod_is_blocked_rather_than_raising():
    """levelOfDetailFromTransform can return 0 for a collapsed transform."""
    assert not text_is_legible(COMPACT_LABEL_HEIGHT, lod=0.0)
