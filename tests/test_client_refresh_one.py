"""Guards on ``ClientSection._refresh_one``, the per-widget refresh wrapper (#128).

The wrapper exists for one reason: the coroutine ``ClientWidget.refresh()`` returns
must not be constructed until something is actually going to await it.  ``gather``
converts each argument into a Task synchronously, and those Tasks have not stepped
yet — a cancel landing in that window (a tab switch, an unhook, app shutdown) tears
them down before they await anything.  A coroutine built by the caller and dropped
there is garbage collected unawaited, which is the ``RuntimeWarning`` in #128.

It is a staticmethod so these can call it without a QApplication.
"""

import asyncio
import gc
import warnings

from bartlebot.gui.sidebar.sections.client_section import ClientSection


class FakeClientWidget:
    """Stands in for ClientWidget; records whether its refresh coroutine ever ran."""

    def __init__(self) -> None:
        self.refreshed = 0

    async def refresh(self) -> None:
        self.refreshed += 1


def _never_awaited(caught: list[warnings.WarningMessage]) -> list[str]:
    return [str(w.message) for w in caught if "never awaited" in str(w.message)]


async def test_refresh_one_awaits_the_widget() -> None:
    widget = FakeClientWidget()
    await ClientSection._refresh_one(widget)
    assert widget.refreshed == 1


async def test_refresh_one_times_out_a_wedged_widget(monkeypatch) -> None:
    """A widget whose read never returns is abandoned rather than stalling the cycle."""
    import bartlebot.gui.sidebar.sections.client_section as mod

    monkeypatch.setattr(mod, "CLIENT_REFRESH_TIMEOUT", 0.05)

    class Wedged(FakeClientWidget):
        async def refresh(self) -> None:
            await asyncio.Event().wait()

    try:
        await ClientSection._refresh_one(Wedged())
    except TimeoutError:
        pass
    else:  # pragma: no cover - only reached if the timeout is dropped
        raise AssertionError("expected the wrapper to time out")


async def test_cancel_before_the_gather_children_start_warns_nothing() -> None:
    """The #128 regression: cancel in the window between gather() and the first step."""
    widgets = [FakeClientWidget(), FakeClientWidget()]

    async def cycle() -> None:
        await asyncio.gather(
            *[ClientSection._refresh_one(w) for w in widgets],
            return_exceptions=True,
        )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        task = asyncio.get_running_loop().create_task(cycle())
        await asyncio.sleep(0)  # let cycle() run as far as gather(), no further
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        gc.collect()

    assert _never_awaited(caught) == []
    assert [w.refreshed for w in widgets] == [0, 0]
