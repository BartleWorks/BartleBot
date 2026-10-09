"""Templates across two roots: the Official ones shipped, and the user's own (#168).

A template is copied into a bot, never linked, so a user template never overrides an
Official one; drafts and releases are written only to the user root; and a bot made
from a user template gets the same setup steps and update review an Official one gives.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from bartlebot.bot.schema import BotConfig, SetupStep, SetupTarget, StateModel, TemplateMeta
from bartlebot.content.new_bot import BotDetails, create_bot, starting_points
from bartlebot.content.store_roots import Origin
from bartlebot.content.templates import setup_steps, template_diff
from bartlebot.content.templates.template_store import (
    ReleaseRefused,
    find_template,
    list_templates,
    release,
    save_draft,
    templates_dirs,
)

_BOSS = SetupStep(text="Name the boss", targets=[SetupTarget(state="t_4", param="enemy")])


def _draft(template_id: str, name: str = "Boss Farmer", label: str = "Defeat boss") -> BotConfig:
    return BotConfig(
        id=template_id,
        name=name,
        initial="t_4",
        recovery_state="t_4",
        states=[StateModel(id="t_4", node_type="handle_combat", label=label, params={"enemy": ""})],
        transitions=[],
        template_meta=TemplateMeta(blurb="Farms one boss.", setup=[_BOSS]),
    )


def _released(root: Path, template_id: str, **kwargs) -> None:
    save_draft(_draft(template_id, **kwargs), root)
    release(template_id, "First release", root=root)


@pytest.fixture
def roots(tmp_path) -> tuple[Path, Path]:
    return tmp_path / "shipped", tmp_path / "user"


def test_both_roots_are_listed_each_with_its_origin(roots):
    shipped, user = roots
    _released(shipped, "t-official", name="Official Farmer")
    _released(user, "t-mine", name="My Farmer")

    found = {t.id: t.origin for t in list_templates(shipped, user)}

    assert found == {"t-official": Origin.SHIPPED, "t-mine": Origin.USER}
    assert [p.template_id for p in starting_points(shipped, user)] == ["t-mine", "t-official"]


def test_new_bot_shows_each_templates_source_and_none_on_the_blank_bot(qapp, roots, tmp_path):
    """Every surface listing templates says where each comes from, in the same words (#149)."""
    from bartlebot.gui.dialogs.new_bot_dialog import NewBotDialog  # noqa: PLC0415

    shipped, user = roots
    _released(shipped, "t-official", name="Official Farmer")
    _released(user, "t-mine", name="My Farmer")

    cards = NewBotDialog(starting_points(shipped, user), tmp_path / "bots").cards

    assert {c.name: c.source_text for c in cards} == {
        "Blank bot": "",
        "My Farmer": "Custom",
        "Official Farmer": "Official",
    }


def test_a_user_template_declaring_an_official_id_is_ignored_with_a_warning(roots, caplog):
    shipped, user = roots
    _released(shipped, "t-same", name="Official")
    _released(user, "t-same", name="Hand copy")

    with caplog.at_level(logging.WARNING, logger="bartlebot.content.templates.template_store"):
        found = list_templates(shipped, user)

    assert [(t.name, t.origin) for t in found] == [("Official", Origin.SHIPPED)]
    assert "never overridden" in caplog.text


def test_a_new_draft_is_written_to_the_user_root(roots):
    shipped, user = roots
    path = save_draft(_draft("t-new"), shipped, user)

    assert path.is_relative_to(templates_dirs(shipped, user)[1])
    assert not (shipped / "templates").exists()


def test_an_official_draft_is_never_written(roots):
    shipped, user = roots
    _released(shipped, "t-official")
    before = (find_template("t-official", shipped).draft_path).read_bytes()

    with pytest.raises(PermissionError):
        save_draft(_draft("t-official", label="Changed"), shipped, user)

    assert find_template("t-official", shipped, user).draft_path.read_bytes() == before
    assert not (user / "templates").exists()


def test_an_official_template_is_never_released(roots):
    shipped, user = roots
    _released(shipped, "t-official")

    with pytest.raises(ReleaseRefused) as refused:
        release("t-official", "Again", root=shipped, user_root=user)

    assert any("Official" in p for p in refused.value.problems)
    assert find_template("t-official", shipped, user).latest_version == 1


def test_a_user_template_is_released_in_the_user_root(roots):
    shipped, user = roots
    save_draft(_draft("t-mine"), shipped, user)

    release("t-mine", "First release", root=shipped, user_root=user)

    template = find_template("t-mine", shipped, user)
    assert template.latest_version == 1
    assert (template.folder / "v1.json").is_relative_to(user)


def test_a_bot_made_from_a_user_template_gets_its_setup_steps_and_its_update_review(roots):
    shipped, user = roots
    _released(shipped, "t-official", name="Official Farmer")
    save_draft(_draft("t-mine", name="My Farmer"), shipped, user)
    release("t-mine", "First release", root=shipped, user_root=user)
    (start,) = [p for p in starting_points(shipped, user) if p.template_id == "t-mine"]
    bot = create_bot(start, BotDetails(name="Lamia"))

    assert setup_steps.steps_for(bot, shipped, user) == [_BOSS]
    assert template_diff.load_update(bot, shipped, user) is None, "not behind yet"

    save_draft(_draft("t-mine", name="My Farmer", label="Defeat the boss"), shipped, user)
    release("t-mine", "Clearer label", root=shipped, user_root=user)

    update = template_diff.load_update(bot, shipped, user)
    assert update is not None and update.latest_version == 2
    assert template_diff.behind_in_file(bot.model_dump(), shipped, user)
