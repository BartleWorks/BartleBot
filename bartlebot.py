import asyncio
import sys

import qasync
from PyQt6.QtWidgets import QApplication

from bartlebot.app_controller import AppController
from bartlebot.logging_setup import configure_logging

if __name__ == "__main__":
    configure_logging()
    app = QApplication(sys.argv)

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    with loop:
        controller = AppController(app)
        loop.run_until_complete(controller.run())
