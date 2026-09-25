"""``RecordButton`` — the node Record button, with the red dot every recorder wears.

Recording is how most node params get filled, so the button has to be found without
being looked for (#106). The glyph does that work: a red circle reads as "record" in
any language, and it keeps its red in both themes rather than taking the text colour
the way every other ``ActionButton`` glyph does. While a recording runs the button
reads *Stop* and the dot becomes a square in the ordinary text colour — a red dot
beside *Stop* would say the opposite of the label.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QWidget

from bartlebot.gui.widgets.action_button import ActionButton, ActionStyle

#: The conventional recorder red. Fixed rather than a theme token: it is a convention
#: the glyph borrows, not a colour of this app, and it reads on light and dark surfaces.
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
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("record", IDLE_TEXT, icon_size=icon_size, style=style, fill=fill, parent=parent)

    @property
    def idle(self) -> bool:
        return self.text() == IDLE_TEXT

    def set_label(self, text: str) -> None:
        """Show *text*, and the glyph that goes with it."""
        # Text first: the re-render set_icon_name triggers reads the tint off it.
        self.setText(text)
        self.set_icon_name("record" if text == IDLE_TEXT else "stop")

    def _icon_tint(self, text: str) -> str:
        return RECORD_RED if self.idle else text
