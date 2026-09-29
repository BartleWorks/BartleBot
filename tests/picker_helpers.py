"""Pick rows in a :class:`PickerButton` the way a user does: open its popup and click them.

Goes through the popup rather than ``set_selection``, which deliberately does not emit —
a test that set the selection that way would step over the very signal it means to test.
"""

from __future__ import annotations

from bartlebot.gui.widgets.picker_popup import PickerButton


def pick(button: PickerButton, *keys: str, on: bool = True) -> None:
    """Toggle each of *keys* on (or off) through the button's popup, then close it."""
    button._open()
    popup = button._popup
    for key in keys:
        popup._on_row_toggled(key, on)
    popup.close()
