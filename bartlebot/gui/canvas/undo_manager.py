"""Snapshot-based undo/redo manager for the bot canvas editor.

UndoManager stores deep copies of BotConfig (via model_dump/model_validate) before
each edit. Structural edits push immediately; param/label edits are debounced 500ms.
"""

import logging
from collections import deque

from PyQt6.QtCore import QTimer

from bartlebot.bot.schema import BotConfig

logger = logging.getLogger(__name__)

_MAX_STEPS = 50


class _Snapshot:
    __slots__ = ("data", "desc")

    def __init__(self, config: BotConfig, desc: str) -> None:
        self.data: dict = config.model_dump()
        self.desc: str = desc


class UndoManager:
    """Manages undo/redo history for the canvas editor using config snapshots.

    Call push() BEFORE a structural edit mutates the config.
    Call begin_debounce() on each keystroke in a param edit sequence, also BEFORE
    the keystroke's edit lands: its first call is the burst's before-snapshot.
    Call flush_debounce() before undo or redo, so an edit made moments ago is the
    step they act on.
    Call cancel_debounce() when selection changes away.
    """

    def __init__(self) -> None:
        self._undo: deque[_Snapshot] = deque(maxlen=_MAX_STEPS)
        self._redo: deque[_Snapshot] = deque(maxlen=_MAX_STEPS)
        self._enabled: bool = True
        self._pending: _Snapshot | None = None  # debounce "before" snapshot

        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._commit_debounce)

    # ------------------------------------------------------------------
    # Structural edits — immediate snapshot
    # ------------------------------------------------------------------

    def push(self, config: BotConfig, desc: str) -> None:
        """Capture a before-snapshot. Call immediately before mutating config."""
        if not self._enabled:
            return
        self._undo.append(_Snapshot(config, desc))
        self._redo.clear()
        logger.debug("Undo push: %s (depth=%d)", desc, len(self._undo))

    # ------------------------------------------------------------------
    # Param/label edits — debounced snapshot
    # ------------------------------------------------------------------

    def begin_debounce(self, config: BotConfig, desc: str) -> None:
        """Call on each properties_changed emission during a typing sequence.

        Captures the before-snapshot only on the first call, then restarts
        the 500ms timer. The snapshot is committed after the timer fires.
        """
        if not self._enabled:
            return
        if self._pending is None:
            self._pending = _Snapshot(config, desc)
        self._timer.start()

    def _commit_debounce(self) -> None:
        if self._pending is not None:
            self._undo.append(self._pending)
            self._redo.clear()
            logger.debug("Undo commit debounce: %s (depth=%d)", self._pending.desc, len(self._undo))
            self._pending = None

    def flush_debounce(self) -> None:
        """Commit the pending debounce snapshot now, without waiting for the timer."""
        self._timer.stop()
        self._commit_debounce()

    def cancel_debounce(self) -> None:
        """Discard the pending debounce snapshot without committing it."""
        self._timer.stop()
        self._pending = None

    # ------------------------------------------------------------------
    # Undo / Redo
    # ------------------------------------------------------------------

    def pop_undo(self, current: BotConfig) -> BotConfig | None:
        """Return the config to restore, or None if the stack is empty.

        Pushes current state onto the redo stack before returning.
        """
        if not self._undo:
            return None
        snap = self._undo.pop()
        self._redo.append(_Snapshot(current, snap.desc))
        logger.debug("Undo: %s (remaining=%d)", snap.desc, len(self._undo))
        return BotConfig.model_validate(snap.data)

    def pop_redo(self, current: BotConfig) -> BotConfig | None:
        """Return the config to restore, or None if the stack is empty."""
        if not self._redo:
            return None
        snap = self._redo.pop()
        self._undo.append(_Snapshot(current, snap.desc))
        logger.debug("Redo: %s (remaining=%d)", snap.desc, len(self._redo))
        return BotConfig.model_validate(snap.data)

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_desc(self) -> str | None:
        return self._undo[-1].desc if self._undo else None

    def redo_desc(self) -> str | None:
        return self._redo[-1].desc if self._redo else None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def clear(self) -> None:
        """Clear both stacks. Call when a new bot is opened or closed."""
        self._undo.clear()
        self._redo.clear()
        self.cancel_debounce()
