"""Utility for loading SVG files from bartlebot/gui/icons/ into QIcon objects."""

from pathlib import Path

from PyQt6.QtCore import QStandardPaths, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

_ICONS_DIR = Path(__file__).parent / "icons"
_DISABLED_OPACITY = 0.35


def svg_icon(name: str, size: int = 20, tint: str | None = None) -> QIcon:
    """Render bartlebot/gui/icons/<name>.svg at *size*×*size* and return a QIcon.

    If *tint* is not given, the icon is tinted with ``THEME.icon_tint`` so that
    toolbar/menu icons automatically adapt to light and dark themes.  Pass
    ``tint=""`` (empty string) to keep the original SVG colors.

    An explicit ``QIcon.Mode.Disabled`` pixmap is always attached.  Qt would
    otherwise synthesise one by desaturating and lightening the Normal pixmap —
    a near no-op here, because every icon has already been flattened to a single
    tint colour, which left icon-only buttons looking identical enabled and
    disabled.
    """
    if tint is None:
        from bartlebot.gui.theme import THEME

        tint = THEME.icon_tint
    icon = QIcon(svg_pixmap(name, size, tint=tint or None))
    icon.addPixmap(svg_pixmap(name, size, tint=tint or None, opacity=_DISABLED_OPACITY), QIcon.Mode.Disabled)
    return icon


def svg_pixmap(name: str, size: int, tint: str | None = None, opacity: float = 1.0) -> QPixmap:
    """Render an SVG icon to a QPixmap, optionally tinting all opaque pixels.

    Pass *tint* as a CSS color string (e.g. ``"#ffffff"``) to recolor every
    non-transparent pixel — useful for rendering icons on dark backgrounds.
    Pass *opacity* below 1.0 to fade the result, which is how the disabled
    variant in :func:`svg_icon` is produced.
    """
    renderer = QSvgRenderer(str(_ICONS_DIR / f"{name}.svg"))
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    renderer.render(p)
    p.end()
    if tint:
        tinted = QPixmap(size, size)
        tinted.fill(Qt.GlobalColor.transparent)
        p2 = QPainter(tinted)
        p2.drawPixmap(0, 0, pm)
        p2.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        p2.fillRect(tinted.rect(), QColor(tint))
        p2.end()
        pm = tinted
    if opacity >= 1.0:
        return pm
    faded = QPixmap(size, size)
    faded.fill(Qt.GlobalColor.transparent)
    p3 = QPainter(faded)
    p3.setOpacity(opacity)
    p3.drawPixmap(0, 0, pm)
    p3.end()
    return faded


def svg_qss_url(name: str, tint: str, size: int = 24) -> str | None:
    """A ``url(...)`` token naming a tinted PNG of *name*, or None if one cannot be made.

    QSS has no way to recolour an image, and it will not take a ``data:`` URI either,
    so a themed icon used inside a stylesheet has to exist on disk as a file per
    colour.  The render goes through :func:`svg_pixmap`, so the tinting is the same
    code path every other icon in the app uses; only the destination is new.

    Written once per (name, tint, size) into the platform cache directory and reused,
    which makes a theme switch a different filename rather than a re-render.  The
    cache directory is a cache in the ordinary sense: deleting it costs one render.

    Returns None rather than raising if the file cannot be written — a stylesheet is
    built during startup and on every theme switch, and a read-only or missing cache
    directory is not worth taking the app down for.  Callers are expected to drop the
    rule that would have used it; see ``_Theme.global_stylesheet``.
    """
    cache = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
    if not cache:
        return None
    path = Path(cache) / "icons" / f"{name}-{size}-{tint.lstrip('#')}.png"
    try:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            if not svg_pixmap(name, size, tint=tint).save(str(path), "PNG"):
                return None
    except OSError:
        return None
    # Quoted, and with forward slashes: an unquoted Windows path breaks the QSS parser
    # on the drive colon, and a backslash is an escape character to it.
    return f'url("{path.as_posix()}")'
