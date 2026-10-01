"""Every shipped template, as released: what a user can be offered must never change underneath them.

Run over every folder in ``templates/``. A released file is immutable, so its bytes are
checked against the hash Release recorded. Every node and subgraph instance in it is
template-owned, so a bot made from it can never mint one of its ids. And an id a
version retired never comes back, since a bot based on the earlier version still has
a node under it, which a later update would otherwise apply to.
"""

from __future__ import annotations

import pytest

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
