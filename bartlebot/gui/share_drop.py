"""Dropping a share file on the window imports it (#171).

An application-wide event filter, not a ``dropEvent`` on the main window: Qt delivers a
drag to the widget under the cursor, and several of ours accept drops of their own (the
canvas, the favorites bar, the combat order list). Without the filter, a share file
dropped on the canvas would go to the canvas, which would refuse it, and nothing would
happen. Only a drag carrying a share file is taken; every other drag goes on to its
widget as before. The main window accepts drops so that a widget accepting none still
passes the drag up to something.

Which file it is comes from its extension here, because a drag says nothing else before
it lands; the import itself reads ``main`` and never trusts the extension.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QEvent, QMimeData, QObject, pyqtSignal

from bartlebot.share_export import EXTENSIONS

_SUFFIXES = frozenset(EXTENSIONS.values())


def is_share_file(path: str | Path) -> bool:
    """Whether ``path`` has a share file's extension, of any kind."""
    return Path(path).suffix.lower() in _SUFFIXES


def share_paths(mime: QMimeData | None) -> list[str]:
    """The local share files a drag carries, in order; empty for any other drag."""
    if mime is None or not mime.hasUrls():
        return []
    paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
    return [p for p in paths if is_share_file(p)]


class ShareFileDrops(QObject):
    """Takes every drag that carries a share file, wherever in the app it lands, and emits the files dropped."""

    #: (list of file paths) dropped.
    files_dropped = pyqtSignal(list)

    _DRAGS = (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt's name
        if event.type() not in self._DRAGS:
            return False
        paths = share_paths(event.mimeData())  # type: ignore[attr-defined]
        if not paths:
            return False
        event.acceptProposedAction()  # type: ignore[attr-defined]
        if event.type() == QEvent.Type.Drop:
            self.files_dropped.emit(paths)
        return True
