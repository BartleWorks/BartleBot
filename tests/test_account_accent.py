"""Tests for the account colour mapping.

What matters about this mapping is not which hue an account gets — nobody can name
that — but that it is the *same* hue everywhere and every time. A colour that moves
between restarts, or that differs between a profile pill and the account card it is
supposed to point at, is worse than no colour at all: it invites a match that is wrong.
"""

from bartlebot.gui import account_accent
from bartlebot.gui.account_accent import ACCOUNT_PALETTE_SIZE, account_color_key, account_color_keys
from bartlebot.gui.account_accent import account_accent as accent


def _accounts(n: int) -> list[dict]:
    return [{"username": f"user{i}@example.com"} for i in range(n)]


def test_a_username_always_lands_in_the_palette():
    for i in range(50):
        assert 1 <= account_color_key(f"user{i}@example.com") <= ACCOUNT_PALETTE_SIZE


def test_the_same_username_always_gets_the_same_slot():
    """Derived with blake2s rather than hash(): Python salts string hashing per process,
    so hash() would repaint the whole launch screen on every restart."""
    assert account_color_key("someone@example.com") == account_color_key("someone@example.com")


def test_the_derived_slot_is_stable_across_releases():
    """Pinned deliberately. Changing the derivation is allowed, but it recolours every
    account of every existing user, so it should be a decision rather than a side effect."""
    assert account_color_key("someone@example.com") == 1


def test_the_palette_is_actually_used():
    """A derivation that piles every account into one slot would pass every test above."""
    keys = {account_color_key(f"user{i}@example.com") for i in range(50)}
    assert len(keys) == ACCOUNT_PALETTE_SIZE


# ---------------------------------------------------------------------------
# Assignment across a list
# ---------------------------------------------------------------------------


def test_a_full_fleet_gets_a_colour_each():
    """The bug this function exists for: derived slots collide like birthdays, and six
    accounts dropped independently into six slots averaged under four distinct colours.
    A palette that repeats is worse than no palette — the repeat invites a wrong match."""
    for count in range(1, ACCOUNT_PALETTE_SIZE + 1):
        keys = account_color_keys(_accounts(count))
        assert len(set(keys.values())) == count, f"{count} accounts did not get {count} colours"


def test_every_account_gets_a_slot():
    keys = account_color_keys(_accounts(5))
    assert set(keys) == {f"user{i}@example.com" for i in range(5)}
    assert all(1 <= k <= ACCOUNT_PALETTE_SIZE for k in keys.values())


def test_more_accounts_than_slots_repeat_as_evenly_as_possible():
    """One colour doubled rather than one colour tripled."""
    keys = account_color_keys(_accounts(ACCOUNT_PALETTE_SIZE + 2))
    counts = {slot: list(keys.values()).count(slot) for slot in set(keys.values())}
    assert set(counts.values()) <= {1, 2}
    assert len(counts) == ACCOUNT_PALETTE_SIZE


def test_reordering_the_accounts_repaints_nothing():
    """Accounts can be dragged into any order in Settings. That is a presentational act
    and must not recolour the launch screen, which is why assignment sorts usernames."""
    accounts = _accounts(6)
    assert account_color_keys(accounts) == account_color_keys(list(reversed(accounts)))


def _uncontested(accounts: list[dict]) -> set[str]:
    """The usernames whose derived slot nothing else in *accounts* wants."""
    prefs = [account_color_key(a["username"]) for a in accounts]
    return {a["username"] for a, p in zip(accounts, prefs) if prefs.count(p) == 1}


def test_an_uncontested_preference_is_always_honoured():
    """The guarantee that makes the colours stable: an account moves only when something
    else wants its slot, so adding or deleting one recolours nothing it does not collide
    with. It fails without the second pass — a displaced account probing forward takes a
    slot some account further down the list was about to derive, and displaces it too."""
    for count in range(1, ACCOUNT_PALETTE_SIZE + 3):
        accounts = _accounts(count)
        keys = account_color_keys(accounts)
        for username in _uncontested(accounts):
            assert keys[username] == account_color_key(username), f"{username} moved among {count} accounts"


def test_adding_an_account_leaves_the_ones_it_does_not_collide_with_alone():
    accounts = _accounts(4)
    before = account_color_keys(accounts)
    grown = accounts + [{"username": "newcomer@example.com"}]
    after = account_color_keys(grown)

    settled = _uncontested(accounts) & _uncontested(grown)
    assert settled  # otherwise the assertion below is vacuous
    assert all(after[u] == before[u] for u in settled)


def test_a_pin_outranks_a_derived_preference():
    """An account that says which colour it is beats one that merely wanted the slot."""
    wanted = account_color_key("someone@example.com")
    accounts = [{"username": "pinner@example.com", "color": wanted}, {"username": "someone@example.com"}]
    keys = account_color_keys(accounts)
    assert keys["pinner@example.com"] == wanted
    assert keys["someone@example.com"] != wanted


def test_pinning_a_colour_repaints_nothing_it_does_not_collide_with():
    """The same guarantee as adding an account, now that a picker can trigger it (#196).
    A user correcting one account's colour must not find the rest of the fleet rearranged
    underneath them — only the account whose slot was actually taken may move."""
    accounts = _accounts(5)
    before = account_color_keys(accounts)
    pinner = accounts[0]["username"]
    free = next(s for s in range(1, ACCOUNT_PALETTE_SIZE + 1) if s not in before.values())

    pinned = [dict(a) for a in accounts]
    pinned[0]["color"] = free
    after = account_color_keys(pinned)

    assert after[pinner] == free
    assert all(after[u] == before[u] for u in before if u != pinner)


def test_pinning_onto_a_taken_slot_moves_only_the_account_that_held_it():
    accounts = _accounts(5)
    before = account_color_keys(accounts)
    pinner = accounts[0]["username"]
    victim = next(u for u in sorted(_uncontested(accounts)) if u != pinner)

    pinned = [dict(a) for a in accounts]
    pinned[0]["color"] = before[victim]
    after = account_color_keys(pinned)

    assert after[pinner] == before[victim]
    assert after[victim] != before[victim]
    settled = _uncontested(accounts) - {pinner, victim}
    assert settled  # otherwise the assertion below is vacuous
    assert all(after[u] == before[u] for u in settled)


def test_two_accounts_may_pin_the_same_colour():
    """A pin is the user saying what they want. Nothing here overrules it — if two
    accounts are told to be blue, they are blue."""
    accounts = [{"username": "a@example.com", "color": 2}, {"username": "b@example.com", "color": 2}]
    assert account_color_keys(accounts) == {"a@example.com": 2, "b@example.com": 2}


def test_an_empty_list_assigns_nothing():
    assert account_color_keys([]) == {}


def test_an_account_can_pin_its_colour():
    assert account_color_key("someone@example.com", {"color": 2}) == 2


def test_a_pinned_colour_outside_the_palette_is_ignored():
    """A stored 12 is a bug or a palette that shrank, and deriving says more than
    clamping every such account onto the last slot."""
    derived = account_color_key("someone@example.com")
    assert account_color_key("someone@example.com", {"color": 12}) == derived
    assert account_color_key("someone@example.com", {"color": 0}) == derived
    assert account_color_key("someone@example.com", {"color": "blue"}) == derived


def test_a_boolean_is_not_a_colour():
    """True is an int in Python and 1 is a valid slot, so a stray flag would silently
    pin every account it touched to the same hue."""
    assert account_color_key("someone@example.com", {"color": True}) == account_color_key("someone@example.com")


def test_every_slot_resolves_to_a_colour():
    for key in range(1, ACCOUNT_PALETTE_SIZE + 1):
        assert accent(key).startswith("#")


def test_the_palette_has_no_duplicate_hues():
    """Two accounts sharing a slot is a collision the derivation accepts; two slots
    sharing a colour is one nothing can tell apart."""
    assert len({accent(k) for k in range(1, ACCOUNT_PALETTE_SIZE + 1)}) == ACCOUNT_PALETTE_SIZE


def test_a_tint_is_the_slot_colour_at_low_alpha():
    """Alpha rather than a colour pre-blended against the surface: a launch card changes
    its background on hover, and a blended fill would show as a rectangle inside it."""
    assert account_accent.account_tint(1) == "rgba(59, 130, 246, 22%)"
