"""The game files the tester reads: zonemap.nav, and each zone's volumes and arrival points.

Dev-only. It reads the wads directly, which the app never does (see tools/CLAUDE.md), and
it needs the game installed. Every read is cached and a failed one returns nothing, so a zone
without a wad costs a log line rather than the tester.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "zone_graph"))

import zonemap_nav  # noqa: E402
from wad_common import GAME_DATA, Deserializer, volumes, zone_wad  # noqa: E402
from zone_data import locations, zone_record  # noqa: E402

logger = logging.getLogger(__name__)

Point = tuple[str, tuple[float, float, float]]


class GameData:
    def __init__(self, game_data: Path = GAME_DATA) -> None:
        self._game_data = game_data
        self._graph: zonemap_nav.ZoneGraph | None = None
        self._deserialize = Deserializer()
        self._volumes: dict[str, list[Point]] = {}
        self._arrivals: dict[str, list[Point]] = {}

    def load_graph(self) -> None:
        try:
            self._graph = zonemap_nav.load(self._game_data)
        except Exception:
            logger.exception("Could not read zonemap.nav; the world tree will only follow recorded links")

    def neighbours(self, zone: str) -> set[str]:
        return self._graph.neighbours(zone) if self._graph is not None else set()

    def volumes(self, zone: str) -> list[Point]:
        """(name, position) of each volume in `zone`."""
        if zone not in self._volumes:
            try:
                self._volumes[zone] = volumes(bytes(zone_wad(zone, self._game_data)["volumes.xml"]))
            except (FileNotFoundError, KeyError):
                logger.info("No volumes.xml for %s", zone)
                self._volumes[zone] = []
        return self._volumes[zone]

    def arrival_points(self, zone: str) -> list[Point]:
        """(name, position) of each named location in `zone`: where the game places arrivals."""
        if zone not in self._arrivals:
            try:
                self._arrivals[zone] = locations(zone_record(zone, self._deserialize, self._game_data))
            except (FileNotFoundError, KeyError):
                logger.info("No gamedata.bin for %s", zone)
                self._arrivals[zone] = []
        return self._arrivals[zone]
