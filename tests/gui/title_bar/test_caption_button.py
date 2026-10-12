"""The painted minimize, maximize and close buttons."""

from __future__ import annotations

import pytest

from bartlebot.gui.title_bar.caption_button import CAPTION_BUTTON_WIDTH, CaptionButton, CaptionKind


@pytest.mark.parametrize("kind", list(CaptionKind))
@pytest.mark.parametrize("maximized", [False, True])
@pytest.mark.parametrize("down", [False, True])
def test_every_kind_paints_in_every_state(qapp, kind, maximized, down):
    button = CaptionButton(kind, 35)
    button.set_maximized(maximized)
    button.setDown(down)

    assert not button.grab().isNull()


def test_a_caption_button_is_windows_width_and_the_bar_s_height(qapp):
    button = CaptionButton(CaptionKind.CLOSE, 35)

    assert (button.width(), button.height()) == (CAPTION_BUTTON_WIDTH, 35)


def test_only_the_maximize_button_becomes_restore(qapp):
    minimize = CaptionButton(CaptionKind.MINIMIZE, 35)
    maximize = CaptionButton(CaptionKind.MAXIMIZE, 35)

    minimize.set_maximized(True)
    maximize.set_maximized(True)

    assert minimize.toolTip() == "Minimize"
    assert maximize.toolTip() == maximize.accessibleName() == "Restore Down"


def test_every_button_names_itself_for_a_screen_reader(qapp):
    for kind in CaptionKind:
        assert CaptionButton(kind, 35).accessibleName()
