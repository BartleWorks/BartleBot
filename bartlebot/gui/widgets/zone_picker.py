"""The zone picker: one button that opens every destination on the navigation map, grouped by world.

Built on PickerPopup in single-select mode. A zone's world is the section it sits in rather than a
second control beside it, so typing a world's name narrows the list to that world (the popup's
broad filter), and the zone id rides along as each row's meta, so a zone can also be found by the
name the tester and the session log use. The world's section header is also where a world icon
goes once game art can be extracted (#29).

zone_sections() and describe_zone() are the data half and need no QApplication.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget

from bartlebot import worlds, zone_names, zone_nav
from bartlebot.gui.widgets.picker_popup import PickerButton, PickerRow, Section

SUGGESTED_TITLE = "Where players are"
POPUP_WIDTH = 480
"""Wider than the tag pickers: a row carries a zone name and a zone id of up to 29 characters."""


def zone_id(zone: str) -> str:
    """The last segment of a zone path, "KT_EntranceHall": what a row shows as its meta."""
    return zone.rsplit("/", 1)[-1]


def zone_sections(nav: zone_nav.NavMap, suggested: Iterable[str] = ()) -> list[Section]:
    """Every destination on the map as picker sections: one per world, both sorted by name.

    `suggested` zones -- where the players are -- come first in a section of their own, when
    they are destinations; each of those rows names its world, since its section does not.
    """
    by_world: list[Section] = []
    for world in nav.worlds.values():
        rows = [
            PickerRow(key=zone, label=zone_names.label(zone), meta=zone_id(zone), description=zone)
            for zone in world.destinations()
        ]
        if rows:
            by_world.append((worlds.label(world.key), sorted(rows, key=lambda r: r.label.casefold())))
    by_world.sort(key=lambda section: section[0].casefold())

    here: list[PickerRow] = []
    for zone in dict.fromkeys(suggested):
        world = nav.world_of(zone)
        if world is not None and zone in world.destinations():
            here.append(
                PickerRow(key=zone, label=zone_names.label(zone), meta=worlds.label(world.key), description=zone)
            )
    return [(SUGGESTED_TITLE, here), *by_world] if here else by_world


def describe_zone(nav: zone_nav.NavMap, zone: str) -> str:
    """ "Entrance Hall · Krokotopia" for a zone on the map; the raw path, marked, for one that is not."""
    world = nav.world_of(zone)
    if world is None or zone not in world.zones:
        return f"{zone} (not on the map)"
    return f"{zone_names.label(zone)} · {worlds.label(world.key)}"


class NavZoneButton(PickerButton):
    """The control for a 'nav_zone' param: shows the chosen zone and its world, and opens the picker.

    The value is the zone path, the only thing the bot file stores; the world is never stored,
    because the map already says which world every zone belongs to.
    """

    changed = pyqtSignal(str)

    def __init__(
        self,
        get_suggested: Callable[[], list[str]] = list,
        nav: zone_nav.NavMap | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            "Zone",
            placeholder="Choose a zone…",
            search_placeholder="Zone, world or zone id…",
            single_select=True,
            popup_width=POPUP_WIDTH,
            broad=True,
            parent=parent,
        )
        self.nav = zone_nav.load() if nav is None else nav
        self._get_suggested = get_suggested
        self.set_sections(zone_sections(self.nav))
        # Refreshed as the button goes down, before it opens the popup: where the players are
        # is worth suggesting only as of now.
        self.pressed.connect(self._refresh_suggestions)
        self.selection_changed.connect(lambda keys: self.changed.emit(keys[0] if keys else ""))

    def value(self) -> str:
        return next(iter(self.selection()), "")

    def set_value(self, zone: str) -> None:
        """Set the zone without emitting ``changed``."""
        self.set_selection([zone] if zone else [])

    def format_text(self, labels: list[str]) -> str:
        zone = self.value()
        nav = getattr(self, "nav", None)  # the base class draws its text before __init__ gets here
        if not zone or nav is None:
            return "Choose a zone…"
        return describe_zone(nav, zone)

    def _refresh_suggestions(self) -> None:
        try:
            suggested = self._get_suggested()
        except Exception:
            suggested = []
        self.set_sections(zone_sections(self.nav, suggested))
