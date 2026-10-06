"""The pure half of the node page's and subgraph node page's folding cards (#142)."""

from bartlebot import param_cards as pc
from bartlebot.bot.schema import StateModel, TransitionModel
from bartlebot.nodes.param_schema import ParamField
from bartlebot.option_groups import summary_text


def _state(sid="a", stops=None):
    return StateModel(id=sid, node_type="enter_dungeon", stops=stops)


def _t(src, dest, when=None):
    return TransitionModel(trigger=f"{src}_{dest}", source=src, dest=dest, when=when)


ZONE = ParamField(key="from_zone", type="zone", label="From Zone", required=True, default="")
DEST = ParamField(key="dungeon_zone", type="zone", label="Dungeon Zone", required=True, default="")
XYZ = ParamField(key="sigil_xyz", type="xyz", label="Sigil XYZ", required=True)
NAME = ParamField(key="dungeon_name", type="str", label="Dungeon Name", default="")
TEAM = ParamField(key="offers_team_up", type="bool", label="Offers Team-Up", default=False)
RUNS = ParamField(key="runs", type="int", label="Runs", default=0)


# -- fold defaults --------------------------------------------------------------


def test_the_node_page_opens_parameters_and_connections_and_folds_the_rest():
    assert pc.node_page_fold_defaults() == {pc.PARAMS: False, pc.CONNECTIONS: False, pc.NOTES: True, pc.ROLE: True}


def test_the_role_card_opens_on_a_node_that_holds_a_role():
    assert pc.node_page_fold_defaults(holds_role=True)[pc.ROLE] is False


def test_a_subgraph_card_with_an_empty_required_field_starts_open_and_a_complete_one_folded():
    folds = pc.call_page_fold_defaults(["inputs", "enter", "sync"], incomplete=["enter", "sync"])
    assert folds == {"inputs": True, "enter": False, "sync": False, pc.ROLE: True}


def test_each_definition_is_a_surface_of_its_own():
    assert pc.call_page_surface("sync-players") != pc.call_page_surface("run-a-dungeon")
    assert pc.call_page_surface("sync-players") != pc.NODE_PAGE_SURFACE


# -- counts and summaries --------------------------------------------------------


def test_to_fill_counts_only_empty_required_fields():
    schema = [ZONE, DEST, XYZ, NAME]
    assert pc.to_fill(schema, {}) == 3
    assert pc.to_fill(schema, {"from_zone": "Aquila/AQ_Z00_Hub", "dungeon_name": ""}) == 2
    assert pc.to_fill_text(2) == "2 to fill"
    assert pc.to_fill_text(0) == ""


def test_a_params_summary_names_zones_by_their_last_segment_and_skips_coordinates():
    params = {
        "from_zone": "Aquila/AQ_Z00_Hub",
        "dungeon_zone": "Aquila/AQ_Z01_MountOlympus",
        "sigil_xyz": {"x": 1, "y": 2, "z": 3},
        "offers_team_up": True,
    }
    assert summary_text(pc.params_summary([ZONE, DEST, XYZ, TEAM], params)) == (
        "AQ_Z00_Hub · AQ_Z01_MountOlympus · Offers Team-Up"
    )


def test_a_number_is_summarized_only_off_its_default():
    assert summary_text(pc.params_summary([RUNS], {"runs": 0})) == "Nothing set"
    assert summary_text(pc.params_summary([RUNS], {"runs": 5})) == "Runs: 5"


def test_a_recordable_node_reads_not_recorded_until_something_record_fills_is_set():
    keys = ["from_zone", "dungeon_zone", "sigil_xyz"]
    assert summary_text(pc.recording_summary([ZONE, DEST, XYZ, TEAM], {"offers_team_up": True}, keys)) == (
        "Not recorded"
    )
    assert summary_text(pc.recording_summary([ZONE, DEST], {"from_zone": "Aquila/AQ_Z00_Hub"}, keys)) == "AQ_Z00_Hub"


def test_notes_summarize_as_their_first_line():
    assert summary_text(pc.notes_summary("\n  Team-Up stays off here.\nSecond line")) == "Team-Up stays off here."
    assert summary_text(pc.notes_summary(None)) == pc.NO_NOTES


def test_role_summary():
    assert summary_text(pc.role_summary(initial=False, recovery=False)) == "Not initial · not recovery"
    assert summary_text(pc.role_summary(initial=True, recovery=False)) == "Initial · not recovery"


# -- routes ------------------------------------------------------------------------


def test_an_unrouted_failure_routes_to_the_recovery_state():
    routes = pc.outgoing_routes(
        _state(), ["SUCCESS", "FAILURE"], [_t("a", "b", "SUCCESS")], recovery_state="hub", state_ids=["a", "b", "hub"]
    )
    assert [(r.when, r.kind, r.dest) for r in routes] == [("SUCCESS", pc.EDGE, "b"), ("FAILURE", pc.RECOVERY, "hub")]
    assert summary_text(pc.routes_summary(routes, {"b": "Sync players"})) == (
        "SUCCESS → Sync players · FAILURE → recovery"
    )


def test_the_recovery_state_does_not_fall_through_to_itself():
    routes = pc.outgoing_routes(
        _state("hub"), ["SUCCESS", "FAILURE"], [_t("hub", "b", "SUCCESS")], recovery_state="hub"
    )
    assert routes[1].kind == pc.UNROUTED


def test_an_unrouted_success_is_not_defaulted():
    routes = pc.outgoing_routes(_state(), ["SUCCESS", "FAILURE"], [_t("a", "b", "FAILURE")], recovery_state="hub")
    assert (routes[0].when, routes[0].kind) == ("SUCCESS", pc.UNROUTED)
    assert pc.routes_summary(routes, {})[0].warning


def test_a_stop_outranks_an_edge():
    routes = pc.outgoing_routes(
        _state(stops={"FAILURE": "Wrong world"}),
        ["SUCCESS", "FAILURE"],
        [_t("a", "b", "SUCCESS"), _t("a", "c", "FAILURE")],
        recovery_state="hub",
    )
    assert (routes[1].kind, routes[1].reason) == (pc.STOP, "Wrong world")


def test_a_node_with_no_edges_ends_the_run():
    routes = pc.outgoing_routes(_state(), ["SUCCESS", "FAILURE"], [], recovery_state="hub")
    assert {r.kind for r in routes} == {pc.END}


def test_an_unconditional_edge_covers_every_unrouted_return_and_keeps_its_priority():
    routes = pc.outgoing_routes(
        _state(), ["SUCCESS", "FAILURE"], [_t("a", "c"), _t("a", "b", "SUCCESS")], recovery_state="hub"
    )
    assert [(r.when, r.dest, r.priority) for r in routes] == [("SUCCESS", "b", 1), (None, "c", 2)]


def test_an_edge_on_a_return_the_node_does_not_declare_is_still_listed():
    routes = pc.outgoing_routes(
        _state(), ["SUCCESS"], [_t("a", "b", "TIMEOUT"), _t("a", "c", "SUCCESS")], recovery_state=None
    )
    assert [r.when for r in routes] == ["SUCCESS", "TIMEOUT"]
