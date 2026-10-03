"""The entity-name snapshot, and what a caller gets when it cannot answer.

Resolution feeds a display column only, so a miss is the ordinary case rather than an
error -- see the module docstring. These pin that the fallback stays a fallback: `label`
never returns an empty string, and `display_name` never invents one.
"""

import json

import pytest

from bartlebot import entity_names


@pytest.fixture
def snapshot(tmp_path):
    path = tmp_path / "entity_names.json"
    path.write_text(json.dumps({"names": {"AQ-Centaur-Artemis": "Crescent Moon Centaur"}}), encoding="utf-8")
    return path


def test_a_named_entity_resolves(snapshot):
    assert entity_names.display_name("AQ-Centaur-Artemis", snapshot) == "Crescent Moon Centaur"


def test_an_unnamed_entity_is_none_not_an_error(snapshot):
    assert entity_names.display_name("WC-Scenery-Rock07", snapshot) is None


def test_an_empty_object_name_resolves_to_none(snapshot):
    assert entity_names.display_name("", snapshot) is None


def test_label_falls_back_to_the_object_name(snapshot):
    assert entity_names.label("WC-Scenery-Rock07", snapshot) == "WC-Scenery-Rock07"


def test_a_missing_snapshot_degrades_rather_than_raising(tmp_path):
    assert entity_names.label("AQ-Centaur-Artemis", tmp_path / "gone.json") == "AQ-Centaur-Artemis"


def test_the_shipped_snapshot_resolves_a_known_entity():
    # A guard on the checked-in file itself: a dump that wrote the wrong key shape, or an
    # empty "names", would pass every test above on its fixture and ship a dead column.
    assert entity_names.display_name("AQ-Centaur-Artemis") == "Crescent Moon Centaur"


# ---------------------------------------------------------------------------
# reagent_node_ids -- the IDs entity_names.json drops, having no display name
# ---------------------------------------------------------------------------


def test_reagent_node_ids_reads_the_ids(tmp_path):
    path = tmp_path / "reagent_nodes.json"
    path.write_text(json.dumps({"ids": ["Ore", "Stone"]}), encoding="utf-8")
    assert entity_names.reagent_node_ids(path) == {"Ore", "Stone"}


def test_a_missing_reagent_snapshot_is_empty_rather_than_raising(tmp_path):
    assert entity_names.reagent_node_ids(tmp_path / "gone.json") == frozenset()


def test_the_shipped_reagent_snapshot_keeps_the_games_own_spelling():
    # "Scraplron" (a lowercase L) is the real ID; a hand "fix" to ScrapIron would make the
    # scrap iron bot collect nothing. The others pin the selection rule: a node outside
    # Reagent_Nodes/ (the Mooshu raid), and a stem that is not the ID (Antiquitie.xml).
    ids = entity_names.reagent_node_ids()
    assert {"Ore", "Stone", "Scraplron", "Raid_MS_Essence_Fire", "Antiquity"} <= ids
    assert "ScrapIron" not in ids
    assert "Black Lotus_PAGauntlet" not in ids  # same model, but not harvestable
