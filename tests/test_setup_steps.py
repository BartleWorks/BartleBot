"""Setup steps (10.4): fill-ins, what "done" means, the error a blank one raises, and what Release refuses.

The rules are Qt-free (``bartlebot/setup_steps.py``), so most of this needs no
``QApplication``. The scene's half is checked through ``_validate_node``, the function
both the node badges and the Run button's error count are built on.
"""

from __future__ import annotations

from datetime import date

import pytest

from bartlebot import setup_steps
from bartlebot.bot.schema import (
    BotConfig,
    RegionModel,
    SetupStep,
    SetupTarget,
    StateModel,
    SubgraphSource,
    TemplateMeta,
    TemplateRef,
)
from bartlebot.gui.canvas.edit_scene import _validate_node
from bartlebot.new_bot import BotDetails, create_bot, starting_points
from bartlebot.template_store import ReleaseRefused, release, release_problems, save_draft

TEMPLATE_ID = "tpl-boss"
#: A destination on the shipped zone map, and a path that is on no map.
_HUB = "WizardCity/WC_Hub"
_NOWHERE = "Nowhere/NW_Zone"

_ZONE = SetupStep(
    text="Choose the zone the dungeon entrance is in",
    targets=[SetupTarget(region="t_sync_players_1", input="state_2.zone")],
)
_ENTER = SetupStep(
    text="Record entering the dungeon",
    targets=[SetupTarget(state="t_3", param=p) for p in ("from_zone", "dungeon_zone", "sigil_xyz", "reset_xyz")],
)
_BOSS = SetupStep(text="Name the boss", targets=[SetupTarget(state="t_4", param="enemy")])
_DROPS = SetupStep(text="Import the boss's drops", targets=[SetupTarget(field="drops")])
_STEPS = [_ZONE, _ENTER, _BOSS, _DROPS]


def _draft(steps=None) -> BotConfig:
    """A boss template in miniature: a subgraph node with a promoted zone, a sigil and an optional enemy."""
    return BotConfig(
        id=TEMPLATE_ID,
        name="Boss Farmer",
        initial="t_sync_players_1_state_2",
        recovery_state="t_sync_players_1_state_2",
        states=[
            StateModel(
                id="t_sync_players_1_state_2", node_type="sync_in_zone", label="Go to zone", params={"zone": ""}
            ),
            StateModel(id="t_3", node_type="enter_dungeon", label="Enter dungeon", params={}),
            StateModel(id="t_4", node_type="handle_combat", label="Defeat boss", params={"enemy": ""}),
        ],
        transitions=[],
        regions=[
            RegionModel(
                id="t_sync_players_1",
                label="Sync players",
                members=["t_sync_players_1_state_2"],
                color=1,
                source=SubgraphSource(subgraph_id="sync", version=1),
            )
        ],
        template_meta=TemplateMeta(blurb="Farms one boss.", setup=list(_STEPS if steps is None else steps)),
    )


def _bot() -> BotConfig:
    """What New Bot makes of that draft: the same graph, no steps of its own."""
    config = _draft()
    config.id = "bot-lamia"
    config.template_meta = None
    config.template = TemplateRef(id=TEMPLATE_ID, version=1)
    return config


def _state(config: BotConfig, state_id: str) -> StateModel:
    return next(s for s in config.states if s.id == state_id)


_XYZ = {"x": 1.0, "y": 2.0, "z": 3.0}


def _fill_sigil(config: BotConfig) -> None:
    _state(config, "t_3").params.update(from_zone=_HUB, dungeon_zone="Aquila/Dungeon", sigil_xyz=_XYZ, reset_xyz=_XYZ)


def _done(config: BotConfig) -> list[bool]:
    return [status.done for status in setup_steps.progress(config, _STEPS)]


# ---------------------------------------------------------------------------
# A target is one of three kinds
# ---------------------------------------------------------------------------


def test_a_target_is_exactly_one_kind():
    assert SetupTarget(state="t_3", param="from_zone").state == "t_3"
    assert SetupTarget(region="t_sync_players_1", input="zone").region == "t_sync_players_1"
    assert SetupTarget(field="drops").field == "drops"

    for mixed in (
        {},
        {"state": "t_3"},
        {"state": "t_3", "input": "zone"},
        {"state": "t_3", "param": "a", "field": "drops"},
    ):
        with pytest.raises(ValueError):
            SetupTarget(**mixed)


def test_a_version_written_before_steps_existed_still_loads():
    config = BotConfig.model_validate(
        {
            "id": TEMPLATE_ID,
            "name": "Boss Farmer",
            "initial": "t_1",
            "recovery_state": "t_1",
            "states": [{"id": "t_1", "node_type": "quicksell"}],
            "transitions": [],
            "template_meta": {"blurb": "Farms one boss."},
        }
    )

    assert config.template_meta.setup == []


# ---------------------------------------------------------------------------
# Blank is the type's own emptiness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field_type", "blank", "filled"),
    [
        ("str", "  ", "Lamia"),
        ("nav_zone", "", _HUB),
        ("str_list", [], ["Amber"]),
        ("xyz", {"x": 0.0, "y": 0.0, "z": 0.0}, _XYZ),
    ],
)
def test_blank_is_the_types_own_emptiness(field_type, blank, filled):
    assert setup_steps.value_empty(field_type, blank)
    assert setup_steps.value_empty(field_type, None)
    assert not setup_steps.value_empty(field_type, filled)


# ---------------------------------------------------------------------------
# Each kind of target, unfilled and filled
# ---------------------------------------------------------------------------


def test_nothing_is_done_in_a_bot_just_made():
    assert _done(_bot()) == [False, False, False, False]
    assert setup_steps.steps_left(_bot(), _STEPS) == 4


def test_a_node_param_step_is_done_when_every_target_is_filled():
    bot = _bot()
    _state(bot, "t_3").params.update(from_zone=_HUB, dungeon_zone="Aquila/Dungeon")
    assert _done(bot)[1] is False, "two of its four fill-ins are still blank"

    _fill_sigil(bot)
    assert _done(bot)[1] is True


def test_an_optional_param_is_a_fill_in_like_any_other():
    bot = _bot()
    assert _done(bot)[2] is False

    _state(bot, "t_4").params["enemy"] = "Lamia"
    assert _done(bot)[2] is True


def test_a_subgraph_input_is_read_from_the_member_it_lands_on():
    bot = _bot()
    assert _done(bot)[0] is False

    _state(bot, "t_sync_players_1_state_2").params["zone"] = _HUB
    assert _done(bot)[0] is True


def test_a_named_input_is_read_through_its_bindings():
    class _Binding:
        state, param = "state_2", "zone"

    class _Input:
        name, bindings = "zone", [_Binding()]

    class _Defn:
        inputs = [_Input()]

    class _Library:
        def get(self, subgraph_id):
            return _Defn() if subgraph_id == "sync" else None

    named = SetupTarget(region="t_sync_players_1", input="zone")
    bot = _bot()
    assert [(s.id, k) for s, k in setup_steps.target_params(bot, named, _Library())] == [
        ("t_sync_players_1_state_2", "zone")
    ]
    assert not setup_steps.target_done(bot, named, _Library())

    _state(bot, "t_sync_players_1_state_2").params["zone"] = _HUB
    assert setup_steps.target_done(bot, named, _Library())


def test_a_nav_zone_that_is_not_a_destination_is_filled_but_not_done():
    bot = _bot()
    _state(bot, "t_sync_players_1_state_2").params["zone"] = _NOWHERE

    assert setup_steps.target_filled(bot, _ZONE.targets[0])
    assert not setup_steps.target_done(bot, _ZONE.targets[0])
    assert _done(bot)[0] is False


def test_the_drop_list_only_keeps_its_step_open():
    bot = _bot()
    assert _done(bot)[3] is False
    assert not setup_steps.fill_ins(bot, [_DROPS]), "a drops target is no node's fill-in, so it raises no error"

    bot = BotConfig.model_validate({**bot.model_dump(), "drops": ["Amber"]})
    assert _done(bot)[3] is True


# ---------------------------------------------------------------------------
# What the user deleted is not asked for
# ---------------------------------------------------------------------------


def test_a_step_whose_node_was_deleted_is_not_listed_or_counted():
    bot = _bot()
    bot.states = [s for s in bot.states if s.id != "t_4"]

    statuses = setup_steps.progress(bot, _STEPS)

    assert [s.step.text for s in statuses] == [_ZONE.text, _ENTER.text, _DROPS.text]
    assert setup_steps.steps_left(bot, _STEPS) == 3


def test_a_step_keeps_the_targets_that_are_left():
    two_nodes = SetupStep(
        text="Set both",
        targets=[SetupTarget(state="t_4", param="enemy"), SetupTarget(state="t_gone", param="enemy")],
    )
    bot = _bot()
    _state(bot, "t_4").params["enemy"] = "Lamia"

    [status] = setup_steps.progress(bot, [two_nodes])

    assert [t.state for t in status.targets] == ["t_4"]
    assert status.done


# ---------------------------------------------------------------------------
# The error: in a bot, never in a draft
# ---------------------------------------------------------------------------


def _errors(config: BotConfig, state_id: str, steps=_STEPS) -> tuple[list[str], list[str]]:
    fills = setup_steps.fill_ins(config, steps)
    _is_error, _is_warning, errors, warnings = _validate_node(_state(config, state_id), config, fill_ins=fills)
    return errors, warnings


def test_a_blank_fill_in_is_an_error_on_its_node_in_a_bot():
    errors, _warnings = _errors(_bot(), "t_3")

    assert errors.count(f"Setup: {_ENTER.text}") == 1, "once per step, though four of its fill-ins are blank"
    assert not any("Required param" in e for e in errors), "the step says it; the bare rule would say it twice"


def test_a_blank_fill_in_is_an_error_whether_or_not_the_param_is_required():
    errors, _warnings = _errors(_bot(), "t_4")

    assert errors == [f"Setup: {_BOSS.text}"]


def test_an_unfilled_input_is_an_error_on_the_member_that_rolls_up():
    errors, _warnings = _errors(_bot(), "t_sync_players_1_state_2")

    assert f"Setup: {_ZONE.text}" in errors


def test_a_filled_fill_in_raises_nothing():
    bot = _bot()
    _fill_sigil(bot)
    _state(bot, "t_4").params["enemy"] = "Lamia"

    assert not [e for e in _errors(bot, "t_3")[0] if e.startswith("Setup")]
    assert _errors(bot, "t_4")[0] == []


def test_a_blank_fill_in_raises_nothing_in_a_draft():
    draft = _draft()

    for state_id in ("t_3", "t_4", "t_sync_players_1_state_2"):
        errors, warnings = _errors(draft, state_id)
        assert not [e for e in errors if "Setup" in e or "Required param" in e], state_id
        assert not [w for w in warnings if "setup step" in w], state_id


def test_a_filled_fill_in_is_a_warning_in_a_draft():
    draft = _draft()
    _state(draft, "t_4").params["enemy"] = "Lamia"

    _errors_, warnings = _errors(draft, "t_4")

    assert any("setup step 3" in w and "blank" in w for w in warnings)


def test_a_blank_required_param_no_step_asks_for_is_an_error_in_a_draft():
    """Nothing would ever ask the user of a bot made from it to fill it."""
    draft = _draft(steps=[_ZONE, _BOSS])

    errors, _warnings = _errors(draft, "t_3", steps=[_ZONE, _BOSS])

    assert any("Required param 'from_zone' is empty" in e for e in errors)


# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------


def _released_store(tmp_path, draft: BotConfig | None = None):
    save_draft(draft or _draft(), tmp_path)
    release(TEMPLATE_ID, "First release", root=tmp_path, today=date(2026, 10, 1))
    return tmp_path


def test_release_takes_a_draft_whose_fill_ins_are_blank(tmp_path):
    root = _released_store(tmp_path)

    assert setup_steps.steps_for(_bot(), root) == _STEPS


def test_release_refuses_a_filled_fill_in_and_writes_nothing(tmp_path):
    draft = _draft()
    _state(draft, "t_4").params["enemy"] = "Lamia"
    save_draft(draft, tmp_path)

    with pytest.raises(ReleaseRefused) as refused:
        release(TEMPLATE_ID, "First release", root=tmp_path)

    assert any("Defeat boss · Enemy Name holds a value" in p for p in refused.value.problems)
    assert not list((tmp_path / "templates" / "boss-farmer").glob("v*.json"))


def test_release_refuses_a_draft_with_drops_when_a_step_asks_for_them(tmp_path):
    draft = BotConfig.model_validate({**_draft().model_dump(), "drops": ["Amber"]})

    problems = setup_steps.draft_problems(draft)

    assert any("Possible drops holds a value" in p for p in problems)


def test_release_refuses_a_target_the_draft_does_not_have(tmp_path):
    gone = SetupStep(text="Name the mob", targets=[SetupTarget(state="t_9", param="enemy")])
    not_a_param = SetupStep(text="Set it", targets=[SetupTarget(state="t_4", param="no_such")])
    no_member = SetupStep(text="Choose", targets=[SetupTarget(region="t_sync_players_1", input="state_9.zone")])

    problems = setup_steps.draft_problems(_draft(steps=[gone, not_a_param, no_member]))

    assert sum("which the draft does not have" in p for p in problems) == 2
    assert any("not a param of that node" in p for p in problems)


def test_release_refuses_a_step_with_no_text_or_no_targets():
    problems = setup_steps.draft_problems(_draft(steps=[SetupStep(text=" ", targets=[])]))

    assert any("has no text" in p for p in problems)
    assert any("asks for nothing" in p for p in problems)


def test_the_store_lists_step_problems_with_its_own(tmp_path):
    draft = _draft()
    _state(draft, "t_4").params["enemy"] = "Lamia"
    save_draft(draft, tmp_path)
    from bartlebot.template_store import find_template  # noqa: PLC0415

    problems = release_problems(draft, find_template(TEMPLATE_ID, tmp_path))

    assert any("holds a value" in p for p in problems)


# ---------------------------------------------------------------------------
# A bot reads its steps from its base version
# ---------------------------------------------------------------------------


def test_a_bot_reads_its_steps_from_its_base_version_not_from_the_draft(tmp_path):
    root = _released_store(tmp_path)
    later = _draft(steps=[_ZONE])
    save_draft(later, root)

    assert setup_steps.steps_for(_bot(), root) == _STEPS, "v1's steps, whatever the draft has moved on to"
    assert setup_steps.steps_for(later, root) == [_ZONE], "a draft's are its own"


def test_a_bot_made_by_new_bot_carries_no_steps_but_finds_them(tmp_path):
    root = _released_store(tmp_path)

    bot = create_bot(starting_points(root)[0], BotDetails(name="Lamia"))

    assert bot.template_meta is None
    assert setup_steps.steps_for(bot, root) == _STEPS
    assert setup_steps.steps_left(bot, setup_steps.steps_for(bot, root)) == 4


def test_a_bot_with_no_template_or_a_missing_one_has_no_steps(tmp_path):
    blank = create_bot(None, BotDetails(name="Scratch"))
    orphan = _bot()

    assert setup_steps.steps_for(blank, tmp_path) == []
    assert setup_steps.steps_for(orphan, tmp_path) == [], "its template is not in this store"
    assert setup_steps.steps_for(None, tmp_path) == []


def test_the_library_counts_a_bots_steps_from_its_file(tmp_path):
    root = _released_store(tmp_path)
    bot = _bot()

    assert setup_steps.left_in_file(bot.file_dump(), root) == 4

    _fill_sigil(bot)
    assert setup_steps.left_in_file(bot.file_dump(), root) == 3
    assert setup_steps.left_in_file(create_bot(None, BotDetails(name="Scratch")).file_dump(), root) == 0
    assert setup_steps.left_in_file({"template": {"id": TEMPLATE_ID, "version": 1}}, root) == 0, "not a bot file"
