"""The one-shot library migration: which failure edges are safe to delete.

Every guard here is a case where deleting the edge would change what the bot does,
which is the whole bar the tool has to clear -- it rewrites tracked files in place
and the library is 87 of them.
"""

from bartlebot.bot.schema import BotConfig, FlagCondition, RegionModel, StateModel, SubgraphSource, TransitionModel
from tools.drop_default_failure_edges import redundant_edges


def _state(sid: str, node_type: str = "sync_in_zone") -> StateModel:
    return StateModel(id=sid, node_type=node_type, params={"zone": "WizardCity/WC_Hub"})


def _edge(source: str, dest: str, when: str | None, **kwargs) -> TransitionModel:
    return TransitionModel(trigger="advance", source=source, dest=dest, when=when, **kwargs)


def _config(transitions, regions=None) -> BotConfig:
    return BotConfig(
        id="test-bot",
        name="Test",
        initial="state_a",
        recovery_state="state_r",
        states=[_state("state_a"), _state("state_b"), _state("state_r", "teleport_to_world_hub")],
        transitions=transitions,
        regions=regions or [],
    )


def _dropped(config: BotConfig) -> list[tuple[str, str | None, str]]:
    return [
        (config.transitions[i].source, config.transitions[i].when, config.transitions[i].dest)
        for i in redundant_edges(config)
    ]


def test_a_plain_failure_edge_into_the_recovery_node_is_dropped():
    config = _config([_edge("state_a", "state_b", "SUCCESS"), _edge("state_a", "state_r", "FAILURE")])
    assert _dropped(config) == [("state_a", "FAILURE", "state_r")]


def test_a_false_edge_is_dropped_the_same_way():
    config = _config([_edge("state_a", "state_b", "TRUE"), _edge("state_a", "state_r", "FALSE")])
    assert _dropped(config) == [("state_a", "FALSE", "state_r")]


def test_a_failure_edge_going_anywhere_else_is_kept():
    config = _config([_edge("state_a", "state_b", "SUCCESS"), _edge("state_a", "state_b", "FAILURE")])
    assert _dropped(config) == []


def test_a_flag_guarded_edge_is_kept():
    """A guard says *when* to recover, which the fall-through cannot express."""
    config = _config(
        [
            _edge("state_a", "state_b", "SUCCESS"),
            _edge("state_a", "state_r", "FAILURE", flags=[FlagCondition(option="Enable Quicksell")]),
        ]
    )
    assert _dropped(config) == []


def test_the_recovery_states_own_failure_edge_is_kept():
    """The engine declines to default that one, so the explicit edge is load-bearing."""
    config = _config([_edge("state_r", "state_a", "SUCCESS"), _edge("state_r", "state_r", "FAILURE")])
    assert _dropped(config) == []


def test_the_last_outgoing_edge_is_kept():
    """Dropping it would make the state terminal, which stops instead of recovering."""
    config = _config([_edge("state_a", "state_r", "FAILURE"), _edge("state_r", "state_a", "SUCCESS")])
    assert _dropped(config) == []


def test_only_one_of_several_last_edges_is_kept():
    """Two droppable edges on one state: the second must see the first already gone."""
    config = _config(
        [_edge("state_a", "state_r", "FAILURE"), _edge("state_a", "state_r", "FALSE")],
    )
    assert _dropped(config) == [("state_a", "FAILURE", "state_r")]


def test_a_locked_subgraph_members_edge_is_kept():
    """Its edges belong to the definition in the library, not to this file."""
    config = _config(
        [_edge("state_a", "state_b", "SUCCESS"), _edge("state_a", "state_r", "FAILURE")],
        regions=[
            RegionModel(
                id="region_1",
                label="Sync",
                members=["state_a"],
                color=1,
                source=SubgraphSource(subgraph_id="sync_players", version=1),
            )
        ],
    )
    assert _dropped(config) == []


def test_an_unlinked_regions_members_are_untouched():
    """A plain region is a visual grouping; it owns nothing."""
    config = _config(
        [_edge("state_a", "state_b", "SUCCESS"), _edge("state_a", "state_r", "FAILURE")],
        regions=[RegionModel(id="region_1", label="Group", members=["state_a"], color=1)],
    )
    assert _dropped(config) == [("state_a", "FAILURE", "state_r")]
