import asyncio
import sys

import qasync
from PyQt6.QtWidgets import QApplication

from bartlebot.app_controller import AppController
from bartlebot.gui.native_widgets import keep_siblings_alien
from bartlebot.logging_setup import configure_logging
from bartlebot.qt_paint_debug import install as install_qt_diagnostics

if __name__ == "__main__":
    configure_logging()
    install_qt_diagnostics()
    keep_siblings_alien()
    app = QApplication(sys.argv)

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    with loop:
        controller = AppController(app)
        loop.run_until_complete(controller.run())
