import logging
import logging.handlers
import platform
import sys
from collections.abc import Callable
from pathlib import Path

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
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "bartlebot.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(_FORMATTER)
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
