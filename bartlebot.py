import asyncio
import sys

from bartleby.shared.keyring_backend import use_windows_backend
from bartleby.shared.product import Product, use

from bartlebot.packaging.install_hooks import run_velopack
from bartlebot.packaging.product import product


def _self_check() -> int:
    from bartlebot.packaging import self_check  # noqa: PLC0415

    return self_check.main()


def _wiki_browser() -> int:
    from bartlebot.game import wiki_browser  # noqa: PLC0415

    return wiki_browser.main(sys.argv[1:])


def _start_logging(running: Product) -> None:
    """The session log and the excepthook, before the controller (``tests/test_startup.py`` runs this)."""
    from bartleby.qt.app.logging_setup import configure_logging, install_excepthook  # noqa: PLC0415

    from bartlebot.packaging.product import HEADER_LIBRARIES  # noqa: PLC0415
    from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415

    # The settings are read once the session file is open: they log as they load.
    configure_logging(running, SettingsManager.instance, libraries=HEADER_LIBRARIES)
    # Before the controller: PyQt6 aborts on an exception escaping a slot without one.
    install_excepthook()


if __name__ == "__main__":
    # First of all: a launch that is one of Velopack's install, update or uninstall hooks
    # runs it and exits here, inside its time limit. A checkout returns at once.
    run_velopack()
    # Which app this is, for every part of bartleby-core that says so.
    running = product()
    use(running)
    # Before anything reads a password, and before the self-check reports on it.
    use_windows_backend()
    if "--self-check" in sys.argv[1:]:
        sys.exit(_self_check())
    # A drop import's window for a page Cloudflare challenges (#278). Before the
    # single-instance check, which would hand the URL to the running app and exit.
    if "--wiki-browser" in sys.argv[1:]:
        sys.exit(_wiki_browser())

    from bartleby.qt.app.single_instance import SingleInstance
    from bartleby.qt.debug.qt_paint_debug import install as install_qt_diagnostics
    from bartleby.qt.native_widgets import keep_siblings_alien
    from PyQt6.QtWidgets import QApplication

    install_qt_diagnostics()
    keep_siblings_alien()
    app = QApplication(sys.argv)
    # One BartleBot per user: a later launch, such as a double-clicked share file, hands
    # its arguments to the running app and exits (#27). Before the log is started, so a
    # launch that only hands over neither writes a session log nor prunes a real one.
    instance = SingleInstance(running)
    if not instance.claim(sys.argv[1:]):
        sys.exit(0)

    import qasync

    from bartlebot.app_controller import AppController

    _start_logging(running)

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    with loop:
        controller = AppController(app)
        controller.listen_for_launches(instance)
        loop.run_until_complete(controller.run())

    from bartleby.qt.app.app_exit import exit_skipping_teardown

    # Python's own teardown frees the Qt wrappers in an order that now and then crashes in
    # sip after the app has quit cleanly. Nothing in it is needed, so it is skipped.
    exit_skipping_teardown(0)
