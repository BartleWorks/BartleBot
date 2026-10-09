"""The player card's pure parts, tested without a QApplication.

What a card *says* and what color it wears are decidable from a username, an index and
the account list, so they live outside the ``QWidget`` subclass and are asked here.
``tests/gui/sidebar/test_player_card_widgets.py`` has the half that genuinely needs a widget.

No real player data anywhere in this file. The names are decoded from the shipped
``name_key`` tables — see CLAUDE.md — so they are realistic and belong to nobody.
"""

from __future__ import annotations

import json

from bartlebot.gui.sidebar.player_card import (
    PLACEHOLDER,
    account_stripe_keys,
    format_xyz,
    player_name_text,
    player_pill_text,
    xyz_clipboard_text,
)
from bartlebot.gui.theming.account_accent import ACCOUNT_PALETTE_SIZE, account_color_keys
from bartlebot.settings_manager import SettingsManager

ACCOUNTS = [
    {"username": "one@example.com", "alias": "One"},
    {"username": "two@example.com", "alias": "Two"},
    {"username": "three@example.com", "alias": "Three"},
]


def _streamer_mode(on: bool) -> None:
    """Turn Streamer Mode on or off. ``isolated_settings`` in conftest puts it back."""
    SettingsManager.instance().streamer_mode = on


# ---------------------------------------------------------------------------
# The stripe
# ---------------------------------------------------------------------------


def test_each_client_takes_the_slot_its_own_account_takes():
    """The whole point of the feature: launch the blue account, get a blue player card.

    Asserted against ``account_color_keys`` rather than against literal slot numbers,
    because the numbers are derived from a digest and de-duplicated across the list —
    hard-coding them would be asserting the digest, and would have to be rewritten every
    time an account is added to this fixture.
    """
    expected = account_color_keys(ACCOUNTS)
    usernames = [a["username"] for a in ACCOUNTS]

    assert account_stripe_keys(usernames, ACCOUNTS) == [expected[u] for u in usernames]


def test_a_client_with_no_account_gets_no_stripe():
    """A window BartleBot did not launch, or one whose identity has not arrived yet.

    ``None`` and not a default color: the stripe is a claim about which account is
    running, and there is nothing here to claim. It is also the *normal* state for the
    first moments of a client's life, since ``account_for`` is filled from inside the
    client loop.
    """
    keys = account_stripe_keys([None, "one@example.com", None], ACCOUNTS)

    assert keys[0] is None
    assert keys[2] is None
    assert keys[1] == account_color_keys(ACCOUNTS)["one@example.com"]


def test_a_username_no_longer_in_the_account_list_gets_no_stripe():
    """An account deleted while its client is still running has no slot to point at.

    Falling back to ``account_color_key``'s per-username derivation would put a color on
    the card that matches nothing anywhere else on screen — which is worse than no color,
    for the same reason a repeating palette is: it invites a match that is wrong.
    """
    assert account_stripe_keys(["gone@example.com"], ACCOUNTS) == [None]


def test_no_stripe_is_asked_for_when_there_are_no_accounts_at_all():
    assert account_stripe_keys([None, None], []) == [None, None]


def test_every_slot_asked_for_is_inside_the_palette():
    keys = account_stripe_keys([a["username"] for a in ACCOUNTS], ACCOUNTS)

    assert all(1 <= key <= ACCOUNT_PALETTE_SIZE for key in keys)


# ---------------------------------------------------------------------------
# The header
# ---------------------------------------------------------------------------


def test_the_header_reads_the_cached_name():
    _streamer_mode(False)

    assert player_name_text("Luke SkullHunter", 0) == "Luke SkullHunter"


def test_an_unknown_name_reads_the_placeholder_rather_than_nothing():
    """ "Not detected yet" is a real state, and an empty header does not say it."""
    _streamer_mode(False)

    assert player_name_text(None, 0) == PLACEHOLDER
    assert player_name_text("", 2) == PLACEHOLDER


def test_streamer_mode_substitutes_rather_than_blanks():
    """Redundant beats ambiguous: the pill already says P2, and blanking would read as unknown."""
    _streamer_mode(True)

    assert player_name_text("Luke SkullHunter", 1) == "Character 2"


def test_streamer_mode_still_shows_the_placeholder_for_an_unknown_name():
    """Not knowing a name is not a privacy question, so it answers the same either way."""
    _streamer_mode(True)

    assert player_name_text(None, 1) == PLACEHOLDER


def test_the_pill_counts_from_one():
    assert player_pill_text(0) == "P1"
    assert player_pill_text(3) == "P4"


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
