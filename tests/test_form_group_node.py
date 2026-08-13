"""Unit tests for the form_group node."""

from unittest.mock import AsyncMock, patch

import bartlebot.nodes.utility.form_group as fg
from bartlebot.group_manager import GroupResult
from bartlebot.nodes.registry import NODE_REGISTRY, NODE_SPECS, NODE_TYPE_TO_CATEGORY
from tests.conftest import FakeClient, FakeMachine

_PARAMS = {"leader_index": 0, "require_all": True}


def _names(*values):
    return patch.object(fg.wizard_names, "resolve_names", new=AsyncMock(return_value=list(values)))


def _form(result: GroupResult):
    return patch.object(fg.group_manager, "form_group", new=AsyncMock(return_value=result))


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_node_is_registered_everywhere():
    """A node missing from any one of these is silently unusable in the editor."""
    assert NODE_REGISTRY["form_group"] is fg.run
    assert NODE_SPECS["form_group"] is fg.NODE_SPEC
    assert NODE_TYPE_TO_CATEGORY["form_group"] == "Utility"


def test_node_warns_about_the_friending_prerequisite():
    assert fg.NODE_SPEC.safety_warning
    assert "friends list" in fg.NODE_SPEC.safety_warning


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


async def test_single_client_succeeds_without_grouping():
    with _form(GroupResult(success=True)) as form:
        result = await fg.run(FakeMachine(), [FakeClient()], _PARAMS)

    assert result == "SUCCESS"
    form.assert_not_called()


async def test_leader_index_out_of_range_fails():
    clients = [FakeClient(), FakeClient()]

    with _form(GroupResult(success=True)) as form:
        result = await fg.run(FakeMachine(), clients, {"leader_index": 5, "require_all": True})

    assert result == "FAILURE"
    form.assert_not_called()


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


async def test_success_passes_names_and_leader_through():
    clients = [FakeClient(), FakeClient()]

    with _names("Alice", "Bob"), _form(GroupResult(success=True, added=["Bob"])) as form:
        result = await fg.run(FakeMachine(), clients, {"leader_index": 1, "require_all": True})

    assert result == "SUCCESS"
    assert form.call_args[0][1] == ["Alice", "Bob"]
    assert form.call_args[1]["leader_index"] == 1


async def test_failure_when_require_all_is_set():
    clients = [FakeClient(), FakeClient()]
    partial = GroupResult(success=False, added=["Bob"], failed=["Carol"])

    with _names("Alice", "Bob"), _form(partial):
        result = await fg.run(FakeMachine(), clients, {"leader_index": 0, "require_all": True})

    assert result == "FAILURE"


async def test_partial_success_allowed_when_require_all_is_off():
    """A partial group still gives everyone in it the first-turn benefit."""
    clients = [FakeClient(), FakeClient()]
    partial = GroupResult(success=False, added=["Bob"], failed=["Carol"])

    with _names("Alice", "Bob"), _form(partial):
        result = await fg.run(FakeMachine(), clients, {"leader_index": 0, "require_all": False})

    assert result == "SUCCESS"


async def test_total_failure_fails_even_when_require_all_is_off():
    """Nobody was added, so there is no group at all — that is a real failure."""
    clients = [FakeClient(), FakeClient()]
    nothing = GroupResult(success=False, added=[], failed=["Bob"])

    with _names("Alice", "Bob"), _form(nothing):
        result = await fg.run(FakeMachine(), clients, {"leader_index": 0, "require_all": False})

    assert result == "FAILURE"


async def test_machine_log_is_forwarded_to_the_engine():
    """Group progress must reach the in-app log panel, not just the log file."""
    clients = [FakeClient(), FakeClient()]
    machine = FakeMachine()

    with _names("Alice", "Bob"), _form(GroupResult(success=True)) as form:
        await fg.run(machine, clients, _PARAMS)

    assert form.call_args[1]["log"] == machine.log
