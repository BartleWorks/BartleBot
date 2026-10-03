"""Every shipped template, as released: what a user can be offered must never change underneath them.

Run over every folder in ``templates/``. A released file is immutable, so its bytes are
checked against the hash Release recorded. Every node and subgraph instance in it is
template-owned, so a bot made from it can never mint one of its ids. And an id a
version retired never comes back, since a bot based on the earlier version still has
a node under it, which a later update would otherwise apply to.
"""

from __future__ import annotations

import pytest

from bartlebot import setup_steps
from bartlebot.bot.schema import BotConfig
from bartlebot.paths import project_root
from bartlebot.template_store import DRAFT_FILE, TEMPLATE_ID_PREFIX, list_templates, sha256_matches

_TEMPLATES = list_templates(project_root())


def _ids(config: BotConfig) -> set[str]:
    return {s.id for s in config.states} | {r.id for r in config.regions if r.source is not None}


def test_every_template_folder_is_a_template():
    folders = sorted(p.name for p in (project_root() / "templates").iterdir() if p.is_dir())

    assert folders, "no shipped template, so these tests check nothing"
    assert sorted(t.folder.name for t in _TEMPLATES) == folders


@pytest.mark.parametrize("template", _TEMPLATES, ids=lambda t: t.folder.name)
def test_every_shipped_template_has_a_draft_and_a_release(template):
    assert (template.folder / DRAFT_FILE).is_file()
    assert template.latest_version is not None, f"{template.name} has never been released"
    assert [v.version for v in template.index.versions] == list(range(1, template.latest_version + 1))


@pytest.mark.parametrize("template", _TEMPLATES, ids=lambda t: t.folder.name)
def test_every_released_file_matches_its_recorded_hash(template):
    changed = [v.file for v in template.index.versions if not sha256_matches(template, v)]

    assert not changed, f"{template.name}: released files changed after release: {changed}"


@pytest.mark.parametrize("template", _TEMPLATES, ids=lambda t: t.folder.name)
def test_every_id_in_a_released_version_is_template_owned(template):
    for entry in template.index.versions:
        config = BotConfig.load(template.folder / entry.file)
        assert config.id == template.id, entry.file
        assert config.is_template, entry.file
        foreign = sorted(i for i in _ids(config) if not i.startswith(TEMPLATE_ID_PREFIX))
        assert not foreign, f"{template.name} {entry.file}: {foreign}"


@pytest.mark.parametrize("template", _TEMPLATES, ids=lambda t: t.folder.name)
def test_an_id_a_version_retired_appears_in_no_later_version(template):
    versions = [_ids(BotConfig.load(template.folder / v.file)) for v in template.index.versions]
    back = []
    for i, earlier in enumerate(versions):
        for j in range(i + 1, len(versions)):
            retired = earlier - versions[j]
            for k in range(j + 1, len(versions)):
                back += [f"{i_id} (v{i + 1}, gone in v{j + 1}, back in v{k + 1})" for i_id in retired & versions[k]]

    assert not back, f"{template.name}: {back}"


@pytest.mark.parametrize("template", _TEMPLATES, ids=lambda t: t.folder.name)
def test_every_setup_target_exists_and_every_fill_in_is_blank(template):
    """In every released version, and in the draft the next one will be cut from."""
    files = [v.file for v in template.index.versions] + [DRAFT_FILE]
    for name in files:
        problems = setup_steps.draft_problems(BotConfig.load(template.folder / name))
        assert not problems, f"{template.name} {name}: {problems}"


@pytest.mark.parametrize("template", _TEMPLATES, ids=lambda t: t.folder.name)
def test_every_blank_required_param_in_a_draft_is_a_fill_in(template):
    """A blank nothing asks for would be an error in every bot made from it, with no step explaining it."""
    from bartlebot.gui.canvas.edit_scene import _validate_node  # noqa: PLC0415

    draft = BotConfig.load(template.folder / DRAFT_FILE)
    fills = setup_steps.fill_ins(draft, draft.template_meta.setup)
    stray = [
        f"{state.id}: {reason}"
        for state in draft.states
        for reason in _validate_node(state, draft, fill_ins=fills)[2]
        if "Required param" in reason
    ]

    assert not stray, f"{template.name}: {stray}"


@pytest.mark.parametrize("template", _TEMPLATES, ids=lambda t: t.folder.name)
def test_a_card_can_show_a_drafts_blurb(template):
    """A New Bot card shows the first paragraph: a sentence about the template, not a list of steps."""
    blurb = BotConfig.load(template.folder / DRAFT_FILE).template_meta.blurb
    first = blurb.strip().split("\n\n", 1)[0]

    assert first and not first.rstrip().endswith(":"), first
    assert "follow these steps" not in blurb


@pytest.mark.parametrize("template", _TEMPLATES, ids=lambda t: t.folder.name)
def test_a_bot_made_from_a_draft_is_stopped_by_its_setup_steps_and_nothing_else(template):
    """What the next release will hand a user: every error is a step to do, and every step has one."""
    from bartlebot.bot.schema import TemplateRef  # noqa: PLC0415
    from bartlebot.gui.canvas.edit_scene import _validate_node  # noqa: PLC0415
    from bartlebot.subgraph_store import SubgraphLibrary  # noqa: PLC0415
    from bartlebot.subgraphs import compose_bot  # noqa: PLC0415

    library = SubgraphLibrary(project_root())
    draft = BotConfig.load(template.folder / DRAFT_FILE)
    steps = draft.template_meta.setup
    bot = draft.model_copy(deep=True)
    bot.template_meta = None
    bot.template = TemplateRef(id=template.id, version=(template.latest_version or 0) + 1)
    bot, _report = compose_bot(bot, library)

    fills = setup_steps.fill_ins(bot, steps, library)
    errors = [e for state in bot.states for e in _validate_node(state, bot, fill_ins=fills)[2]]

    assert steps, f"{template.name} asks for nothing"
    assert errors and all(e.startswith(setup_steps.ERROR_PREFIX) for e in errors), errors
    assert setup_steps.steps_left(bot, steps, library) == len(steps)
    node_steps = [s for s in steps if any(t.field is None for t in s.targets)]
    assert {e[len(setup_steps.ERROR_PREFIX) :] for e in errors} == {s.text for s in node_steps}


@pytest.mark.parametrize("template", _TEMPLATES, ids=lambda t: t.folder.name)
def test_every_template_opens_with_sync_players(template):
    """Starting and recovering both go through Player Sync, so a fix to it reaches every bot.

    In every released version and the draft. Gauntlet included: it syncs in The Commons,
    where it also comes back to for potions.
    """
    from bartlebot.subgraphs import entry_port_of, linked_regions  # noqa: PLC0415

    sync_players = "45419e29-77a2-4923-b779-a0ab2d948878"
    for name in [v.file for v in template.index.versions] + [DRAFT_FILE]:
        config = BotConfig.load(template.folder / name)
        calls = [r for r in linked_regions(config) if r.source.subgraph_id == sync_players]
        assert len(calls) == 1, f"{template.name} {name}: {len(calls)} Sync players nodes"
        region = calls[0]
        assert entry_port_of(region.source, region.id, config.initial) == "entry_start", f"{template.name} {name}"
        assert entry_port_of(region.source, region.id, config.recovery_state) == "entry_recover", (
            f"{template.name} {name}"
        )


@pytest.mark.parametrize("name", ["Boss Farmer", "Spellement Farmer"])
def test_a_boss_fight_asks_for_a_strategy_of_its_own(name):
    """A boss is the fight a default strategy is wrong for, so its template leaves it exposed."""
    from bartlebot.fight_keys import exposed_combat_states  # noqa: PLC0415

    draft = BotConfig.load(next(t for t in list_templates() if t.name == name).draft_path)
    fights = [s for s in draft.states if s.node_type == "handle_combat"]

    assert fights and exposed_combat_states(draft) == fights
