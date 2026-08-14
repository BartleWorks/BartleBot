"""Tests for clipboard XYZ parsing in the parameter panel."""

from bartlebot.gui.canvas.param_panel import parse_xyz_text


def test_parses_json_object():
    assert parse_xyz_text('{"x": 1.5, "y": -2, "z": 3}') == {"x": 1.5, "y": -2.0, "z": 3.0}


def test_json_partial_object_updates_only_named_axes():
    assert parse_xyz_text('{"y": 7}') == {"y": 7.0}


def test_json_non_numeric_axis_is_dropped():
    assert parse_xyz_text('{"x": "abc", "z": 4}') == {"z": 4.0}


def test_parses_xyz_call():
    assert parse_xyz_text("XYZ(1, 2, 3)") == {"x": 1.0, "y": 2.0, "z": 3.0}


def test_parses_deimos_line_verbatim():
    # A whole line lifted out of a Deimos script, command prefix and all.
    text = "mass tp XYZ(-531.5061645507812, -3408.298095703125, 4.999969482421875)"
    assert parse_xyz_text(text) == {
        "x": -531.5061645507812,
        "y": -3408.298095703125,
        "z": 4.999969482421875,
    }


def test_xyz_call_is_case_insensitive_and_tolerates_whitespace():
    assert parse_xyz_text("  xyz ( 1 ,2 ,  3.25 )  ") == {"x": 1.0, "y": 2.0, "z": 3.25}


def test_xyz_call_accepts_exponent_and_bare_decimal_forms():
    assert parse_xyz_text("XYZ(+.5, 1e3, -2.5E-2)") == {"x": 0.5, "y": 1000.0, "z": -0.025}


def test_multiline_paste_takes_the_first_call():
    text = "mass tp XYZ(1, 2, 3)\nmass walkto XYZ(4, 5, 6)"
    assert parse_xyz_text(text) == {"x": 1.0, "y": 2.0, "z": 3.0}


def test_xyz_call_with_too_few_numbers_is_rejected():
    assert parse_xyz_text("XYZ(1, 2)") == {}


def test_garbage_returns_empty():
    assert parse_xyz_text("not coordinates at all") == {}


def test_empty_clipboard_returns_empty():
    assert parse_xyz_text("") == {}


def test_json_non_object_returns_empty():
    assert parse_xyz_text("[1, 2, 3]") == {}
