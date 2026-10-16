"""Hand wizwalker a cache folder under the vendor root (#234).

wizwalker caches what it reads out of the game's wads -- ``wad_cache.data``,
``template_ids.json``, ``langmap.json`` -- in ``get_cache_folder()``, whose default is
``%LocalAppData%/StarrFox/WizWalker/Cache``: named after the library's original author,
outside the vendor root, and left behind by uninstall. ``override_cache_folder`` points it
at :func:`cache_dir` instead, and the app calls :func:`apply_cache_dir` at startup, before
anything reads the cache.

The vendor level, not ``BartleBot/``: the cache describes the game's files, not this
product, and a second product by the same publisher builds the same one. And
``%LocalAppData%``, not ``%APPDATA%``: ``wad_cache.data`` records the sizes of this
machine's game files, so it is wrong on any other machine a roaming profile would carry it to.

The old folder is neither moved nor deleted. It is a cache, rebuilt on demand, and other
wizwalker tools share it.
"""

from __future__ import annotations

from pathlib import Path

from wizwalker.utils import override_cache_folder

from bartlebot.shared.vendor import local_vendor_dir


def cache_dir() -> Path:
    """``%LocalAppData%/Bartleby/cache/wizwalker``."""
    return local_vendor_dir() / "cache" / "wizwalker"


def apply_cache_dir() -> None:
    """Point wizwalker's ``get_cache_folder()`` at :func:`cache_dir`."""
    override_cache_folder(cache_dir())
