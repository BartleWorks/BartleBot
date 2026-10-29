"""BartleBot as a :class:`~bartleby.shared.product.Product`: what bartleby-core is told about the running app.

Core names no product (Stage 5 of #167), so the session log's header and file names, the
single-instance pipe, the shutdown line and every message core shows read BartleBot's name
from here. :data:`NAME` is the constant for BartleBot's own code; :func:`product` is the
whole value, built at startup, since its developer mode, build kind and version are read
from this process.
"""

from __future__ import annotations

from pathlib import Path

from bartleby.shared.product import Product

#: As the user sees it, and the folder under ``%APPDATA%/Bartleby/``.
NAME = "BartleBot"

#: BartleBot's accent in the light theme and in the dark, the themes' own defaults.
ACCENT = "#3366bb"
ACCENT_DARK = "#4488cc"

#: The logo's SVG, which ``bartlebot.gui.logo`` draws every raster from.
LOGO_SVG = Path(__file__).resolve().parents[1] / "gui" / "logo" / "bartlebot.svg"

#: Libraries whose versions the session log's header names, beside BartleBot's own. A
#: packaged build carries their metadata only because ``tools/build_package.py`` copies it.
HEADER_LIBRARIES = ("PyQt6", "qasync", "transitions", "wizwalker")


def product() -> Product:
    """BartleBot, as this process runs it: its version, whether it is a build, and developer mode.

    In developer mode its session logs go in the checkout's ``logs/``, as every store's user
    root does (``content/store_roots.py``); otherwise in ``%APPDATA%/Bartleby/BartleBot/logs``.
    """
    from bartlebot.content.store_roots import dev_mode  # noqa: PLC0415 - kept off this module's import path
    from bartlebot.packaging.build_kind import build_kind  # noqa: PLC0415
    from bartlebot.paths import project_root  # noqa: PLC0415
    from bartlebot.updates.check import current_version  # noqa: PLC0415

    dev = dev_mode()
    return Product(
        name=NAME,
        version=current_version(),
        accent=ACCENT,
        accent_dark=ACCENT_DARK,
        logo=LOGO_SVG,
        dev_mode=dev,
        build_kind=build_kind(),
        log_root=project_root() / "logs" if dev else None,
    )
