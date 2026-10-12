"""Tests for automatic grouping at run start and after crash recovery.

Both call sites share one rule that matters more than the grouping itself: a grouping
failure must never stop the bot. Running ungrouped is a degraded run; stopping turns a
single crash into a dead overnight session.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bartlebot.clients.client_manager as cm
import bartlebot.clients.group_manager as gm
import bartlebot.game.wizard_names as wn
from bartlebot.clients.client_manager import ClientManager
from bartlebot.clients.group_manager import GroupResult, GroupState
from tests.conftest import FakeClient

# Both hooks survey group state before deciding whether to regroup. These stand in for the
# survey's two interesting answers: nobody grouped, and everybody in ONE group. A pair of
# grouped clients with mismatched rosters is a *split* group, which is not "already
# grouped" — that case is covered in test_group_manager.
UNGROUPED = [GroupState(), GroupState()]
ONE_GROUP = [
    GroupState(grouped=True, roster=("W0", "W1")),
    GroupState(grouped=True, roster=("W1", "W0")),
]
SPLIT = [
    GroupState(grouped=True, roster=("W0", "Stranger One")),
    GroupState(grouped=True, roster=("W1", "Stranger Two")),
]


class RegroupHarness:
    """Hosts the recovery regroup method without constructing a real ClientManager."""

    _regroup_after_recovery = ClientManager._regroup_after_recovery
    _refresh_group_routes = ClientManager._refresh_group_routes

    def __init__(self, clients: list) -> None:
        self._clients = clients
        self._machine = MagicMock()
        self._machine.is_stopped = False
        self._machine.settle_fights = AsyncMock()


@pytest.fixture
def settings(monkeypatch):
    """Stub SettingsManager.instance() so no real settings.json is touched."""
    stub = MagicMock()
    stub.auto_group_players = True
    monkeypatch.setattr(cm.SettingsManager, "instance", staticmethod(lambda: stub))
    return stub


@pytest.fixture(autouse=True)
def _no_name_detection():
    """Names resolve instantly — detection itself is covered by test_wizard_names."""
    with patch.object(wn, "resolve_names", new=AsyncMock(side_effect=lambda cs: [f"W{i}" for i in range(len(cs))])):
        yield


def _clients(n: int = 2) -> list[FakeClient]:
    return [FakeClient() for _ in range(n)]


# ---------------------------------------------------------------------------
# Post-recovery grouping
# ---------------------------------------------------------------------------


async def test_regroup_runs_when_clients_are_ungrouped(settings):
    harness = RegroupHarness(_clients())

    with (
        patch.object(gm, "read_group_states", new=AsyncMock(return_value=UNGROUPED)),
        patch.object(gm, "form_group", new=AsyncMock(return_value=GroupResult(success=True, added=["W1"]))) as form,
    ):
        await harness._regroup_after_recovery()

    form.assert_awaited_once()


async def test_regroup_skips_when_already_grouped(settings):
    """In the steady state this costs one membership survey and no invites."""
    harness = RegroupHarness(_clients())

    with (
        patch.object(gm, "read_group_states", new=AsyncMock(return_value=ONE_GROUP)),
        patch.object(gm, "form_group", new=AsyncMock()) as form,
    ):
        await harness._regroup_after_recovery()

    form.assert_not_called()


async def test_regroup_runs_when_clients_are_in_separate_groups(settings):
    """Both clients report grouped, so the per-client check alone would skip the regroup
    and leave the fleet split — losing the first turn it exists to guarantee."""
    harness = RegroupHarness(_clients())

    with (
        patch.object(gm, "read_group_states", new=AsyncMock(return_value=SPLIT)),
        patch.object(gm, "form_group", new=AsyncMock(return_value=GroupResult(success=False))) as form,
    ):
        await harness._regroup_after_recovery()

    form.assert_awaited_once()


async def test_regroup_skips_when_the_setting_is_off(settings):
    settings.auto_group_players = False
    harness = RegroupHarness(_clients())

    with (
        patch.object(gm, "read_group_states", new=AsyncMock(return_value=UNGROUPED)) as grouped,
        patch.object(gm, "form_group", new=AsyncMock()) as form,
    ):
        await harness._regroup_after_recovery()

    grouped.assert_not_called()
    form.assert_not_called()


async def test_regroup_skips_with_fewer_than_two_clients(settings):
    harness = RegroupHarness(_clients(1))

    with patch.object(gm, "form_group", new=AsyncMock()) as form:
        await harness._regroup_after_recovery()

    form.assert_not_called()


async def test_regroup_failure_does_not_stop_the_bot(settings):
    """A failed regroup must leave the FSM alone so recovery can still resume it."""
    harness = RegroupHarness(_clients())

    with (
        patch.object(gm, "read_group_states", new=AsyncMock(return_value=UNGROUPED)),
        patch.object(gm, "form_group", new=AsyncMock(return_value=GroupResult(success=False, failed=["W1"]))),
    ):
        await harness._regroup_after_recovery()

    harness._machine.stop.assert_not_called()


async def test_regroup_exception_does_not_propagate(settings):
    """An unexpected error must not escape into _run_recovery_queue, which stops the bot."""
    harness = RegroupHarness(_clients())

    with (
        patch.object(gm, "read_group_states", new=AsyncMock(side_effect=RuntimeError("UI read exploded"))),
        patch.object(gm, "form_group", new=AsyncMock()),
    ):
        await harness._regroup_after_recovery()

    harness._machine.stop.assert_not_called()


async def test_recovery_queue_settles_fights_then_regroups_before_jumping_to_recovery_state(settings):
    """Ordering matters: the bot must resume with the group already restored, and no fight may
    be going when the survey opens the social window over the cards (#203)."""
    calls: list[str] = []

    harness = RegroupHarness(_clients())
    harness._pending_recovery = {}
    harness._recovery_in_progress = True
    harness._get_recovery_state = lambda: "start"
    harness._machine.force_state = MagicMock(side_effect=lambda s: calls.append("force_state"))
    harness._machine.resume = MagicMock(side_effect=lambda: calls.append("resume"))
    harness._machine.settle_fights = AsyncMock(side_effect=lambda: calls.append("settle"))
    harness._regroup_after_recovery = AsyncMock(side_effect=lambda: calls.append("regroup"))
    harness._settle_recovery_batch = AsyncMock()

    await ClientManager._run_recovery_queue(harness)

    assert calls == ["settle", "regroup", "force_state", "resume"]


async def test_a_recovery_regroup_updates_the_group_routes_in_the_runs_port_map(settings):
    """The map was surveyed before the crash; the group it describes may be gone."""
    harness = RegroupHarness(_clients())
    after = [GroupState(True, ("W0", "W1")), GroupState(True, ("W0", "W1"))]

    with (
        patch.object(gm, "read_group_states", new=AsyncMock(return_value=UNGROUPED)),
        patch.object(gm, "form_group", new=AsyncMock(return_value=GroupResult(success=True, states=after))),
    ):
        await harness._regroup_after_recovery()

    harness._machine.port_map.set_groups.assert_called_once_with(["W0", "W1"], [("W0", "W1"), ("W0", "W1")])


async def test_a_recovery_that_finds_the_group_intact_still_refreshes_the_routes(settings):
    harness = RegroupHarness(_clients())

    with patch.object(gm, "read_group_states", new=AsyncMock(return_value=ONE_GROUP)):
        await harness._regroup_after_recovery()

    harness._machine.port_map.set_groups.assert_called_once_with(["W0", "W1"], [s.roster for s in ONE_GROUP])


async def test_a_run_with_no_port_map_yet_is_left_to_survey_for_itself(settings):
    harness = RegroupHarness(_clients())
    harness._machine.port_map = None

    with patch.object(gm, "read_group_states", new=AsyncMock(return_value=ONE_GROUP)):
        await harness._regroup_after_recovery()  # must not raise


async def test_a_failed_recovery_regroup_leaves_the_port_map_alone(settings):
    """Nothing trustworthy was read, so the routes are left for failed ports to demote."""
    harness = RegroupHarness(_clients())

    with patch.object(gm, "read_group_states", new=AsyncMock(side_effect=RuntimeError("boom"))):
        await harness._regroup_after_recovery()

    harness._machine.port_map.set_groups.assert_not_called()


# ---------------------------------------------------------------------------
# Pre-run grouping
# ---------------------------------------------------------------------------


class PreRunHarness:
    """Hosts the pre-run grouping method without constructing a real AppController."""

    def __init__(self, clients: list) -> None:
        from bartlebot.app_controller import AppController  # noqa: PLC0415

        self._form_pre_run_group = AppController._form_pre_run_group.__get__(self)
        self._client_manager = MagicMock()
        self._client_manager.clients = clients


@pytest.fixture
def app_settings(monkeypatch):
    import bartlebot.app_controller as ac  # noqa: PLC0415

    stub = MagicMock()
    stub.auto_group_players = True
    monkeypatch.setattr(ac.SettingsManager, "instance", staticmethod(lambda: stub))
    return stub


async def test_pre_run_grouping_runs_when_ungrouped(app_settings):
    harness = PreRunHarness(_clients())

    with (
        patch.object(gm, "read_group_states", new=AsyncMock(return_value=UNGROUPED)),
        patch.object(gm, "form_group", new=AsyncMock(return_value=GroupResult(success=True))) as form,
    ):
        await harness._form_pre_run_group()

    form.assert_awaited_once()


async def test_pre_run_grouping_runs_when_clients_are_in_separate_groups(app_settings):
    """As above, at the other call site — a split group must not read as "already done"."""
    harness = PreRunHarness(_clients())

    with (
        patch.object(gm, "read_group_states", new=AsyncMock(return_value=SPLIT)),
        patch.object(gm, "form_group", new=AsyncMock(return_value=GroupResult(success=False))) as form,
    ):
        await harness._form_pre_run_group()

    form.assert_awaited_once()


async def test_pre_run_grouping_skips_when_the_setting_is_off(app_settings):
    app_settings.auto_group_players = False
    harness = PreRunHarness(_clients())

    with patch.object(gm, "form_group", new=AsyncMock()) as form:
        await harness._form_pre_run_group()

    form.assert_not_called()


async def test_pre_run_grouping_skips_when_already_grouped(app_settings):
    harness = PreRunHarness(_clients())

    with (
        patch.object(gm, "read_group_states", new=AsyncMock(return_value=ONE_GROUP)),
        patch.object(gm, "form_group", new=AsyncMock()) as form,
    ):
        await harness._form_pre_run_group()

    form.assert_not_called()


async def test_pre_run_grouping_failure_does_not_abort_the_run(app_settings):
    """_start_bot must proceed to machine.run() even if grouping blew up."""
    harness = PreRunHarness(_clients())

    with (
        patch.object(gm, "read_group_states", new=AsyncMock(side_effect=RuntimeError("boom"))),
        patch.object(gm, "form_group", new=AsyncMock()),
    ):
        await harness._form_pre_run_group()  # must not raise


async def test_pre_run_grouping_hands_the_states_form_group_left_to_the_port_survey(app_settings):
    """A regroup must not cost the port survey a second visit to every social window."""
    harness = PreRunHarness(_clients())
    after = [GroupState(True, ("A", "B")), GroupState(True, ("A", "B"))]

    with (
        patch.object(gm, "read_group_states", new=AsyncMock(return_value=UNGROUPED)),
        patch.object(wn, "resolve_names", new=AsyncMock(return_value=["A", "B"])),
        patch.object(gm, "form_group", new=AsyncMock(return_value=GroupResult(success=True, states=after))),
    ):
        assert await harness._form_pre_run_group() == after


async def test_pre_run_grouping_hands_over_its_survey_when_nothing_changed(app_settings):
    harness = PreRunHarness(_clients())

    with patch.object(gm, "read_group_states", new=AsyncMock(return_value=ONE_GROUP)):
        assert await harness._form_pre_run_group() == ONE_GROUP


async def test_pre_run_grouping_hands_over_nothing_when_it_is_off_or_fails(app_settings):
    """The survey then reads the Group tab itself — the only case it should."""
    harness = PreRunHarness(_clients())
    with patch.object(gm, "read_group_states", new=AsyncMock(side_effect=RuntimeError("boom"))):
        assert await harness._form_pre_run_group() is None

    app_settings.auto_group_players = False
    assert await harness._form_pre_run_group() is None
