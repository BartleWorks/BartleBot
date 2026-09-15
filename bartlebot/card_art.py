"""Where extracted card art lives, and how a template name finds its image.

The game ships every spell's art as a DDS texture inside its own WADs. Nothing in
this repo may carry one: the repo goes public at 1.0.0 and a committed game texture
is the same exposure a committed wizard name is (see "No real player data" in the
root ``CLAUDE.md``). So the art is *extracted by the user, on their own machine*, by
``tools/extract_card_art.py``, into the machine-local product directory -- the same
place ``settings_manager`` already writes ``autosave/``, and gitignored by
construction because it is not in the tree at all.

This module is the read side, and it is deliberately the *only* thing that knows the
cache's shape. It is Qt-free so the lookup can be tested without a ``QApplication``;
turning a path into a pixmap is ``gui/widgets/card_art_tile.py``'s job.

THE LOOKUP KEY IS THE TEMPLATE NAME
    ``deck_io`` keys a saved deck by ``SpellTemplate.name()`` -- the internal name,
    "Storm Lord - T05 - A" and not "Storm Lord" -- and the extractor reads the art
    filename off that same template's ``m_imageName``. So a deck entry finds its art
    by exact dict lookup, with no normalization and no tier parsing. Do not look art
    up by display name: one display name can cover many templates.

EVERY LOOKUP MAY RETURN NONE, AND THAT IS THE ORDINARY CASE
    Most users will never run the extractor, so ``art_path`` returning None is not an
    error and must never be logged as one. Individual cards miss too: a handful of
    templates name an image the game's own files do not contain, and the snapshot can
    be older than the game. Callers render their text form instead.
"""

from __future__ import annotations

import json
import logging
from functools import cache
from pathlib import Path

from bartlebot.shared.vendor import product_dir

logger = logging.getLogger(__name__)

#: The cache directory's name beneath the product directory.
ART_DIRNAME = "art"

#: Where the extractor writes the per-spell PNGs, relative to :func:`art_dir`.
SPELLS_DIRNAME = "spells"

#: Maps template name -> PNG filename. Written by the extractor, read here.
INDEX_FILENAME = "index.json"


def art_dir() -> Path:
    """``%APPDATA%/Bartleby/BartleBot/art`` -- the extracted art cache.

    May not exist: the extractor is run by hand, and most installs never run it.
    """
    return product_dir() / ART_DIRNAME


def index_path() -> Path:
    return art_dir() / INDEX_FILENAME


def spells_dir() -> Path:
    return art_dir() / SPELLS_DIRNAME


@cache
def _index() -> dict[str, str]:
    """Template name -> PNG filename, or empty when no art has been extracted.

    Cached because it is read once per card per redraw and the file only changes when
    the extractor runs -- which is a manual act, after which :func:`forget_cache`
    is what makes the new file visible. A missing file is the common case and says
    nothing; an unreadable one is worth a line in the log, since the user did run the
    extractor and is entitled to know why they see no art.
    """
    path = index_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("could not read extracted card art index at %s", path)
        return {}
    images = raw.get("images")
    if not isinstance(images, dict):
        logger.warning("card art index at %s has no usable 'images' map", path)
        return {}
    return images


def forget_cache() -> None:
    """Drop the cached index, so the next lookup re-reads it from disk.

    For the extractor finishing inside a running app, and for tests pointing the
    product directory somewhere else.
    """
    _index.cache_clear()


def is_available() -> bool:
    """Whether any card art has been extracted on this machine."""
    return bool(_index())


def art_path(template_name: str) -> Path | None:
    """The PNG for *template_name*, or None if there is not one on this machine.

    None covers every "no art" case on purpose -- nothing extracted, this card not in
    the index, or an index entry whose file has since been deleted -- because every
    caller does the same thing about all three.
    """
    filename = _index().get(template_name)
    if not filename:
        return None
    path = spells_dir() / filename
    return path if path.exists() else None
