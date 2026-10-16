"""Write a made-up extracted art cache into the test's own ``%APPDATA%``.

Every test already has an ``%APPDATA%`` of its own (``tests/conftest.py``), so this writes
where ``card_art`` reads. The PNGs are solid squares, never the game's art. Writing does
not announce anything: a test that wants the surfaces told calls
``art_signal.announce_art_changed()`` itself, as ``ArtExtraction`` does after a run.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from PyQt6.QtGui import QColor, QPixmap

from bartlebot.game import card_art


def _png(path, size: int = 64) -> None:
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor("red"))
    pixmap.save(str(path))


def write_extracted_art(icons: Iterable[str] = (), images: Iterable[str] = ()) -> None:
    """An art cache holding these icon keys and these spell templates, then ``forget_cache``.

    Needs a ``QApplication``, since the squares are drawn with ``QPixmap``.
    """
    card_art.icons_dir().mkdir(parents=True, exist_ok=True)
    card_art.spells_dir().mkdir(parents=True, exist_ok=True)
    icon_map = {key: f"{key}.png" for key in icons}
    for filename in icon_map.values():
        _png(card_art.icons_dir() / filename)
    image_map = {template: f"spell_{n}.png" for n, template in enumerate(images)}
    for filename in image_map.values():
        _png(card_art.spells_dir() / filename)
    card_art.index_path().write_text(json.dumps({"icons": icon_map, "images": image_map}), encoding="utf-8")
    card_art.forget_cache()
