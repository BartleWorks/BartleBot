"""Tests for the zone picker's data half: destinations by world, their labels, and the broad filter.

No QApplication: zone_sections, describe_zone and filter_sections are plain functions.
"""

import json

import pytest

from bartlebot import card_art, zone_nav
from bartlebot.gui.widgets.picker_popup import PickerRow, filter_sections
from bartlebot.gui.widgets.zone_picker import (
    SUGGESTED_LIMIT,
    SUGGESTED_TITLE,
    describe_zone,
    is_destination,
    world_icon_resolver,
    world_keys_by_title,
    zone_id,
    zone_sections,
)

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


def test_the_suggestions_stop_at_the_limit_counting_only_destinations():
    zones = {f"WizardCity/WC_Zone{i}": {} for i in range(SUGGESTED_LIMIT + 3)}
    nav = zone_nav.parse({"worlds": {"WizardCity": {"hub": "WizardCity/WC_Zone0", "zones": zones}}})

    title, rows = zone_sections(nav, ["Housing/Castle", *zones])[0]

    assert title == SUGGESTED_TITLE
    assert [row.key for row in rows] == list(zones)[:SUGGESTED_LIMIT]


def test_no_suggestions_means_no_suggestion_section():
    assert SUGGESTED_TITLE not in dict(zone_sections(_nav()))


def test_a_zone_is_described_world_first_with_its_id_and_an_unknown_one_is_marked():
    assert describe_zone(_nav(), _ENTRANCE).startswith("Krokotopia · ")
    assert describe_zone(_nav(), _ENTRANCE).endswith(" (KT_EntranceHall)")
    assert describe_zone(_nav(), "Nowhere/Zone") == "Nowhere/Zone (not on the map)"


def test_only_a_listed_zone_is_a_destination():
    assert is_destination(_nav(), _ENTRANCE)
    assert not is_destination(_nav(), _DOJO)
    assert not is_destination(_nav(), "Nowhere/Zone")


def test_the_shipped_map_keeps_pass_through_zones_out_of_the_list():
    nav = zone_nav.load()

    for zone in (
        "WizardCity/Interiors/WC_Housing_Dorm_Interior",
        "MooShu/Interiors/MS_RockDojoT2",
        "MooShu/Interiors/MS_WaterDojot1_Common",
        "Khrysalis/Interiors/KR_Z11_I07_MagiHouseMain",
    ):
        assert zone in nav.world_of(zone).zones
        assert not is_destination(nav, zone)


# -- section emblems ---------------------------------------------------------


@pytest.fixture
def art_home(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    card_art.forget_cache()
    yield tmp_path
    card_art.forget_cache()


def test_every_section_title_maps_back_to_its_world_key():
    """The sections are captioned with the localized display name; this is how a header
    gets back to the key its emblem is stored under."""
    assert world_keys_by_title(_nav()) == {"Wizard City": "WizardCity", "Krokotopia": "Krokotopia"}


def test_the_suggestions_section_is_not_a_world():
    """ "Recent Zones" spans worlds, so an emblem there would claim one."""
    assert SUGGESTED_TITLE not in world_keys_by_title(_nav())


def test_every_world_on_the_shipped_map_has_a_title_that_resolves():
    """A world whose label collided with another's would silently lose its emblem."""
    nav = zone_nav.load()

    titles = world_keys_by_title(nav)

    assert len(titles) == len(nav.worlds), "two worlds share a display name"
    assert set(titles.values()) == set(nav.worlds)


def test_nothing_extracted_resolves_every_section_to_no_icon(art_home):
    """The ordinary install: the header draws its caption alone."""
    resolve = world_icon_resolver(_nav())

    assert resolve("Krokotopia") is None
    assert resolve(SUGGESTED_TITLE) is None


def test_a_title_that_is_not_a_world_never_looks_up_an_icon(art_home):
    card_art.icons_dir().mkdir(parents=True, exist_ok=True)
    card_art.index_path().write_text(json.dumps({"icons": {"world_krokotopia": "k.png"}}), encoding="utf-8")

    assert world_icon_resolver(_nav())("Not A World") is None


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
