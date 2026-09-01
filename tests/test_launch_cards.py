"""Tests for the Quick Launch cards' text.

The cards are QWidgets and need a QApplication, so what is tested here is the text they
build — extracted as plain functions for exactly that reason, per CLAUDE.md.
"""

from bartlebot.gui.game_embed import launch_cards
from bartlebot.settings_manager import SettingsManager

LUKE = {"gid": 1001, "name": "Luke SkullHunter"}
MISHA = {"gid": 1002, "name": "Misha SkullSeer"}


# ---------------------------------------------------------------------------
# Account card
# ---------------------------------------------------------------------------


def test_character_summary_lists_the_names():
    assert launch_cards.character_summary([LUKE, MISHA]) == "Luke SkullHunter · Misha SkullSeer"


def test_character_summary_says_why_it_is_empty():
    """ "(none)" would read as a fault. Nothing can be known about an account until a
    character on it has been played, and the line has to say so."""
    assert "launch one" in launch_cards.character_summary([])


def test_character_summary_truncates_a_long_roster():
    roster = [{"gid": 1000 + i, "name": f"Wizard{i}"} for i in range(7)]
    summary = launch_cards.character_summary(roster)
    assert summary.endswith("+3 more")
    assert "Wizard4" not in summary


def test_character_summary_does_not_truncate_at_exactly_the_limit():
    roster = [{"gid": 1000 + i, "name": f"Wizard{i}"} for i in range(launch_cards.MAX_CHARACTERS_SHOWN)]
    assert "more" not in launch_cards.character_summary(roster)


def test_character_summary_honours_streamer_mode():
    SettingsManager.instance().streamer_mode = True
    summary = launch_cards.character_summary([LUKE, MISHA])
    assert summary == "Character 1 · Character 2"


def test_character_names_number_by_roster_position():
    """Position comes from the roster's own order so a character reads the same way each
    time the card is rebuilt — otherwise the placeholder cannot tell two of them apart."""
    SettingsManager.instance().streamer_mode = True
    assert launch_cards.character_names([LUKE, MISHA]) == ["Character 1", "Character 2"]


# ---------------------------------------------------------------------------
# Profile card
# ---------------------------------------------------------------------------


def test_account_pills_use_aliases():
    accounts = [{"username": "someone@example.com", "alias": "Main"}]
    assert launch_cards.account_display_names(["someone@example.com"], accounts) == ["Main"]


def test_account_pills_keep_launch_order():
    """A profile's account order decides which window becomes Player 1, so sorting these
    for looks would misreport what the profile does."""
    accounts = [
        {"username": "a@example.com", "alias": "Zeta"},
        {"username": "b@example.com", "alias": "Alpha"},
    ]
    assert launch_cards.account_display_names(["a@example.com", "b@example.com"], accounts) == ["Zeta", "Alpha"]


def test_a_deleted_account_still_gets_a_pill():
    """Dropping it would make a profile that cannot launch look complete."""
    assert launch_cards.account_display_names(["gone@example.com"], []) == ["g***m"]


def test_an_account_without_an_alias_is_censored():
    accounts = [{"username": "someone@example.com", "alias": ""}]
    assert launch_cards.account_display_names(["someone@example.com"], accounts) == ["s***m"]


def test_resolution_label_shows_a_pinned_resolution():
    assert launch_cards.resolution_label({"resolution": [1280, 720]}) == "1280×720"


def test_no_resolution_label_when_the_profile_follows_the_app_setting():
    """A pill on every card would be noise; the interesting case is the one that differs."""
    assert launch_cards.resolution_label({}) is None
    assert launch_cards.resolution_label({"resolution": []}) is None
    assert launch_cards.resolution_label({"resolution": [1280]}) is None


def test_profile_summary_counts_clients():
    assert launch_cards.profile_summary({"accounts": ["a@example.com", "b@example.com"]}) == "2 clients"
    assert launch_cards.profile_summary({"accounts": ["a@example.com"]}) == "1 client"


def test_profile_summary_flags_a_profile_that_cannot_launch():
    assert "cannot launch" in launch_cards.profile_summary({})
    assert "cannot launch" in launch_cards.profile_summary({"accounts": []})
