"""Game art in the app: the run with its progress, setup's choice, and Settings › General › Game art (#232).

:class:`ArtExtraction` runs :func:`bartlebot.game.art_extraction.extract_art` off the GUI
thread and shows how far it has got. A run that writes art announces it once
(:mod:`bartleby.qt.art_signal`), so what is already on screen redraws with it. Setup's Game art page holds one, and so does
:class:`ArtExtractionDialog`, which the Settings row opens for a run after a game patch.
The choice on Custom setup's Options page is :func:`extract_art_row`, with the same text the
Settings row carries.

Art is optional everywhere (``bartlebot/gui/CLAUDE.md``, "Game art is optional"), so a
failed run says why, logs it, and lets the user carry on.
"""

from __future__ import annotations

import asyncio
import logging
import threading

from bartleby.qt.art_signal import announce_art_changed
from bartleby.qt.settings.rows import Binding, SettingRow
from bartleby.qt.theming.theme import THEME
from bartleby.qt.theming.themed import ThemedMixin
from bartleby.qt.widgets.buttons.action_button import ActionButton
from bartleby.qt.widgets.labels.themed_label import ThemedTextLabel
from bartleby.qt.widgets.layout.dialog_footer import dialog_footer
from bartleby.shared.preferences import PreferenceStore
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QDialog, QProgressBar, QVBoxLayout, QWidget

from bartlebot.game import art_extraction, card_art
from bartlebot.game.art_extraction import (
    ExtractionCancelled,
    ExtractionError,
    ExtractionResult,
    Progress,
    installed_game_data_dir,
)

logger = logging.getLogger(__name__)

EXTRACT_TITLE = "Extract game art"
EXTRACT_SUMMARY = "Show spell cards, schools, worlds and tags with the game's own pictures."
#: The cost, said once for setup's choice and the Settings row.
EXTRACT_DETAILS = (
    "The pictures take about 35 MB in AppData, and need extracting again when a game update adds "
    "spells. Without them, cards and lists show names only."
)

SETTINGS_TITLE = "Game art"
EXTRACT_BUTTON = "Extract"
NOT_EXTRACTED = "Not extracted: cards and lists show names only."
EXTRACTED = "Extracted on {date}: {files:,} pictures for {templates:,} cards."
EXTRACTED_UNDATED = "Extracted: {files:,} pictures for {templates:,} cards."

#: Under the bar: the step, with how far through it when it has a count.
STEP_COUNTED = "{step} · {done:,} of {total:,}"
STARTING = "Starting…"
DONE = "Extracted {files:,} pictures for {templates:,} cards and {icons:,} icons ({mb:.0f} MB)."
FAILED = "Couldn't extract the game art. {reason} Cards and lists show names until the art is extracted."
NO_GAME = "Wizard101 wasn't found. Choose the game folder, then try again."

#: The progress bar's height.
BAR_PX = 8

DIALOG_TITLE = "Extract Game Art"
CLOSE = "Close"
CANCEL = "Cancel"


class ArtExtraction(ThemedMixin, QWidget):
    """A run of the extractor, with a progress bar, the step it is on, and how it ended.

    :meth:`start` runs it in a worker thread, from a task this widget owns; progress
    reaches the GUI thread through the loop. :meth:`stop` asks the run to stop, and the
    extractor removes what it wrote; once stopped, nothing more is drawn. Without a
    running loop, as in a test that never awaits, :meth:`start` does nothing.
    """

    #: The run ended, finished or failed, but not stopped. True when it worked.
    finished = pyqtSignal(bool)

    def __init__(self, prefs: PreferenceStore | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._prefs = prefs
        self._task: asyncio.Task | None = None
        self._cancel = threading.Event()
        self._stopped = False
        self._result: ExtractionResult | None = None
        self._error: str | None = None
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)
        self._bar = QProgressBar()
        self._bar.setTextVisible(False)
        self._bar.setRange(0, 1)
        self._bar.setValue(0)
        column.addWidget(self._bar)
        self._step = ThemedTextLabel(STARTING, css=f"font-size: {THEME.font_small_px}px;")
        self._step.setWordWrap(True)
        column.addWidget(self._step)
        self._outcome = ThemedTextLabel("", color="text")
        self._outcome.setWordWrap(True)
        self._outcome.setVisible(False)
        column.addWidget(self._outcome)
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        # Drawn by hand: Qt's Windows 11 style draws a bar without text as a hairline,
        # and in the dark theme its fill barely shows against the track.
        self._bar.setFixedHeight(BAR_PX)
        self._bar.setStyleSheet(
            f"QProgressBar {{ background: {THEME.surface}; border: 1px solid {THEME.border};"
            f" border-radius: {BAR_PX // 2}px; }}"
            f"QProgressBar::chunk {{ background: {THEME.accent}; border-radius: {BAR_PX // 2 - 1}px; }}"
        )

    @property
    def task(self) -> asyncio.Task | None:
        return self._task

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def ended(self) -> bool:
        """Whether the run finished or failed: what the caller waits for."""
        return self._result is not None or self._error is not None

    @property
    def ok(self) -> bool:
        return self._result is not None

    @property
    def step_text(self) -> str:
        return self._step.text()

    @property
    def step_shown(self) -> bool:
        """Whether the step line is up: while a run goes, and hidden once its outcome is."""
        return not self._step.isHidden()

    @property
    def outcome_text(self) -> str:
        return self._outcome.text()

    @property
    def bar(self) -> QProgressBar:
        return self._bar

    def start(self) -> bool:
        """Start a run unless one is running or has worked. Whether one is running now."""
        if self.running or self.ok:
            return self.running
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        self._stopped = False
        self._cancel = threading.Event()
        self._error = None
        self._outcome.setVisible(False)
        # A failed run hid it for its outcome; a retry reports its steps again.
        self._step.setText(STARTING)
        self._step.setVisible(True)
        # Empty, not busy: a stylesheet draws Qt's busy bar as a half-full chunk, which
        # read as 50% done before the first step had counted anything.
        self._bar.setRange(0, 1)
        self._bar.setValue(0)
        self._task = loop.create_task(self._run(loop, self._cancel))
        return True

    def stop(self) -> None:
        """Ask a run to stop. The extractor removes what it wrote; nothing more is drawn."""
        self._stopped = True
        self._cancel.set()

    async def _run(self, loop: asyncio.AbstractEventLoop, cancel: threading.Event) -> None:
        game = installed_game_data_dir(self._prefs)
        if game is None:
            self._fail(NO_GAME)
            return

        def progress(p: Progress) -> None:
            loop.call_soon_threadsafe(self._show, p)

        try:
            result = await asyncio.to_thread(art_extraction.extract_art, game, progress, cancelled=cancel.is_set)
        except ExtractionCancelled:
            return
        except ExtractionError as exc:
            logger.warning("Game art was not extracted: %s", exc)
            self._fail(str(exc))
            return
        except Exception as exc:
            logger.warning("Game art extraction failed", exc_info=True)
            self._fail(f"{exc}.")
            return
        self._succeed(result)

    def _show(self, p: Progress) -> None:
        if self._stopped:
            return
        if p.total:
            self._bar.setRange(0, p.total)
            self._bar.setValue(p.done)
            self._step.setText(STEP_COUNTED.format(step=p.step, done=p.done, total=p.total))
        else:
            self._bar.setRange(0, 0)
            self._step.setText(f"{p.step}…")

    def _succeed(self, result: ExtractionResult) -> None:
        self._result = result
        # Before the stopped check: a stop that came after the extractor's last check
        # did not undo anything, so the new art is on disk and every surface should show it.
        announce_art_changed()
        if self._stopped:
            return
        self._bar.setRange(0, 1)
        self._bar.setValue(1)
        self._step.setVisible(False)
        self._outcome.set_color("text")
        self._outcome.setText(
            DONE.format(files=result.files, templates=result.templates, icons=result.icons, mb=result.size_bytes / 1e6)
        )
        self._outcome.setVisible(True)
        self.finished.emit(True)

    def _fail(self, reason: str) -> None:
        self._error = reason
        if self._stopped:
            return
        self._bar.setRange(0, 1)
        self._bar.setValue(0)
        self._step.setVisible(False)
        self._outcome.set_color("error")
        self._outcome.setText(FAILED.format(reason=reason))
        self._outcome.setVisible(True)
        self.finished.emit(False)


def extract_art_row(checked: bool) -> SettingRow:
    """Custom setup's choice: a check box on *checked*, on by default."""
    box = QCheckBox()
    box.setChecked(checked)
    return SettingRow(EXTRACT_TITLE, EXTRACT_SUMMARY, box, default=True, details=EXTRACT_DETAILS)


class ArtExtractionDialog(QDialog):
    """Settings' run: an :class:`ArtExtraction`, Cancel while it runs, and Close once it has ended.

    It starts on construction. Cancel, Escape or the close button stop the run, and the art
    before it stays.
    """

    def __init__(self, prefs: PreferenceStore | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(DIALOG_TITLE)
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self._extraction = ArtExtraction(prefs)
        layout.addWidget(self._extraction)
        footer = dialog_footer(self, commit_text=CLOSE, cancel_text=CANCEL)
        self._close = footer.commit
        self._cancel = footer.cancel
        self._close.setEnabled(False)
        layout.addLayout(footer.layout)
        self._extraction.finished.connect(self._on_finished)
        self._extraction.start()

    @property
    def extraction(self) -> ArtExtraction:
        return self._extraction

    @property
    def close_button(self) -> ActionButton:
        return self._close

    @property
    def cancel_button(self) -> ActionButton:
        return self._cancel

    def _on_finished(self, _ok: bool) -> None:
        self._close.setEnabled(True)
        self._cancel.setVisible(False)

    def done(self, result: int) -> None:
        self._extraction.stop()
        super().done(result)


def _status() -> str:
    index = card_art.manifest()
    images = index.get("images")
    if not isinstance(images, dict) or not images:
        return NOT_EXTRACTED
    files = len(set(images.values()))
    generated = index.get("generated")
    if generated:
        return EXTRACTED.format(date=generated, files=files, templates=len(images))
    return EXTRACTED_UNDATED.format(files=files, templates=len(images))


def game_art_row(prefs: PreferenceStore | None = None) -> tuple[SettingRow, ActionButton]:
    """Settings › General's row: whether art is extracted, and **Extract**, which opens the dialog."""
    button = ActionButton("download", EXTRACT_BUTTON, fill=False)
    row = SettingRow(
        SETTINGS_TITLE,
        _status(),
        button,
        default=None,
        details=EXTRACT_DETAILS,
        # An action, not a value: nothing to change, revert or describe.
        binding=Binding(read=lambda: None, write=lambda _v: None, signals=(), describe=lambda _v: ""),
    )

    def open_dialog() -> None:
        dialog = ArtExtractionDialog(prefs, parent=row.window())
        dialog.finished.connect(lambda _r: row.summary.setText(_status()))
        dialog.open()

    button.clicked.connect(lambda _checked: open_dialog())
    return row, button
