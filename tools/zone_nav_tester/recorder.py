"""Turn a stream of client samples into recorded zone transitions, with the kind detected.

Pure: the tester polls the client and feeds ``Sample``s in; nothing here touches a client, so
every rule below is covered by tests/test_zone_nav_recorder.py.

HOW A KIND IS DETECTED
    Everything is decided from the samples taken *before* the loading screen started. During a
    load the UI is gone, so the hub button reads disabled and the prompt reads absent for every
    kind alike.

    list    the zone-list window was open
    hub     the hub button was enabled, then disabled just before loading (clicking it starts
            the cooldown; a button already cooling down never reads enabled, so it cannot
            false-positive). Not trusted at all while the speedhack was on for that last
            sample: the game's own pre-load HUD hide reads as this exact pattern under the
            speedhack (confirmed live: 9 straight enabled reads then one disabled read, on
            every plain walk, only once the speedhack was switched on) even though no button
            was ever clicked.
    object  the interact prompt was showing
    walk    none of those
    ride    an object or walk transition into a cutscene zone -- the dragon is boarded by
            interacting, the airships through a gate -- then out of it again without the player
            walking anywhere. Only zones the caller says could be one are held back for this, so
            a transition into a mapped zone is reported at once. The Krokotopia boats ride
            inside the zone they start in and keep the hub button disabled throughout, so their
            skip reads as a hub button use: they are added by hand.

WHICH COORDINATES
    trigger_xyz     the last position before loading: a walk's xyz
    prompt_xyz      the last position with the prompt showing: an object's interact_xyz
    pre_prompt_xyz  the last position before the prompt appeared that is PROMPT_CLEAR_DISTANCE
                    from the object: somewhere it does not show. None if there was no such spot
    far_xyz         the last position at least RESET_DISTANCE from the trigger: a walk's
                    fallback reset, when no arrival point is known
    arrival_xyz     where the player landed

A LIST OBJECT WITHOUT A ZONE CHANGE
    ListWatcher reports each opening of a list window, zone change or not: where the player
    stood when it opened is the object's interact position, and the same pre-prompt rule
    as above gives its reset. It is separate from Recorder because a list object belongs to
    one zone -- recording it should never need the player to leave, and a list use that
    does leave is still a Transition as before.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace

XYZ = tuple[float, float, float]

WALK, OBJECT, LIST, HUB, RIDE = "walk", "object", "list", "hub", "ride"

SIGNAL_WINDOW = 2.0
"""Seconds before a loading screen in which a prompt, the list window or a button click counts."""
RESET_DISTANCE = 300.0
PROMPT_CLEAR_DISTANCE = 150.0
"""An object's reset must be at least this far from where its prompt showed."""
RIDE_MAX_MOVE = 150.0
"""Further than this from the arrival point, and the player walked: not a cutscene zone."""
HISTORY_SECONDS = 30.0


@dataclass(frozen=True)
class Sample:
    t: float
    zone: str | None
    xyz: XYZ
    loading: bool = False
    prompt: bool = False
    list_open: bool = False
    world_gate: bool = False
    """The open list window is a World Gate's, not a zone list's. Meaningless unless list_open."""
    hub_enabled: bool = False
    teleport_cooldown: float | None = 0.0
    """Seconds left on the hub/HOME cooldown; None if the buttons are disabled with no timer to read."""
    speed_boosted: bool = False


@dataclass(frozen=True)
class Transition:
    kind: str
    from_zone: str
    to_zone: str
    trigger_xyz: XYZ
    prompt_xyz: XYZ | None
    pre_prompt_xyz: XYZ | None
    far_xyz: XYZ | None
    arrival_xyz: XYZ
    ride_zone: str | None = None
    board: str | None = None
    """For a ride, how it was boarded: OBJECT or WALK."""


def distance(a: XYZ, b: XYZ) -> float:
    return math.dist(a, b)


class Recorder:
    """Feed samples in order; ``feed`` returns the transitions each sample completed, usually none."""

    def __init__(self, may_be_ride_zone: Callable[[str], bool] = lambda zone: False) -> None:
        self._may_be_ride_zone = may_be_ride_zone
        self._zone: str | None = None
        self._history: deque[Sample] = deque()
        self._departure: list[Sample] | None = None  # the history as it stood when loading began
        self._held: Transition | None = None  # arrived in a possible ride zone; waiting to see

    def reset(self) -> None:
        """Forget everything, e.g. after the tester moved the client itself."""
        self.__init__(self._may_be_ride_zone)

    def feed(self, sample: Sample) -> list[Transition]:
        if sample.loading:
            if self._departure is None:
                self._departure = list(self._history)
            return []
        if not sample.zone:
            return []

        if self._zone is None:
            self._zone = sample.zone
        if sample.zone == self._zone:
            self._departure = None  # a load that ended where it began: not a transition
            self._remember(sample)
            return self._release_if_walked(sample)

        before = self._departure if self._departure is not None else list(self._history)
        self._departure = None
        came_from, self._zone = self._zone, sample.zone
        self._history.clear()
        self._remember(sample)
        if not before:
            return []
        return self._arrived(_classify(before, came_from, sample))

    def _remember(self, sample: Sample) -> None:
        self._history.append(sample)
        while self._history and sample.t - self._history[0].t > HISTORY_SECONDS:
            self._history.popleft()

    def _arrived(self, transition: Transition) -> list[Transition]:
        held, self._held = self._held, None
        if held is not None and transition.from_zone == held.to_zone:
            # Out of the cutscene zone without walking: one ride, from the zone before it.
            return [
                replace(
                    held,
                    kind=RIDE,
                    to_zone=transition.to_zone,
                    arrival_xyz=transition.arrival_xyz,
                    ride_zone=held.to_zone,
                    board=held.kind,
                )
            ]
        done = [held] if held is not None else []
        # A ride with a cutscene zone starts at an interaction (the dragon) or a gate (the airships).
        if transition.kind in (OBJECT, WALK) and self._may_be_ride_zone(transition.to_zone):
            self._held = transition
            return done
        return [*done, transition]

    def _release_if_walked(self, sample: Sample) -> list[Transition]:
        if self._held is not None and distance(sample.xyz, self._held.arrival_xyz) > RIDE_MAX_MOVE:
            held, self._held = self._held, None
            return [held]
        return []


def _classify(before: list[Sample], came_from: str, arrival: Sample) -> Transition:
    last = before[-1]
    recent = [s for s in before if last.t - s.t <= SIGNAL_WINDOW]

    if any(s.list_open for s in recent):
        kind = LIST
    elif not last.hub_enabled and not last.speed_boosted and any(s.hub_enabled for s in recent):
        kind = HUB
    elif any(s.prompt for s in recent):
        kind = OBJECT
    else:
        kind = WALK

    prompt_xyz, pre_prompt_xyz = _prompt_points(before)
    far = next((s.xyz for s in reversed(before) if distance(s.xyz, last.xyz) >= RESET_DISTANCE), None)
    return Transition(kind, came_from, arrival.zone, last.xyz, prompt_xyz, pre_prompt_xyz, far, arrival.xyz)


def _prompt_points(samples: list[Sample]) -> tuple[XYZ | None, XYZ | None]:
    """(the last position with the prompt showing, a spot clear of it before that run), or Nones."""
    prompted = [i for i, s in enumerate(samples) if s.prompt]
    if not prompted:
        return None, None
    prompt_xyz = samples[prompted[-1]].xyz
    # The last spot before the final run of prompt samples that is clearly away from the
    # object. Just "the sample before the prompt" is not enough: a player standing still
    # while the prompt comes up is already where it shows.
    start = prompted[-1]
    while start > 0 and samples[start - 1].prompt:
        start -= 1
    pre_prompt_xyz = next(
        (
            s.xyz
            for s in reversed(samples[:start])
            if not s.prompt and distance(s.xyz, prompt_xyz) >= PROMPT_CLEAR_DISTANCE
        ),
        None,
    )
    return prompt_xyz, pre_prompt_xyz


@dataclass(frozen=True)
class ListObject:
    """A list window opened in `zone`: where its object is, and a reset clear of its prompt."""

    zone: str
    interact_xyz: XYZ
    reset_xyz: XYZ | None
    """None if the player was never PROMPT_CLEAR_DISTANCE from the object without the prompt."""
    world_gate: bool


class ListWatcher:
    """Feed samples in order; ``feed`` returns a ListObject the moment a list window opens."""

    def __init__(self) -> None:
        self._zone: str | None = None
        self._history: deque[Sample] = deque()
        self._was_open = False

    def reset(self) -> None:
        """Forget everything, e.g. after the tester moved the client itself."""
        self.__init__()

    def feed(self, sample: Sample) -> ListObject | None:
        if sample.loading or not sample.zone:
            # _was_open carries through the load: a list window still up on arrival is the
            # one just used, left behind, not a new list opened in the new zone.
            return None
        if sample.zone != self._zone:
            self._zone = sample.zone
            self._history.clear()
        opened = sample.list_open and not self._was_open
        self._was_open = sample.list_open
        # The opening sample is at the object by definition, whether or not a poll caught the
        # prompt before the window covered it, so it counts as the last prompted position.
        self._history.append(replace(sample, prompt=True) if opened else sample)
        while sample.t - self._history[0].t > HISTORY_SECONDS:
            self._history.popleft()
        if not opened:
            return None
        _, reset_xyz = _prompt_points(list(self._history))
        return ListObject(sample.zone, sample.xyz, reset_xyz, sample.world_gate)
