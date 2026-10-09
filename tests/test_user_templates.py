"""User templates (#164): forks and their id prefixes, Rename and Delete, the gallery, and the Templates dialog.

The store and the gallery are Qt-free and tested on a ``tmp_path`` with two roots, the
shipped one and the user's, as the app has outside developer mode. The dialog and the
controller are real widget tests through the harness the template draft tests use. The
first test is the issue's done-when, run against the real shipped Boss Farmer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import QPointF
from PyQt6.QtWidgets import QInputDialog, QMessageBox

from bartlebot import app_controller
from bartlebot.bot.schema import BotConfig, SetupStep, SetupTarget, StateModel, TemplateMeta, TransitionModel
from bartlebot.content.new_bot import BotDetails, create_bot, starting_points
from bartlebot.content.store_roots import Origin
from bartlebot.content.subgraphs.subgraph_store import SubgraphLibrary
from bartlebot.content.templates import setup_steps, template_diff, template_gallery
from bartlebot.content.templates.template_store import (
    TEMPLATE_ID_PREFIX,
    NameTaken,
    TemplateIndex,
    bots_based_on,
    count_bots_based_on,
    delete_template,
    deletion_refused,
    draft_name_refused,
    duplicate_template,
    find_template,
    fork_prefix,
    id_prefixes,
    is_template_owned,
    list_templates,
    load_draft,
    load_version,
    name_clash,
    new_template,
    release,
    release_problems,
    rename_template,
    renamed_in_draft,
    save_draft,
)
from bartlebot.gui.canvas.bot_canvas import BotMode
from bartlebot.paths import project_root
from tests.test_subgraph_integration import ControllerHarness

OFFICIAL_ID = "tpl-official"
BOSS_FARMER_ID = "4ece5834-9dfc-481a-9093-7a9309bc4b28"

_STEP = SetupStep(text="Name the boss", targets=[SetupTarget(state="t_2", param="enemy")])


def _official_draft(name: str = "Official Farmer") -> BotConfig:
    return BotConfig(
        id=OFFICIAL_ID,
        name=name,
        initial="t_1",
        recovery_state="t_1",
        tags=["Boss"],
        states=[
            StateModel(id="t_1", node_type="quicksell", position={"x": 0.0, "y": 0.0}),
            StateModel(id="t_2", node_type="handle_combat", params={"enemy": ""}, position={"x": 440.0, "y": 0.0}),
        ],
        transitions=[TransitionModel(trigger="advance", source="t_1", dest="t_2", when="SUCCESS")],
        template_meta=TemplateMeta(blurb="Farms one boss.\n\nMore about it.", setup=[_STEP]),
    )


@pytest.fixture
def roots(tmp_path) -> tuple[Path, Path]:
    """``(shipped, user)``, with one Official template released as v1 in the shipped root."""
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    save_draft(_official_draft(), shipped)
    release(OFFICIAL_ID, "First release", root=shipped)
    return shipped, user


def _official(roots):
    return find_template(OFFICIAL_ID, *roots)


def _add(config: BotConfig, state_id: str, x: float = 880.0) -> None:
    config.states.append(StateModel(id=state_id, node_type="quicksell", position={"x": x, "y": 0.0}))


# ---------------------------------------------------------------------------
# The done-when, against the real Boss Farmer
# ---------------------------------------------------------------------------


@pytest.fixture
def real(tmp_path, monkeypatch):
    """The shipped templates and subgraphs, with the user root on a ``tmp_path``, outside dev mode."""
    monkeypatch.setattr(app_controller, "dev_mode", lambda: False)
    return project_root(), tmp_path / "user"


@pytest.fixture
def real_harness(qapp, real, no_modals):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    shipped, user = real
    library = SubgraphLibrary(shipped, user)
    canvas = BotCanvas()
    canvas.set_subgraph_library(library)
    h = ControllerHarness(canvas, library)
    h._templates_root = shipped
    h._templates_user_root = user
    h._user_bots_dirs = lambda: (user / "bots",)
    canvas.template_save_requested.connect(lambda: h._save_bot())
    canvas.template_release_requested.connect(lambda: h._release_template())
    yield h
    canvas.prepare_for_shutdown()


def test_a_user_duplicates_boss_farmer_changes_it_releases_v1_and_makes_a_bot_from_it(real_harness, real, monkeypatch):
    """The issue's done-when, without dev mode, through the controller and the real shipped template."""
    shipped, user = real
    h = real_harness
    boss = find_template(BOSS_FARMER_ID, shipped, user)
    assert boss.origin is Origin.SHIPPED
    answers = iter([("My Boss Farmer", True), ("First release of mine", True)])
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: next(answers))

    assert h._duplicate_template(boss)

    canvas = h._window.bot_canvas
    assert canvas.mode is BotMode.EDIT
    fork = find_template(h._loaded_config.id, shipped, user)
    assert fork.origin is Origin.USER and fork.name == "My Boss Farmer"
    own = fork_prefix(fork.id)
    added = canvas.scene.create_state_at("quicksell", QPointF(2000.0, 2000.0))
    assert added.startswith(own), "a fork mints under its own prefix"
    canvas.crumb_bar.save_button.click()
    canvas.crumb_bar.release_button.click()

    fork = find_template(fork.id, shipped, user)
    assert fork.latest_version == 1, "Release refused the fork"
    point = next(p for p in starting_points(shipped, user) if p.template_id == fork.id)
    assert point.origin is Origin.USER and point.name == "My Boss Farmer"
    bot = create_bot(point, BotDetails(name="Lord Nightshade"))
    assert bot.template.id == fork.id and bot.template.version == 1
    assert added in {s.id for s in bot.states}
    assert setup_steps.steps_for(bot, shipped, user), "a bot from the fork gets its setup steps"
    assert boss == find_template(BOSS_FARMER_ID, shipped, user), "the Official template is untouched"


# ---------------------------------------------------------------------------
# Prefixes
# ---------------------------------------------------------------------------


def test_a_fork_prefix_is_eight_characters_from_the_id_and_never_t_():
    assert fork_prefix("4ece5834-9dfc-481a-9093-7a9309bc4b28") == "t4ece58_"
    assert fork_prefix("tpl-official") == "tffca00_", "padded when an id has too few hex digits"
    for template_id in ("4ece5834-9dfc", "ab", "no hex here"):
        prefix = fork_prefix(template_id)
        assert len(prefix) == 8 and prefix != TEMPLATE_ID_PREFIX and not prefix.startswith(TEMPLATE_ID_PREFIX)


def test_a_template_made_before_forks_mints_t_alone(roots):
    assert id_prefixes(load_version(OFFICIAL_ID, 1, *roots)) == (TEMPLATE_ID_PREFIX,)


def test_a_new_template_is_blank_custom_unreleased_and_mints_its_own_prefix(roots):
    template = new_template("Mob Sweeper", root=roots[0], user_root=roots[1])

    assert template.origin is Origin.USER and template.latest_version is None
    assert template.folder.parent == roots[1] / "templates"
    draft = load_draft(template.id, *roots)
    assert draft.states == [] and draft.name == "Mob Sweeper"
    assert id_prefixes(draft) == (fork_prefix(template.id),)
    assert draft.template_meta.forked_from is None
    assert template.id not in {p.template_id for p in starting_points(*roots)}, "nothing released, not offered"


# ---------------------------------------------------------------------------
# Duplicate
# ---------------------------------------------------------------------------


def test_duplicating_an_official_template_makes_a_custom_fork_of_its_latest_release(roots):
    fork = duplicate_template(_official(roots), "My Farmer", root=roots[0], user_root=roots[1])

    assert fork.origin is Origin.USER and fork.id != OFFICIAL_ID and fork.latest_version is None
    draft = load_draft(fork.id, *roots)
    meta = draft.template_meta
    assert (meta.forked_from.id, meta.forked_from.version) == (OFFICIAL_ID, 1)
    assert id_prefixes(draft) == (fork_prefix(fork.id), TEMPLATE_ID_PREFIX)
    assert [s.id for s in draft.states] == ["t_1", "t_2"], "seeded from v1, ids and all"
    assert meta.setup == [_STEP] and meta.blurb.startswith("Farms one boss.")
    assert draft.name == "My Farmer" and draft.template is None and draft.history == []
    assert _official(roots).index.versions[0].sha256 == _official(roots).index.versions[0].sha256


def test_a_fork_releases_with_inherited_and_own_ids(roots):
    fork = duplicate_template(_official(roots), "My Farmer", root=roots[0], user_root=roots[1])
    draft = load_draft(fork.id, *roots)
    _add(draft, f"{fork_prefix(fork.id)}3")
    save_draft(draft, *roots)

    entry = release(fork.id, "Mine", root=roots[0], user_root=roots[1])

    assert entry.version == 1


def test_a_fork_may_not_release_an_id_under_its_sources_prefix_that_the_source_lacks(roots):
    """``t_3`` is an id Official Farmer could add next release: a fork never uses it."""
    fork = duplicate_template(_official(roots), "My Farmer", root=roots[0], user_root=roots[1])
    draft = load_draft(fork.id, *roots)
    _add(draft, "t_3")

    problems = release_problems(draft, fork, root=roots[0], user_root=roots[1])

    assert any("t_3" in p and "Official Farmer" in p for p in problems)


def test_a_fork_may_not_release_an_id_under_no_prefix_of_its_lineage(roots):
    fork = duplicate_template(_official(roots), "My Farmer", root=roots[0], user_root=roots[1])
    draft = load_draft(fork.id, *roots)
    _add(draft, "state_9")
    _add(draft, "tabcdef_1", x=1320.0)

    problems = release_problems(draft, fork, root=roots[0], user_root=roots[1])

    assert any("state_9" in p and "tabcdef_1" in p and "Not template-owned" in p for p in problems)


def test_a_fork_of_a_fork_inherits_both_prefixes_and_mints_a_third(roots):
    first = duplicate_template(_official(roots), "Mine", root=roots[0], user_root=roots[1])
    draft = load_draft(first.id, *roots)
    _add(draft, f"{fork_prefix(first.id)}3")
    save_draft(draft, *roots)
    release(first.id, "v1", root=roots[0], user_root=roots[1])

    second = duplicate_template(find_template(first.id, *roots), "Mine too", root=roots[0], user_root=roots[1])

    second_draft = load_draft(second.id, *roots)
    assert id_prefixes(second_draft) == (fork_prefix(second.id), fork_prefix(first.id), TEMPLATE_ID_PREFIX)
    assert len(set(id_prefixes(second_draft))) == 3
    assert second_draft.template_meta.forked_from.id == first.id
    assert release_problems(second_draft, second, root=roots[0], user_root=roots[1]) == []


def test_duplicating_an_unreleased_draft_of_your_own_copies_the_draft_without_forked_from(roots):
    mine = new_template("Mine", root=roots[0], user_root=roots[1])
    draft = load_draft(mine.id, *roots)
    _add(draft, f"{fork_prefix(mine.id)}1")
    draft.initial = draft.recovery_state = f"{fork_prefix(mine.id)}1"
    save_draft(draft, *roots)

    copy = duplicate_template(find_template(mine.id, *roots), "Copy", root=roots[0], user_root=roots[1])

    copied = load_draft(copy.id, *roots)
    assert copied.template_meta.forked_from is None
    assert id_prefixes(copied) == (fork_prefix(copy.id), fork_prefix(mine.id))
    assert [s.id for s in copied.states] == [f"{fork_prefix(mine.id)}1"]


def test_a_fork_draft_mints_new_nodes_under_its_own_prefix(qapp, roots):
    from bartlebot.gui.canvas.edit_scene import EditScene  # noqa: PLC0415

    fork = duplicate_template(_official(roots), "Mine", root=roots[0], user_root=roots[1])
    scene = EditScene()
    scene.setup(load_draft(fork.id, *roots))

    own = fork_prefix(fork.id)
    assert scene.state_id_prefix == own and scene.instance_id_prefix == own
    assert scene.mint_state_id(["t_1", "t_2"]) == f"{own}1"
    assert scene.mint_instance_id("Sync Players").startswith(f"{own}sync_players_")


# ---------------------------------------------------------------------------
# A bot made from a fork, and its update
# ---------------------------------------------------------------------------


def test_a_bot_made_from_a_fork_is_offered_the_forks_next_release(roots):
    """The update diff treats the fork's own nodes as template-owned, as it does ``t_`` ones."""
    fork = duplicate_template(_official(roots), "Mine", root=roots[0], user_root=roots[1])
    release(fork.id, "v1", root=roots[0], user_root=roots[1])
    point = next(p for p in starting_points(*roots) if p.template_id == fork.id)
    bot = create_bot(point, BotDetails(name="A bot"))
    draft = load_draft(fork.id, *roots)
    new_id = f"{fork_prefix(fork.id)}3"
    _add(draft, new_id)
    draft.states[0].label = "Sell first"
    save_draft(draft, *roots)
    release(fork.id, "v2", root=roots[0], user_root=roots[1])

    update = template_diff.load_update(bot, *roots)
    changes = template_diff.diff(update, bot)

    keys = {u.key for c in changes for u in c.units}
    assert ("node", new_id) in keys, "a node the fork added is offered"
    assert ("label", "t_1") in keys, "a change to an inherited node is offered"


def test_a_bots_own_nodes_are_never_template_owned(roots):
    fork = duplicate_template(_official(roots), "Mine", root=roots[0], user_root=roots[1])
    prefixes = id_prefixes(load_draft(fork.id, *roots))

    assert not is_template_owned("state_4", prefixes)
    assert not is_template_owned("sync_players_1", prefixes)
    assert is_template_owned("t_1", prefixes) and is_template_owned(f"{fork_prefix(fork.id)}2", prefixes)


# ---------------------------------------------------------------------------
# Rename
# ---------------------------------------------------------------------------


def test_renaming_your_own_template_shows_at_once_and_a_release_carries_it(roots):
    fork = duplicate_template(_official(roots), "Mine", root=roots[0], user_root=roots[1])
    release(fork.id, "v1", root=roots[0], user_root=roots[1])

    rename_template(fork.id, "Renamed", root=roots[0], user_root=roots[1])

    assert find_template(fork.id, *roots).name == "Renamed"
    assert load_version(fork.id, 1, *roots).name == "Mine", "a released file never changes"
    assert next(p for p in starting_points(*roots) if p.template_id == fork.id).name == "Renamed"
    release(fork.id, "v2", root=roots[0], user_root=roots[1])
    template = find_template(fork.id, *roots)
    assert template.index.name is None and template.name == "Renamed"
    assert "name" not in (template.folder / "template.json").read_text(encoding="utf-8")


def test_an_official_template_is_never_renamed(roots):
    with pytest.raises(PermissionError):
        rename_template(OFFICIAL_ID, "Mine now", root=roots[0], user_root=roots[1])
    assert _official(roots).name == "Official Farmer"


def test_a_name_another_template_has_is_refused_without_case(roots):
    """New, Duplicate and Rename all write through save_draft, which keeps names apart."""
    mine = new_template("Mine", root=roots[0], user_root=roots[1])

    with pytest.raises(NameTaken, match="Official Farmer"):
        new_template("official farmer", root=roots[0], user_root=roots[1])
    with pytest.raises(NameTaken):
        duplicate_template(_official(roots), "MINE", root=roots[0], user_root=roots[1])
    with pytest.raises(NameTaken):
        rename_template(mine.id, "Official Farmer", root=roots[0], user_root=roots[1])

    assert [t.name for t in list_templates(*roots)] == ["Mine", "Official Farmer"]
    rename_template(mine.id, "MINE", root=roots[0], user_root=roots[1])
    assert find_template(mine.id, *roots).name == "MINE", "its own name, in other case, is not taken"


def test_a_name_a_draft_carries_is_taken_too(tmp_path):
    """With the roots one, a released template renamed in its draft keeps the new name to itself."""
    save_draft(_official_draft(), tmp_path)
    release(OFFICIAL_ID, "First", root=tmp_path)
    rename_template(OFFICIAL_ID, "Boss Sweeper", root=tmp_path)

    assert name_clash("boss sweeper", root=tmp_path) == "Boss Sweeper"
    assert name_clash("Boss Sweeper", OFFICIAL_ID, root=tmp_path) is None


def test_a_draft_already_sharing_a_name_still_saves_until_its_name_changes(roots):
    """A clash made before the rule never blocks a save; only taking a name does."""
    mine = new_template("Mine", root=roots[0], user_root=roots[1])
    draft = load_draft(mine.id, *roots)
    draft.name = "Official Farmer"
    draft.save(mine.draft_path)

    save_draft(load_draft(mine.id, *roots), *roots)

    draft.name = "official FARMER"
    assert "Official Farmer" in draft_name_refused(draft, *roots)


def test_with_one_root_renaming_a_released_template_waits_for_its_next_release(tmp_path):
    """Developer mode: the index is what ships, so a rename lives in the draft until Release."""
    save_draft(_official_draft(), tmp_path)
    release(OFFICIAL_ID, "First", root=tmp_path)
    index = find_template(OFFICIAL_ID, tmp_path).folder / "template.json"
    before = index.read_bytes()

    template = rename_template(OFFICIAL_ID, "Boss Sweeper", root=tmp_path)

    assert index.read_bytes() == before
    assert template.name == "Official Farmer" and renamed_in_draft(template) == "Boss Sweeper"
    entry = next(e for e in template_gallery.gallery(root=tmp_path) if e.id == OFFICIAL_ID)
    assert entry.detail == "v1 · no bots · renamed to Boss Sweeper in draft"
    release(OFFICIAL_ID, "Renamed", root=tmp_path)
    template = find_template(OFFICIAL_ID, tmp_path)
    assert template.name == "Boss Sweeper" and renamed_in_draft(template) is None


def test_with_one_root_an_unreleased_template_renames_at_once(tmp_path):
    scratch = new_template("Scratch", root=tmp_path)

    assert rename_template(scratch.id, "Sketch", root=tmp_path).name == "Sketch"


def test_saving_an_unrenamed_draft_never_writes_the_index(roots):
    """What keeps a shipped ``template.json`` byte-identical when the maintainer saves a draft."""
    index = _official(roots).folder / "template.json"
    before = index.read_bytes()

    save_draft(load_draft(OFFICIAL_ID, roots[0], roots[0]), roots[0], roots[0])

    assert index.read_bytes() == before
    assert TemplateIndex.load(index).name is None


# ---------------------------------------------------------------------------
# Delete, and the bots made from a template
# ---------------------------------------------------------------------------


def _bot_from(roots, template_id: str, bots_dir: Path, name: str) -> BotConfig:
    point = next(p for p in starting_points(*roots) if p.template_id == template_id)
    bot = create_bot(point, BotDetails(name=name))
    bots_dir.mkdir(parents=True, exist_ok=True)
    bot.save(bots_dir / f"{name.lower().replace(' ', '-')}.json")
    return bot


def test_the_bots_made_from_a_template_are_counted_and_named(roots, tmp_path):
    bots = tmp_path / "bots"
    fork = duplicate_template(_official(roots), "Mine", root=roots[0], user_root=roots[1])
    release(fork.id, "v1", root=roots[0], user_root=roots[1])
    _bot_from(roots, fork.id, bots / "Wizard City", "Zeta")
    _bot_from(roots, fork.id, bots, "Alpha")
    _bot_from(roots, OFFICIAL_ID, bots, "Other")
    (bots / "broken.json").write_text("{", encoding="utf-8")

    assert bots_based_on(fork.id, bots) == ["Alpha", "Zeta"]
    assert count_bots_based_on(bots) == {fork.id: 2, OFFICIAL_ID: 1}


def test_deleting_your_own_template_leaves_its_bots_running_without_steps_or_updates(roots, tmp_path):
    fork = duplicate_template(_official(roots), "Mine", root=roots[0], user_root=roots[1])
    release(fork.id, "v1", root=roots[0], user_root=roots[1])
    bot = _bot_from(roots, fork.id, tmp_path / "bots", "A bot")
    assert setup_steps.steps_for(bot, *roots), "cached while the template is there"

    delete_template(fork.id, root=roots[0], user_root=roots[1])

    assert find_template(fork.id, *roots) is None and not fork.folder.exists()
    reloaded = BotConfig.load(tmp_path / "bots" / "a-bot.json")
    assert setup_steps.steps_for(reloaded, *roots) == []
    assert template_diff.load_update(reloaded, *roots) is None


def test_an_official_template_is_never_deleted(roots):
    assert "Official" in deletion_refused(_official(roots), *roots)
    with pytest.raises(PermissionError):
        delete_template(OFFICIAL_ID, root=roots[0], user_root=roots[1])
    assert _official(roots) is not None


def test_with_one_root_a_released_template_is_never_deleted_but_an_unreleased_one_is(tmp_path):
    """Developer mode: the one root is what ships, and nothing released is ever removed."""
    save_draft(_official_draft(), tmp_path)
    release(OFFICIAL_ID, "First", root=tmp_path)
    scratch = new_template("Scratch", root=tmp_path)

    assert "released" in deletion_refused(find_template(OFFICIAL_ID, tmp_path), tmp_path)
    delete_template(scratch.id, root=tmp_path)
    assert [t.id for t in list_templates(tmp_path)] == [OFFICIAL_ID]


# ---------------------------------------------------------------------------
# The gallery
# ---------------------------------------------------------------------------


def test_the_gallery_lists_every_template_with_what_each_allows(roots, tmp_path):
    fork = duplicate_template(_official(roots), "Mine", root=roots[0], user_root=roots[1])
    release(fork.id, "v1", root=roots[0], user_root=roots[1])
    _bot_from(roots, fork.id, tmp_path / "bots", "A bot")
    blank = new_template("Blank", root=roots[0], user_root=roots[1])

    entries = {e.id: e for e in template_gallery.gallery((tmp_path / "bots",), root=roots[0], user_root=roots[1])}

    official, mine, empty = entries[OFFICIAL_ID], entries[fork.id], entries[blank.id]
    assert (official.may_edit, official.may_duplicate, official.may_rename, official.may_delete) == (
        False,
        True,
        False,
        False,
    )
    assert official.edit_refused == template_gallery.OFFICIAL_EDIT_REFUSED
    assert (mine.may_edit, mine.may_duplicate, mine.may_rename, mine.may_delete) == (True, True, True, True)
    assert (empty.may_edit, empty.may_duplicate) == (True, True)
    assert official.summary == "Farms one boss." and official.icon_tag == "Boss"
    assert official.detail == "v1 · no bots"
    assert mine.detail == "v1 · 1 bot · from Official Farmer v1"
    assert empty.detail == "Not released yet · no bots"


def test_developer_mode_allows_edit_on_an_official_template(roots):
    entries = template_gallery.gallery(root=roots[0], user_root=roots[1], dev=True)

    assert next(e for e in entries if e.id == OFFICIAL_ID).may_edit


def test_a_copy_is_offered_a_name_past_the_ones_taken():
    assert template_gallery.copy_name("Boss Farmer", ["Boss Farmer"]) == "Boss Farmer (2)"
    assert template_gallery.copy_name("Boss Farmer", ["boss farmer (2)", "Boss Farmer (3)"]) == "Boss Farmer (4)"


# ---------------------------------------------------------------------------
# The dialog
# ---------------------------------------------------------------------------


@pytest.fixture
def gallery_roots(roots, tmp_path):
    fork = duplicate_template(_official(roots), "Mine", root=roots[0], user_root=roots[1])
    release(fork.id, "v1", root=roots[0], user_root=roots[1])
    return roots, fork.id


def _dialog(roots, **callbacks):
    from bartlebot.gui.dialogs.templates_dialog import TemplatesDialog  # noqa: PLC0415

    return TemplatesDialog(
        lambda: template_gallery.gallery(root=roots[0], user_root=roots[1]),
        rename=callbacks.get("rename", lambda e: False),
        delete=callbacks.get("delete", lambda e: False),
    )


def test_building_the_dialog_never_shows_a_parentless_widget(qapp, gallery_roots, parentless_shows):
    roots, _fork = gallery_roots
    dialog = _dialog(roots)
    dialog.reload()

    assert parentless_shows == []


def test_the_dialog_shows_a_card_per_template_with_its_source_and_detail(qapp, gallery_roots):
    roots, _fork = gallery_roots
    dialog = _dialog(roots)

    assert [(c.name, c.source_text, c.detail_text) for c in dialog.cards] == [
        ("Mine", "Custom", "v1 · no bots · from Official Farmer v1"),
        ("Official Farmer", "Official", "v1 · no bots"),
    ]


def test_the_verbs_follow_the_selected_card(qapp, gallery_roots):
    roots, fork = gallery_roots
    dialog = _dialog(roots)

    dialog.select(OFFICIAL_ID)
    assert not dialog.edit_button.isEnabled() and dialog.duplicate_button.isEnabled()
    assert not dialog.rename_button.isEnabled() and not dialog.delete_button.isEnabled()
    assert dialog.edit_button.toolTip() == template_gallery.OFFICIAL_EDIT_REFUSED
    assert dialog.new_button.isEnabled()
    assert dialog.card_menu_actions(OFFICIAL_ID) == [
        ("Edit", False),
        ("Duplicate…", True),
        ("Rename…", False),
        ("Delete…", False),
    ]

    dialog.select(fork)
    assert all(b.isEnabled() for b in (dialog.edit_button, dialog.rename_button, dialog.delete_button))


def test_the_source_filter_narrows_the_cards(qapp, gallery_roots):
    roots, _fork = gallery_roots
    dialog = _dialog(roots)

    dialog.source_filter.set_selection(["Official"])
    dialog.source_filter.selection_changed.emit(["Official"])

    assert [c.name for c in dialog.cards] == ["Official Farmer"]
    assert dialog.selected.id == OFFICIAL_ID


def test_edit_new_and_duplicate_end_the_dialog_with_a_request(qapp, gallery_roots):
    from bartlebot.gui.dialogs.templates_dialog import TemplatesVerb  # noqa: PLC0415

    roots, fork = gallery_roots
    dialog = _dialog(roots)
    dialog.select(OFFICIAL_ID)
    dialog.edit_button.click()
    assert dialog.request is None, "Edit is off on an Official template"

    dialog.duplicate_button.click()
    assert (dialog.request.verb, dialog.request.entry.id) == (TemplatesVerb.DUPLICATE, OFFICIAL_ID)

    dialog = _dialog(roots)
    dialog.select(fork)
    dialog.edit_button.click()
    assert (dialog.request.verb, dialog.request.entry.id) == (TemplatesVerb.EDIT, fork)

    dialog = _dialog(roots)
    dialog.new_button.click()
    assert (dialog.request.verb, dialog.request.entry) == (TemplatesVerb.NEW, None)


def test_rename_and_delete_reload_the_gallery(qapp, gallery_roots):
    roots, fork = gallery_roots

    def rename(entry):
        rename_template(entry.id, "Renamed", root=roots[0], user_root=roots[1])
        return True

    def delete(entry):
        delete_template(entry.id, root=roots[0], user_root=roots[1])
        return True

    dialog = _dialog(roots, rename=rename, delete=delete)
    dialog.select(fork)
    dialog.rename_button.click()
    assert "Renamed" in [c.name for c in dialog.cards]
    assert dialog.selected.id == fork, "the renamed card stays selected"

    dialog.delete_button.click()
    assert [c.name for c in dialog.cards] == ["Official Farmer"]


# ---------------------------------------------------------------------------
# The controller
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=False)
def no_modals(monkeypatch):
    for name in ("question", "warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.StandardButton.No)


@pytest.fixture
def harness(qapp, roots, tmp_path, monkeypatch, no_modals):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas  # noqa: PLC0415

    monkeypatch.setattr(app_controller, "dev_mode", lambda: False)
    library = SubgraphLibrary(tmp_path / "library")
    canvas = BotCanvas()
    canvas.set_subgraph_library(library)
    h = ControllerHarness(canvas, library)
    h._templates_root, h._templates_user_root = roots
    h._user_bots_dirs = lambda: (tmp_path / "bots",)
    yield h
    canvas.prepare_for_shutdown()


def test_an_official_draft_is_refused_and_your_own_opens(harness, roots, monkeypatch):
    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: told.append(a[2]))

    harness._open_bot(str(_official(roots).draft_path))
    assert harness._loaded_config is None and "Duplicate" in told[0]

    mine = new_template("Mine", root=roots[0], user_root=roots[1])
    assert harness._edit_template(mine)
    assert harness._loaded_config.id == mine.id and harness._window.bot_canvas.mode is BotMode.EDIT


def test_new_asks_for_a_name_and_opens_a_blank_draft(harness, roots, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Mob Sweeper", True))

    assert harness._new_template()

    config = harness._loaded_config
    assert config.is_template and config.name == "Mob Sweeper" and config.states == []
    assert find_template(config.id, *roots).origin is Origin.USER


def test_cancelling_duplicate_writes_nothing(harness, roots, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("", False))

    assert not harness._duplicate_template(_official(roots))
    assert [t.id for t in list_templates(*roots)] == [OFFICIAL_ID]


def test_renaming_the_open_draft_renames_the_document_too(harness, roots, monkeypatch):
    mine = new_template("Mine", root=roots[0], user_root=roots[1])
    harness._edit_template(mine)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Renamed", True))
    entry = next(e for e in harness._template_gallery() if e.id == mine.id)

    assert harness._rename_template(entry)

    assert harness._loaded_config.name == "Renamed"
    assert harness._window.bot_canvas.crumb_bar.text == "Template draft · Renamed"
    harness._save_bot()
    assert find_template(mine.id, *roots).name == "Renamed", "the next save keeps the new name"


def test_a_taken_name_asks_again_with_what_was_typed(harness, roots, monkeypatch):
    asked = []
    answers = iter([("official farmer", True), ("Mob Sweeper", True)])

    def get_text(_parent, _title, label, text=""):
        asked.append((label, text))
        return next(answers)

    monkeypatch.setattr(QInputDialog, "getText", get_text)

    assert harness._new_template()

    assert harness._loaded_config.name == "Mob Sweeper"
    assert asked[1] == (
        "There is already a template called 'Official Farmer'.\n\nWhat is the template called?",
        "official farmer",
    )


def test_saving_a_draft_renamed_in_its_header_to_a_taken_name_is_refused(harness, roots, monkeypatch):
    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: told.append(a[2]))
    mine = new_template("Mine", root=roots[0], user_root=roots[1])
    harness._edit_template(mine)
    harness._loaded_config.name = "Official Farmer"

    assert not harness._save_bot()

    assert "Official Farmer" in told[0]
    assert load_draft(mine.id, *roots).name == "Mine"


def test_delete_names_the_bots_made_from_it_closes_its_open_draft_and_deletes(harness, roots, tmp_path, monkeypatch):
    fork = duplicate_template(_official(roots), "Mine", root=roots[0], user_root=roots[1])
    release(fork.id, "v1", root=roots[0], user_root=roots[1])
    _bot_from(roots, fork.id, tmp_path / "bots", "Lord Nightshade")
    harness._edit_template(find_template(fork.id, *roots))
    harness._has_unsaved_changes = True
    asked = []

    def answer(*a, **k):
        asked.append(a[2])
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "warning", answer)
    entry = next(e for e in harness._template_gallery() if e.id == fork.id)

    assert harness._delete_template(entry)

    assert "Lord Nightshade" in asked[0] and "keep running" in asked[0]
    assert harness._loaded_config is None
    assert find_template(fork.id, *roots) is None


def test_declining_delete_keeps_the_template(harness, roots, monkeypatch):
    mine = new_template("Mine", root=roots[0], user_root=roots[1])
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    entry = next(e for e in harness._template_gallery() if e.id == mine.id)

    assert not harness._delete_template(entry)
    assert find_template(mine.id, *roots) is not None


def test_the_templates_dialog_hands_its_request_to_the_controller(harness, roots, monkeypatch):
    from bartlebot.gui.dialogs import templates_dialog  # noqa: PLC0415

    mine = new_template("Mine", root=roots[0], user_root=roots[1])

    class _Pick(templates_dialog.TemplatesDialog):
        def __init__(self, load, _parent, **callbacks):
            super().__init__(load, None, **callbacks)

        def exec(self):
            self.select(mine.id)
            self.edit_button.click()
            return self.result()

    monkeypatch.setattr(app_controller, "TemplatesDialog", _Pick)

    assert harness._open_templates_dialog()
    assert harness._loaded_config.id == mine.id
