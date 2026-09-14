"""Tests for the zone picker's data half: destinations by world, their labels, and the broad filter.

No QApplication: zone_sections, describe_zone and filter_sections are plain functions.
"""

from bartlebot import zone_nav
from bartlebot.gui.widgets.picker_popup import PickerRow, filter_sections
from bartlebot.gui.widgets.zone_picker import SUGGESTED_TITLE, describe_zone, zone_id, zone_sections

_HUB = "Krokotopia/KT_Hub"
_ENTRANCE = "Krokotopia/KT_Krokosphinx/KT_EntranceHall"
_DOJO = "Krokotopia/KT_Dojo"
_SHOPS = "WizardCity/WC_Shop_Area"


def _nav() -> zone_nav.NavMap:
    return zone_nav.parse(
        {
            "worlds": {
                "WizardCity": {"hub": "WizardCity/WC_Hub", "zones": {"WizardCity/WC_Hub": {}, _SHOPS: {}}},
                "Krokotopia": {
                    "hub": _HUB,
                    "zones": {_HUB: {}, _ENTRANCE: {}, _DOJO: {"destination": False}},
                },
            }
        }
    )


def test_every_destination_is_listed_under_its_world_and_connectors_are_not():
    sections = dict(zone_sections(_nav()))

    assert list(sections) == ["Krokotopia", "Wizard City"]
    assert {row.key for row in sections["Krokotopia"]} == {_HUB, _ENTRANCE}
    labels = [row.label for row in sections["Krokotopia"]]
    assert labels == sorted(labels, key=str.casefold)


def test_a_row_carries_its_zone_id_and_full_path():
    [row] = [r for r in dict(zone_sections(_nav()))["Krokotopia"] if r.key == _ENTRANCE]

    assert row.meta == zone_id(_ENTRANCE) == "KT_EntranceHall"
    assert row.description == _ENTRANCE


def test_zones_the_players_are_in_come_first_when_they_are_destinations():
    sections = zone_sections(_nav(), [_DOJO, _SHOPS, "Housing/Castle", _SHOPS])

    title, rows = sections[0]
    assert title == SUGGESTED_TITLE
    assert [row.key for row in rows] == [_SHOPS]
    assert rows[0].meta == "Wizard City"


def test_no_suggestions_means_no_suggestion_section():
    assert SUGGESTED_TITLE not in dict(zone_sections(_nav()))


def test_a_zone_is_described_with_its_world_and_an_unknown_one_is_marked():
    assert describe_zone(_nav(), _ENTRANCE).endswith(" · Krokotopia")
    assert describe_zone(_nav(), "Nowhere/Zone") == "Nowhere/Zone (not on the map)"


_ROWS_K = [PickerRow("k1", "Entrance Hall", meta="KT_EntranceHall"), PickerRow("k2", "The Oasis", meta="KT_Hub")]
_ROWS_C = [PickerRow("c1", "Entrance", meta="CL_Z01")]
_SECTIONS = [("Krokotopia", _ROWS_K), ("Celestia", _ROWS_C)]


def test_every_term_has_to_match_the_row_or_its_world():
    assert filter_sections(_SECTIONS, "kroko entr", broad=True) == [("Krokotopia", [_ROWS_K[0]])]


def test_a_worlds_name_alone_keeps_its_whole_section():
    assert filter_sections(_SECTIONS, "celestia", broad=True) == [("Celestia", _ROWS_C)]


def test_a_zone_id_finds_its_row():
    assert filter_sections(_SECTIONS, "kt_hub", broad=True) == [("Krokotopia", [_ROWS_K[1]])]


def test_the_default_filter_still_ignores_section_titles_and_meta():
    assert filter_sections(_SECTIONS, "celestia") == []
    assert filter_sections(_SECTIONS, "kt_hub") == []
