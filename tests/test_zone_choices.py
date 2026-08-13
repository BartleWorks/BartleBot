"""Tests for merge_zone_choices — what the canvas zone dropdown offers.

The list is assembled from two sources: the zones clients are standing in right
now, read off the player cards, and the zones remembered from previous sessions.
Whatever comes out is written into a bot config when the user picks it, so a
placeholder leaking through is a real defect, not a cosmetic one.
"""

from bartlebot.app_controller import merge_zone_choices


class TestLiveZones:
    def test_live_zones_come_first(self) -> None:
        live, choices = merge_zone_choices(["WC/Commons"], ["KT/Krokosphinx", "MB/Marleybone"])

        assert live == ["WC/Commons"]
        assert choices == ["WC/Commons", "KT/Krokosphinx", "MB/Marleybone"]

    def test_duplicate_live_zones_collapse_keeping_first_seen_order(self) -> None:
        """Four clients in the same place is the normal case, not the exception."""
        live, choices = merge_zone_choices(["WC/Commons", "KT/Bazaar", "WC/Commons", "WC/Commons"], [])

        assert live == ["WC/Commons", "KT/Bazaar"]
        assert choices == ["WC/Commons", "KT/Bazaar"]

    def test_blank_and_placeholder_zones_are_dropped(self) -> None:
        """A card shows "—" before its first successful read, and while loading."""
        live, choices = merge_zone_choices(["—", "", "WC/Commons", "—"], [])

        assert live == ["WC/Commons"]
        assert choices == ["WC/Commons"]

    def test_all_unreadable_leaves_only_the_recents(self) -> None:
        live, choices = merge_zone_choices(["—", "—"], ["KT/Bazaar"])

        assert live == []
        assert choices == ["KT/Bazaar"]


class TestRecentZones:
    def test_no_clients_returns_recents_unchanged(self) -> None:
        live, choices = merge_zone_choices([], ["KT/Bazaar", "MB/Marleybone"])

        assert live == []
        assert choices == ["KT/Bazaar", "MB/Marleybone"]

    def test_a_recent_zone_that_is_also_live_is_not_listed_twice(self) -> None:
        live, choices = merge_zone_choices(["WC/Commons"], ["KT/Bazaar", "WC/Commons"])

        assert live == ["WC/Commons"]
        assert choices == ["WC/Commons", "KT/Bazaar"]

    def test_recent_order_is_preserved(self) -> None:
        _, choices = merge_zone_choices([], ["c", "a", "b"])

        assert choices == ["c", "a", "b"]


def test_nothing_anywhere_gives_an_empty_dropdown() -> None:
    assert merge_zone_choices([], []) == ([], [])
