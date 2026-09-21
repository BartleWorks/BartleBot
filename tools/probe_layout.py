"""Measure what the bot canvas does to itself during an interaction.

The canvas rendering bugs in #83 were both invisible to reading and obvious to
measurement, so this is the tool to reach for before forming a theory about either of
them.  It builds a real ``BotCanvas`` offscreen, drives a scripted interaction, and prints
three things:

* the bar's height at each step, taken twice -- synchronously, and again after the event
  loop has turned.  A disagreement between those two means a parent laid the widget out
  from a stale size hint, which is the toolbar-flicker bug.
* every height each widget was given, in order, so a round trip that should visit two
  heights and visits five says so.
* the event stream, with each paint's damaged rect, which is where a stale-pixel bug
  (#73) shows itself: a paint rect smaller than the widget leaves the rest as it was.

No game client is involved and nothing is hooked -- it is a widget test bench.

Usage::

    uv run python tools/probe_layout.py mode           # Run/Edit switches (#83, the toolbar)
    uv run python tools/probe_layout.py mode --tags 2  # ... with a tagged bot, which wraps
    uv run python tools/probe_layout.py mode --trace   # plus the full event stream
    uv run python tools/probe_layout.py paints         # paint damage rects (#73)
    uv run python tools/probe_layout.py mode --watch FavoritesBar

``--watch`` names the widget class the height timeline is reported for; the default is
``BotHeader``, which is Bar B.

``--show`` runs on a real screen rather than offscreen, and it is needed for more than the
paint records.  The offscreen platform resolves the ``fusion`` style where the app resolves
``windows11``, and fusion's controls are much wider at the same font metrics -- a tag pill
is 80px against 59px.  Heights come out close; **widths do not, so anything about wrapping
or eliding has to be measured with ``--show``**.  Paint records need it too: only a real
backend actually paints.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "scenario",
        choices=("mode", "paints"),
        help="mode: flip between Run and Edit.  paints: resize and scroll, and report paint damage.",
    )
    parser.add_argument("--tags", type=int, default=0, help="how many tags the bot carries (default 0)")
    parser.add_argument("--cycles", type=int, default=3, help="how many round trips (default 3)")
    parser.add_argument("--width", type=int, default=1200, help="canvas width (default 1200)")
    parser.add_argument("--height", type=int, default=800, help="canvas height (default 800)")
    parser.add_argument("--watch", default="BotHeader", help="widget class for the height timeline")
    parser.add_argument("--trace", action="store_true", help="print the whole event stream")
    parser.add_argument("--limit", type=int, default=60, help="how many trace lines to print (default 60)")
    parser.add_argument("--show", action="store_true", help="use a real screen rather than the offscreen backend")
    return parser.parse_args(argv)


def _config(tags: int):
    """A bot with enough metadata that every field in the bar has something to show."""
    from bartlebot.bot.schema import BotConfig, StateModel
    from bartlebot.bot_tags import BOT_TAGS

    return BotConfig(
        id="layout-probe",
        name="Layout Probe",
        version="1.0",
        authors="Probe",
        tags=list(BOT_TAGS[:tags]),
        initial="a",
        recovery_state="a",
        states=[StateModel(id="a", node_type="change_to_next_realm", params={})],
        transitions=[],
    )


def _scenario_mode(canvas, app, tracer, args, snapshots, target):
    """Flip between Run and Edit, snapshotting either side of each event loop turn.

    Both snapshots matter and they are the whole diagnosis.  The synchronous one is what
    the canvas's own layout would read if its layout request were served first, which is
    exactly what happened; the settled one is what the user is left looking at.
    """
    from bartlebot.qt_layout_debug import size_snapshot

    snapshots.append(size_snapshot(target, "run (initial)"))
    for i in range(args.cycles):
        tracer.mark(f"{i}: to edit")
        canvas.enter_edit_mode()
        snapshots.append(size_snapshot(target, f"{i}: edit sync"))
        app.processEvents()
        snapshots.append(size_snapshot(target, f"{i}: edit settled"))

        tracer.mark(f"{i}: to run")
        canvas.exit_edit_mode()
        snapshots.append(size_snapshot(target, f"{i}: run sync"))
        app.processEvents()
        snapshots.append(size_snapshot(target, f"{i}: run settled"))


def _scenario_paints(canvas, app, tracer, args, snapshots, target):
    """Resize and scroll the graph, which is what #73's stale pixels follow."""
    from bartlebot.qt_layout_debug import size_snapshot

    view = canvas.view
    snapshots.append(size_snapshot(target, "before"))
    for i in range(args.cycles):
        tracer.mark(f"{i}: resize")
        canvas.resize(args.width - 40 * (i + 1), args.height)
        app.processEvents()
        tracer.mark(f"{i}: scroll")
        bar = view.verticalScrollBar()
        bar.setValue(bar.value() + 60)
        app.processEvents()
        snapshots.append(size_snapshot(target, f"{i}: after scroll"))


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.show:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    from bartlebot.gui.canvas.bot_canvas import BotCanvas
    from bartlebot.qt_layout_debug import (
        EVENT_NAMES,
        LayoutTracer,
        format_records,
        format_snapshots,
        incomplete_paints,
        summarize,
        timeline,
        transient_values,
        unsettled,
    )

    canvas = BotCanvas()
    canvas.resize(args.width, args.height)
    canvas.show()
    canvas.setup(_config(args.tags))
    app.processEvents()

    # Attached after setup, so the trace holds the interaction and not the construction.
    tracer = LayoutTracer(events=tuple(EVENT_NAMES))
    traced = tracer.watch(canvas)
    target = canvas.header
    snapshots: list = []

    tracer.start("begin")
    runner = _scenario_mode if args.scenario == "mode" else _scenario_paints
    runner(canvas, app, tracer, args, snapshots, target)
    tracer.stop()

    print(
        f"scenario={args.scenario} tags={args.tags} cycles={args.cycles} "
        f"canvas={args.width}x{args.height} widgets traced={traced} records={len(tracer.records)}"
    )

    print("\n== geometry against hint, per step ==")
    print(format_snapshots(snapshots))
    stuck = [s for s in unsettled(snapshots) if s.phase.endswith("settled") or s.phase == "before"]
    if stuck:
        print(f"\n  {len(stuck)} settled step(s) still disagree with their own hint -- a stale-hint bug:")
        for s in stuck:
            print(f"    {s.phase}: {s.widget} is {s.height}px, and asks for {s.wanted}px")
    else:
        print("\n  every settled step agrees with its own hint.")

    print(f"\n== every height {args.watch} was given ==")
    heights = timeline(tracer.records, args.watch)
    if not heights:
        print(f"  no resize records for {args.watch} -- it was never laid out again, or the name is wrong")
    else:
        for phase, height in heights:
            print(f"  {phase:<16} {height}")
        values = [h for _, h in heights]
        resting = {s.height for s in snapshots if s.phase.endswith("settled") or s.phase == "before"}
        odd = transient_values(values, resting)
        if odd:
            print(f"\n  passed through {sorted(set(odd))} -- painted, and no step asked for it.")
            print(f"  resting heights were {sorted(resting)}.")
        else:
            print(f"\n  visited only its resting heights {sorted(resting)} -- nothing transient was painted.")

    partial = incomplete_paints(tracer.records)
    if partial:
        print(f"\n== {len(partial)} paint(s) narrower than their widget ==")
        print("   (normal in itself; the first place to look for stale pixels)")
        for r in partial[:20]:
            print(f"  {r.phase:<16} {r.widget:<24} geometry={r.geometry} damage={r.damage}")

    print("\n== busiest widgets ==")
    for widget, name, event, count in summarize(tracer.records)[:15]:
        print(f"  {count:>5}  {widget:<26} {name:<16} {event}")

    if args.trace:
        print("\n== event stream ==")
        print(format_records(tracer.records, limit=args.limit))

    canvas.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
