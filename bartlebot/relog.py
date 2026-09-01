"""When a client should be relogged, and nothing about how.

Two triggers, both decided here so the policy can be tested without a clock, a client, or
Qt -- following ``client_refresh_interval()`` in ``nodes/timing.py`` as precedent.

**The timer (#163).** A game client degrades the longer it stays logged in: measured over
801 fights, median combat duration climbed 23 s -> 32 s, and everything outside the game
process has been ruled out as the cause. Nothing can be done about it from here, and a
relog is the only thing that clears it, so the mitigation is to relog before the cost
becomes worth paying. Time-based rather than fight-based: short sessions of 30+ fights have
repeatedly failed to reproduce the degradation while two idle hours have, so fights are not
the unit that matters.

**The GDI ceiling (#164).** One client reached exactly 10,000 GDI objects inside a single
session, which is the Windows default per-process ``GDIProcessHandleQuota``. Past that
point every GDI allocation in that process fails and the game has no reason to handle it
gracefully. This is a fault ceiling rather than a preference, which is why the threshold is
a constant here and not a setting.
"""

from __future__ import annotations

SECONDS_PER_HOUR = 3600.0

GDI_QUOTA = 10_000
# The Windows default per-process GDIProcessHandleQuota. Not ours to change, and a process
# that reaches it starts failing allocations.

GDI_RELOG_THRESHOLD = 8_000
# 20% headroom. The one client that ran into this accumulated roughly 3x faster than its
# peers throughout a session, so the gap between here and the ceiling is minutes of grace,
# not seconds -- enough to park the FSM and close the client in an orderly way.


def relog_due(now: float, last_relog_at: float, interval_hours: float) -> bool:
    """Whether the scheduled relog is due.

    *last_relog_at* is measured from the start of the run for the first cycle, so a run
    shorter than one interval never relogs. Both timestamps are monotonic seconds; the
    caller owns the clock.

    An interval of zero -- or anything negative, which only a hand-edited settings file
    produces -- means the feature is off.
    """
    if interval_hours <= 0:
        return False
    return (now - last_relog_at) >= interval_hours * SECONDS_PER_HOUR


def gdi_relog_due(gdi_count: int, threshold: int = GDI_RELOG_THRESHOLD) -> bool:
    """Whether a client is close enough to the GDI quota to be relogged for it.

    A count of zero means the read failed rather than that the process holds no GDI
    objects -- a live game client always holds thousands -- so it is never a trigger.
    """
    if gdi_count <= 0:
        return False
    return gdi_count >= threshold
