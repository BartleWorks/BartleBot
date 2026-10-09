"""Developer-only tracer for #73: pixels the canvas changed and Qt never repainted.

On only with ``BARTLEBOT_DEV`` set (see ``store_roots.dev_mode``). It watches rather
than hooks: an event filter on the canvas viewport, a timer, and the scene's
``about_to_change`` signal. It adds no calls to the item or scene code.

**What it checks.** An item on screen occupies its device bounding rect. When that
rect changes, or the item appears or disappears, two areas now hold the wrong
pixels: the one it was drawn in, and the one it should be drawn in. Both are
*owed* a repaint. Every viewport ``Paint`` event pays off the region it covers, and
anything still owed after ``OWED_DEADLINE_S`` is a lost repaint. That is the
symptom #73 describes, caught when it happens instead of when someone notices it.

Each stale finding is logged as a WARNING with the item, which area (``was`` = old
pixels never erased, ``now`` = new pixels never drawn), the unpainted rect, and the
last few canvas events. Three more lines are there to test one theory: that the lost
repaints come from Qt's *compatibility* update path, which a listener on
``scene.changed`` switches every view onto. ``update path`` says which path the scene
was on; ``paints since`` lists every Paint the viewport got after the change, which
tells "nothing was ever scheduled" from "a paint came but missed"; and ``scene rect``
says whether the gap lay inside it, since the compatibility path repaints a removed
item by repainting the scene rect and nothing past it. **The tracer itself must never
connect ``scene.changed``**, or it would put the canvas on the path it is testing. A screenshot of what is actually on screen goes to
``logs/repaint/``. It is taken with ``QScreen.grabWindow``, never ``QWidget.grab``,
which re-renders the widget and would hide the stale pixels. Like the rest of
``logs/``, it is gitignored: the canvas can show anything a node label holds.

**Coordinates.** Rects are kept in *content* coordinates: viewport pixels minus the
view transform's translation. Scrolling and the centring indent then cancel out,
so a pan doesn't look like every item moving. A change of scale does move
everything, and Qt repaints the whole viewport for it, so a zoom re-baselines the
ledger instead of comparing across it.

**What it cannot see.** It trusts ``boundingRect()``. A paint that spills outside
it (the node badge case in #73's third comment) is invisible here, and so is a
change of look that keeps the rect, such as a snap highlight being cleared. Both
would need the items to report more than Qt already knows about them.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen, QRegion
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsView

from bartlebot.logging_setup import session_log_dir

logger = logging.getLogger(__name__)

#: How long an owed repaint may stay unpaid before it counts as lost. Qt paints on
#: the next event-loop turn, so a real repaint lands well inside this.
OWED_DEADLINE_S = 0.5
#: How often the ledger looks at the scene when nothing is painting. A lost
#: repaint may mean no Paint event arrives at all, so paints alone aren't enough.
SAMPLE_INTERVAL_MS = 100
#: How many recent canvas events a finding carries for context.
CONTEXT_EVENTS = 12
#: Cap on screenshots per session, so a systematic false positive can't fill the disk.
MAX_SCREENSHOTS = 20
#: How many recent viewport paints are kept, to say which came after a stale change.
#: A drag paints every frame, so this only needs to span the deadline.
PAINT_HISTORY = 32


# ---------------------------------------------------------------------------
# The ledger: pure bookkeeping, no widgets
# ---------------------------------------------------------------------------


@dataclass
class Stale:
    """An area an item change left owed, still unpainted when the deadline passed."""

    what: str
    kind: str  # "was": old pixels never erased; "now": new pixels never drawn
    rect: QRect  # the whole owed area, content coordinates
    missing: QRegion  # the part of it no paint covered
    age: float  # seconds from the change to the report


@dataclass
class _Owed:
    what: str
    kind: str
    rect: QRect
    missing: QRegion
    since: float


class RepaintLedger:
    """Which screen areas item changes have left owed, and which paints paid them.

    ``observe`` compares the items' rects with the previous observation and records
    what each change owes, ``painted`` subtracts a Paint region, and ``expired``
    hands back whatever is still owed past the deadline. All rects are in content
    coordinates. The caller does the mapping.
    """

    def __init__(self, deadline: float = OWED_DEADLINE_S) -> None:
        self._deadline = deadline
        self._rects: dict[int, QRect] | None = None
        self._owed: list[_Owed] = []

    @property
    def owed_count(self) -> int:
        return len(self._owed)

    def reset(self) -> None:
        """Forget everything; the next ``observe`` is a fresh baseline."""
        self._rects = None
        self._owed.clear()

    def observe(self, now: float, rects: dict[int, QRect], names: dict[int, str], visible: QRect) -> None:
        """Record what changed since the last observation.

        *rects* maps an item key to its rect, *names* describes each key for a report
        (it must also cover the keys that just disappeared), and *visible* is the
        viewport in content coordinates: only what is on screen can be stale.
        """
        previous, self._rects = self._rects, dict(rects)
        if previous is None:
            return
        for key, old in previous.items():
            if rects.get(key) != old:
                self._owe(now, names.get(key, "?"), "was", old, visible)
        for key, new in rects.items():
            if previous.get(key) != new:
                self._owe(now, names.get(key, "?"), "now", new, visible)

    def painted(self, region: QRegion) -> None:
        """Pay off whatever *region* (content coordinates) covers."""
        still: list[_Owed] = []
        for owed in self._owed:
            owed.missing = owed.missing.subtracted(region)
            if not owed.missing.isEmpty():
                still.append(owed)
        self._owed = still

    def expired(self, now: float) -> list[Stale]:
        """Pop and return every owed area older than the deadline."""
        late = [o for o in self._owed if now - o.since >= self._deadline]
        if late:
            self._owed = [o for o in self._owed if now - o.since < self._deadline]
        return [Stale(o.what, o.kind, o.rect, o.missing, now - o.since) for o in late]

    def _owe(self, now: float, what: str, kind: str, rect: QRect, visible: QRect) -> None:
        on_screen = rect.intersected(visible)
        if on_screen.isEmpty():
            return
        self._owed.append(_Owed(what, kind, rect, QRegion(on_screen), now))


# ---------------------------------------------------------------------------
# The tracer: wires a ledger to a live view
# ---------------------------------------------------------------------------


def describe_item(item: QGraphicsItem) -> str:
    """A short, log-safe name for *item*: its class and the state ids it belongs to.

    Reads public attributes only. State ids and node types are author-chosen
    identifiers, never player data.
    """
    kind = type(item).__name__
    state_id = getattr(item, "state_id", None)
    if isinstance(state_id, str):
        return f"{kind}({state_id})"
    src, dst = getattr(item, "src_node", None), getattr(item, "dst_node", None)
    if src is not None and dst is not None:
        when = getattr(item, "when", None)
        arrow = f"-{when}->" if when else "->"
        return f"{kind}({getattr(src, 'state_id', '?')} {arrow} {getattr(dst, 'state_id', '?')})"
    if kind == "QGraphicsLineItem":
        return "QGraphicsLineItem(drag preview?)"
    return kind


class RepaintTracer(QObject):
    """Watches one view's viewport and logs every item change Qt never repainted."""

    def __init__(
        self, view: QGraphicsView, deadline: float = OWED_DEADLINE_S, screenshot_dir: Path | None = None
    ) -> None:
        super().__init__(view)
        self._screenshot_dir = screenshot_dir or session_log_dir() / "repaint"
        self._view = view
        self._ledger = RepaintLedger(deadline)
        self._names: dict[int, str] = {}
        # Held for one sample so a deleted item's id() can't be reused by a new one
        # before the ledger has compared them. Never called, only kept alive.
        self._held: dict[int, QGraphicsItem] = {}
        self._last_tick = time.monotonic()
        self._scale: tuple[float, float] | None = None
        self._offset: QPoint | None = None
        self._events: deque[str] = deque(maxlen=CONTEXT_EVENTS)
        # (monotonic time, painted region's bounding rect, rect count), viewport coordinates
        self._paints: deque[tuple[float, QRect, int]] = deque(maxlen=PAINT_HISTORY)
        self._update_path: str | None = None
        self._t0 = time.monotonic()
        self._screenshots = 0
        self._scene = None

        view.viewport().installEventFilter(self)
        self._timer = QTimer(self)
        self._timer.setInterval(SAMPLE_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        logger.info("Canvas repaint tracer on (BARTLEBOT_DEV); lost repaints are logged here as WARNINGs")

    @property
    def ledger(self) -> RepaintLedger:
        return self._ledger

    # -- sampling -----------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001
        if event.type() == QEvent.Type.Paint and obj is self._view.viewport():
            # Observe first: the paint about to happen is drawing the scene as it
            # is now, so it pays for changes made up to this moment.
            self.sample()
            offset = self._offset if self._offset is not None else QPoint()
            region = event.region()
            self._paints.append((time.monotonic(), region.boundingRect(), region.rectCount()))
            self._ledger.painted(region.translated(-offset))
        return False

    def _tick(self) -> None:
        now = time.monotonic()
        stalled = now - self._last_tick > 3 * SAMPLE_INTERVAL_MS / 1000
        self._last_tick = now
        handle = self._view.window().windowHandle()
        if not self._view.isVisible() or handle is None or not handle.isExposed():
            # Nothing paints while minimised, and it all repaints on the way back.
            self._ledger.reset()
            return
        self.sample()
        if stalled:
            # The loop was blocked, so the paints that pay for changes made before
            # the block haven't had their turn yet. Give them one more tick.
            return
        self.report_expired(now)

    def report_expired(self, now: float | None = None) -> list[Stale]:
        """Log, and return, every owed area past its deadline at *now*."""
        stale = self._ledger.expired(time.monotonic() if now is None else now)
        self._report(stale)
        return stale

    def sample(self) -> None:
        """Compare every item's on-screen rect with the last sample."""
        scene = self._view.scene()
        if scene is None:
            return
        self._watch_scene(scene)
        path = update_path(scene)
        if path != self._update_path:
            if self._update_path is not None:
                self._note(f"update path -> {path}")
            self._update_path = path
        vt = self._view.viewportTransform()
        scale = (round(vt.m11(), 6), round(vt.m22(), 6))
        offset = QPoint(round(vt.dx()), round(vt.dy()))
        if scale != self._scale:
            if self._scale is not None:
                self._note(f"zoom {self._scale[0]:.3f} -> {scale[0]:.3f}: re-baselined")
            self._scale = scale
            self._ledger.reset()
        elif offset != self._offset and self._offset is not None:
            d = offset - self._offset
            self._note(f"mapping moved by ({d.x()}, {d.y()})")
        self._offset = offset

        rects: dict[int, QRect] = {}
        held: dict[int, QGraphicsItem] = {}
        for item in scene.items():
            if not item.isVisible() or item.flags() & QGraphicsItem.GraphicsItemFlag.ItemHasNoContents:
                continue
            key = id(item)
            held[key] = item
            device = item.deviceTransform(vt).mapRect(item.boundingRect()).toAlignedRect()
            rects[key] = device.translated(-offset)
            if key not in self._names:
                self._names[key] = describe_item(item)
        visible = self._view.viewport().rect().translated(-offset)
        self._ledger.observe(time.monotonic(), rects, self._names, visible)
        # The owed entries carry their own names, so a key that just went is done with.
        self._names = {k: v for k, v in self._names.items() if k in rects}
        self._held = held

    def _watch_scene(self, scene) -> None:  # noqa: ANN001
        if scene is self._scene:
            return
        self._scene = scene
        scene.sceneRectChanged.connect(lambda r: self._note(f"sceneRect -> {_fmt_rectf(r)}"))
        about = getattr(scene, "about_to_change", None)
        if about is not None:
            about.connect(lambda desc: self._note(f"edit: {desc}"))

    # -- reporting ----------------------------------------------------------

    def _note(self, text: str) -> None:
        self._events.append(f"+{time.monotonic() - self._t0:8.3f}s {text}")

    def _report(self, stale: list[Stale]) -> None:
        if not stale:
            return
        offset = self._offset if self._offset is not None else QPoint()
        scene = self._view.scene()
        scene_rect = self._view.mapFromScene(scene.sceneRect()).boundingRect() if scene is not None else QRect()
        lines = []
        for s in stale:
            gap = s.missing.boundingRect().translated(offset)
            inside = "inside" if scene_rect.contains(gap) else "NOT inside"
            lines.append(
                f"  {s.what}: {s.kind} {_fmt_rect(s.rect.translated(offset))} "
                f"unpainted {_fmt_rect(gap)} after {s.age:.2f}s, {inside} the scene rect"
            )
        since = time.monotonic() - max(s.age for s in stale)
        paints = [f"    +{t - since:.3f}s {_fmt_rect(r)} in {n} rect(s)" for t, r, n in self._paints if t >= since] or [
            "    (none)"
        ]
        shot = self._screenshot([s.missing.translated(offset) for s in stale])
        context = "\n".join(f"    {e}" for e in self._events) or "    (none)"
        logger.warning(
            "Lost canvas repaint (#73): %d area(s), viewport coordinates\n%s\n"
            "  update path: %s\n  scene rect: %s\n  paints since the change:\n%s\n"
            "  recent canvas events:\n%s%s",
            len(stale),
            "\n".join(lines),
            update_path(scene) if scene is not None else "(no scene)",
            _fmt_rect(scene_rect),
            "\n".join(paints),
            context,
            f"\n  screenshot: {shot}" if shot else "",
        )

    def _screenshot(self, regions: list[QRegion]) -> str | None:
        """Grab the real screen pixels under the viewport and outline the unpainted areas."""
        if self._screenshots >= MAX_SCREENSHOTS:
            return None
        vp = self._view.viewport()
        screen = vp.screen()
        if screen is None:
            return None
        top_left = vp.mapToGlobal(QPoint(0, 0))
        geo = screen.geometry()
        pix = screen.grabWindow(0, top_left.x() - geo.x(), top_left.y() - geo.y(), vp.width(), vp.height())
        if pix.isNull():
            return None
        painter = QPainter(pix)
        pen = QPen(QColor(255, 0, 255), 1, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        for region in regions:
            painter.drawRect(region.boundingRect().adjusted(-2, -2, 1, 1))
        painter.end()
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        path = self._screenshot_dir / f"stale_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
        if not pix.save(str(path)):
            return None
        self._screenshots += 1
        return str(path)


def update_path(scene) -> str:  # noqa: ANN001
    """Which way Qt delivers this scene's item updates to its views.

    ``direct`` is Qt's default, each item's update sent straight to each view. Any
    listener on ``scene.changed`` switches every view to ``compat``: updates are
    batched into that queued signal and each view maps the scene rects it is handed.
    Once that happens Qt connects the views themselves too, so the count includes them.
    """
    n = scene.receivers(scene.changed)
    return f"compat ({n} scene.changed listener(s))" if n else "direct"


def _fmt_rect(r: QRect) -> str:
    return f"({r.x()},{r.y()} {r.width()}x{r.height()})"


def _fmt_rectf(r) -> str:  # noqa: ANN001
    return f"({r.x():.0f},{r.y():.0f} {r.width():.0f}x{r.height():.0f})"
