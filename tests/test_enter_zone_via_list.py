"""Unit tests for the enter_zone_via_list node."""

from unittest.mock import AsyncMock, patch

import bartlebot.nodes.zone_entry.enter_zone_via_list as evl
from tests.conftest import FakeClient, FakeMachine

_PARAMS = {
    "zone": "TestZone",
    "to_zone": "DestZone",
    "interact_xyz": {"x": 0.0, "y": 0.0, "z": 0.0},
    "reset_xyz": {"x": 1.0, "y": 0.0, "z": 0.0},
    "zone_list_entry": "Krokotopia",
}

_NH = "bartlebot.nodes.zone_entry.enter_zone_via_list.node_helpers"


def _ready_client(zone: str = "TestZone") -> FakeClient:
    """Client with no stale prompt and interact prompt appearing at interact_xyz."""
    c = FakeClient()
    c.zone_name.return_value = zone
    # First call: stale-prompt check → no stale prompt.
    # Second call onward: prompt is present at interact_xyz.
    c.is_in_npc_range.side_effect = [False, True]
    return c


async def test_wrong_zone_fails():
    """Zone check: client not in expected zone → FAILURE before any teleport."""
    client = FakeClient()
    client.zone_name.return_value = "WrongZone"

    with patch.object(evl, "POLL_INTERVAL", 0):
        result = await evl.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def test_success_entry_on_first_page():
    """Happy path: zone list opens, entry is on page 1 → select → teleport → SUCCESS."""
    client = _ready_client()
    client.zone_name.side_effect = ["TestZone", "DestZone"]

    with (
        patch.object(evl, "POLL_INTERVAL", 0),
        patch.object(evl, "ZONE_CHANGE_TIMEOUT", 0.1),
        patch(f"{_NH}.is_visible_by_path", AsyncMock(return_value=True)),
        patch(f"{_NH}.get_text_by_path", AsyncMock(side_effect=["Krokotopia", "", "", ""] * 10)),
        patch(f"{_NH}.click_by_path", AsyncMock(return_value=True)),
    ):
        result = await evl.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"


async def test_success_entry_on_second_page():
    """Entry not on page 1; after one page turn it appears on page 2 → SUCCESS."""
    client = _ready_client()
    client.zone_name.side_effect = ["TestZone", "DestZone"]

    # Texts consumed in order:
    #   page 1 scan (4) → page-changed detection after Next (4) → page 2 scan (4)
    opt_texts = (
        ["Wizard City", "Marleybone", "Mooshu", "Dragonspyre"]  # page 1 scan
        + ["Krokotopia", "", "", ""]  # page_changed check
        + ["Krokotopia", "", "", ""]  # page 2 scan → match
    )
    opt_iter = iter(opt_texts)

    async def _get_text(c, path):
        try:
            return next(opt_iter)
        except StopIteration:
            return ""

    with (
        patch.object(evl, "POLL_INTERVAL", 0),
        patch.object(evl, "ZONE_CHANGE_TIMEOUT", 0.1),
        patch(f"{_NH}.is_visible_by_path", AsyncMock(return_value=True)),
        patch(f"{_NH}.get_text_by_path", _get_text),
        patch(f"{_NH}.click_by_path", AsyncMock(return_value=True)),
    ):
        result = await evl.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"


async def test_fuzzy_match_partial_name():
    """Entry 'Krokotopia' matches list text 'Krokotopian Oasis' via substring containment."""
    client = _ready_client()
    client.zone_name.side_effect = ["TestZone", "DestZone"]

    with (
        patch.object(evl, "POLL_INTERVAL", 0),
        patch.object(evl, "ZONE_CHANGE_TIMEOUT", 0.1),
        patch(f"{_NH}.is_visible_by_path", AsyncMock(return_value=True)),
        # List shows a longer name; entry is a substring → fuzzy match succeeds
        patch(f"{_NH}.get_text_by_path", AsyncMock(side_effect=["Krokotopian Oasis", "", "", ""] * 10)),
        patch(f"{_NH}.click_by_path", AsyncMock(return_value=True)),
    ):
        result = await evl.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"


async def test_circular_detection_fails_when_no_match():
    """Entry absent from all pages; wrap-around detected via repeated page signature → cancel → FAILURE."""
    client = _ready_client()
    click_mock = AsyncMock(return_value=True)

    # Two-page simulation:
    #   page 1 scan (A×4) → page_changed to page 2 (B×4) → page 2 scan (B×4)
    #   → page_changed wraps back to page 1 (A×4) → page 1 scan again (A×4) → sig seen → False
    opt_texts = (
        ["A", "B", "C", "D"]  # page 1 scan
        + ["E", "F", "G", "H"]  # page_changed: page 2 appeared
        + ["E", "F", "G", "H"]  # page 2 scan
        + ["A", "B", "C", "D"]  # page_changed: wrapped back to page 1
        + ["A", "B", "C", "D"]  # page 1 scan → sig already in visited → return False
    )
    opt_iter = iter(opt_texts)

    async def _get_text(c, path):
        try:
            return next(opt_iter)
        except StopIteration:
            return ""

    with (
        patch.object(evl, "POLL_INTERVAL", 0),
        patch(f"{_NH}.is_visible_by_path", AsyncMock(return_value=True)),
        patch(f"{_NH}.get_text_by_path", _get_text),
        patch(f"{_NH}.click_by_path", click_mock),
    ):
        result = await evl.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    paths_clicked = [call.args[1] for call in click_mock.call_args_list]
    assert evl.UNIVERSE_CANCEL_BUTTON in paths_clicked


async def test_entry_not_found_cancels_and_fails():
    """Alias for clarity: entry absent, cancel clicked before returning FAILURE."""
    # Covered by test_circular_detection_fails_when_no_match; this simpler version
    # uses a single page whose sig is immediately seen again after the Next click.
    client = _ready_client()
    click_mock = AsyncMock(return_value=True)

    # page 1 scan → page_changed returns same texts (single page, Next wraps back) → sig seen
    opt_texts = (
        ["A", "A", "A", "A"]  # page 1 scan
        + ["A", "A", "A", "A"]  # page_changed check: same → times out; next iter: same sig
    )
    opt_iter = iter(opt_texts)

    async def _get_text(c, path):
        try:
            return next(opt_iter)
        except StopIteration:
            return "A"

    with (
        patch.object(evl, "POLL_INTERVAL", 0),
        patch(f"{_NH}.is_visible_by_path", AsyncMock(return_value=True)),
        patch(f"{_NH}.get_text_by_path", _get_text),
        patch(f"{_NH}.click_by_path", click_mock),
    ):
        result = await evl.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    paths_clicked = [call.args[1] for call in click_mock.call_args_list]
    assert evl.UNIVERSE_CANCEL_BUTTON in paths_clicked


async def test_prompt_never_appears_fails():
    """Interact prompt never shows after MAX_RETRIES → FAILURE."""
    client = FakeClient()
    client.is_in_npc_range.return_value = False

    with patch.object(evl, "POLL_INTERVAL", 0):
        result = await evl.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    assert client.teleport.call_count == evl.MAX_RETRIES * 2


async def test_zone_list_never_opens_fails():
    """NPC prompt appears but zone list UI never becomes visible → FAILURE."""
    client = _ready_client()

    with (
        patch.object(evl, "POLL_INTERVAL", 0),
        patch(f"{_NH}.is_visible_by_path", AsyncMock(return_value=False)),
    ):
        result = await evl.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_zone_change_timeout_fails():
    """Entry found, teleport clicked, but clients never reach to_zone → FAILURE."""
    client = _ready_client()
    client.zone_name.return_value = "TestZone"  # never reaches DestZone

    with (
        patch.object(evl, "POLL_INTERVAL", 0),
        patch.object(evl, "ZONE_CHANGE_TIMEOUT", 0.01),
        patch(f"{_NH}.is_visible_by_path", AsyncMock(return_value=True)),
        patch(f"{_NH}.get_text_by_path", AsyncMock(side_effect=["Krokotopia", "", "", ""] * 20)),
        patch(f"{_NH}.click_by_path", AsyncMock(return_value=True)),
    ):
        result = await evl.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"


async def test_stale_prompt_cleared():
    """Pre-existing prompt is cleared via reset_xyz before the retry loop starts."""
    client = FakeClient()
    call_count = 0

    async def _npc_range():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return True  # stale prompt on node entry
        if call_count == 2:
            return False  # cleared after teleport to reset_xyz
        return True  # prompt at interact_xyz on first attempt

    client.is_in_npc_range.side_effect = _npc_range
    client.zone_name.side_effect = ["TestZone", "DestZone"]

    with (
        patch.object(evl, "POLL_INTERVAL", 0),
        patch.object(evl, "ZONE_CHANGE_TIMEOUT", 0.1),
        patch(f"{_NH}.is_visible_by_path", AsyncMock(return_value=True)),
        patch(f"{_NH}.get_text_by_path", AsyncMock(side_effect=["Krokotopia", "", "", ""] * 10)),
        patch(f"{_NH}.click_by_path", AsyncMock(return_value=True)),
    ):
        result = await evl.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    first_tp = client.teleport.call_args_list[0]
    assert first_tp.args[0].x == 1.0  # reset_xyz


# ---------------------------------------------------------------------------
# Dungeon exit warning (issue #84)
# ---------------------------------------------------------------------------


async def test_instance_prompt_is_answered_after_the_teleport_click():
    """Using a zone list from inside a dungeon raises the exit prompt, which holds the port.

    Blocking on the answer is safe here only because what follows waits on the arrival
    zone, which stays true once it happens — unlike an is_loading() wait, which can be
    missed entirely if the node spends the Safety Delay elsewhere first.
    """
    clients = [_ready_client(), _ready_client()]
    for c in clients:
        c.zone_name.side_effect = ["TestZone", "DestZone"]
    answer = AsyncMock(return_value=None)

    with (
        patch.object(evl, "POLL_INTERVAL", 0),
        patch.object(evl, "ZONE_CHANGE_TIMEOUT", 0.1),
        patch(f"{_NH}.is_visible_by_path", AsyncMock(return_value=True)),
        patch(f"{_NH}.get_text_by_path", AsyncMock(side_effect=["Krokotopia", "", "", ""] * 10)),
        patch(f"{_NH}.click_by_path", AsyncMock(return_value=True)),
        patch(f"{_NH}.answer_instance_prompts", answer),
    ):
        result = await evl.run(FakeMachine(), clients, _PARAMS)

    assert result == "SUCCESS"
    answer.assert_awaited_once()
    assert answer.await_args[0][0] == clients


async def test_a_refused_teleport_fails_instead_of_waiting_for_a_zone_change():
    """A gauntlet will not let anyone change areas, so the arrival wait cannot succeed."""
    from bartlebot import dialog_captions

    clients = [_ready_client()]
    machine = FakeMachine()

    with (
        patch.object(evl, "POLL_INTERVAL", 0),
        patch.object(evl, "ZONE_CHANGE_TIMEOUT", 0.1),
        patch(f"{_NH}.is_visible_by_path", AsyncMock(return_value=True)),
        patch(f"{_NH}.get_text_by_path", AsyncMock(side_effect=["Krokotopia", "", "", ""] * 10)),
        patch(f"{_NH}.click_by_path", AsyncMock(return_value=True)),
        patch(
            f"{_NH}.answer_instance_prompts",
            AsyncMock(return_value=dialog_captions.GAUNTLET_NO_ZONE_CHANGE),
        ),
    ):
        result = await evl.run(machine, clients, _PARAMS)

    assert result == "FAILURE"
    assert any("in a Gauntlet" in msg or "gauntlet" in msg for msg, _lvl in machine.logs)


# ---------------------------------------------------------------------------
# Sequential mode
# ---------------------------------------------------------------------------


def _positional_client() -> FakeClient:
    """Client whose interact prompt shows at interact_xyz (x=0) and not at reset_xyz (x=1)."""
    c = FakeClient()
    state = {"at_interact": False, "gone": False}

    async def _teleport(xyz):
        state["at_interact"] = xyz.x == 0.0

    async def _in_range():
        return state["at_interact"] and not state["gone"]

    async def _zone():
        return "DestZone" if state["gone"] else "TestZone"

    c.teleport.side_effect = _teleport
    c.is_in_npc_range.side_effect = _in_range
    c.zone_name.side_effect = _zone
    c.state = state
    return c


async def test_sequential_each_client_uses_the_list_alone():
    """sequential=True: a client holds the list from its keypress to its Teleport click."""
    c1, c2 = _positional_client(), _positional_client()
    clients = [c1, c2]
    params = {**_PARAMS, "sequential": True}
    holders: list[int] = []

    async def _click(client, path):
        if list(path) == list(evl.UNIVERSE_TELEPORT_BUTTON):
            # The client releases the object here, so exactly one may be mid-turn.
            assert holders.count(clients.index(client)) <= 1
            holders.append(clients.index(client))
            client.state["gone"] = True
        return True

    with (
        patch.object(evl, "POLL_INTERVAL", 0),
        patch.object(evl, "ZONE_CHANGE_TIMEOUT", 0.1),
        patch(f"{_NH}.OBJECT_USE_DELAY", 0),
        patch(f"{_NH}.is_visible_by_path", AsyncMock(return_value=True)),
        patch(f"{_NH}.get_text_by_path", AsyncMock(side_effect=["Krokotopia", "", "", ""] * 20)),
        patch(f"{_NH}.click_by_path", AsyncMock(side_effect=_click)),
    ):
        result = await evl.run(FakeMachine(), clients, params)

    assert result == "SUCCESS"
    assert holders == [0, 1]  # in order, one at a time


async def test_sequential_entry_not_found_cancels_and_fails():
    """A client that cannot find its entry cancels the list rather than leaving it open."""
    client = _positional_client()
    machine = FakeMachine()
    params = {**_PARAMS, "sequential": True}
    clicked: list[list[str]] = []

    async def _click(_client, path):
        clicked.append(list(path))
        return True

    with (
        patch.object(evl, "POLL_INTERVAL", 0),
        patch.object(evl, "_MAX_PAGES", 2),
        patch(f"{_NH}.OBJECT_USE_DELAY", 0),
        patch(f"{_NH}.is_visible_by_path", AsyncMock(return_value=True)),
        patch(f"{_NH}.get_text_by_path", AsyncMock(return_value="Wizard City")),
        patch(f"{_NH}.click_by_path", AsyncMock(side_effect=_click)),
    ):
        result = await evl.run(machine, [client], params)

    assert result == "FAILURE"
    assert list(evl.UNIVERSE_CANCEL_BUTTON) in clicked
    assert any("could not open the zone list or select" in msg for msg, _ in machine.logs)


async def test_sequential_prompt_never_appears_fails():
    """A client that never gets the prompt is named, and the list is never opened."""
    client = _positional_client()
    client.is_in_npc_range.side_effect = None
    client.is_in_npc_range.return_value = False
    machine = FakeMachine()
    params = {**_PARAMS, "sequential": True}

    with (
        patch.object(evl, "POLL_INTERVAL", 0),
        patch(f"{_NH}.OBJECT_USE_DELAY", 0),
        patch(f"{_NH}.is_visible_by_path", AsyncMock(return_value=True)),
        patch(f"{_NH}.click_by_path", AsyncMock(return_value=True)),
    ):
        result = await evl.run(machine, [client], params)

    assert result == "FAILURE"
    assert any("interact prompt never appeared for client(s) [1]" in msg for msg, _ in machine.logs)


# ══════════════════════════════════════════════════════════════════════════════
# Zone List Aliases — the same entry under another locale's spelling
# ══════════════════════════════════════════════════════════════════════════════


def test_queries_dedupes_and_puts_the_primary_entry_first():
    assert evl._queries("Wizard City", ["Sorcelleville", "  wizard city  ", ""]) == [
        "wizard city",
        "sorcelleville",
    ]


def test_queries_survives_a_missing_alias_list():
    assert evl._queries("Krokotopia", None) == ["krokotopia"]


def test_fuzzy_match_hits_on_an_alias_not_just_the_primary():
    queries = evl._queries("Dragonspyre", ["Drachenfels", "Roc du dragon"])

    assert evl._fuzzy_match("Drachenfels", queries) is True
    assert evl._fuzzy_match("Roc du dragon", queries) is True
    assert evl._fuzzy_match("Krokotopia", queries) is False


async def test_alias_is_selected_when_the_list_is_in_another_locale():
    """A German client lists 'Drachenfels'; the English entry alone would never match."""
    client = _ready_client()
    client.zone_name.side_effect = ["TestZone", "DestZone"]
    params = {**_PARAMS, "zone_list_entry": "Dragonspyre", "zone_list_aliases": ["Drachenfels"]}

    with (
        patch.object(evl, "POLL_INTERVAL", 0),
        patch.object(evl, "ZONE_CHANGE_TIMEOUT", 0.1),
        patch(f"{_NH}.is_visible_by_path", AsyncMock(return_value=True)),
        patch(f"{_NH}.get_text_by_path", AsyncMock(side_effect=["Drachenfels", "", "", ""] * 10)),
        patch(f"{_NH}.click_by_path", AsyncMock(return_value=True)),
    ):
        result = await evl.run(FakeMachine(), [client], params)

    assert result == "SUCCESS"


async def test_no_aliases_behaves_exactly_as_before():
    """The param is additive: a bot that never sets it must be unaffected."""
    client = _ready_client()
    client.zone_name.side_effect = ["TestZone", "DestZone"]

    with (
        patch.object(evl, "POLL_INTERVAL", 0),
        patch.object(evl, "ZONE_CHANGE_TIMEOUT", 0.1),
        patch(f"{_NH}.is_visible_by_path", AsyncMock(return_value=True)),
        patch(f"{_NH}.get_text_by_path", AsyncMock(return_value="Drachenfels")),
        patch(f"{_NH}.click_by_path", AsyncMock(return_value=True)),
    ):
        # _PARAMS asks for "Krokotopia" and carries no aliases, so the German row must miss.
        # Every page reads the same, so the visited-signature check wraps and gives up.
        result = await evl.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
