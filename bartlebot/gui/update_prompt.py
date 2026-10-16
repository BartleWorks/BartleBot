"""When the update popup opens, and what each of its choices leaves behind.

One check at startup, in an installed build with the setting on, run in a worker thread
after the window is up, so it never delays it (``updates/check.py``). Help › Check for
Updates… runs the same check whatever the setting.

**The popup opens once per version.** A version newer than ``updates.last_offered`` opens
it, and opening it records the version. After *Not now* the title bar keeps a quiet
button, "Update 1.2.0", which opens it again, and the next launch shows the button rather
than the popup for the same version. A newer version opens the popup again.

**It never opens over anything.** First-run setup, a launch-time import's summary and any
other modal dialog hold it back, and so does a running bot, which an update must never
interrupt: it waits, and opens once the app is free (:class:`UpdatePrompt.busy`).

Every dialog here opens from a timer's callback, never from inside the check's task,
since a nested ``exec()`` inside a task stalls every other task (``bartlebot/gui/CLAUDE.md``,
qasync).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QMessageBox, QWidget

from bartlebot.gui.dialogs.update_dialog import Choice, UpdateDialog
from bartlebot.settings_manager import SettingsManager
from bartlebot.updates import check as checks
from bartlebot.updates.check import CheckResult, Offer, PreparedUpdate, Status

logger = logging.getLogger(__name__)

#: How often a popup held back by a dialog or a running bot looks again.
RETRY_MS = 1000

BUTTON_TEXT = "Update {version}"
CHECK_TITLE = "Check for updates"
UP_TO_DATE = "BartleBot {version} is up to date."
UNREACHABLE = "Couldn't check for updates: {reason} Check your connection, and try again later."
REFUSED = (
    "An update was found, but its signature doesn't match, so it was not offered: {reason} "
    "This can mean the download was changed on the way. Try again later."
)
PENDING = "BartleBot {version} installs when you close BartleBot."


class UpdatePrompt(QObject):
    """The startup check, the popup's timing, and the title bar's button. See the module docstring."""

    #: The title bar's update button text, or "" to hide it.
    button_changed = pyqtSignal(str)

    def __init__(
        self,
        window: QWidget,
        *,
        busy: Callable[[], bool],
        running: Callable[[], bool],
        restart: Callable[[], None],
        check: Callable[[], CheckResult] = checks.check,
        prepare: Callable[..., PreparedUpdate] = checks.prepare,
        apply: Callable[..., None] = checks.apply_on_exit,
        installed: Callable[[], bool] = checks.installed,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._window = window
        self._busy = busy
        self._running = running
        self._restart = restart
        self._check = check
        self._prepare = prepare
        self._apply = apply
        self._installed = installed
        self._task: asyncio.Task | None = None
        self._offer: Offer | None = None
        self._pending_version: str | None = None
        # What waits for the app to be free: the popup, or a manual check's answer.
        self._waiting: Callable[[], None] | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._release)

    # -- what the app reads -------------------------------------------------------------

    @property
    def offer(self) -> Offer | None:
        """The newer release the last check found, or None."""
        return self._offer

    @property
    def pending_version(self) -> str | None:
        """The version Velopack applies when the app closes, once the user chose one."""
        return self._pending_version

    @property
    def task(self) -> asyncio.Task | None:
        return self._task

    @property
    def can_check(self) -> bool:
        """Whether this build updates itself at all: only one Velopack installed."""
        return self._installed()

    # -- checking -----------------------------------------------------------------------

    def check_at_startup(self) -> bool:
        """Start the startup check, unless this is not an installed build or the setting is off. Whether it started."""
        if not self._installed():
            return False
        if not SettingsManager.instance().check_for_updates_at_startup:
            logger.info("Not checking for updates: the setting is off")
            return False
        return self._start(manual=False)

    def check_now(self) -> bool:
        """Help › Check for Updates…: the same check, whatever the setting, and an answer either way."""
        if not self._installed():
            return False
        if self._pending_version is not None:
            self._wait_for_free(lambda: self._tell(PENDING.format(version=self._pending_version)))
            return False
        return self._start(manual=True)

    def _start(self, *, manual: bool) -> bool:
        if self._task is not None and not self._task.done():
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        self._task = loop.create_task(self._run(manual))
        return True

    async def _run(self, manual: bool) -> None:
        try:
            result = await asyncio.to_thread(self._check)
        except Exception:
            logger.warning("The update check failed", exc_info=True)
            return
        self.take_result(result, manual=manual)

    def take_result(self, result: CheckResult, *, manual: bool = False) -> None:
        """What a check found: the popup, the button, or, for a manual check, an answer."""
        if result.status is Status.OFFERED and result.offer is not None:
            self._offer = result.offer
            if manual or checks.should_popup(result.offer.version, SettingsManager.instance().last_offered_update):
                self._wait_for_free(self._popup)
            else:
                self.button_changed.emit(BUTTON_TEXT.format(version=result.offer.version))
            return
        if not manual:
            return
        if result.status is Status.UP_TO_DATE:
            message = UP_TO_DATE.format(version=checks.current_version())
        elif result.status is Status.REFUSED:
            message = REFUSED.format(reason=result.reason)
        else:
            message = UNREACHABLE.format(reason=result.reason)
        self._wait_for_free(lambda: self._tell(message, warn=result.status is Status.REFUSED))

    # -- waiting for the app to be free ---------------------------------------------------

    def busy(self) -> bool:
        """Whether a dialog is open or a bot runs: either holds the popup back."""
        return self._busy() or self._running()

    def _wait_for_free(self, show: Callable[[], None]) -> None:
        self._waiting = show
        if not self._timer.isActive():
            self._timer.start(0)

    def _release(self) -> None:
        if self._waiting is None:
            return
        if self.busy():
            self._timer.start(RETRY_MS)
            return
        show, self._waiting = self._waiting, None
        show()

    # -- the popup ----------------------------------------------------------------------

    def open_offer(self) -> None:
        """The title bar's button: the popup again for the offer it names."""
        if self._offer is not None and self._pending_version is None:
            self._popup()

    def _popup(self) -> None:
        offer = self._offer
        if offer is None:
            return
        sm = SettingsManager.instance()
        if checks.should_popup(offer.version, sm.last_offered_update):
            sm.last_offered_update = offer.version
            sm.save()
        dialog = UpdateDialog(
            offer, lambda o, progress: self._prepare(o, progress=progress), running=self._running(), parent=self._window
        )
        dialog.exec()
        self.finish(dialog.choice, dialog.prepared)

    def finish(self, choice: Choice, prepared: PreparedUpdate | None) -> None:
        """What a choice in the popup leaves: a button after *Not now*, an update waiting for the app to close otherwise."""
        offer = self._offer
        if choice is Choice.LATER or prepared is None:
            if offer is not None:
                self.button_changed.emit(BUTTON_TEXT.format(version=offer.version))
            return
        try:
            self._apply(prepared, restart=choice is Choice.RESTART)
        except Exception as exc:
            logger.warning("Velopack would not take the update to %s", prepared.version, exc_info=True)
            self._tell(f"Couldn't hand the update to the installer: {exc}", warn=True)
            if offer is not None:
                self.button_changed.emit(BUTTON_TEXT.format(version=offer.version))
            return
        self._pending_version = prepared.version
        self.button_changed.emit("")
        if choice is Choice.RESTART:
            self._restart()

    def _tell(self, message: str, *, warn: bool = False) -> None:
        if warn:
            QMessageBox.warning(self._window, CHECK_TITLE, message)
        else:
            QMessageBox.information(self._window, CHECK_TITLE, message)
