"""Export the logo's raster files from its SVG, for the places outside the app (#26).

The app builds its own window icon at run time (``bartlebot.gui.logo``); this writes
the files everything else wants, all from the same snapping:

* ``bartlebot.ico``: the tree alone, as the window icon draws it, at every size in
  ``ICON_SIZES``, for the installer and the exe.
* ``bartlebot-<size>.png``: the square with its background, one per icon size plus
  512 for the GitHub avatar and the Discord server icon.
* ``bartlebot-splash.png``: the square at ``SPLASH_SIZE``, the window Setup.exe shows
  while it installs.

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

#: Setup.exe's splash, in pixels at 100% scaling. Velopack draws the image as it is,
#: scaled by the monitor's scaling, and lays a 12 px progress bar (scaled the same) along
#: its bottom edge. 24 px cells leave the tree a cell and a half of background below it,
#: 36 px, so the bar never covers the tree.
SPLASH_SIZE = 288


def export(out_dir: Path) -> list[Path]:
    """Write the .ico and every PNG into *out_dir*; returns the paths written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for size in sorted({*ICON_SIZES, *AVATAR_SIZES}):
        path = out_dir / f"bartlebot-{size}.png"
        if not logo_image(size).save(str(path), "PNG"):
            raise OSError(f"Could not write {path}")
        written.append(path)
    written.append(write_icon(out_dir))
    written.append(write_splash(out_dir))
    return written


def write_icon(out_dir: Path) -> Path:
    """Write ``bartlebot.ico`` into *out_dir* and return its path. ``tools/build_package.py`` uses it too."""
    out_dir.mkdir(parents=True, exist_ok=True)
    # Pillow resamples a single image to each size by default, which would undo the
    # snapping; handing it every size ready-made keeps each one exactly as drawn.
    frames = [_pil(tree_image(size)) for size in ICON_SIZES]
    ico = out_dir / "bartlebot.ico"
    largest = frames[-1]
    largest.save(ico, format="ICO", sizes=[f.size for f in frames], append_images=frames[:-1])
    return ico


def write_splash(out_dir: Path) -> Path:
    """Write ``bartlebot-splash.png`` into *out_dir* and return its path. ``tools/build_package.py`` uses it too.

    The square, not the disc: Velopack's progress bar is opaque and runs the image's full
    width, so on the disc's transparent corners it would float below the logo.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "bartlebot-splash.png"
    if not logo_image(SPLASH_SIZE).save(str(path), "PNG"):
        raise OSError(f"Could not write {path}")
    return path


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
