"""Export the logo's raster files from its SVG, for the places outside the app (#26).

The app builds its own window icon at run time (``bartlebot.gui.logo``); this writes
the files everything else wants, all from the same snapping:

* ``bartlebot.ico``: the tree alone, as the window icon draws it, at every size in
  ``ICON_SIZES``, for the installer and the exe.
* ``bartlebot-<size>.png``: the square with its background, one per icon size plus
  512 for the GitHub avatar and the Discord server icon.

Nothing is written into the repo unless you name a path in it: the SVG is the
tracked source, and these are regenerated from it whenever they are needed.

    uv run python tools/export_logo.py OUT_DIR
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

from PIL import Image
from PyQt6.QtCore import QBuffer, QIODevice
from PyQt6.QtGui import QGuiApplication

from bartlebot.gui.logo import ICON_SIZES, logo_image, tree_image

#: Sizes for avatars and headers, written as PNG beside the icon sizes.
AVATAR_SIZES = (512,)


def export(out_dir: Path) -> list[Path]:
    """Write the .ico and every PNG into *out_dir*; returns the paths written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for size in sorted({*ICON_SIZES, *AVATAR_SIZES}):
        path = out_dir / f"bartlebot-{size}.png"
        if not logo_image(size).save(str(path), "PNG"):
            raise OSError(f"Could not write {path}")
        written.append(path)

    # Pillow resamples a single image to each size by default, which would undo the
    # snapping; handing it every size ready-made keeps each one exactly as drawn.
    frames = [_pil(tree_image(size)) for size in ICON_SIZES]
    ico = out_dir / "bartlebot.ico"
    largest = frames[-1]
    largest.save(ico, format="ICO", sizes=[f.size for f in frames], append_images=frames[:-1])
    written.append(ico)
    return written


def _pil(image) -> Image.Image:
    """A QImage as a Pillow image, through PNG so the alpha survives as it is."""
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return Image.open(io.BytesIO(bytes(buffer.data())))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out_dir", type=Path, help="directory to write the .ico and PNGs into")
    args = parser.parse_args()
    _app = QGuiApplication(sys.argv)  # noqa: F841 - QPainter needs one alive
    for path in export(args.out_dir):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
