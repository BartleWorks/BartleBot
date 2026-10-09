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
        self.teleport_started = asyncio.Event()

    async def position(self):
        self.started.set()
        await asyncio.sleep(9999)

    async def get_base_entity_list(self):  # pragma: no cover - never reached
        return []

    async def teleport(self, *args, **kwargs):
        self.teleport_started.set()
        await asyncio.sleep(9999)


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


async def test_teleporting_with_no_selection_starts_nothing(qapp):
    dlg = EntityBrowserDialog(_StubClient())

    dlg._on_teleport()

    assert dlg._teleport_task is None
    dlg.close()


async def test_closing_cancels_an_in_flight_teleport(qapp):
    client = _StubClient()
    dlg = EntityBrowserDialog(client)
    _select_an_entity(dlg)

    dlg._on_teleport()
    # A ceiling, not a cadence: the event is set on the teleport's first await.
    await asyncio.wait_for(client.teleport_started.wait(), timeout=10)

    dlg.close()

    with pytest.raises(asyncio.CancelledError):
        await dlg._teleport_task


async def test_a_second_teleport_replaces_the_first(qapp):
    client = _StubClient()
    dlg = EntityBrowserDialog(client)
    _select_an_entity(dlg)

    dlg._on_teleport()
    first = dlg._teleport_task
    dlg._on_teleport()

    assert dlg._teleport_task is not first
    assert first.cancelled() or first.cancelling()
    dlg.close()
