"""The properties panel's three themed widgets, and the two shapes they use.

``_DynamicParamForm`` holds references, because ``load_schema`` clears and refills
them in step with the widgets. ``_FlagConditionsWidget`` cannot: groups and rows come
and go as the user edits, so it names its parts with objectNames and sets one sheet on
itself, which styles a row built *after* a theme switch with no bookkeeping.

The field label is the interesting one. Its required marker is a ``<span>`` carrying
``THEME.error`` inside the label's own *text*, so restyling it means rewriting the
text — and nothing in the guard test can see that, because the guard looks for
setStyleSheet and this is setText.
"""

import sys

import pytest
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication

import bartlebot.gui.theme as theme_mod
from bartlebot.gui.canvas.param_panel import (
    _AppSettingStrWidget,
    _DynamicParamForm,
    _FlagConditionsWidget,
)
from bartlebot.gui.theme import THEME, build_custom_theme
from bartlebot.nodes.param_schema import ParamField


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def theme():
    before = theme_mod.active_theme()
    yield theme_mod.set_theme
    theme_mod.set_theme_instance(before)


def _switch(qapp, widget, theme, mode: str) -> None:
    theme(mode)
    qapp.sendEvent(widget, QEvent(QEvent.Type.StyleChange))


SCHEMA = [
    ParamField(key="zone", type="str", label="Zone", required=True),
    ParamField(key="note", type="str", label="Note", default_hint="empty"),
]


# ---------------------------------------------------------------------------
# _DynamicParamForm
# ---------------------------------------------------------------------------


def test_the_required_marker_colour_is_rebuilt(qapp, theme):
    """The marker is THEME.error inside rich text, not a stylesheet — a restyle that
    only re-set stylesheets would leave the asterisk in the old palette."""
    theme("light")
    form = _DynamicParamForm()
    form.load_schema(SCHEMA, {})
    label, _field, _spaced = form._field_labels[0]
    assert THEME.error in label.text()
    light_error = THEME.error

    _switch(qapp, form, theme, "dark")

    assert THEME.error != light_error, "the two themes must differ for this to prove anything"
    assert THEME.error in label.text()
    assert light_error not in label.text()


def test_field_labels_recolour(qapp, theme):
    theme("light")
    form = _DynamicParamForm()
    form.load_schema(SCHEMA, {})
    before = [lbl.styleSheet() for lbl, _, _ in form._field_labels]

    _switch(qapp, form, theme, "dark")

    assert [lbl.styleSheet() for lbl, _, _ in form._field_labels] != before


def test_the_second_field_keeps_its_top_margin(qapp, theme):
    """The margin distinguishes the first field from the rest, and is part of the
    same string as the colour — so a careless restyle drops the spacing."""
    theme("light")
    form = _DynamicParamForm()
    form.load_schema(SCHEMA, {})

    _switch(qapp, form, theme, "dark")

    first, second = (lbl.styleSheet() for lbl, _, _ in form._field_labels)
    assert "margin-top" not in first
    assert "margin-top: 8px" in second


def test_the_default_hint_recolours(qapp, theme):
    theme("light")
    form = _DynamicParamForm()
    form.load_schema(SCHEMA, {})
    assert form._muted_labels, "the optional field should have produced a hint label"
    before = form._muted_labels[0].styleSheet()

    _switch(qapp, form, theme, "dark")

    assert form._muted_labels[0].styleSheet() != before


def test_the_empty_required_border_is_re_derived(qapp, theme):
    """The red border is applied outside load_schema, so it is only re-derived by
    re-running the validation the restyle calls."""
    theme("light")
    form = _DynamicParamForm()
    form.load_schema(SCHEMA, {})
    zone = form.widget("zone")
    assert THEME.error in zone.styleSheet(), "an empty required field should be marked"
    light_error = THEME.error

    _switch(qapp, form, theme, "dark")

    assert THEME.error in zone.styleSheet()
    assert light_error not in zone.styleSheet()


def test_a_placeholder_form_recolours(qapp, theme):
    theme("light")
    form = _DynamicParamForm()
    form.load_schema([], {})
    before = form._muted_labels[0].styleSheet()

    _switch(qapp, form, theme, "dark")

    assert form._muted_labels[0].styleSheet() != before


# ---------------------------------------------------------------------------
# _FlagConditionsWidget
# ---------------------------------------------------------------------------


def test_the_flag_widget_recolours(qapp, theme):
    theme("light")
    w = _FlagConditionsWidget()
    before = w.styleSheet()

    _switch(qapp, w, theme, "dark")

    assert w.styleSheet() != before
    assert THEME.error in w.styleSheet()
    assert THEME.border in w.styleSheet()


def test_a_group_added_after_a_switch_is_still_styled(qapp, theme):
    """The reason this widget uses objectNames rather than a list of references:
    rows and groups outlive no restyle, and a list would have to be kept in step."""
    theme("light")
    w = _FlagConditionsWidget()

    _switch(qapp, w, theme, "dark")
    w._add_group()

    frame = w._groups[-1][0]
    assert frame.objectName() == "FlagGroupFrame"
    assert THEME.border in w.styleSheet()


# ---------------------------------------------------------------------------
# _AppSettingStrWidget
# ---------------------------------------------------------------------------


def test_the_legacy_warning_recolours(qapp, theme):
    """Switched through a custom theme rather than light/dark, because THEME.warning
    happens to hold the same value in both — a gold that cannot lighten and still pass
    WCAG AA — so the usual pair would prove nothing here."""
    theme("light")
    w = _AppSettingStrWidget("recent_friend_names", lambda: [], legacy_value="Someone Elsewhere")
    assert w._warn is not None
    before = w._warn.styleSheet()

    theme_mod.set_theme_instance(build_custom_theme({"warning": "#ff00ff"}, base=theme_mod.active_theme()))
    qapp.sendEvent(w, QEvent(QEvent.Type.StyleChange))

    assert w._warn.styleSheet() != before
    assert "#ff00ff" in w._warn.styleSheet()


def test_no_legacy_value_means_no_warning_to_restyle(qapp, theme):
    theme("light")
    w = _AppSettingStrWidget("recent_friend_names", lambda: [])
    assert w._warn is None

    _switch(qapp, w, theme, "dark")  # must not raise
