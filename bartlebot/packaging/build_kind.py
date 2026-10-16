"""Whether this process is a packaged build or a checkout, said once at startup.

A log gets pasted into a bug report without saying where the app came from, and a
frozen build fails in ways a checkout never does (``docs/RELEASING.md``). So the
session log's header says which this is. PyInstaller sets ``sys.frozen`` in the
bootloader; a checkout run never has it.
"""

from __future__ import annotations

import sys

PACKAGED = "packaged (PyInstaller)"
CHECKOUT = "checkout"


def is_packaged() -> bool:
    """Whether this process runs from a packaged build rather than from a checkout."""
    return bool(getattr(sys, "frozen", False))


def build_kind() -> str:
    """:data:`PACKAGED` or :data:`CHECKOUT`, for the log's header. Never a path: logs get shared."""
    return PACKAGED if is_packaged() else CHECKOUT
