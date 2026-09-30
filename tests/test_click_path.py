"""Unit tests for the click_path node."""

from unittest.mock import AsyncMock, MagicMock, patch

import bartlebot.nodes.interaction.click_path as cp
from tests.conftest import FakeClient, FakeMachine

_PATH = ["WorldView", "windowHUD", "btnOk"]
_CONFIRM_PATH = ["WorldView", "windowHUD", "dlgResult"]


def _make_window(*, visible: bool = True, grayed: bool = False):
    """A resolved UI window as the node sees it: visibility and grayed state only."""
    window = MagicMock()
    window.is_visible = AsyncMock(return_value=visible)
    window.is_control_grayed = AsyncMock(return_value=grayed)
    return window


def _resolver(window, count: int = 1):
    """Patch double for resolve_path_match returning a fixed (window, candidate count)."""
    return AsyncMock(return_value=(window, count))


async def test_success_no_confirm():
    """Path visible+enabled → click fires → SUCCESS."""
    client = FakeClient()
    with (
        patch.object(cp, "resolve_path_match", _resolver(_make_window())),
        patch.object(cp, "click_window", AsyncMock(return_value=True)),
    ):
        result = await cp.run(FakeMachine(), [client], {"path": _PATH, "availability_timeout": 0})
    assert result == "SUCCESS"


async def test_success_with_confirm_visible():
    """Click fires; confirm path becomes visible → SUCCESS."""
    client = FakeClient()
    with (
        patch.object(cp, "resolve_path_match", _resolver(_make_window())),
        patch.object(cp, "click_window", AsyncMock(return_value=True)),
        patch.object(cp, "is_visible_by_path", AsyncMock(return_value=True)),
    ):
        result = await cp.run(
            FakeMachine(),
            [client],
            {
                "path": _PATH,
                "availability_timeout": 0,
                "confirm_path": _CONFIRM_PATH,
                "confirm_visible": True,
                "confirm_timeout": 0.01,
            },
        )
    assert result == "SUCCESS"


async def test_success_with_confirm_enabled():
    """Click fires; confirm path becomes enabled (not grayed) → SUCCESS."""
    client = FakeClient()
    with (
        patch.object(cp, "resolve_path_match", _resolver(_make_window())),
        patch.object(cp, "click_window", AsyncMock(return_value=True)),
        patch.object(cp, "is_enabled_by_path", AsyncMock(return_value=True)),
    ):
        result = await cp.run(
            FakeMachine(),
            [client],
            {
                "path": _PATH,
                "availability_timeout": 0,
                "confirm_path": _CONFIRM_PATH,
                "confirm_visible": False,
                "confirm_timeout": 0.01,
            },
        )
    assert result == "SUCCESS"


async def test_failure_path_not_visible():
    """Path never visible → FAILURE without clicking."""
    client = FakeClient()
    mock_click = AsyncMock(return_value=True)
    with (
        patch.object(cp, "resolve_path_match", _resolver(_make_window(visible=False))),
        patch.object(cp, "click_window", mock_click),
    ):
        result = await cp.run(FakeMachine(), [client], {"path": _PATH, "availability_timeout": 0})
    assert result == "FAILURE"
    mock_click.assert_not_called()


async def test_failure_path_grayed():
    """Path visible but grayed out → FAILURE without clicking."""
    client = FakeClient()
    mock_click = AsyncMock(return_value=True)
    with (
        patch.object(cp, "resolve_path_match", _resolver(_make_window(grayed=True))),
        patch.object(cp, "click_window", mock_click),
    ):
        result = await cp.run(FakeMachine(), [client], {"path": _PATH, "availability_timeout": 0})
    assert result == "FAILURE"
    mock_click.assert_not_called()


async def test_failure_click_returns_false():
    """click_window returns False (race: element grayed between check and click) → FAILURE."""
    client = FakeClient()
    with (
        patch.object(cp, "resolve_path_match", _resolver(_make_window())),
        patch.object(cp, "click_window", AsyncMock(return_value=False)),
    ):
        result = await cp.run(FakeMachine(), [client], {"path": _PATH, "availability_timeout": 0})
    assert result == "FAILURE"


async def test_failure_confirm_timeout():
    """Click fires but confirm path never becomes visible → FAILURE."""
    client = FakeClient()
    with (
        patch.object(cp, "resolve_path_match", _resolver(_make_window())),
        patch.object(cp, "click_window", AsyncMock(return_value=True)),
        patch.object(cp, "is_visible_by_path", AsyncMock(return_value=False)),
        patch.object(cp, "POLL_INTERVAL", 0),
    ):
        result = await cp.run(
            FakeMachine(),
            [client],
            {
                "path": _PATH,
                "availability_timeout": 0,
                "confirm_path": _CONFIRM_PATH,
                "confirm_visible": True,
                "confirm_timeout": 0.01,
            },
        )
    assert result == "FAILURE"


async def test_no_wait_when_availability_timeout_zero():
    """availability_timeout=0 does a single check — poll_until is not invoked."""
    client = FakeClient()
    with (
        patch.object(cp, "resolve_path_match", _resolver(_make_window())),
        patch.object(cp, "click_window", AsyncMock(return_value=True)),
        patch.object(cp, "poll_until") as mock_poll,
    ):
        result = await cp.run(FakeMachine(), [client], {"path": _PATH, "availability_timeout": 0})
    assert result == "SUCCESS"
    mock_poll.assert_not_called()


async def test_multiple_clients_all_must_succeed():
    """Two clients; second client's path is grayed → FAILURE."""
    client1 = FakeClient()
    client2 = FakeClient()

    async def _resolve(client, path, **kwargs):
        return _make_window(grayed=client is client2), 1

    with (
        patch.object(cp, "resolve_path_match", _resolve),
        patch.object(cp, "click_window", AsyncMock(return_value=True)),
    ):
        result = await cp.run(FakeMachine(), [client1, client2], {"path": _PATH, "availability_timeout": 0})
    assert result == "FAILURE"


async def test_empty_path_returns_failure():
    """Empty path parameter → immediate FAILURE without any window checks."""
    result = await cp.run(FakeMachine(), [FakeClient()], {"path": []})
    assert result == "FAILURE"


# ---------------------------------------------------------------------------
# Ambiguous paths: several elements share one path (a menu of identical rows)
# ---------------------------------------------------------------------------


async def test_match_params_are_passed_through_to_the_resolver():
    """Match Text / Exact / Index reach resolve_path_match as given."""
    client = FakeClient()
    resolver = _resolver(_make_window(), count=6)
    with (
        patch.object(cp, "resolve_path_match", resolver),
        patch.object(cp, "click_window", AsyncMock(return_value=True)),
    ):
        result = await cp.run(
            FakeMachine(),
            [client],
            {
                "path": _PATH,
                "availability_timeout": 0,
                "match_text": "Krokotopia",
                "match_text_exact": True,
                "match_index": 2,
            },
        )
    assert result == "SUCCESS"
    assert resolver.await_args.kwargs == {
        "match_text": "Krokotopia",
        "match_text_exact": True,
        "match_index": 2,
    }


async def test_defaults_select_the_first_match():
    """With no match params the node behaves as it always did: first match, no filter."""
    client = FakeClient()
    resolver = _resolver(_make_window(), count=1)
    with (
        patch.object(cp, "resolve_path_match", resolver),
        patch.object(cp, "click_window", AsyncMock(return_value=True)),
    ):
        result = await cp.run(FakeMachine(), [client], {"path": _PATH, "availability_timeout": 0})
    assert result == "SUCCESS"
    assert resolver.await_args.kwargs == {"match_text": "", "match_text_exact": False, "match_index": 0}


async def test_ambiguous_path_warns_once():
    """Several matches and no match params → a warning naming the count, said once."""
    client = FakeClient()
    machine = FakeMachine()
    with (
        patch.object(cp, "resolve_path_match", _resolver(_make_window(), count=6)),
        patch.object(cp, "click_window", AsyncMock(return_value=True)),
    ):
        result = await cp.run(machine, [client], {"path": _PATH, "availability_timeout": 0})
    assert result == "SUCCESS"
    warnings = [m for m, _ in machine.logs if "matched 6 elements" in m]
    assert len(warnings) == 1


async def test_no_ambiguity_warning_when_match_params_narrow_it():
    """An explicit Match Index means the author chose; nothing to warn about."""
    client = FakeClient()
    machine = FakeMachine()
    with (
        patch.object(cp, "resolve_path_match", _resolver(_make_window(), count=6)),
        patch.object(cp, "click_window", AsyncMock(return_value=True)),
    ):
        await cp.run(machine, [client], {"path": _PATH, "availability_timeout": 0, "match_index": 3})
    assert not [m for m, _ in machine.logs if "matched 6 elements" in m]


async def test_failure_when_text_matches_nothing():
    """The path matched rows, but none carried the text → FAILURE, and the log says so."""
    client = FakeClient()
    machine = FakeMachine()
    with (
        patch.object(cp, "resolve_path_match", _resolver(None, count=6)),
        patch.object(cp, "click_window", AsyncMock(return_value=True)),
    ):
        result = await cp.run(
            machine,
            [client],
            {"path": _PATH, "availability_timeout": 0, "match_text": "Nowhere"},
        )
    assert result == "FAILURE"
    assert any("none of the 6 matched elements had text 'Nowhere'" in m for m, _ in machine.logs)


async def test_failure_when_match_index_out_of_range():
    """Index past the end of the matches → FAILURE naming the count."""
    client = FakeClient()
    machine = FakeMachine()
    with (
        patch.object(cp, "resolve_path_match", _resolver(None, count=3)),
        patch.object(cp, "click_window", AsyncMock(return_value=True)),
    ):
        result = await cp.run(machine, [client], {"path": _PATH, "availability_timeout": 0, "match_index": 7})
    assert result == "FAILURE"
    assert any("match index 7 out of range (3 matched)" in m for m, _ in machine.logs)


async def test_failure_when_path_matches_nothing():
    """No candidates at all is reported differently from a rejected filter."""
    client = FakeClient()
    machine = FakeMachine()
    with (
        patch.object(cp, "resolve_path_match", _resolver(None, count=0)),
        patch.object(cp, "click_window", AsyncMock(return_value=True)),
    ):
        result = await cp.run(machine, [client], {"path": _PATH, "availability_timeout": 0})
    assert result == "FAILURE"
    assert any("path matched no elements" in m for m, _ in machine.logs)


async def test_clicks_the_window_the_checks_passed_on():
    """The click targets the resolved window, not a fresh walk of the path."""
    client = FakeClient()
    chosen = _make_window()
    mock_click = AsyncMock(return_value=True)
    with (
        patch.object(cp, "resolve_path_match", _resolver(chosen, count=4)),
        patch.object(cp, "click_window", mock_click),
    ):
        result = await cp.run(FakeMachine(), [client], {"path": _PATH, "availability_timeout": 0, "match_index": 2})
    assert result == "SUCCESS"
    mock_click.assert_awaited_once_with(client, chosen)


# ---------------------------------------------------------------------------
# Click Clients: the UI is only open on some clients
# ---------------------------------------------------------------------------


async def test_click_clients_limits_the_node_to_the_selected_clients():
    """Client 2 has no such path; selecting only client 1 still succeeds."""
    client1 = FakeClient()
    client2 = FakeClient()
    seen = []

    async def _resolve(client, path, **kwargs):
        seen.append(client)
        return (_make_window(), 1) if client is client1 else (None, 0)

    with (
        patch.object(cp, "resolve_path_match", _resolve),
        patch.object(cp, "click_window", AsyncMock(return_value=True)) as mock_click,
    ):
        result = await cp.run(
            FakeMachine(),
            [client1, client2],
            {"path": _PATH, "click_clients": [1], "availability_timeout": 0},
        )
    assert result == "SUCCESS"
    assert seen == [client1]
    assert mock_click.await_count == 1


async def test_click_clients_empty_means_every_client():
    """No selection keeps the old behavior: every client must find the path."""
    client1 = FakeClient()
    client2 = FakeClient()

    async def _resolve(client, path, **kwargs):
        return (_make_window(), 1) if client is client1 else (None, 0)

    with (
        patch.object(cp, "resolve_path_match", _resolve),
        patch.object(cp, "click_window", AsyncMock(return_value=True)),
    ):
        result = await cp.run(
            FakeMachine(),
            [client1, client2],
            {"path": _PATH, "click_clients": [], "availability_timeout": 0},
        )
    assert result == "FAILURE"


async def test_click_clients_selecting_an_absent_client_is_a_no_op():
    """A selection naming only clients that are not connected succeeds without clicking."""
    with (
        patch.object(cp, "resolve_path_match", _resolver(_make_window())),
        patch.object(cp, "click_window", AsyncMock(return_value=True)) as mock_click,
    ):
        result = await cp.run(
            FakeMachine(),
            [FakeClient()],
            {"path": _PATH, "click_clients": [3], "availability_timeout": 0},
        )
    assert result == "SUCCESS"
    mock_click.assert_not_called()
