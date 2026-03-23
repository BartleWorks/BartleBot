import sys
import asyncio

from PyQt6.QtWidgets import QApplication
import qasync

from bartlebot.logging_setup import configure_logging
from bartlebot.app_controller import AppController


if __name__ == "__main__":
    configure_logging()
    app = QApplication(sys.argv)

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    with loop:
        controller = AppController(app)
        loop.run_until_complete(controller.run())
