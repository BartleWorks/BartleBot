"""Record what Qt does to a widget subtree, so a layout or repaint bug can be measured.

Written for the two halves of #83, and the reason it exists is that both of them were
*invisible to reading*.  The toolbar flickered on every Run/Edit switch; three plausible
causes were wrong, and the actual one -- the canvas resolving the bar's height from a
size hint one event-loop pass stale, and painting the intermediate value -- only showed
up once the bar's height was printed at each step of a switch.  Guessing cost more than
measuring would have.

Two things to measure, and they answer different questions.

:class:`LayoutTracer` records the event stream: show, hide, resize, move, paint and
layout-request, per widget, in order, with geometry.  That is what says *how many*
distinct states a widget passed through -- a widget laid out three times during one user
action is painted three times, and that is what a flicker is.  Paint records also carry
the damaged rect, which is the question #73 asks: a paint whose rect is smaller than the
widget's own leaves whatever was underneath the rest of it on screen.

:func:`size_snapshot` records the other half, which no event reports: the difference
between what a widget's geometry *is* and what the widget currently says it should be.  A
widget whose height disagrees with that is one whose parent measured it at the wrong
moment and never asked again -- the state Bar B was left in, eighteen pixels too tall,
until an unrelated resize put it right.  Take one snapshot synchronously after the action
and one after the event loop has turned; if they differ, the parent read a stale hint.

"Should be" is ``heightForWidth`` for a widget whose layout wraps, and ``sizeHint`` only
for one whose does not -- see :attr:`SizeSnapshot.wanted`.  A ``FlowLayout``'s hint is
deliberately one row of everything, so measuring a wrapped widget against it reports a bug
on every healthy trace, and does so on precisely the widgets a real layout bug likes to
hide in.

Nothing here is installed by default and nothing hooks the app.  Attach a tracer to the
subtree in question, drive the interaction, and read the records back.
``tools/probe_layout.py`` does exactly that for the bot canvas and is the worked example.

The analysis helpers are pure functions over records, so what a trace *means* is testable
without a ``QApplication``.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QObject

logger = logging.getLogger(__name__)

#: The events worth recording, and the short name each is reported under.  Paint and
#: LayoutRequest are the two that make a flicker legible: one layout request per user
#: action is healthy, and three means the widget was drawn three ways.
EVENT_NAMES: dict[QEvent.Type, str] = {
    QEvent.Type.Show: "show",
    QEvent.Type.Hide: "hide",
    QEvent.Type.ShowToParent: "show-to-parent",
    QEvent.Type.HideToParent: "hide-to-parent",
    QEvent.Type.Resize: "resize",
    QEvent.Type.Move: "move",
    QEvent.Type.Paint: "paint",
    QEvent.Type.LayoutRequest: "layout-request",
    QEvent.Type.UpdateRequest: "update-request",
    QEvent.Type.PolishRequest: "polish-request",
}

#: A useful default: the events that change what is on screen, without the move and paint
#: noise a single resize produces across a deep subtree.
STRUCTURAL_EVENTS: tuple[QEvent.Type, ...] = (
    QEvent.Type.Show,
    QEvent.Type.Hide,
    QEvent.Type.Resize,
    QEvent.Type.LayoutRequest,
)


@dataclass(frozen=True)
class Record:
    """One event, as it was delivered."""

    seq: int
    #: Free-text mark from :meth:`LayoutTracer.mark`, naming the step this fell under.
    phase: str
    widget: str
    name: str
    event: str
    #: The widget's own geometry (x, y, w, h) at delivery.
    geometry: tuple[int, int, int, int]
    #: For a paint, the damaged rect (x, y, w, h) in widget coordinates; else ``None``.
    damage: tuple[int, int, int, int] | None = None


@dataclass(frozen=True)
class SizeSnapshot:
    """What a widget's geometry is, beside what its hints say it should be."""

    phase: str
    widget: str
    height: int
    hint_height: int
    minimum_hint_height: int
    width: int
    hint_width: int
    #: What the widget's own layout says it needs at this width, when that layout
    #: reports height-for-width; ``None`` when it does not.  This, not ``hint_height``,
    #: is what a wrapping widget should be measured against.
    wanted_height: int | None = None

    @property
    def wanted(self) -> int:
        """The height this widget should have been given, by its own account.

        For a wrapping widget that is ``heightForWidth`` at its current width, not
        ``sizeHint``: a ``FlowLayout``'s hint is deliberately *one row of everything* --
        what it would like, not what it needs -- so a wrapped widget is legitimately
        shorter than its hint and comparing the two reports a bug on every healthy
        trace.  That false positive is worth naming, because it fires on exactly the
        widgets a layout bug is most likely to be hiding in.
        """
        return self.wanted_height if self.wanted_height is not None else self.hint_height

    @property
    def settled(self) -> bool:
        """True when the geometry agrees with what the widget asked for.

        A widget that is *not* settled after the event loop has turned is one whose
        parent measured it too early and was never told to ask again.
        """
        return self.height == self.wanted


# ---------------------------------------------------------------------------
# Pure analysis -- no QApplication required
# ---------------------------------------------------------------------------


def summarize(records: list[Record]) -> list[tuple[str, str, str, int]]:
    """Count the records per (widget, name, event), busiest first.

    The count is the point: one resize per widget per user action is a layout doing its
    job, and five is a widget being laid out five different ways while the user watches.
    """
    counts = Counter((r.widget, r.name, r.event) for r in records)
    return [(w, n, e, c) for (w, n, e), c in counts.most_common()]


def timeline(records: list[Record], widget: str, event: str = "resize") -> list[tuple[str, int]]:
    """Every height *widget* was given, in order, as (phase, height).

    The shape that names a flicker: a round trip between two modes should visit two
    heights, and Bar B visited five.
    """
    return [(r.phase, r.geometry[3]) for r in records if r.widget == widget and r.event == event]


def transient_values(values: list[int], settled: set[int]) -> list[int]:
    """The values passed through that are not a resting state, in order.

    A widget is *painted* at every value it is given, so a value here is a frame the user
    saw that no mode asked for.  Separating the two is what distinguishes a bar that is
    legitimately taller in Edit mode from one that overshoots on the way there.
    """
    return [v for v in values if v not in settled]


def unsettled(snapshots: list[SizeSnapshot]) -> list[SizeSnapshot]:
    """The snapshots whose geometry disagrees with what the widget asked for.

    Take these *after* the event loop has turned.  One that is still unsettled is a
    stale-hint bug: nothing further is coming to correct it, so it stays wrong on screen
    until some unrelated event forces the parent to lay out again.
    """
    return [s for s in snapshots if not s.settled]


def incomplete_paints(records: list[Record]) -> list[Record]:
    """Paints whose damaged rect does not cover the widget.

    Not a bug by itself -- repainting only what changed is the whole point of a damage
    rect -- but it is the first thing to look at for stale pixels: anything the rect
    leaves out keeps whatever was drawn there before.
    """
    out = []
    for r in records:
        if r.event != "paint" or r.damage is None:
            continue
        _, _, width, height = r.geometry
        if r.damage != (0, 0, width, height):
            out.append(r)
    return out


def format_records(records: list[Record], limit: int | None = None) -> str:
    """One line per record, for pasting into an issue."""
    rows = records if limit is None else records[:limit]
    lines = [f"{'seq':>5}  {'phase':<14} {'widget':<26} {'name':<16} {'event':<15} geometry"]
    for r in rows:
        damage = f"  damage={r.damage}" if r.damage is not None else ""
        lines.append(f"{r.seq:>5}  {r.phase:<14} {r.widget:<26} {r.name:<16} {r.event:<15} {r.geometry}{damage}")
    if limit is not None and len(records) > limit:
        lines.append(f"  ... {len(records) - limit} more")
    return "\n".join(lines)


def format_snapshots(snapshots: list[SizeSnapshot]) -> str:
    """A table of geometry against hint, with the unsettled rows flagged."""
    lines = [f"{'phase':<20} {'widget':<24} {'height':>7} {'wanted':>7} {'hint':>7} {'minhint':>8}  settled"]
    for s in snapshots:
        flag = "yes" if s.settled else "NO  <-- parent measured a stale hint"
        lines.append(
            f"{s.phase:<20} {s.widget:<24} {s.height:>7} {s.wanted:>7} "
            f"{s.hint_height:>7} {s.minimum_hint_height:>8}  {flag}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def size_snapshot(widget, phase: str) -> SizeSnapshot:
    """Geometry beside hints, for one widget, right now.

    Call it twice per step -- once before the event loop turns and once after -- because
    the disagreement between those two is the whole diagnosis for a stale-hint bug.
    """
    hint = widget.sizeHint()
    minimum = widget.minimumSizeHint()
    layout = widget.layout()
    wanted = None
    if layout is not None and layout.hasHeightForWidth():
        # What the parent will actually size it by.  ``QWidgetItem`` reads this from the
        # layout, never from the widget, which is also why a wrapping widget cannot floor
        # its own height through a ``sizeHint`` or ``heightForWidth`` override.
        wanted = layout.totalHeightForWidth(widget.width())
    return SizeSnapshot(
        phase=phase,
        widget=type(widget).__name__,
        height=widget.height(),
        hint_height=hint.height(),
        minimum_hint_height=minimum.height(),
        width=widget.width(),
        hint_width=hint.width(),
        wanted_height=wanted,
    )


class LayoutTracer(QObject):
    """Event filter that records what happens to a widget subtree.

    Install it on a subtree, :meth:`mark` each step of the interaction so the records say
    which step they belong to, and read :attr:`records` back.  It records nothing until
    :meth:`start`, so attaching it does not capture the setup.

    Widgets created *after* :meth:`watch` are not traced -- Qt has no subtree-wide hook,
    so ``watch`` walks the children that exist.  Call it again after a rebuild.
    """

    def __init__(self, events: tuple[QEvent.Type, ...] | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._events = set(events) if events is not None else set(EVENT_NAMES)
        self.records: list[Record] = []
        self._phase = "setup"
        self._running = False
        self._seq = 0

    # -- control -------------------------------------------------------

    def watch(self, widget) -> int:
        """Trace *widget* and every descendant that exists now.  Returns the count."""
        widget.installEventFilter(self)
        traced = 1
        for child in widget.findChildren(QObject):
            child.installEventFilter(self)
            traced += 1
        return traced

    def start(self, phase: str = "start") -> None:
        self._phase = phase
        self._running = True

    def stop(self) -> None:
        self._running = False

    def mark(self, phase: str) -> None:
        """Name the step the records from here on belong to."""
        self._phase = phase

    def clear(self) -> None:
        self.records.clear()
        self._seq = 0

    # -- the filter ----------------------------------------------------

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if not self._running:
            return False
        kind = event.type()
        if kind not in self._events:
            return False
        try:
            self.records.append(self._record(obj, event, kind))
        except (AttributeError, RuntimeError):
            # A widget mid-destruction, or a QObject that is not a widget at all.  A
            # tracer must never be the reason a run behaves differently.
            pass
        return False

    def _record(self, obj: QObject, event: QEvent, kind: QEvent.Type) -> Record:
        geometry = obj.geometry().getRect() if hasattr(obj, "geometry") else (0, 0, 0, 0)
        damage = event.rect().getRect() if kind == QEvent.Type.Paint else None
        self._seq += 1
        return Record(
            seq=self._seq,
            phase=self._phase,
            widget=type(obj).__name__,
            name=obj.objectName(),
            event=EVENT_NAMES.get(kind, str(kind)),
            geometry=geometry,
            damage=damage,
        )
