"""``RecordButton`` — the node Record button, with the red dot every recorder wears.

Recording is how most node params get filled, so the button has to be found without
being looked for (#106). The glyph does that work: a red circle reads as "record" in
any language, and it keeps its red in both themes rather than taking the text color
the way every other ``ActionButton`` glyph does. While a recording runs the button
reads *Stop* and the dot becomes a square in the ordinary text color — a red dot
beside *Stop* would say the opposite of the label.

Given the connected players, it reads *Record ▾* while idle with two or more of them:
a click then asks which one to record, as the Fill buttons beside it do (#85).
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtWidgets import QWidget

from bartlebot.gui.widgets.buttons.action_button import ActionButton, ActionStyle
from bartlebot.gui.widgets.pickers.player_picker import picker_label

#: The conventional recorder red. Fixed rather than a theme token: it is a convention
#: the glyph borrows, not a color of this app, and it reads on light and dark surfaces.
RECORD_RED = "#e53935"

IDLE_TEXT = "Record"


class RecordButton(ActionButton):
    """An ``ActionButton`` whose glyph follows its label: a red dot idle, a square while stopping.

    :meth:`set_label` is the ``on_btn`` callback ``recording.run_recording`` drives, so the
    recorders keep reporting plain text and never learn about icons.
    """

    def __init__(
        self,
        *,
        icon_size: int = 16,
        style: ActionStyle = ActionStyle.TERTIARY,
        fill: bool = False,
        get_clients: Callable[[], list] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        # Set before super().__init__, whose first render reads both.
        self._label = IDLE_TEXT
        self._get_clients = get_clients or (lambda: [])
        super().__init__("record", IDLE_TEXT, icon_size=icon_size, style=style, fill=fill, parent=parent)
        self._show()

    @property
    def idle(self) -> bool:
        return self._label == IDLE_TEXT

    def set_label(self, text: str) -> None:
        """Show *text*, and the glyph that goes with it."""
        self._label = text
        self._show()
        self.set_icon_name("record" if self.idle else "stop")

    def refresh_players(self) -> None:
        """Re-read the connected players -- call when one connects or disconnects."""
        self._show()

    def enterEvent(self, event) -> None:  # type: ignore[override]
        self._show()
        super().enterEvent(event)

    def _show(self) -> None:
        # Text before the glyph: the re-render set_icon_name triggers reads the tint off it.
        self.setText(picker_label(self._label, len(self._get_clients())) if self.idle else self._label)

    def _icon_tint(self, text: str) -> str:
        return RECORD_RED if self.idle else text
