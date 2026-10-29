"""Where BartleBot reserves a scroll area's gutter: a pane whose content grows under the user.

``reserve_scrollbar_gutter`` is core's, the Qt stand-in for ``scrollbar-gutter: stable``, and
its own tests are core's since Stage 6 of #167. The Combat Strategies dialog's two are
checked in test_combat_strategy_dialog.py.
"""

from bartleby.qt.widgets.layout.scroll_gutter import is_gutter_reserved
from PyQt6.QtWidgets import QScrollArea, QWidget

from bartlebot.gui.canvas.param_panel import _ParamPanel
from bartlebot.gui.sidebar.combat_tab import CombatTab
from bartlebot.gui.widgets.pickers.picker_popup import PickerPopup


def _scroll_areas(widget: QWidget) -> list[QScrollArea]:
    return widget.findChildren(QScrollArea)


def test_the_picker_popup_reserves_its_gutter(qapp):
    """Every keystroke in its search changes how many rows there are."""
    popup = PickerPopup("T", [])

    assert [is_gutter_reserved(a) for a in _scroll_areas(popup)] == [True]


def test_every_inspector_page_reserves_its_gutter(qapp):
    """All of them, so the inspector keeps one content width whichever page is up."""
    areas = _scroll_areas(_ParamPanel())

    assert len(areas) == 4
    assert all(is_gutter_reserved(a) for a in areas)


def test_the_combat_tab_reserves_its_gutter(qapp):
    """Its sections fold, and decks come and go."""
    areas = _scroll_areas(CombatTab())

    assert areas and all(is_gutter_reserved(a) for a in areas)
