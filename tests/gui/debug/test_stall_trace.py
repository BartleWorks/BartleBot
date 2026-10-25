"""The stall tracer: a held event loop leaves the stack that held it, and an idle one leaves nothing."""

import asyncio
import logging
import os
import time

from bartlebot.gui.debug.stall_trace import KEEP_FILES, StallTracer


def _hold_the_loop(seconds: float) -> None:
    """Stands in for whatever freezes the window: synchronous, on the loop's own thread."""
    time.sleep(seconds)


async def test_a_held_loop_dumps_the_stack_that_held_it(tmp_path, caplog):
    tracer = StallTracer(tmp_path, interval=0.02, dump_after=0.3)
    with caplog.at_level(logging.WARNING, logger="bartlebot.gui.debug.stall_trace"):
        tracer.start()
        try:
            await asyncio.sleep(0.05)
            _hold_the_loop(0.8)
            await asyncio.sleep(0.1)  # the heartbeat that sees it let go
        finally:
            tracer.stop()

    text = tracer.path.read_text(encoding="utf-8")
    assert "in _hold_the_loop" in text, "the dump names the function holding the loop, taken while it held"
    assert "--- The event loop was held for" in text
    [record] = [r for r in caplog.records if "Event loop held for" in r.getMessage()]
    assert str(tracer.path) in record.getMessage()


async def test_a_loop_never_held_leaves_no_file(tmp_path, caplog):
    tracer = StallTracer(tmp_path, interval=0.02, dump_after=2.0)
    with caplog.at_level(logging.WARNING, logger="bartlebot.gui.debug.stall_trace"):
        tracer.start()
        try:
            await asyncio.sleep(0.1)
        finally:
            tracer.stop()

    assert not tracer.path.exists()
    assert not [r for r in caplog.records if "Event loop held for" in r.getMessage()]


async def test_starting_keeps_only_the_newest_files(tmp_path):
    for i in range(KEEP_FILES + 2):
        old = tmp_path / f"stalls_2026-01-01_00-00-{i:02d}.txt"
        old.write_text("dump", encoding="utf-8")
        os.utime(old, (i, i))
    tracer = StallTracer(tmp_path, dump_after=2.0)
    tracer.start()
    try:
        assert len(list(tmp_path.glob("stalls_*.txt"))) == KEEP_FILES
    finally:
        tracer.stop()
