"""Which accounts have a launch running right now.

WHY THIS IS NOT DERIVED FROM ``_client_order``

A launch reserves its slots before the first process is spawned, so the reserved ordering
already names every account a launch is about to bring up -- it looks like the pending set
and it is the reason ``reserve_slots`` was the obvious place to hang this off. It is the
right signal to *start* on and the wrong one to *end* on: a reservation is never cleared
by failure. Nothing in ``ClientManager`` takes a slot back when a launch dies at the login
screen, so a pending set read out of the ordering acquires a permanent artifact of every
launch that did not finish, and the tile spins for the rest of the session.

So in-flight is tracked explicitly, and the entry is what has an end. ``begin`` opens one
per account and ``end`` closes it -- from the caller's ``finally``, so a raise closes it
too.

WHAT AN ENTRY MEANS, AND WHEN A TILE STOPS BEING PENDING

An entry means "a launch is running for this account", which is *not* the same as "there
is nothing on screen for it". The window is titled seconds after login and gets a real
tile then (see ``_place_pending_accounts``), while the entry stays open through the whole
auto-select drive that follows. Those two facts are both wanted, and they answer different
questions:

    the entry     guards re-launch. Still open during the drive, because a second click
                  then would spawn a second window for an account that already has one.

    the tile      is pending only while the entry is open *and* no window has appeared.
                  ``build_tile_slots`` applies that rule; it is why the pending state
                  covers spawn-to-login rather than the whole minutes-long launch.

This module holds no Qt and no I/O: it is asked a question by ``build_tile_slots`` on
every detection tick.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

__all__ = ["LaunchAttempt", "LaunchStage", "LaunchTracker"]


class LaunchStage(Enum):
    """How far a launch has got, in the coarse steps the launcher actually sequences.

    Deliberately coarse, because the launcher is. ``launch_accounts`` waits for every
    window's login screen in one shared gather, so "waiting for the login screen" is a fact
    about the *batch* and has no per-client answer to report; the clients only diverge from
    ``instance_login`` onward. Past that, the auto-select drive is a single call that
    returns a bool at the end, so breaking LOADING down further would mean new
    instrumentation inside ``auto_select`` rather than passing through something that
    already exists. It is not worth it: what the user is actually asking of a slow launch
    is whether it is stuck, and ``LaunchAttempt.elapsed`` answers that without knowing any
    more about the phase than its name.

    ``value`` is the caption, so the view does not need a second table to render one.
    """

    STARTING = "Launching game"
    LOGGING_IN = "Logging in"
    LOADING = "Loading"
    # Terminal. A launch that gave up, which is a thing the launcher does quietly far more
    # often than it does loudly -- the auto-select drive returning False is a timeout, not
    # an exception, and nothing was reading it.
    FAILED = "Launch failed"

    @property
    def is_terminal(self) -> bool:
        """Whether this is a stage a launch stops at rather than moves through."""
        return self is LaunchStage.FAILED


@dataclass
class LaunchAttempt:
    """One account's in-flight launch."""

    username: str
    stage: LaunchStage = LaunchStage.STARTING
    started_at: float = field(default_factory=time.monotonic)
    # When the current stage was entered. Elapsed is reported per stage rather than per
    # launch: the question a stalled launch raises is "stuck doing what, and for how long",
    # and a total that keeps climbing across a normal stage change does not answer it.
    stage_since: float = field(default_factory=time.monotonic)
    # The resolution the window will open at, where the launch said: what its pending tile
    # holds room for until the window is there to measure.
    resolution: tuple[int, int] | None = None

    def elapsed(self, now: float | None = None) -> float:
        """Seconds spent in the current stage."""
        return (time.monotonic() if now is None else now) - self.stage_since


class LaunchTracker:
    """The set of accounts with a launch running, keyed by username.

    Username is the key rather than the slot because it is the only identifier that exists
    at every point this is consulted: before the spawn there is no window and no client,
    and the slot can still move underneath a launch when an unrelated client disconnects.
    """

    def __init__(self) -> None:
        self._attempts: dict[str, LaunchAttempt] = {}

    def __contains__(self, username: object) -> bool:
        return username in self._attempts

    def __len__(self) -> int:
        return len(self._attempts)

    def begin(self, usernames: list[str], resolution: tuple[int, int] | None = None) -> list[str]:
        """Open an attempt for each of *usernames* not already in flight, and return those.

        Returns the accepted subset rather than a bool so a partly-busy batch launches the
        rest of itself. A profile of four with one account already launching should bring
        up the other three; refusing the whole profile would make the guard worse than the
        double-launch it exists to prevent.
        """
        accepted = [u for u in usernames if u not in self._attempts]
        for username in accepted:
            self._attempts[username] = LaunchAttempt(username, resolution=resolution)
        return accepted

    def set_stage(self, username: str, stage: LaunchStage) -> bool:
        """Move an in-flight account to *stage*. True if that changed anything.

        An account with no attempt open is ignored rather than resurrected: stage reports
        come from the launcher, which does not know that the batch it was handed has since
        been ended by a caller's ``finally``, and a launch the user is no longer waiting on
        should not reappear on screen because a late report arrived for it.
        """
        attempt = self._attempts.get(username)
        if attempt is None or attempt.stage is stage:
            return False
        attempt.stage = stage
        attempt.stage_since = time.monotonic()
        return True

    def fail(self, username: str) -> bool:
        """Mark an in-flight account as failed, leaving its attempt open."""
        return self.set_stage(username, LaunchStage.FAILED)

    def end(self, usernames: list[str] | None = None) -> None:
        """Close the attempts for *usernames*, or every attempt when None.

        A failed attempt survives this. The ``finally`` that ends a batch runs in the same
        breath as the failure that set it, so closing it there would take the explanation
        off screen in the tick it appeared -- and a launch that gave up quietly has nothing
        else to say it: no exception, no message box, and no window to look at. It stays
        until ``dismiss``, which is what the user clicking it does.
        """
        if usernames is None:
            self._attempts = {u: a for u, a in self._attempts.items() if a.stage.is_terminal}
            return
        for username in usernames:
            attempt = self._attempts.get(username)
            if attempt is not None and not attempt.stage.is_terminal:
                del self._attempts[username]

    def dismiss(self, username: str) -> bool:
        """Close one attempt whatever stage it is in. True if there was one to close."""
        return self._attempts.pop(username, None) is not None

    def failed(self) -> list[str]:
        """Every account whose attempt is sitting in a terminal stage."""
        return [u for u, a in self._attempts.items() if a.stage.is_terminal]

    def attempt(self, username: str) -> LaunchAttempt | None:
        """The open attempt for *username*, or None."""
        return self._attempts.get(username)

    def in_flight(self) -> dict[str, LaunchAttempt]:
        """Every open attempt, as a copy safe to iterate while the launch mutates it."""
        return dict(self._attempts)
