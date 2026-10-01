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
