"""Export the logo's raster files as BartleBot, for the places outside the app (#26).

Core's ``bartleby.qt.logo.export`` holds what it writes, and why.

    uv run python tools/export_logo.py OUT_DIR
"""

from __future__ import annotations

import sys

from bartleby.qt.logo.export import main
from bartleby.shared.product import use

from bartlebot.packaging.product import product

if __name__ == "__main__":
    use(product())
    sys.exit(main())
