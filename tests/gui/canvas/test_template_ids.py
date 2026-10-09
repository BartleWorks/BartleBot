"""The editor's id scheme: a template draft mints ``t_`` ids, a bot never does.

Real scene tests (QApplication required); nothing reads pixels.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF

from bartlebot.bot.schema import BotConfig, StateModel, TemplateMeta


def _config(*state_ids: str, template: bool) -> BotConfig:
    return BotConfig(
        id="tpl-boss" if template else "bot",
        name="Boss Farmer",
        initial=state_ids[0],
        recovery_state=state_ids[0],
        states=[StateModel(id=sid, node_type="handle_potions", params={}) for sid in state_ids],
        transitions=[],
        template_meta=TemplateMeta() if template else None,
    )


def _scene(qapp, config: BotConfig):
    from bartlebot.gui.canvas.edit_scene import EditScene

    scene = EditScene()
    scene.setup(config)
    return scene


def test_a_draft_mints_template_owned_nodes_and_instances(qapp):
    scene = _scene(qapp, _config("t_1", "t_2", template=True))

    assert scene.create_state_at("handle_potions", QPointF(0, 0)) == "t_3"
    assert scene.mint_instance_id("Sync players") == "t_sync_players_1"


def test_a_bot_made_from_a_template_mints_its_own_ids_beside_the_templates(qapp):
    scene = _scene(qapp, _config("t_1", "t_6", "t_sync_players_1_state_2", "state_3", template=False))

    assert scene.create_state_at("handle_potions", QPointF(0, 0)) == "state_4"
    assert scene.mint_instance_id("Sync players") == "sync_players_1"


def test_a_draft_never_mints_an_id_an_earlier_release_used(qapp):
    scene = _scene(qapp, _config("t_1", template=True))
    scene.set_reserved_ids("tpl-boss", {"t_1", "t_5", "t_sync_players_2"})

    assert scene.create_state_at("handle_potions", QPointF(0, 0)) == "t_6"
    assert scene.mint_instance_id("Sync players") == "t_sync_players_3"


def test_reserved_ids_apply_only_to_the_document_they_were_set_for(qapp):
    """A definition opened over the draft, or the next bot, must not inherit them."""
    scene = _scene(qapp, _config("t_1", template=True))
    scene.set_reserved_ids("tpl-boss", {"t_9"})
    scene.setup(_config("t_1", template=True).model_copy(update={"id": "another-template"}))

    assert scene.reserved_ids == frozenset()
    assert scene.mint_state_id(["t_1"]) == "t_2"

    scene.setup(_config("t_1", template=True))
    assert scene.reserved_ids == {"t_9"}
    scene.set_reserved_ids(None)
    assert scene.reserved_ids == frozenset()


# ---------------------------------------------------------------------------
# A blank required param is a fault in a draft too, unless a setup step asks for it
# (tests/content/templates/test_setup_steps.py has the fill-in half)
# ---------------------------------------------------------------------------


def _with_blank_zone(template: bool) -> BotConfig:
    config = _config("t_1" if template else "state_1", template=template)
    config.states[0].node_type = "enter_dungeon"
    config.states[0].params = {}
    return config


def test_a_blank_required_param_is_an_error_in_a_draft_with_no_step_for_it():
    from bartlebot.gui.canvas.edit_scene import _validate_node

    config = _with_blank_zone(template=True)
    is_error, _is_warning, errors, _warnings = _validate_node(config.states[0], config)

    assert is_error
    assert any("Required param" in e and "is empty" in e for e in errors)


def test_the_same_blank_is_an_error_in_a_bot():
    from bartlebot.gui.canvas.edit_scene import _validate_node

    config = _with_blank_zone(template=False)
    is_error, _is_warning, errors, _warnings = _validate_node(config.states[0], config)

    assert is_error
    assert any("Required param" in e and "is empty" in e for e in errors)
