"""The region page on its own, out of the panel. Collapse through it is ``test_region_collapse.py``."""

from __future__ import annotations

from bartlebot.bot.schema import RegionModel
from bartlebot.gui.canvas.region_page import RegionPage


def _region() -> RegionModel:
    return RegionModel(id="r", label="Hub", color=0, members=["a"])


def test_building_and_showing_the_page_never_shows_a_parentless_widget(qapp, parentless_shows):
    page = RegionPage()
    page.show_region(_region(), collapsed=True, fights=["Boss"])

    assert parentless_shows == []


def test_clear_lets_go_of_the_region_so_an_edit_no_longer_reaches_it(qapp):
    page = RegionPage()
    region = _region()
    page.show_region(region)
    page.clear()
    page.region_label_edit.setText("Lobby")

    assert page.region is None
    assert region.label == "Hub"
