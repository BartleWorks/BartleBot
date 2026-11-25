"""Which zones a bot names that have no walking map.

Every move inside a zone walks (``nodes/walking.py``), on the zone maps core ships. A zone
with none can't be walked in, so a bot naming one is warned about when it loads and refused
at Run. The check reads only the maps' index (``ZoneMaps.has``), never a map: it runs on
every validation pass, and its budget is 10 ms on the largest shipped bot.

The zones are the ones the restricted-zone check reads (``restricted_zones._zone_param_keys``),
from every state of the bot, a subgraph's members included. A glob pattern has a map when
any mapped zone matches it.
"""

from __future__ import annotations

import contextlib
import fnmatch
import logging
import threading
import time
from collections import deque
from collections.abc import Iterable

from bartleby.runtime.movement import zone_maps

from bartlebot.bot.schema import StateModel
from bartlebot.game.restricted_zones import _zone_param_keys

logger = logging.getLogger(__name__)

#: The maps the check asks: core's shipped ones, so a test can hand it its own index.
MAPS = zone_maps.SHIPPED

#: Said after the zones wherever a bot is refused for one.
NO_MAP_TEXT = (
    "Every move in a zone walks, along that zone's walking map, and these zones have none. "
    "Choose another zone, or report the zone so a map can be added."
)


def _has_map(zone: str) -> bool:
    if any(c in zone for c in "*?["):
        return any(fnmatch.fnmatchcase(mapped, zone) for mapped in MAPS.index)
    return MAPS.has(zone)


def unmapped_zones(states: Iterable[StateModel]) -> list[tuple[str, str]]:
    """(state label, zone) for every zone a state names that has no walking map."""
    found: list[tuple[str, str]] = []
    for state in states:
        params = state.params or {}
        for key in _zone_param_keys(params):
            zone = params.get(key)
            if isinstance(zone, str) and zone and not _has_map(zone):
                found.append((state.label or state.id, zone))
    return found


def warning(zone: str) -> str:
    """The validation warning on a node naming *zone*."""
    return f"{zone} has no walking map, so the bot can't be run"


def describe_found(found: Iterable[tuple[str, str]]) -> str:
    """One bullet per zone :func:`unmapped_zones` found: the node and the zone."""
    return "\n".join(f"• {label}: {zone}" for label, zone in found)


class MapWarmer:
    """Builds and saves each zone map's walking copy on a thread of its own, before a walk needs it.

    A walk's first load in a zone otherwise builds it then: 2 to 16 s of planning before the
    first step. Every zone is built once per map release and saved (core's ``ZoneMaps.warm``,
    under ``%LocalAppData%/Bartleby/cache/zone_maps``), so a warm that finds every copy saved
    costs about half a second. The order: the zones :meth:`prefer` was given (the open bot's,
    when it opens and at Run), then every zone Sync in Zone's routes cross (``zone_nav.json``),
    then the rest. A build holds the event loop at most about 30 ms at a time, measured while
    building Triton Avenue, the largest map.
    """

    def __init__(self, maps=None) -> None:
        self._maps = maps
        self._lock = threading.Lock()
        self._pending: deque[str] = deque()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.built = 0

    @property
    def maps(self):
        return MAPS if self._maps is None else self._maps

    def prefer(self, zones: Iterable[str]) -> None:
        """Build these first, in order, ahead of whatever is still waiting. A glob is skipped."""
        wanted = [z for z in dict.fromkeys(zones) if z and not any(c in z for c in "*?[") and self.maps.has(z)]
        with self._lock:
            for zone in reversed(wanted):
                with contextlib.suppress(ValueError):
                    self._pending.remove(zone)
                self._pending.appendleft(zone)
        if wanted:
            self._wake.set()

    def start(self, network: Iterable[str] = ()) -> None:
        """Queue every mapped zone, *network*'s first, and start the thread."""
        if self._thread is not None:
            return
        everything = self.maps.zones()
        with self._lock:
            queued = set(self._pending)
            for zone in [*dict.fromkeys(network), *everything]:
                if zone not in queued and self.maps.has(zone):
                    self._pending.append(zone)
                    queued.add(zone)
        self._thread = threading.Thread(target=self._run, name="zone-map-warm", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop after the zone being built, if any."""
        self._stop.set()
        self._wake.set()

    def _next(self) -> str | None:
        """The next zone, or None having cleared the wake: under the lock, so a prefer() that
        queues one after this has its wake seen."""
        with self._lock:
            if self._pending:
                return self._pending.popleft()
            self._wake.clear()
            return None

    def _run(self) -> None:
        started, reported = time.monotonic(), 0
        while not self._stop.is_set():
            zone = self._next()
            if zone is None:
                if self.built != reported:
                    reported = self.built
                    logger.info(
                        "zone maps: %d built and saved in %.0f s; every map is ready",
                        self.built,
                        time.monotonic() - started,
                    )
                self._wake.wait()
                continue
            try:
                self.built += self.maps.warm([zone], self._stop)
            except Exception:  # noqa: BLE001 - one bad map must not stop the rest; its walk builds it again
                logger.warning("zone maps: %s could not be built ahead", zone, exc_info=True)


#: The app's one warmer: started by ``AppController.run``, preferred by the open bot.
WARMER = MapWarmer()


def bot_zones(states: Iterable[StateModel]) -> list[str]:
    """Every zone a bot's states name, in order, for :meth:`MapWarmer.prefer`."""
    zones: list[str] = []
    for state in states:
        params = state.params or {}
        zones.extend(params[k] for k in _zone_param_keys(params) if isinstance(params.get(k), str))
    return zones
