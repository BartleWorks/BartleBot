import datetime
import logging
import logging.handlers
import platform
import sys
from collections.abc import Callable

from bartlebot.paths import project_root

_FORMATTER = logging.Formatter(
    "[%(asctime)s] [%(levelname)-7s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


class _GuiBridgeHandler(logging.Handler):
    """Forwards formatted log records to registered GUI callbacks."""

    def __init__(self) -> None:
        super().__init__()
        self._callbacks: list[Callable[[str, int], None]] = []

    def add_callback(self, cb: Callable[[str, int], None]) -> None:
        self._callbacks.append(cb)

    def remove_callback(self, cb: Callable[[str, int], None]) -> None:
        self._callbacks = [c for c in self._callbacks if c is not cb]

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        for cb in list(self._callbacks):
            try:
                cb(msg, record.levelno)
            except Exception:
                pass  # never let GUI errors silence the logger


_gui_bridge = _GuiBridgeHandler()


def configure_logging() -> None:
    """Call once at startup before any other bartlebot imports."""
    log_dir = project_root() / "logs"
    log_dir.mkdir(exist_ok=True)

    session_name = datetime.datetime.now().strftime("bartlebot_%Y-%m-%d_%H-%M-%S.log")
    file_handler = logging.FileHandler(log_dir / session_name, encoding="utf-8")
    file_handler.setFormatter(_FORMATTER)

    # Prune old session logs — keep only the 10 most recent.
    old_logs = sorted(log_dir.glob("bartlebot_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in old_logs[10:]:
        try:
            old.unlink()
        except OSError:
            pass
    _gui_bridge.setFormatter(_FORMATTER)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(_gui_bridge)

    # Suppress chatty third-party debug output
    logging.getLogger("qasync").setLevel(logging.WARNING)
    logging.getLogger("transitions").setLevel(logging.WARNING)
    logging.getLogger("wizwalker").setLevel(logging.INFO)

    logger = logging.getLogger(__name__)
    logger.info("=== BartleBot startup ===")
    logger.info("Python %s", sys.version.split()[0])
    logger.info("Platform: %s", platform.platform())
    for lib in ("PyQt6", "qasync", "transitions", "wizwalker"):
        try:
            import importlib.metadata

            logger.info("%s==%s", lib, importlib.metadata.version(lib))
        except Exception:
            pass


def add_gui_callback(cb: Callable[[str, int], None]) -> None:
    _gui_bridge.add_callback(cb)


def remove_gui_callback(cb: Callable[[str, int], None]) -> None:
    _gui_bridge.remove_callback(cb)
