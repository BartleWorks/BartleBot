"""New Bot without the dialog (10.3): the starting points, the bot it builds, and the file name rule."""

from __future__ import annotations

import pytest

from bartlebot.bot.schema import BotConfig, StateModel, TemplateMeta, TemplateRef
from bartlebot.new_bot import (
    FALLBACK_STEM,
    BotDetails,
    StartingPoint,
    clean_stem,
    create_bot,
    file_stem,
    library_folders,
    starting_points,
    unique_file_name,
)
from bartlebot.template_store import release, save_draft


def _draft(template_id: str = "tpl-boss", name: str = "Boss Farmer", blurb: str = "Farms one boss.") -> BotConfig:
    return BotConfig(
        id=template_id,
        name=name,
        initial="t_1",
        recovery_state="t_1",
        tags=["Gear", "Boss", "Team-Up"],
        states=[StateModel(id="t_1", node_type="handle_potions", params={})],
        transitions=[],
        template_meta=TemplateMeta(blurb=blurb),
    )


def _released(root, draft: BotConfig | None = None, note: str = "First release") -> BotConfig:
    draft = draft or _draft()
    save_draft(draft, root)
    release(draft.id, note, root=root)
    return draft


# ---------------------------------------------------------------------------
# Starting points
# ---------------------------------------------------------------------------


def test_a_released_template_is_offered_at_its_latest_version(tmp_path):
    _released(tmp_path)
    _released(tmp_path, _draft(name="Boss Farmer II"), note="Renamed")

    [point] = starting_points(tmp_path)

    assert (point.template_id, point.version, point.name) == ("tpl-boss", 2, "Boss Farmer II")
    assert point.blurb == "Farms one boss."


def test_a_template_with_only_a_draft_is_not_offered(tmp_path):
    save_draft(_draft(), tmp_path)

    assert starting_points(tmp_path) == []


def test_a_version_that_does_not_load_is_skipped_not_fatal(tmp_path):
    _released(tmp_path)
    _released(tmp_path, _draft("tpl-mob", "Mob Farmer"))
    (tmp_path / "templates" / "mob-farmer" / "v1.json").write_text("{", encoding="utf-8")

    assert [p.name for p in starting_points(tmp_path)] == ["Boss Farmer"]


def test_a_card_wears_its_templates_first_tag(tmp_path):
    _released(tmp_path)
    untagged = _draft("tpl-bare", "Bare")
    untagged.tags = []
    _released(tmp_path, untagged)

    bare, boss = starting_points(tmp_path)

    assert boss.icon_tag == "Gear"
    assert bare.icon_tag is None


def test_every_shipped_template_wears_a_different_tag():
    icons = {p.name: p.icon_tag for p in starting_points()}

    assert icons == {
        "Boss Farmer": "Boss",
        "Dungeon Farmer": "Dungeon",
        "Gauntlet Farmer": "Gauntlet",
        "Mob Farmer": "Mobs",
        "Reagent Collector": "Reagents",
        "Spellement Farmer": "Spellements",
    }


def test_a_card_shows_the_blurbs_first_paragraph(tmp_path):
    _released(tmp_path, _draft(blurb="Farms one boss.\n\n1) Do this.\n\n2) Then that."))

    [point] = starting_points(tmp_path)

    assert point.summary == "Farms one boss."


# ---------------------------------------------------------------------------
# create_bot
# ---------------------------------------------------------------------------


def _point(tmp_path) -> StartingPoint:
    _released(tmp_path)
    return starting_points(tmp_path)[0]


def test_a_bot_made_from_a_template_records_it_and_is_a_bot(tmp_path):
    point = _point(tmp_path)

    bot = create_bot(point, BotDetails(name="  Lamia  ", description="Farms Lamia.", tags=["Boss", "Jewels"]))

    assert bot.template == TemplateRef(id="tpl-boss", version=1)
    assert bot.template_meta is None and not bot.is_template
    assert bot.id and bot.id != point.template_id
    assert (bot.name, bot.description, bot.tags) == ("Lamia", "Farms Lamia.", ["Jewels", "Boss"])
    assert [s.id for s in bot.states] == ["t_1"]


def test_creating_a_bot_leaves_the_starting_point_as_it_was(tmp_path):
    point = _point(tmp_path)
    before = point.config.model_dump()

    create_bot(point, BotDetails(name="Lamia"))

    assert point.config.model_dump() == before


def test_two_bots_from_one_template_get_two_ids(tmp_path):
    point = _point(tmp_path)

    assert create_bot(point, BotDetails(name="A")).id != create_bot(point, BotDetails(name="B")).id


def test_an_empty_description_stays_empty(tmp_path):
    point = _point(tmp_path)

    assert create_bot(point, BotDetails(name="Lamia", description="   ")).description is None
    assert create_bot(None, BotDetails(name="Lamia")).description is None


def test_a_blank_bot_has_no_nodes_and_no_template():
    bot = create_bot(None, BotDetails(name="Scratch", tags=["Mobs"]))

    assert bot.template is None and bot.template_meta is None
    assert bot.states == [] and bot.transitions == []
    assert (bot.initial, bot.recovery_state) == ("", ""), "nothing for either role to name yet"
    assert (bot.name, bot.tags) == ("Scratch", ["Mobs"])
    assert bot.id


def test_the_shipped_store_offers_every_template():
    names = [p.name for p in starting_points()]

    assert len(names) >= 6
    assert {"Boss Farmer", "Dungeon Farmer"} <= set(names)


@pytest.mark.parametrize("point", starting_points(), ids=lambda p: p.name)
def test_a_bot_from_every_shipped_template_survives_a_load_and_save(point, tmp_path):
    bot = create_bot(point, BotDetails(name="Made from a template"))
    path = tmp_path / "bot.json"
    bot.save(path)
    written = path.read_bytes()

    loaded = BotConfig.load(path)
    loaded.save(path)

    assert path.read_bytes() == written
    assert loaded.template == TemplateRef(id=point.template_id, version=point.version)
    assert loaded.template_meta is None
    assert loaded.id != point.template_id
    assert loaded.description is None
    assert b"template_meta" not in written


# ---------------------------------------------------------------------------
# The file name
# ---------------------------------------------------------------------------


def test_a_file_stem_is_the_names_slug():
    assert file_stem("Lamia's Lair (Solo)") == "lamia-s-lair-solo"


def test_a_name_with_nothing_to_slug_still_gets_a_stem():
    assert file_stem("!!!") == FALLBACK_STEM


@pytest.mark.parametrize(
    ("typed", "stem"),
    [
        ("My Bot", "My Bot"),
        ("my_bot.json", "my_bot"),
        ("  a/b\\c:d  ", "abcd"),
        ("trailing. ", "trailing"),
        ('<>:"|?*', FALLBACK_STEM),
    ],
)
def test_a_typed_stem_is_kept_as_typed_but_made_safe(typed, stem):
    assert clean_stem(typed) == stem


def test_a_free_name_is_used_as_it_is(tmp_path):
    assert unique_file_name("lamia", tmp_path) == "lamia.json"


def test_a_name_that_clashes_is_numbered_past_every_one_taken(tmp_path):
    (tmp_path / "lamia.json").write_text("{}", encoding="utf-8")
    assert unique_file_name("lamia", tmp_path) == "lamia-2.json"

    (tmp_path / "LAMIA-2.json").write_text("{}", encoding="utf-8")
    assert unique_file_name("lamia", tmp_path) == "lamia-3.json"


def test_a_folder_that_does_not_exist_yet_holds_nothing(tmp_path):
    assert unique_file_name("lamia", tmp_path / "13_aquila") == "lamia.json"


def test_the_library_folders_are_listed_in_folder_order_empty_ones_too(tmp_path):
    for name in ("13_aquila", "02_krokotopia", "subgraphs", ".git"):
        (tmp_path / name).mkdir()
    (tmp_path / "loose.json").write_text("{}", encoding="utf-8")

    assert library_folders(tmp_path) == ["02_krokotopia", "13_aquila"]
    assert library_folders(tmp_path / "missing") == []


def test_a_templates_authors_are_not_the_new_bots(tmp_path):
    """Whoever wrote the template did not write this bot. It used to say they had."""
    point = _point(tmp_path)
    point.config.authors = "BartleBot"

    assert create_bot(point, BotDetails(name="Lamia")).authors is None
    assert create_bot(point, BotDetails(name="Lamia", authors="  Luke SkullHunter ")).authors == "Luke SkullHunter"
    assert create_bot(None, BotDetails(name="Scratch", authors="Luke SkullHunter")).authors == "Luke SkullHunter"
    assert point.config.authors == "BartleBot"
