"""The properties panel's themed widgets, and the shapes they use.

``_DynamicParamForm`` holds references, because ``load_schema`` clears and refills
them in step with the widgets. ``_FlagConditionsWidget`` cannot: groups and rows come
and go as the user edits, so it names its parts with objectNames and sets one sheet on
itself, which styles a row built *after* a theme switch with no bookkeeping.

The field label is the interesting one. Its required marker is a ``<span>`` carrying
``THEME.error`` inside the label's own *text*, so restyling it means rewriting the
text — and nothing in the guard test can see that, because the guard looks for
setStyleSheet and this is setText.

``_ParamPanel`` itself is the fourth, and shows the other blind spot: it had a
hand-written ``changeEvent`` that restyled three of its ten themed widgets, which
satisfies the guard — the guard asks whether a class handles ``StyleChange``, not
whether it handles all of it. The help button beside the node type dropdown was one
of the seven it missed.
"""

import sys

import pytest
from PyQt6.QtCore import QEvent
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QLabel

import bartlebot.gui.theme as theme_mod
from bartlebot.bot.schema import StateModel, TransitionModel
from bartlebot.gui.canvas.param_panel import (
    _AppSettingStrWidget,
    _DynamicParamForm,
    _FlagConditionsWidget,
    _ParamPanel,
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


# ---------------------------------------------------------------------------
# _ParamPanel
# ---------------------------------------------------------------------------


def _panel(theme, mode: str = "light") -> "_ParamPanel":
    theme(mode)
    return _ParamPanel()


def test_the_node_help_button_recolours(qapp, theme):
    """The '?' beside the node type dropdown: three THEME colours in one sheet set
    once in __init__, which is what kept it in the old palette across a switch."""
    panel = _panel(theme)
    before = panel._node_help_btn.styleSheet()
    light_accent = THEME.accent

    _switch(qapp, panel, theme, "dark")

    assert panel._node_help_btn.styleSheet() != before
    assert THEME.accent in panel._node_help_btn.styleSheet()
    assert light_accent not in panel._node_help_btn.styleSheet()


def test_the_node_type_category_headers_recolour(qapp, theme):
    """These carry their colour as a model item foreground, not a stylesheet, so
    they need re-setting by hand rather than falling out of a sheet rewrite."""
    panel = _panel(theme)
    assert panel._node_type_headers, "the dropdown should have category headers"
    light_muted = QColor(THEME.text_muted)
    assert panel._node_type_headers[0].foreground().color() == light_muted

    _switch(qapp, panel, theme, "dark")

    assert QColor(THEME.text_muted) != light_muted
    assert panel._node_type_headers[0].foreground().color() == QColor(THEME.text_muted)


def test_the_idle_page_and_section_titles_recolour(qapp, theme):
    panel = _panel(theme)
    before = [
        panel._idle_lbl.styleSheet(),
        panel._returns_title.styleSheet(),
        panel._record_status_lbl.styleSheet(),
        panel._flags_lbl.styleSheet(),
    ]
    assert all(before), "every one of these should be styled at construction"

    _switch(qapp, panel, theme, "dark")

    after = [
        panel._idle_lbl.styleSheet(),
        panel._returns_title.styleSheet(),
        panel._record_status_lbl.styleSheet(),
        panel._flags_lbl.styleSheet(),
    ]
    assert all(a != b for a, b in zip(after, before))


def test_the_connection_rows_are_rebuilt_after_a_switch(qapp, theme):
    """Rows are built per selection and hold a chip colour and an accent link, so
    the restyle replays the build — on the next event-loop turn, because it deletes
    the rows it replaces."""
    panel = _panel(theme)
    state = StateModel(id="a", node_type="wait")
    other = StateModel(id="b", node_type="wait")
    edge = TransitionModel(trigger="t", source="a", dest="b", when="SUCCESS")
    panel.show_state(state, [state, other], [edge])
    light_accent = THEME.accent
    assert any(light_accent in w.styleSheet() for w in _conn_labels(panel))

    _switch(qapp, panel, theme, "dark")
    qapp.processEvents()  # the rebuild is deferred by a single-shot timer
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)  # retire the replaced rows

    sheets = [w.styleSheet() for w in _conn_labels(panel)]
    assert any(THEME.accent in s for s in sheets)
    assert not any(light_accent in s for s in sheets)


def test_an_idle_panel_survives_a_switch(qapp, theme):
    """With no node shown there is nothing to rebuild, and the deferred rebuild must
    not fire into an empty panel."""
    panel = _panel(theme)

    _switch(qapp, panel, theme, "dark")
    qapp.processEvents()  # must not raise


def _conn_labels(panel) -> list:
    """Every label inside the connections list, at any depth."""
    return panel._conn_body.findChildren(QLabel)
