"""Task lifetime for the entity browser's background load.

``_load_entities`` writes to this dialog's widgets after every await, and it
awaits two memory reads per entity across the whole entity list — so a load
easily outlives a dialog the user closed. Without cancellation those writes
land on a deleted C++ object. See "Background task lifetime in GUI widgets" in
``bartlebot/gui/CLAUDE.md``.
"""

import asyncio
import sys

import pytest
from PyQt6.QtWidgets import QApplication

from bartlebot.gui.entity_browser import EntityBrowserDialog


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


class _StubClient:
    """Never resolves, so the load is always still in flight when we close."""

    def __init__(self) -> None:
        self.body = self
        self.started = asyncio.Event()

    async def position(self):
        self.started.set()
        await asyncio.sleep(9999)

    async def get_base_entity_list(self):  # pragma: no cover - never reached
        return []


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
