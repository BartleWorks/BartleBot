"""One BartleBot per user: a later launch hands its arguments to the running app and exits (#27).

A double-clicked share file starts the exe with the file as its argument. Without this,
each double-click would open a second app, with its own clients and its own window. So
the first launch listens on a ``QLocalServer``, and every later one connects, sends its
arguments and exits before building anything. The running app raises its window and
imports each share file it was handed, as it does a file named at its own launch.

**The name** is the vendor, the product and the user, so two people signed in to one
machine each have their own (:func:`server_name`). The user is hashed, since a pipe name
can be listed by anyone on the machine. A checkout gets a name of its own, and another
under ``BARTLEBOT_DEV``, so a developer's double-click never reaches the installed app
and the installed app never swallows a checkout's launch.

This keeps one *window* per user. It does not keep two processes off one game client:
a checkout, BartleBuddy or another tool can still hook it. That is #233.
"""

from __future__ import annotations

import ctypes
import getpass
import hashlib
import json
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from bartleby.shared.vendor import PRODUCT, VENDOR
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QWidget

from bartlebot.content.store_roots import dev_mode
from bartlebot.packaging.build_kind import is_packaged

logger = logging.getLogger(__name__)

#: How long a later launch waits to reach the running app before deciding there is none.
CONNECT_TIMEOUT_MS = 1000
#: How long it waits for its arguments to be taken.
SEND_TIMEOUT_MS = 2000
#: How often handed-over arguments look again for the app to be free of a modal dialog.
BUSY_RETRY_MS = 250
#: ``AllowSetForegroundWindow``'s "any process": lets the running app take the foreground.
_ASFW_ANY = -1


def server_name(*, packaged: bool | None = None, dev: bool | None = None, user: str | None = None) -> str:
    """The name this launch looks for and listens on: ``Bartleby.BartleBot.<user hash>``, with a suffix for a checkout."""
    packaged = is_packaged() if packaged is None else packaged
    dev = dev_mode() if dev is None else dev
    user = getpass.getuser() if user is None else user
    name = f"{VENDOR}.{PRODUCT}.{hashlib.sha256(user.encode('utf-8')).hexdigest()[:16]}"
    if not packaged:
        name += ".checkout"
    if dev:
        name += ".dev"
    return name


def absolute_arguments(args: Sequence[str]) -> list[str]:
    """``args`` with every one naming a file made absolute, since the running app has its own working folder."""
    return [str(Path(a).resolve()) if Path(a).exists() else a for a in args]


class SingleInstance(QObject):
    """The first launch's server, or a later launch's handoff to it."""

    #: A later launch's arguments, its exe left off and its paths absolute.
    arguments_received = pyqtSignal(list)

    def __init__(self, name: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._name = name or server_name()
        self._server: QLocalServer | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_listening(self) -> bool:
        return self._server is not None and self._server.isListening()

    def claim(self, args: Sequence[str]) -> bool:
        """True when this launch is the one to run; False when it handed ``args`` to the one running.

        Losing a race to another launch starting at the same moment is a handoff too. A
        server that cannot listen at all leaves this launch running alone, with a warning.
        """
        if self._hand_over(args):
            return False
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if server.listen(self._name):
            server.newConnection.connect(self._accept)
            self._server = server
            return True
        if self._hand_over(args):
            return False
        logger.warning("Could not listen for later launches (%s); this one runs alone", server.errorString())
        return True

    def close(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None

    def _hand_over(self, args: Sequence[str]) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(self._name)
        if not socket.waitForConnected(CONNECT_TIMEOUT_MS):
            return False
        if sys.platform == "win32":
            # Windows gives the foreground only to the process the user acted on, which is
            # this one; this lets the running app raise its window instead.
            ctypes.windll.user32.AllowSetForegroundWindow(_ASFW_ANY)
        socket.write(json.dumps(absolute_arguments(args)).encode("utf-8"))
        sent = socket.waitForBytesWritten(SEND_TIMEOUT_MS)
        socket.disconnectFromServer()
        if socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
            socket.waitForDisconnected(SEND_TIMEOUT_MS)
        if not sent:
            logger.warning("The running BartleBot did not take this launch's arguments")
        return True

    def _accept(self) -> None:
        assert self._server is not None
        while (socket := self._server.nextPendingConnection()) is not None:
            received = bytearray()
            socket.readyRead.connect(lambda s=socket, buf=received: buf.extend(s.readAll().data()))
            socket.disconnected.connect(lambda s=socket, buf=received: self._finish(s, buf))

    def _finish(self, socket: QLocalSocket, received: bytearray) -> None:
        received.extend(socket.readAll().data())
        socket.deleteLater()
        try:
            args = json.loads(received.decode("utf-8"))
        except ValueError:
            logger.warning("A later launch sent something unreadable; ignored")
            return
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            logger.warning("A later launch sent something other than its arguments; ignored")
            return
        logger.info("A later launch handed over %d argument(s)", len(args))
        self.arguments_received.emit(args)


class LaunchQueue(QObject):
    """Handed-over arguments, released once the app is free of a modal dialog.

    An import opens its summary modally, so a file double-clicked while setup, another
    import's summary or any other dialog is open waits for it to close, and is never
    stacked on top.
    """

    #: The arguments of every launch handed over since the last release, in order.
    ready = pyqtSignal(list)

    def __init__(self, busy: Callable[[], bool], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._busy = busy
        self._pending: list[str] = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._release)

    def put(self, args: Sequence[str]) -> None:
        self._pending.extend(args)
        if not self._timer.isActive():
            self._timer.start(0)

    def _release(self) -> None:
        if not self._pending:
            return
        if self._busy():
            self._timer.start(BUSY_RETRY_MS)
            return
        args, self._pending = self._pending, []
        self.ready.emit(args)


def raise_window(window: QWidget) -> None:
    """Bring ``window`` to the front, restoring it when it is minimized."""
    if window.isMinimized():
        window.showNormal()
    window.raise_()
    window.activateWindow()
