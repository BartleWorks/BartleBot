"""Tests for the Quick Launch cards' text.

The cards are QWidgets and need a QApplication, so what is tested here is the text they
build — extracted as plain functions for exactly that reason, per CLAUDE.md.
"""

from bartlebot.game.game_sizing import FitProblem
from bartlebot.gui.game_embed import launch_cards
from bartlebot.gui.theming import account_accent
from bartlebot.settings_manager import SettingsManager

LUKE = {"gid": 1001, "name": "Luke SkullHunter"}
MISHA = {"gid": 1002, "name": "Misha SkullSeer"}


def _displays(usernames, accounts):
    """Just the pill captions — most of these tests are about the text, not the color."""
    return [spec.display for spec in launch_cards.account_pill_specs(usernames, accounts)]


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


def _measure(text: str) -> int:
    """A stand-in font: every character is one unit wide."""
    return len(text)


def test_fitting_shows_every_name_that_fits():
    names = ["Aa", "Bb", "Cc"]
    assert launch_cards.fit_character_summary(names, 100, _measure) == "Aa · Bb · Cc"


def test_fitting_beats_the_fixed_count_when_there_is_room():
    """The complaint this exists for: a card with room for six names showed four."""
    names = [f"W{i}" for i in range(6)]
    fitted = launch_cards.fit_character_summary(names, 100, _measure)
    assert fitted == " · ".join(names)
    assert "more" not in fitted


def test_fitting_wraps_onto_further_rows_before_dropping_a_name():
    """The account column is short of width, not height, so a name that will not fit
    beside the others goes below them rather than into "+N more"."""
    names = ["Aaaa", "Bbbb", "Cccc", "Dddd"]
    assert launch_cards.fit_character_summary(names, 11, _measure) == "Aaaa · Bbbb\nCccc · Dddd"


def test_fitting_breaks_between_names_only():
    """Never inside one: a wizard name is two words, and Qt's own word wrap would split
    it across rows — which is why the rows are built here rather than left to QLabel."""
    rows = launch_cards.fit_character_summary(["Aaaa Bbbb", "Cccc Dddd"], 9, _measure).split("\n")
    assert rows == ["Aaaa Bbbb", "Cccc Dddd"]


def test_fitting_stops_at_the_row_limit():
    """Three rows is where a card stops being a card; the pill beside the name already
    says how many characters there are."""
    names = [f"Name{i}" for i in range(8)]
    fitted = launch_cards.fit_character_summary(names, 5, _measure)
    assert len(fitted.split("\n")) == launch_cards.MAX_CHARACTER_ROWS
    assert fitted.endswith("more")


def test_fitting_folds_the_overflow_into_a_count():
    names = ["Aaaa", "Bbbb", "Cccc", "Dddd"]
    # One row of 13: room for one name and the tail ("Aaaa  +3 more"), not two.
    fitted = launch_cards.fit_character_summary(names, 13, _measure, max_rows=1)
    assert fitted == "Aaaa  +3 more"


def test_fitting_lays_out_the_tail_it_adds():
    """A line that just fits without "+N more" does not fit with it, and the tail is
    what makes the difference — so candidates are laid out whole."""
    names = ["Aaaa", "Bbbb", "Cccc"]
    # "Aaaa · Bbbb" is 11 and would fit in 12, but "Aaaa · Bbbb  +1 more" is 20.
    assert launch_cards.fit_character_summary(names, 12, _measure, max_rows=1) == "Aaaa  +2 more"


def test_fitting_always_shows_one_name():
    """A card too narrow for a single name lets the label elide it — "+3 more" alone
    says nothing about who is on the account."""
    names = ["Aaaaaaaaaa", "Bb", "Cc", "Dd"]
    assert launch_cards.fit_character_summary(names, 4, _measure, max_rows=1).startswith("Aaaaaaaaaa")


def test_fitting_a_lone_name_never_appends_a_count():
    assert launch_cards.fit_character_summary(["Aaaaaaaaaa"], 4, _measure) == "Aaaaaaaaaa"


def test_fitting_falls_back_to_the_count_with_no_width():
    """The card's state before it has been laid out."""
    names = [f"W{i}" for i in range(6)]
    assert launch_cards.fit_character_summary(names, 0, _measure) == " · ".join(names)


def test_fitting_honors_streamer_mode_through_character_names():
    SettingsManager.instance().streamer_mode = True
    names = launch_cards.character_names([LUKE, MISHA])
    assert launch_cards.fit_character_summary(names, 100, _measure) == "Character 1 · Character 2"


def test_character_summary_honors_streamer_mode():
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
    assert _displays(["someone@example.com"], accounts) == ["Main"]


def test_account_pills_keep_launch_order():
    """A profile's account order decides which window becomes Player 1, so sorting these
    for looks would misreport what the profile does."""
    accounts = [
        {"username": "a@example.com", "alias": "Zeta"},
        {"username": "b@example.com", "alias": "Alpha"},
    ]
    assert _displays(["a@example.com", "b@example.com"], accounts) == ["Zeta", "Alpha"]


def test_a_deleted_account_still_gets_a_pill():
    """Dropping it would make a profile that cannot launch look complete."""
    assert _displays(["gone@example.com"], []) == ["g***m"]


def test_an_account_without_an_alias_is_censored():
    accounts = [{"username": "someone@example.com", "alias": ""}]
    assert _displays(["someone@example.com"], accounts) == ["s***m"]


def test_a_profile_pill_is_colored_by_its_account():
    """The whole point of the color: the pill and that account's own card match, so a
    profile's membership can be read by looking rather than by parsing four names."""
    account = {"username": "someone@example.com", "alias": "Main"}
    (spec,) = launch_cards.account_pill_specs(["someone@example.com"], [account])
    assert spec.color_key == account_accent.account_color_key("someone@example.com", account)


def test_a_pinned_color_beats_the_derived_one():
    account = {"username": "someone@example.com", "alias": "Main", "color": 4}
    (spec,) = launch_cards.account_pill_specs(["someone@example.com"], [account])
    assert spec.color_key == 4


def test_a_deleted_account_still_gets_a_color():
    """A gray pill would read as "no account color" rather than as the missing account
    it is, and there is no card left to match it against either way."""
    (spec,) = launch_cards.account_pill_specs(["gone@example.com"], [])
    assert 1 <= spec.color_key <= account_accent.ACCOUNT_PALETTE_SIZE


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


def test_the_launch_note_names_the_numbers_not_the_advice():
    """The advice is in the column banner and the button's tooltip already."""
    problem = FitProblem(resolution=(1280, 720), client_count=2, need=(2600, 760), available=(1900, 1000))

    note = launch_cards.launch_note_for(problem)

    assert note == "Doesn't fit: needs 2600×760, and 1900×1000 is available"


def test_the_launch_note_passes_a_plain_reason_through_and_is_empty_without_one():
    assert launch_cards.launch_note_for("needs more room") == "needs more room"
    assert launch_cards.launch_note_for(None) == ""
