"""Task lifetime for the entity browser's background load.

``_load_entities`` writes to this dialog's widgets after every await, and it
awaits two memory reads per entity across the whole entity list — so a load
easily outlives a dialog the user closed. Without cancellation those writes
land on a deleted C++ object. See "Background task lifetime in GUI widgets" in
``bartlebot/gui/CLAUDE.md``.
"""

import asyncio

import pytest
from PyQt6.QtWidgets import QTreeWidgetItem

from bartlebot.gui.dialogs.entity_browser import _POS_ROLE, EntityBrowserDialog


class _StubClient:
    """Never resolves, so the load is always still in flight when we close."""

    def __init__(self) -> None:
        self.body = self
        self.started = asyncio.Event()
        self.walk_started = asyncio.Event()

    async def position(self):
        self.started.set()
        await asyncio.sleep(9999)

    async def get_base_entity_list(self):  # pragma: no cover - never reached
        return []


class _StubTools:
    """The authoring tools, whose walk never ends, so it is still in flight when we close."""

    def __init__(self, client: _StubClient) -> None:
        self._client = client
        self.walks: list[tuple] = []

    async def walk_to(self, clients, goal, *, approach=False):
        self.walks.append((clients, goal, approach))
        self._client.walk_started.set()
        await asyncio.sleep(9999)


@pytest.fixture
def tools(monkeypatch):
    from bartlebot.clients import authoring_tools  # noqa: PLC0415

    def install(client):
        stub = _StubTools(client)
        monkeypatch.setattr(authoring_tools, "_current", stub)
        return stub

    return install


def _select_an_entity(dlg, pos=(1.0, 2.0, 3.0)):
    """Put one selected row in the tree, as a finished load would have."""
    item = QTreeWidgetItem(["AQ-Cerberus-A", "Cerberus", "0.0", "1.0, 2.0, 3.0"])
    item.setData(0, _POS_ROLE, pos)
    dlg._tree.addTopLevelItem(item)
    item.setSelected(True)
    return item


def test_constructing_without_a_running_loop_schedules_nothing(qapp):
    # Sync context: no ambient loop to schedule onto, and asking for one is an
    # error rather than a deprecation as of pytest-asyncio 1.4.
    dlg = EntityBrowserDialog(_StubClient())
    assert dlg._load_task is None
    dlg.deleteLater()


async def test_the_load_task_is_kept_referenced(qapp):
    dlg = EntityBrowserDialog(_StubClient())
    assert isinstance(dlg._load_task, asyncio.Task)
    dlg.close()


async def test_closing_cancels_an_in_flight_load(qapp):
    client = _StubClient()
    dlg = EntityBrowserDialog(client)
    # A ceiling, not a cadence: the event is set on the load task's first await,
    # so this nominally costs nothing and is generous purely so it cannot flake
    # under -n auto on a loaded machine. See tests/CLAUDE.md.
    await asyncio.wait_for(client.started.wait(), timeout=10)

    dlg.close()

    assert dlg._closed is True
    with pytest.raises(asyncio.CancelledError):
        await dlg._load_task


async def test_refreshing_replaces_the_previous_load(qapp):
    dlg = EntityBrowserDialog(_StubClient())
    first = dlg._load_task

    dlg._on_refresh()

    assert dlg._load_task is not first
    assert first.cancelled() or first.cancelling()
    dlg.close()


async def test_walking_with_no_selection_starts_nothing(qapp):
    dlg = EntityBrowserDialog(_StubClient())

    dlg._on_walk()

    assert dlg._walk_task is None
    dlg.close()


async def test_the_walk_goes_to_the_entity_s_edge_and_writes_no_position(qapp, tools):
    """It walks, since nothing but walking.hop writes a position (Stage 7 of #322)."""
    client = _StubClient()
    stub = tools(client)
    dlg = EntityBrowserDialog(client)
    _select_an_entity(dlg)

    dlg._on_walk()
    await asyncio.wait_for(client.walk_started.wait(), timeout=10)

    assert stub.walks == [([client], (1.0, 2.0, 3.0), True)]
    assert dlg._walk_btn.text() == "Walk to"
    dlg.close()


async def test_closing_cancels_an_in_flight_walk(qapp, tools):
    client = _StubClient()
    tools(client)
    dlg = EntityBrowserDialog(client)
    _select_an_entity(dlg)

    dlg._on_walk()
    # A ceiling, not a cadence: the event is set on the walk's first await.
    await asyncio.wait_for(client.walk_started.wait(), timeout=10)

    dlg.close()

    with pytest.raises(asyncio.CancelledError):
        await dlg._walk_task


async def test_a_second_walk_replaces_the_first(qapp, tools):
    client = _StubClient()
    tools(client)
    dlg = EntityBrowserDialog(client)
    _select_an_entity(dlg)

    dlg._on_walk()
    first = dlg._walk_task
    dlg._on_walk()

    assert dlg._walk_task is not first
    assert first.cancelled() or first.cancelling()
    dlg.close()
