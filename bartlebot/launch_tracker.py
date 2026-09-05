"""Which accounts have a launch running right now.

WHY THIS IS NOT DERIVED FROM ``_client_order`` (#186)

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

__all__ = ["LaunchAttempt", "LaunchTracker"]


@dataclass
class LaunchAttempt:
    """One account's in-flight launch."""

    username: str
    started_at: float = field(default_factory=time.monotonic)


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

    def begin(self, usernames: list[str]) -> list[str]:
        """Open an attempt for each of *usernames* not already in flight, and return those.

        Returns the accepted subset rather than a bool so a partly-busy batch launches the
        rest of itself. A profile of four with one account already launching should bring
        up the other three; refusing the whole profile would make the guard worse than the
        double-launch it exists to prevent.
        """
        accepted = [u for u in usernames if u not in self._attempts]
        for username in accepted:
            self._attempts[username] = LaunchAttempt(username)
        return accepted

    def end(self, usernames: list[str] | None = None) -> None:
        """Close the attempts for *usernames*, or every attempt when None."""
        if usernames is None:
            self._attempts.clear()
            return
        for username in usernames:
            self._attempts.pop(username, None)

    def attempt(self, username: str) -> LaunchAttempt | None:
        """The open attempt for *username*, or None."""
        return self._attempts.get(username)

    def in_flight(self) -> dict[str, LaunchAttempt]:
        """Every open attempt, as a copy safe to iterate while the launch mutates it."""
        return dict(self._attempts)
