"""Tests for the zone picker's data half: destinations by world, their labels, and the broad filter.

No QApplication: zone_sections, describe_zone and filter_sections are plain functions.
"""

import json

import pytest

from bartlebot import card_art, zone_nav
from bartlebot.gui.widgets.picker_popup import PickerRow, filter_sections
from bartlebot.gui.widgets.zone_picker import (
    SUGGESTED_TITLE,
    ListZoneButton,
    describe_zone,
    is_destination,
    list_destination_sections,
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
    """ "Where players are" spans worlds, so an emblem there would claim one."""
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


# ── The list-destination picker ────────────────────────────────────────────────

_LIST_HUB, _LIST_A, _LIST_B = "Krokotopia/KT_Hub", "Krokotopia/KT_A", "Krokotopia/KT_B"
_WC_HUB = "WizardCity/WC_Hub"


def _list_nav() -> zone_nav.NavMap:
    """Two lists: Krokotopia's, with an object in its hub and in A and B a destination only, and
    Wizard City's, which shares no zone with it."""
    spot = {"interact_xyz": {"x": 1.0, "y": 0.0, "z": 0.0}, "reset_xyz": {"x": 2.0, "y": 0.0, "z": 0.0}}
    return zone_nav.parse(
        {
            "worlds": {
                "Krokotopia": {
                    "hub": _LIST_HUB,
                    "zones": {_LIST_HUB: {}, _LIST_A: {}, _LIST_B: {}, _DOJO: {"destination": False}},
                    "list_networks": [
                        {"zones": {_LIST_HUB: spot, _LIST_A: spot, _LIST_B: {}}, "verified": True, "source": "code"}
                    ],
                },
                "WizardCity": {
                    "hub": _WC_HUB,
                    "zones": {_WC_HUB: {}, _SHOPS: {}},
                    "list_networks": [{"zones": {_WC_HUB: spot, _SHOPS: spot}, "verified": True, "source": "code"}],
                },
            }
        }
    )


def test_the_destinations_are_one_section_titled_by_the_world():
    [(title, rows)] = list_destination_sections(_list_nav(), _LIST_HUB)

    assert title == "Krokotopia"
    assert {row.key for row in rows} == {_LIST_A, _LIST_B}
    assert {row.meta for row in rows} == {"KT_A", "KT_B"}


def test_a_zone_with_no_list_object_has_no_destinations_to_offer():
    assert list_destination_sections(_list_nav(), _LIST_B) == []
    assert list_destination_sections(_list_nav(), _DOJO) == []


def test_the_button_says_which_field_to_fill_in_first(qapp):
    button = ListZoneButton(lambda: "", nav=_list_nav())

    assert button.text() == "Pick a Zone first"


def test_the_button_names_the_destination_it_holds(qapp):
    button = ListZoneButton(lambda: _LIST_HUB, nav=_list_nav())
    button.set_value(_LIST_A)
    button.refresh()

    assert "KT_A" in button.text()


def test_moving_the_zone_to_another_list_drops_a_destination_it_does_not_reach(qapp):
    """B's row would otherwise stay showing, and go into the bot file, for a list that cannot reach it."""
    source = _LIST_HUB
    button = ListZoneButton(lambda: source, nav=_list_nav())
    button.set_value(_LIST_B)

    source = _WC_HUB
    button.refresh()

    assert button.value() == ""


def test_moving_the_zone_to_one_with_no_list_at_all_drops_the_destination(qapp):
    source = _LIST_HUB
    button = ListZoneButton(lambda: source, nav=_list_nav())
    button.set_value(_LIST_B)

    source = _DOJO
    button.refresh()

    assert button.value() == ""


def test_moving_the_zone_keeps_a_destination_the_new_list_still_reaches(qapp):
    source = _LIST_HUB
    button = ListZoneButton(lambda: source, nav=_list_nav())
    button.set_value(_LIST_B)

    source = _LIST_HUB
    button.refresh()

    assert button.value() == _LIST_B


def test_rebuilding_without_pruning_keeps_a_stale_destination(qapp):
    """A file whose destination has gone stale says so by failing when it runs, not by being
    quietly emptied the moment it is opened."""
    source = _LIST_HUB
    button = ListZoneButton(lambda: source, nav=_list_nav())
    button.set_value(_LIST_B)

    source = _DOJO
    button.refresh(prune=False)

    assert button.value() == _LIST_B
