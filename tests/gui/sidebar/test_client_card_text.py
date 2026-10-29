"""BartleBot's player card rows, tested without a QApplication: XYZ as shown, and as copied.

The card's other pure parts -- the stripe, the header and the pill -- are core's, and so
are their tests (``bartleby-core/tests/qt/sidebar/test_player_card_text.py``).
"""

from __future__ import annotations

import json

from bartlebot.gui.sidebar.client_card import format_xyz, xyz_clipboard_text

# ---------------------------------------------------------------------------
# XYZ: what is shown, and what is copied
# ---------------------------------------------------------------------------


def test_the_displayed_position_is_one_decimal_and_space_separated():
    assert format_xyz(-1.25, 0.0, 344.678) == "X=-1.2  Y=0.0  Z=344.7"


def test_the_copied_position_is_json_at_two_decimals():
    """What a node takes, which is not what a column reads well."""
    assert json.loads(xyz_clipboard_text(-1.25, 0.0, 344.678)) == {"x": -1.25, "y": 0.0, "z": 344.68}


def test_the_copied_position_is_not_the_displayed_one():
    """The two forms are deliberately different, which is why every row carries both."""
    assert xyz_clipboard_text(1.0, 2.0, 3.0) != format_xyz(1.0, 2.0, 3.0)
