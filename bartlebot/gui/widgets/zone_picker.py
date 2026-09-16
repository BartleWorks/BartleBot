"""The zone picker: one button that opens every destination on the navigation map, grouped by world.

Built on PickerPopup in single-select mode. A zone's world is the section it sits in rather than a
second control beside it, so typing a world's name narrows the list to that world (the popup's
broad filter), and the zone id rides along as each row's meta, so a zone can also be found by the
name the tester and the session log use.

Each world's section header carries that world's emblem, extracted from the game by
``tools/extract_card_art.py`` and found through ``card_art.world_icon_path`` (#29). Every one of
the 25 sections resolves: 22 worlds have a ``Button_World_`` texture of their own, and the three
that are dungeon groups rather than worlds -- Castle Darkmoor, Kembaalung, Zigazag -- share
the generic dungeon badge. **The icon is decoration over a caption that already carries the
meaning**, so every failure to resolve one is silent and ordinary: most installs have extracted no
art at all, and the header simply draws its text. The suggestions section never gets one, because
"Where players are" spans worlds and an emblem there would claim a single one.

zone_sections(), describe_zone() and world_keys_by_title() are the data half and need no
QApplication; world_icon_resolver() needs one only once art exists to turn into a pixmap.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QSizePolicy, QWidget

from bartlebot import card_art, worlds, zone_names, zone_nav
from bartlebot.gui.widgets.card_art_tile import pixmap_for_path
from bartlebot.gui.widgets.picker_popup import SECTION_ICON_PX, PickerButton, PickerRow, Section

SUGGESTED_TITLE = "Where players are"
POPUP_WIDTH = 480
"""Wider than the tag pickers: a row carries a zone name and a zone id of up to 29 characters."""
MINIMUM_WIDTH = 80
"""The narrowest the button may be squeezed to; its caption is clipped below its own width."""


def zone_id(zone: str) -> str:
    """The last segment of a zone path, "KT_EntranceHall": what a row shows as its meta."""
    return zone.rsplit("/", 1)[-1]


def world_keys_by_title(nav: zone_nav.NavMap) -> dict[str, str]:
    """Section title -> the nav world key it was built from.

    The sections are captioned with ``worlds.label``, which is localized and is not the
    key; this is how a header gets back to the key its icon is stored under. Built from
    the same ``nav.worlds`` the sections are, so the two cannot disagree about which
    worlds exist -- and ``SUGGESTED_TITLE`` is deliberately absent, since "Where players
    are" spans worlds and an emblem there would claim one.
    """
    return {worlds.label(world.key): world.key for world in nav.worlds.values()}


def world_icon_resolver(nav: zone_nav.NavMap, size: int = SECTION_ICON_PX) -> Callable[[str], QPixmap | None]:
    """A ``title -> pixmap`` lookup for the picker's section headers.

    Returns None for every "no icon" case, and they are all ordinary: a title that is not
    a world (the suggestions section), a world whose emblem this machine has not extracted,
    and an install that has never run the extractor at all -- which is most of them. The
    header draws its caption alone in every one of those.

    The title map is built once per resolver rather than per call; the pixmap itself comes
    from ``pixmap_for_path``, whose cache is keyed on the file's mtime, so re-running the
    extractor while the app is open replaces what is cached.
    """
    titles = world_keys_by_title(nav)

    def resolve(title: str) -> QPixmap | None:
        key = titles.get(title)
        if key is None:
            return None
        path = card_art.world_icon_path(key)
        return None if path is None else pixmap_for_path(path, size)

    return resolve


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


def is_destination(nav: zone_nav.NavMap, zone: str) -> bool:
    """Whether `zone` is in the picker's list: on the map, and not a zone routes only pass through.

    Go To Zone fails on anything else, so a zone that arrives some other way -- filled from where
    a player stands -- is checked against this.
    """
    world = nav.world_of(zone)
    return world is not None and zone in world.destinations()


def describe_zone(nav: zone_nav.NavMap, zone: str) -> str:
    """ "Krokotopia · Entrance Hall (KT_EntranceHall)" for a zone on the map; the raw path, marked, for one that is not.

    The world leads because it is the coarser choice, the way the picker's sections read; the zone
    id trails so the name the session log uses is visible without opening the picker.
    """
    world = nav.world_of(zone)
    if world is None or zone not in world.zones:
        return f"{zone} (not on the map)"
    return f"{worlds.label(world.key)} · {zone_names.label(zone)} ({zone_id(zone)})"


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
        # Resolved before the base class, because the resolver handed to every popup this
        # button opens is built from it.
        resolved_nav = zone_nav.load() if nav is None else nav
        super().__init__(
            "Zone",
            placeholder="Choose a zone…",
            search_placeholder="Zone, world or zone id…",
            single_select=True,
            popup_width=POPUP_WIDTH,
            broad=True,
            section_icon=world_icon_resolver(resolved_nav),
            parent=parent,
        )
        self.nav = resolved_nav
        self._get_suggested = get_suggested
        # Spans its column; the caption is clipped rather than widening the panel to fit it.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
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

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        # QToolButton's minimum is its full text width, which would hold the panel open for a long zone id.
        return QSize(MINIMUM_WIDTH, super().minimumSizeHint().height())

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
