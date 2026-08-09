"""The single source of truth for "where is the project root".

Several places used to derive this independently — some from ``Path.cwd()``
(silently wrong unless launched from the project root), some from
``Path(__file__).parent.parent`` (correct, but duplicated per call site with no
guarantee the two stay in agreement).  ``project_root()`` replaces both.
"""

from __future__ import annotations

from pathlib import Path


def project_root() -> Path:
    """Return the BartleBot project root, independent of the current working directory."""
    return Path(__file__).resolve().parent.parent
