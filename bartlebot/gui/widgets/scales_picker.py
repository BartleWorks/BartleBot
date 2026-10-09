"""The Scales of Knowledge picker: one button that opens every Scales in the shipped table, grouped by world.

The sibling of the zone picker (zone_picker.py), on the same PickerPopup in single-select mode.
A row's world is the section it sits in and its level rides along as the row's meta, so typing
a world's name or "level 90" narrows the list as well as a dungeon's name does.

scales_sections() is the data half and needs no QApplication. The section icons follow the zone
picker's rule: decoration over a caption that already carries the meaning, so a world with no
extracted emblem draws its text alone.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QSizePolicy, QWidget

from bartlebot.game import card_art, scales, worlds
from bartlebot.gui.widgets.card_art_tile import art_icon
from bartlebot.gui.widgets.picker_popup import PickerButton, PickerRow, Section

PLACEHOLDER = "Choose a Scales of Knowledge…"
POPUP_WIDTH = 420
MINIMUM_WIDTH = 80
"""The narrowest the button may be squeezed to; its caption is clipped below its own width."""


def level_text(level: int | None) -> str:
    """ "level 90", or nothing for a Scales whose level the table does not record."""
    return "" if level is None else f"level {level}"


def scales_sections(table: Mapping[str, scales.Scales]) -> list[Section]:
    """Every Scales as picker sections: one per world, sorted by name; rows by level, then name."""
    by_world: dict[str, list[scales.Scales]] = {}
    for entry in table.values():
        by_world.setdefault(entry.world, []).append(entry)
    sections: list[Section] = []
    for world, entries in by_world.items():
        entries.sort(key=lambda e: (e.level is None, e.level or 0, e.name.casefold()))
        rows = [PickerRow(key=e.key, label=e.name, meta=level_text(e.level), description=e.zone) for e in entries]
        sections.append((worlds.label(world), rows))
    sections.sort(key=lambda section: section[0].casefold())
    return sections


def world_icon_resolver(table: Mapping[str, scales.Scales]) -> Callable[[str], QIcon | None]:
    """A ``title -> icon`` lookup for the section headers. None for every world with no extracted emblem."""
    titles = {worlds.label(entry.world): entry.world for entry in table.values()}

    def resolve(title: str) -> QIcon | None:
        world = titles.get(title)
        if world is None:
            return None
        path = card_art.world_icon_path(world)
        return None if path is None else art_icon(path)

    return resolve


def default_text(default: scales.Scales | None) -> str:
    """The caption of an unset button: what the bot's world gives it, or a prompt when it gives nothing."""
    return PLACEHOLDER if default is None else f"Bot's world: {scales.describe(default)}"


def describe_key(table: Mapping[str, scales.Scales], key: str) -> str:
    """The button's caption for a stored key; the raw key, marked, for one the table does not have."""
    entry = table.get(key)
    return f"{key} (not a known Scales)" if entry is None else scales.describe(entry)


class ScalesButton(PickerButton):
    """The control for a 'scales' param. The value is the entry's key, the only thing a bot file stores."""

    changed = pyqtSignal(str)

    def __init__(
        self,
        table: Mapping[str, scales.Scales] | None = None,
        get_default: Callable[[], scales.Scales | None] = lambda: None,
        parent: QWidget | None = None,
    ) -> None:
        # Resolved before the base class, because the resolver handed to every popup is built from it.
        resolved = scales.load() if table is None else table
        # What an unset value means in this bot, read each time the caption is drawn.
        self._get_default = get_default
        super().__init__(
            "Scales of Knowledge",
            placeholder=PLACEHOLDER,
            search_placeholder="Dungeon, world or level…",
            single_select=True,
            popup_width=POPUP_WIDTH,
            broad=True,
            section_icon=world_icon_resolver(resolved),
            parent=parent,
        )
        self.table = resolved
        # Spans its column; the caption is clipped rather than widening the panel to fit it.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.set_sections(scales_sections(self.table))
        self.selection_changed.connect(lambda keys: self.changed.emit(keys[0] if keys else ""))

    def value(self) -> str:
        return next(iter(self.selection()), "")

    def set_value(self, key: str) -> None:
        """Set the Scales without emitting ``changed``."""
        self.set_selection([key] if key else [])

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        # QToolButton's minimum is its full text width, which would hold the panel open for a long caption.
        return QSize(MINIMUM_WIDTH, super().minimumSizeHint().height())

    def format_text(self, labels: list[str]) -> str:
        key = self.value()
        table = getattr(self, "table", None)  # the base class draws its text before __init__ gets here
        if table is None:
            return PLACEHOLDER
        if key:
            return describe_key(table, key)
        return default_text(self._default())

    def _default(self) -> scales.Scales | None:
        try:
            return self._get_default()
        except Exception:
            return None

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        # The bot's world can change while the field is hidden, and the default follows it.
        super().showEvent(event)
        self._refresh_text()
