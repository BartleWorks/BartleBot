"""The template store: folders, drafts, released versions, and the ids a draft must avoid."""

from __future__ import annotations

import hashlib
import json
from datetime import date

import pytest

from bartlebot.bot.schema import BotConfig, RegionModel, StateModel, SubgraphSource, TemplateMeta, TemplateRef
from bartlebot.paths import project_root
from bartlebot.template_store import (
    DRAFT_FILE,
    INDEX_FILE,
    TEMPLATE_ID_PREFIX,
    ReleaseRefused,
    TemplateIndex,
    find_template,
    latest_version,
    list_templates,
    load_draft,
    load_version,
    release,
    retired_ids,
    save_draft,
    used_ids,
)


def _draft(template_id: str = "tpl-boss", name: str = "Boss Farmer", *state_ids: str) -> BotConfig:
    ids = state_ids or ("t_1",)
    return BotConfig(
        id=template_id,
        name=name,
        initial=ids[0],
        recovery_state=ids[0],
        states=[StateModel(id=sid, node_type="handle_potions", params={}) for sid in ids],
        transitions=[],
        template_meta=TemplateMeta(blurb="Farms one boss."),
    )


def _release(root, config: BotConfig, note: str = "Release") -> int:
    """Save ``config`` as the draft and release it, as the maintainer does."""
    save_draft(config, root)
    return release(config.id, note, root=root, today=date(2026, 10, 1)).version


# ---------------------------------------------------------------------------
# Drafts and the folder layout
# ---------------------------------------------------------------------------


def test_a_new_draft_gets_a_folder_named_from_it_and_an_empty_index(tmp_path):
    path = save_draft(_draft(), tmp_path)

    assert path == tmp_path / "templates" / "boss-farmer" / DRAFT_FILE
    index = TemplateIndex.load(path.parent / INDEX_FILE)
    assert index.id == "tpl-boss"
    assert index.versions == []


def test_saving_a_draft_again_keeps_its_folder_whatever_it_is_called_now(tmp_path):
    first = save_draft(_draft(), tmp_path)
    renamed = _draft(name="Boss Farmer, Revised")

    assert save_draft(renamed, tmp_path) == first
    assert load_draft("tpl-boss", tmp_path).name == "Boss Farmer, Revised"


def test_two_templates_with_one_name_get_two_folders(tmp_path):
    a = save_draft(_draft("tpl-a", "Boss Farmer"), tmp_path)
    b = save_draft(_draft("tpl-b", "Boss Farmer"), tmp_path)

    assert a.parent.name == "boss-farmer"
    assert b.parent.name == "boss-farmer-2"


def test_a_bot_is_not_saved_as_a_draft(tmp_path):
    bot = _draft()
    bot.template_meta = None

    with pytest.raises(ValueError, match="not a template draft"):
        save_draft(bot, tmp_path)
    assert not (tmp_path / "templates").exists()


def test_a_missing_draft_is_an_error_not_a_blank_one(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_draft("nope", tmp_path)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def test_listing_is_sorted_by_name_and_skips_a_folder_with_no_index(tmp_path):
    save_draft(_draft("tpl-z", "Zeta"), tmp_path)
    save_draft(_draft("tpl-a", "Alpha"), tmp_path)
    (tmp_path / "templates" / "stray").mkdir()
    (tmp_path / "templates" / "broken").mkdir()
    (tmp_path / "templates" / "broken" / INDEX_FILE).write_text("{not json", encoding="utf-8")

    assert [t.name for t in list_templates(tmp_path)] == ["Alpha", "Zeta"]


def test_no_store_lists_nothing(tmp_path):
    assert list_templates(tmp_path) == []


def test_a_template_is_named_by_its_latest_release_not_its_draft(tmp_path):
    """New Bot builds its cards from released versions; a draft's rename is not released yet."""
    save_draft(_draft(), tmp_path)
    _release(tmp_path, _draft())
    save_draft(_draft(name="Boss Farmer (work in progress)"), tmp_path)

    assert find_template("tpl-boss", tmp_path).name == "Boss Farmer"


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


def test_nothing_is_released_until_release(tmp_path):
    save_draft(_draft(), tmp_path)

    assert latest_version("tpl-boss", tmp_path) is None
    assert latest_version("unknown", tmp_path) is None


def test_the_latest_version_is_the_highest_released(tmp_path):
    save_draft(_draft(), tmp_path)
    _release(tmp_path, _draft("tpl-boss", "Boss Farmer", "t_1"), "First release")
    _release(tmp_path, _draft("tpl-boss", "Boss Farmer", "t_1", "t_2"), "Adds a node")

    assert latest_version("tpl-boss", tmp_path) == 2
    assert [s.id for s in load_version("tpl-boss", 1, tmp_path).states] == ["t_1"]
    assert [s.id for s in load_version("tpl-boss", 2, tmp_path).states] == ["t_1", "t_2"]


def test_a_version_never_released_is_an_error(tmp_path):
    save_draft(_draft(), tmp_path)
    _release(tmp_path, _draft())

    with pytest.raises(LookupError):
        load_version("tpl-boss", 2, tmp_path)


def test_used_ids_are_every_id_any_version_used_retired_ones_included(tmp_path):
    save_draft(_draft(), tmp_path)
    _release(tmp_path, _draft("tpl-boss", "Boss Farmer", "t_1", "t_2"))
    v2 = _draft("tpl-boss", "Boss Farmer", "t_1", "t_3")
    v2.regions = [
        RegionModel(
            id="t_sync_players_1",
            label="Sync players",
            members=["t_1"],
            color=1,
            source=SubgraphSource(subgraph_id="sg", version=1),
        )
    ]
    _release(tmp_path, v2)

    assert used_ids("tpl-boss", tmp_path) == {"t_1", "t_2", "t_3", "t_sync_players_1"}


def test_a_draft_with_nothing_released_has_no_used_ids(tmp_path):
    save_draft(_draft(), tmp_path)

    assert used_ids("tpl-boss", tmp_path) == frozenset()


# ---------------------------------------------------------------------------
# The two new fields on BotConfig
# ---------------------------------------------------------------------------


def test_a_bot_without_either_field_writes_neither(tmp_path):
    bot = _draft()
    bot.template_meta = None
    bot.save(tmp_path / "bot.json")

    data = json.loads((tmp_path / "bot.json").read_text(encoding="utf-8"))
    assert "template" not in data
    assert "template_meta" not in data
    assert not bot.is_template


def test_a_bot_made_from_a_template_keeps_its_base_through_a_save(tmp_path):
    bot = _draft()
    bot.template_meta = None
    bot.template = TemplateRef(id="tpl-boss", version=3)
    bot.save(tmp_path / "bot.json")

    loaded = BotConfig.load(tmp_path / "bot.json")
    assert loaded.template == TemplateRef(id="tpl-boss", version=3)
    loaded.save(tmp_path / "again.json")
    assert (tmp_path / "again.json").read_bytes() == (tmp_path / "bot.json").read_bytes()


# ---------------------------------------------------------------------------
# The shipped templates
# ---------------------------------------------------------------------------


def _shipped():
    templates = list_templates(project_root())
    assert templates, "no shipped template, so this checks nothing"
    return templates


def test_every_shipped_draft_round_trips_byte_for_byte(tmp_path):
    for template in _shipped():
        dest = tmp_path / f"{template.folder.name}.json"
        load_draft(template.id, project_root()).save(dest)
        assert dest.read_bytes() == template.draft_path.read_bytes(), template.name


def test_every_shipped_draft_is_a_template_under_its_own_id():
    for template in _shipped():
        draft = load_draft(template.id, project_root())
        assert draft.is_template, template.name
        assert draft.template is None, template.name
        assert draft.id == template.id, template.name


def test_every_node_and_instance_in_a_shipped_draft_is_template_owned():
    for template in _shipped():
        draft = load_draft(template.id, project_root())
        ids = [s.id for s in draft.states] + [r.id for r in draft.regions if r.source is not None]
        foreign = [i for i in ids if not i.startswith(TEMPLATE_ID_PREFIX)]
        assert not foreign, f"{template.name}: {foreign}"


# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------


def _store_state(root) -> dict[str, bytes]:
    """Every file under the template's folder, by name: what a refused release must leave alone."""
    folder = find_template("tpl-boss", root).folder
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir())}


def test_release_copies_the_saved_draft_byte_for_byte_and_records_it(tmp_path):
    path = save_draft(_draft(), tmp_path)

    entry = release("tpl-boss", "  First release  ", root=tmp_path, today=date(2026, 10, 8))

    folder = path.parent
    assert (entry.version, entry.file, entry.note, entry.released) == (1, "v1.json", "First release", "2026-10-08")
    assert (folder / "v1.json").read_bytes() == path.read_bytes()
    assert entry.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert TemplateIndex.load(folder / INDEX_FILE).versions == [entry]


def test_releases_number_upward_and_leave_earlier_versions_alone(tmp_path):
    _release(tmp_path, _draft("tpl-boss", "Boss Farmer", "t_1"))
    v1 = (find_template("tpl-boss", tmp_path).folder / "v1.json").read_bytes()

    assert _release(tmp_path, _draft("tpl-boss", "Boss Farmer", "t_1", "t_2")) == 2
    assert _release(tmp_path, _draft("tpl-boss", "Boss Farmer", "t_1", "t_2")) == 3
    assert (find_template("tpl-boss", tmp_path).folder / "v1.json").read_bytes() == v1
    assert [v.version for v in find_template("tpl-boss", tmp_path).index.versions] == [1, 2, 3]


def test_release_takes_the_draft_on_disk_not_one_in_memory(tmp_path):
    """The controller saves first; anything not saved is not part of the release."""
    save_draft(_draft("tpl-boss", "Boss Farmer", "t_1"), tmp_path)
    _draft("tpl-boss", "Boss Farmer", "t_1", "t_2")  # an unsaved edit, never written

    release("tpl-boss", "First", root=tmp_path)

    assert [s.id for s in load_version("tpl-boss", 1, tmp_path).states] == ["t_1"]


@pytest.mark.parametrize(
    ("state_ids", "regions", "canvas_errors", "note", "expected"),
    [
        (("t_1",), [], ["Defeat boss: Unknown node type"], "Note", "Unknown node type"),
        (("t_1", "state_4"), [], [], "Note", "Not template-owned (no t_ prefix): state_4"),
        (("t_1",), ["sync_players_1"], [], "Note", "Not template-owned (no t_ prefix): sync_players_1"),
        (("t_1",), [], [], "   ", "needs a note"),
    ],
    ids=["canvas-error", "bot-owned-node", "bot-owned-instance", "blank-note"],
)
def test_release_refuses_and_writes_nothing(tmp_path, state_ids, regions, canvas_errors, note, expected):
    draft = _draft("tpl-boss", "Boss Farmer", *state_ids)
    draft.regions = [
        RegionModel(id=rid, label="Sync", members=["t_1"], color=1, source=SubgraphSource(subgraph_id="sg", version=1))
        for rid in regions
    ]
    save_draft(draft, tmp_path)
    before = _store_state(tmp_path)

    with pytest.raises(ReleaseRefused) as refused:
        release("tpl-boss", note, canvas_errors, root=tmp_path)

    assert any(expected in p for p in refused.value.problems), refused.value.problems
    assert _store_state(tmp_path) == before


def test_a_plain_region_needs_no_prefix(tmp_path):
    """A plain region is layout: nothing a bot is updated by, so no id to own."""
    draft = _draft("tpl-boss", "Boss Farmer", "t_1")
    draft.regions = [RegionModel(id="region_1", label="Loop", members=["t_1"], color=1)]

    save_draft(draft, tmp_path)

    assert release("tpl-boss", "First", root=tmp_path).version == 1


def test_an_id_a_version_retired_never_comes_back(tmp_path):
    _release(tmp_path, _draft("tpl-boss", "Boss Farmer", "t_1", "t_2"))
    _release(tmp_path, _draft("tpl-boss", "Boss Farmer", "t_1"))
    assert retired_ids(find_template("tpl-boss", tmp_path)) == {"t_2"}
    save_draft(_draft("tpl-boss", "Boss Farmer", "t_1", "t_2"), tmp_path)
    before = _store_state(tmp_path)

    with pytest.raises(ReleaseRefused, match="retired: t_2"):
        release("tpl-boss", "Bring it back", root=tmp_path)
    assert _store_state(tmp_path) == before


def test_a_draft_under_another_id_or_recording_a_template_is_refused(tmp_path):
    path = save_draft(_draft(), tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["template"] = {"id": "tpl-other", "version": 1}
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ReleaseRefused, match="records a template of its own"):
        release("tpl-boss", "Note", root=tmp_path)

    data.pop("template")
    data["id"] = "tpl-other"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ReleaseRefused, match="is not the template's"):
        release("tpl-boss", "Note", root=tmp_path)
    assert find_template("tpl-boss", tmp_path).latest_version is None
