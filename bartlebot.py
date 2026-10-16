import asyncio
import sys

from bartlebot.packaging.install_hooks import run_velopack
from bartlebot.shared.keyring_backend import use_windows_backend


def _self_check() -> int:
    from bartlebot.packaging import self_check  # noqa: PLC0415

    return self_check.main()


if __name__ == "__main__":
    # First of all: a launch that is one of Velopack's install, update or uninstall hooks
    # runs it and exits here, inside its time limit. A checkout returns at once.
    run_velopack()
    # Before anything reads a password, and before the self-check reports on it.
    use_windows_backend()
    if "--self-check" in sys.argv[1:]:
        sys.exit(_self_check())

    import qasync
    from PyQt6.QtWidgets import QApplication

    from bartlebot.app_controller import AppController
    from bartlebot.gui.debug.qt_paint_debug import install as install_qt_diagnostics
    from bartlebot.gui.native_widgets import keep_siblings_alien
    from bartlebot.logging_setup import configure_logging

    configure_logging()
    install_qt_diagnostics()
    keep_siblings_alien()
    app = QApplication(sys.argv)

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    with loop:
        controller = AppController(app)
        loop.run_until_complete(controller.run())
