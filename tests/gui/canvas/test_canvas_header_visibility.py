"""Bar B appears with the bot and goes away with it.

With no bot loaded the header has nothing to name and nothing its buttons can act
on — Run, Stop and Edit are all disabled — so it reads as a second strip of chrome
above the bot library, at the one moment the library should be the whole surface.
It is hidden until a bot is open, and the library takes the space back.
"""

import pytest

from bartlebot.bot.schema import BotConfig, StateModel


@pytest.fixture
def canvas(qapp):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas

    c = BotCanvas()
    c.resize(900, 600)
    yield c
    c.deleteLater()


def _config():
    return BotConfig(
        id="test-bot",
        name="t",
        initial="a",
        recovery_state="a",
        states=[StateModel(id="a", node_type="change_realm", params={})],
        transitions=[],
    )


def test_an_empty_canvas_shows_no_header(canvas):
    assert not canvas.header_visible


def test_loading_a_bot_brings_the_header_back(canvas):
    canvas.setup(_config())

    assert canvas.header_visible


def test_closing_the_bot_hides_it_again(canvas):
    canvas.setup(_config())
    canvas.close_bot()

    assert not canvas.header_visible


def test_the_library_fills_the_panel_when_the_header_is_gone(canvas):
    """The placeholder is positioned by hand, so a hidden header must free its rows.

    A hidden widget keeps its last geometry: reading the header's height
    unconditionally would leave the library indented by a bar with nothing above it.
    """
    canvas.setup(_config())
    canvas.close_bot()

    assert canvas._placeholder.geometry().top() == 0
    assert canvas._placeholder.height() == canvas.height()
