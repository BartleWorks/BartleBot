"""The surfaces that show options, once the options are no longer in the file.

Deriving declarations moves the option list from the bot file to the NodeSpecs,
and two widgets read that list: the Config tab, which shows every option a bot
has, and the canvas, which reports a transition guarding on one that nothing
declares. Neither can be checked by reading a bot file any more.

Real widget tests (QApplication required), in the style of
test_canvas_property_edits. Only the wiring is exercised -- nothing reads pixels.
"""

import pytest

from bartlebot.bot.schema import BotConfig, FlagCondition, StateModel, TransitionModel


def _config(states=("enter_dungeon",), options=None, transitions=None):
    return BotConfig(
        id="test-bot",
        name="t",
        initial="s0",
        recovery_state="s0",
        options=options or [],
        # enter_dungeon's Challenge Mode is required only by a state that says its
        # dungeon offers one, which is what these tests use it as an exemplar of.
        states=[
            StateModel(
                id=f"s{i}",
                node_type=nt,
                params={"offers_challenge_mode": True} if nt == "enter_dungeon" else {},
            )
            for i, nt in enumerate(states)
        ],
        transitions=transitions or [],
    )


# Widgets are drained after each test: a QGraphicsScene destroyed with items
# still selected corrupts the heap on the way out, and an OptionsSection built
# with no parent is a *top-level* widget that would otherwise outlive its test
# inside a QApplication the whole worker shares.
_BUILT: list = []
_WIDGETS: list = []


@pytest.fixture(autouse=True)
def _shut_down_widgets(qapp):
    yield
    while _BUILT:
        _BUILT.pop().prepare_for_shutdown()
    while _WIDGETS:
        _WIDGETS.pop().deleteLater()
    qapp.processEvents()


def _section(config):
    from bartlebot.gui.sidebar.sections.options_section import OptionsSection

    section = OptionsSection()
    _WIDGETS.append(section)
    section.set_config(config)
    return section


def test_the_config_tab_shows_an_option_the_file_does_not_hold(qapp):
    """The bot file is empty of options; the node still requires three.

    Only two of the three are on *this* surface. ``enter_dungeon`` also requires Safety
    Delay, which is the user's value and so has no declaration to edit here."""
    config = _config()

    section = _section(config)

    assert set(section.option_widgets) == {"Challenge Mode", "Player Realms"}
    assert config.options == []


def test_a_value_only_the_user_owns_is_not_on_the_authors_surface(qapp):
    """Safety Delay describes the connection rather than the dungeon, so a
    declaration of it would outrank the app setting the user set it in -- the
    per-node pin the override-layer work removed, one level up. The row lives in Option Values instead."""
    section = _section(_config())

    assert "Safety Delay" not in section.option_widgets


def test_an_option_no_node_requires_is_not_shown(qapp):
    section = _section(_config(states=("interact",)))

    assert "Challenge Mode" not in section.option_widgets


def test_editing_a_derived_value_writes_a_declaration_into_the_file(qapp):
    """Editing here is editing the *declaration* -- the author's recommended
    value -- and a declaration has to be in the file to exist at all. The
    save-time prune takes it back out if it ends up agreeing with the NodeSpec."""
    config = _config()
    section = _section(config)

    section.option_widgets["Challenge Mode"].setChecked(True)

    assert [(o.name, o.default) for o in config.options] == [("Challenge Mode", True)]


def test_editing_a_derived_value_twice_writes_one_declaration(qapp):
    config = _config()
    section = _section(config)

    section.option_widgets["Challenge Mode"].setChecked(True)
    section.option_widgets["Challenge Mode"].setChecked(False)

    assert [(o.name, o.default) for o in config.options] == [("Challenge Mode", False)]


def test_a_hand_added_option_still_shows_and_edits_in_place(qapp):
    config = _config(options=[{"name": "Skip to boss", "type": "bool", "default": False}])
    section = _section(config)

    section.option_widgets["Skip to boss"].setChecked(True)

    assert config.options[0].default is True
    assert len(config.options) == 1


def test_the_canvas_warns_about_a_condition_on_an_undeclared_option(qapp):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas

    config = _config(
        transitions=[
            TransitionModel(
                trigger="advance",
                source="s0",
                dest="s0",
                flags=[FlagCondition(option="Skip to boss")],
            )
        ]
    )
    canvas = BotCanvas()
    _BUILT.append(canvas)
    canvas.setup(config)

    assert any("Skip to boss" in w for w in canvas.validation_warnings())


def test_the_canvas_is_quiet_about_a_condition_on_a_derived_option(qapp):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas

    config = _config(
        transitions=[
            TransitionModel(
                trigger="advance",
                source="s0",
                dest="s0",
                flags=[FlagCondition(option="Challenge Mode")],
            )
        ]
    )
    canvas = BotCanvas()
    _BUILT.append(canvas)
    canvas.setup(config)

    assert not any("Challenge Mode" in w for w in canvas.validation_warnings())


def test_loading_a_bot_writes_no_options_into_it(qapp):
    """The churn this epic exists to stop: opening a bot used to inject a
    declaration for every option any node in it required."""
    from bartlebot.gui.canvas.bot_canvas import BotCanvas

    config = _config()
    canvas = BotCanvas()
    _BUILT.append(canvas)
    canvas.setup(config)
    canvas.enter_edit_mode()

    assert config.options == []


# ---------------------------------------------------------------------------
# Bounds ride along with the declaration
# ---------------------------------------------------------------------------


def test_a_specs_bounds_reach_the_composed_option_and_never_the_file(qapp):
    """Derived, exactly like ``type`` and ``description``: the spin box has to know
    the range, and a bot file has no business carrying it."""
    from bartlebot.bot.schema import BotOptionModel
    from bartlebot.options.bot_options import compose_options, storable_options

    # An author who pinned their own default is the case where a declaration *is*
    # written -- so it is the case that proves the bounds are not.
    config = _config(options=[BotOptionModel(name="Safety Delay", type="float", default=5.0)])

    composed = next(o for o in compose_options(config) if o.name == "Safety Delay")
    assert (composed.minimum, composed.suffix) == (0.0, " s")

    stored = config.file_dump()["options"]
    assert [o["name"] for o in stored] == ["Safety Delay"]
    assert set(stored[0]) == {"name", "type", "default"}
    assert [o.name for o in storable_options(config)] == ["Safety Delay"]


def test_a_specs_minimum_reaches_the_spin_box(qapp):
    """One option-type -> widget mapping, so both surfaces and the Add/Edit dialog
    pick the range up for free."""
    from bartlebot.gui.widgets.option_widgets import make_option_widget
    from bartlebot.options.bot_options import compose_options

    opt = next(o for o in compose_options(_config()) if o.name == "Safety Delay")
    widget = make_option_widget(opt.type, opt.default, minimum=opt.minimum, maximum=opt.maximum, suffix=opt.suffix)
    _WIDGETS.append(widget)

    assert widget.minimum() == 0.0
    assert widget.suffix() == " s"
    # And the value that motivated the bound cannot be typed.
    widget.setValue(-1.0)
    assert widget.value() == 0.0


def test_an_unbounded_option_keeps_the_wide_range(qapp):
    from bartlebot.gui.widgets.option_widgets import make_option_widget

    widget = make_option_widget("float", 1.0)
    _WIDGETS.append(widget)

    assert widget.minimum() < 0
