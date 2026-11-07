"""A run as the canvas sees it: idle, running or paused, the node it is in, and for how long.

``BotCanvas`` drives it from the controller's calls (``set_running``, ``set_paused``,
``set_idle``, ``set_state``) and draws what it says: the Run/Pause button, the lit
node, and Bar B's status line. While a run goes it ticks once a second, so the elapsed
time on that line moves.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from bartleby.qt.theming.theme import THEME
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

IDLE = "idle"
RUNNING = "running"
PAUSED = "paused"

_STATUS = {IDLE: "Idle", RUNNING: "Running", PAUSED: "Paused"}


def status_dot_color(state: str) -> str:
    """The status dot's color for a run in *state*."""
    if state == RUNNING:
        return THEME.success
    if state == PAUSED:
        return THEME.status_paused
    return THEME.text_muted  # idle / fallback


def elapsed_text(seconds: float | None) -> str:
    """``HH:MM:SS`` for *seconds*, and all zeros before a run has started."""
    if seconds is None:
        return "00:00:00"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


class RunStatus(QObject):
    """The run's state, the node it is in, and its clock. Draws nothing: the canvas does."""

    #: Once a second while a run goes: the elapsed time has moved.
    ticked = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._state = IDLE
        self._start_time: float | None = None
        self._active_state: str | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.ticked)

    @property
    def state(self) -> str:
        """``IDLE``, ``RUNNING`` or ``PAUSED``."""
        return self._state

    @property
    def is_idle(self) -> bool:
        return self._state == IDLE

    @property
    def active_state(self) -> str | None:
        """The id of the state the run is in, or None before it enters one and once it stops."""
        return self._active_state

    def start(self) -> None:
        """A run began: the clock starts from zero."""
        self._state = RUNNING
        self._start_time = time.monotonic()
        self._timer.start()

    def pause(self) -> None:
        self._state = PAUSED

    def resume(self) -> None:
        self._state = RUNNING

    def stop(self) -> str | None:
        """The run ended. Returns the state it was in, for the canvas to dim."""
        self._state = IDLE
        self._timer.stop()
        left, self._active_state = self._active_state, None
        return left

    def enter(self, state_id: str) -> str | None:
        """The run entered *state_id*. Returns the state it left, for the canvas to dim."""
        left, self._active_state = self._active_state, state_id
        return left

    def forget_active(self) -> None:
        """Another bot was loaded: the state the run was in is not one of its."""
        self._active_state = None

    def shutdown(self) -> None:
        """Stop the clock for good: the canvas is being torn down."""
        self._timer.stop()

    def elapsed_text(self) -> str:
        """How long since the run started, as ``HH:MM:SS``."""
        return elapsed_text(None if self._start_time is None else time.monotonic() - self._start_time)

    def describe(self, label_of: Callable[[str], str]) -> tuple[str, str, str, str]:
        """``(dot color, status, state label, elapsed)`` for Bar B's status line.

        *label_of* turns the active state's id into what the line calls it. Idle says
        neither where nor how long.
        """
        color = status_dot_color(self._state)
        if self._state == IDLE:
            return color, _STATUS[IDLE], "", ""
        label = label_of(self._active_state) if self._active_state else ""
        return color, _STATUS[self._state], label, self.elapsed_text()
