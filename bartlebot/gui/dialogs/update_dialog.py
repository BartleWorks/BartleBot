"""The update popup: a newer release, its notes, and the three ways on.

*Install when I close BartleBot* downloads the update now and has Velopack apply it once
the app has closed, whenever that is, so a bot that is running carries on. *Restart now*
downloads it, then closes the app the usual way and starts the new version; it is
disabled while a bot runs, since a restart would end it. *Not now* closes the popup, and
the title bar keeps a quiet button for the update (``gui/update_prompt.py``).

The download happens here, with a bar, so a choice is never left half done behind the
user's back: the popup closes once Velopack has the update, and says why when it could
not get it. Escape, or the window's close button, is *Not now*, and leaves a download
running to nothing (its result is dropped).
"""

from __future__ import annotations

import asyncio
import enum
import logging
from collections.abc import Callable

from PyQt6.QtWidgets import QDialog, QProgressBar, QTextBrowser, QVBoxLayout, QWidget

from bartlebot.gui.theming.theme import THEME
from bartlebot.gui.theming.themed import ThemedMixin, set_style_sheet
from bartlebot.gui.widgets.buttons.action_button import ActionButton, ActionStyle
from bartlebot.gui.widgets.labels.themed_label import ThemedTextLabel
from bartlebot.gui.widgets.layout.dialog_footer import dialog_footer
from bartlebot.updates.check import Offer, PreparedUpdate
from bartlebot.updates.feed import FeedError

logger = logging.getLogger(__name__)

TITLE = "Update available"
HEADING = "BartleBot {version} is available"
CURRENT = "You have {current}. Nothing changes until you choose, and an update never stops a running bot."
NO_NOTES = 'What changed is on <a href="{url}">its release page</a>.'
INSTALL_ON_CLOSE = "Install when I close BartleBot"
RESTART_NOW = "Restart now"
NOT_NOW = "Not now"
RESTART_BLOCKED = "Stop the running bot first: restarting would end it."
DOWNLOADING = "Downloading {version}…"
DOWNLOADED_OF = "Downloading {version}… {done:.1f} of {total:.1f} MB"
CHECKING = "Checking the download…"
FAILED = "Couldn't get the update: {reason} Nothing was changed, and you can try again now or at the next start."

#: The notes' height, so a long changelog scrolls inside the popup rather than growing it.
NOTES_HEIGHT_PX = 220
#: The progress bar's height, as the art extraction's.
BAR_PX = 8


class Choice(enum.Enum):
    LATER = "later"
    ON_CLOSE = "on close"
    RESTART = "restart"


#: How the popup gets the update: the offer, and a ``(bytes so far, total)`` callback, to a
#: prepared update. Blocking; the popup runs it in a worker thread.
Preparer = Callable[[Offer, Callable[[int, int], None]], PreparedUpdate]


class UpdateDialog(ThemedMixin, QDialog):
    """The popup for one offer. :attr:`choice` and :attr:`prepared` say how it ended."""

    def __init__(
        self,
        offer: Offer,
        prepare: Preparer,
        *,
        running: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.setMinimumWidth(520)
        self._offer = offer
        self._prepare = prepare
        self._running = running
        self._choice = Choice.LATER
        self._prepared: PreparedUpdate | None = None
        self._task: asyncio.Task | None = None
        self._closed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self._heading = ThemedTextLabel(
            HEADING.format(version=offer.version),
            color="text",
            css=f"font-size: {THEME.font_heading_px}px; font-weight: 600;",
        )
        layout.addWidget(self._heading)
        self._current = ThemedTextLabel(CURRENT.format(current=offer.current))
        self._current.setWordWrap(True)
        layout.addWidget(self._current)

        self._notes = QTextBrowser()
        self._notes.setOpenExternalLinks(True)
        self._notes.setFixedHeight(NOTES_HEIGHT_PX)
        if offer.notes.strip():
            self._notes.setMarkdown(offer.notes)
        else:
            self._notes.setHtml(NO_NOTES.format(url=offer.release_page))
        layout.addWidget(self._notes)

        self._bar = QProgressBar()
        self._bar.setTextVisible(False)
        self._bar.setVisible(False)
        layout.addWidget(self._bar)
        self._status = ThemedTextLabel("", css=f"font-size: {THEME.font_small_px}px;")
        self._status.setWordWrap(True)
        self._status.setVisible(False)
        layout.addWidget(self._status)

        self._restart = ActionButton("revert", RESTART_NOW, style=ActionStyle.SECONDARY)
        footer = dialog_footer(
            self,
            commit_text=INSTALL_ON_CLOSE,
            commit_icon="download",
            on_commit=lambda: self.choose(Choice.ON_CLOSE),
            cancel_text=NOT_NOW,
            extras=[self._restart],
        )
        self._install = footer.commit
        self._not_now = footer.cancel
        layout.addLayout(footer.layout)
        self._restart.clicked.connect(lambda: self.choose(Choice.RESTART))
        self._restart.setEnabled(not running)
        self._restart.setToolTip(RESTART_BLOCKED if running else "")
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        # Drawn by hand, as the art extraction's: Qt's Windows 11 style draws a bar
        # without text as a hairline.
        self._bar.setFixedHeight(BAR_PX)
        set_style_sheet(
            self._bar,
            f"QProgressBar {{ background: {THEME.surface}; border: 1px solid {THEME.border};"
            f" border-radius: {BAR_PX // 2}px; }}"
            f"QProgressBar::chunk {{ background: {THEME.accent}; border-radius: {BAR_PX // 2 - 1}px; }}",
        )

    # -- what the caller reads -------------------------------------------------------

    @property
    def offer(self) -> Offer:
        return self._offer

    @property
    def choice(self) -> Choice:
        """How the popup ended: :attr:`Choice.LATER` unless an update was downloaded for a choice."""
        return self._choice

    @property
    def prepared(self) -> PreparedUpdate | None:
        """The downloaded update, for :attr:`Choice.ON_CLOSE` and :attr:`Choice.RESTART`."""
        return self._prepared

    @property
    def task(self) -> asyncio.Task | None:
        return self._task

    @property
    def install_button(self) -> ActionButton:
        return self._install

    @property
    def restart_button(self) -> ActionButton:
        return self._restart

    @property
    def not_now_button(self) -> ActionButton:
        return self._not_now

    @property
    def heading_text(self) -> str:
        return self._heading.text()

    @property
    def current_text(self) -> str:
        return self._current.text()

    @property
    def notes(self) -> QTextBrowser:
        return self._notes

    @property
    def status_text(self) -> str:
        return self._status.text() if not self._status.isHidden() else ""

    # -- choosing ---------------------------------------------------------------------

    def choose(self, choice: Choice) -> bool:
        """Download the update for ``choice``, and close once Velopack has it. Whether a download started.

        Refused while one runs, for a restart while a bot runs, and with no event loop.
        """
        if choice is Choice.LATER:
            self.reject()
            return False
        if self._task is not None and not self._task.done():
            return False
        if choice is Choice.RESTART and self._running:
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        self._set_busy(True)
        self._bar.setRange(0, 0)
        self._show_status(DOWNLOADING.format(version=self._offer.version), "text_muted")
        self._task = loop.create_task(self._download(loop, choice))
        return True

    async def _download(self, loop: asyncio.AbstractEventLoop, choice: Choice) -> None:
        def progress(done: int, total: int) -> None:
            loop.call_soon_threadsafe(self._show_progress, done, total)

        try:
            prepared = await asyncio.to_thread(self._prepare, self._offer, progress)
        except FeedError as exc:
            logger.warning("The update to %s was not downloaded: %s", self._offer.version, exc)
            self._fail(str(exc))
            return
        except Exception as exc:
            logger.warning("The update to %s was not downloaded", self._offer.version, exc_info=True)
            self._fail(f"{exc}.")
            return
        if self._closed:
            return
        self._prepared = prepared
        self._choice = choice
        self.accept()

    def _show_progress(self, done: int, total: int) -> None:
        if self._closed:
            return
        if total and done < total:
            self._bar.setRange(0, total)
            self._bar.setValue(done)
            self._show_status(
                DOWNLOADED_OF.format(version=self._offer.version, done=done / 1e6, total=total / 1e6), "text_muted"
            )
        else:
            self._bar.setRange(0, 0)
            self._show_status(CHECKING, "text_muted")

    def _fail(self, reason: str) -> None:
        if self._closed:
            return
        self._set_busy(False)
        self._bar.setVisible(False)
        self._show_status(FAILED.format(reason=reason), "error")

    def _set_busy(self, busy: bool) -> None:
        self._bar.setVisible(busy)
        self._install.setEnabled(not busy)
        self._restart.setEnabled(not busy and not self._running)

    def _show_status(self, text: str, color: str) -> None:
        self._status.set_color(color)
        self._status.setText(text)
        self._status.setVisible(True)

    def done(self, result: int) -> None:
        self._closed = True
        if self._task is not None and not self._task.done() and self._prepared is None:
            # The thread runs on; whatever it returns is dropped.
            self._task.cancel()
        super().done(result)
