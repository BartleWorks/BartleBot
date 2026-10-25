"""The run trace: spans, the hooks on wizwalker, and the file a run leaves behind."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from wizwalker.client import Client
from wizwalker.memory.memory_objects.window import Window
from wizwalker.memory.memory_reader import MemoryReader
from wizwalker.mouse_handler import MouseHandler

from bartlebot.bot import run_trace


@pytest.fixture
def dev(monkeypatch):
    monkeypatch.setenv("BARTLEBOT_DEV", "1")


@pytest.fixture
def stopped():
    """Never leave a trace, or its hooks on wizwalker's classes, to the next test."""
    yield
    run_trace.stop()


def _records(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _spans(records) -> dict[str, dict]:
    return {r["name"]: r for r in records if r["kind"] == "span"}


class _FakeProcess:
    def read_bytes(self, address, size):
        return b"\0" * size


def _reader() -> MemoryReader:
    reader = object.__new__(MemoryReader)
    reader.process = _FakeProcess()
    return reader


async def test_off_outside_developer_mode(tmp_path, stopped):
    assert run_trace.start(lambda client: 1, tmp_path) is None
    assert not run_trace.active()
    assert not list(tmp_path.iterdir())
    with run_trace.span("anything") as span:
        assert span is None


async def test_spans_nest_and_a_task_started_inside_one_belongs_to_it(dev, tmp_path, stopped):
    clients = [object(), object()]
    path = run_trace.start(lambda c: clients.index(c) + 1, tmp_path)

    async def per_client(client):
        with run_trace.span("inner", client=client):
            with run_trace.span(f"leaf{clients.index(client)}"):
                await asyncio.sleep(0)

    with run_trace.span("outer"):
        await asyncio.gather(*(per_client(c) for c in clients))
    run_trace.stop()

    records = _records(path)
    assert records[0]["kind"] == "start"
    spans = _spans(records)
    inner = [r for r in records if r.get("name") == "inner"]
    assert {r["parent"] for r in inner} == {spans["outer"]["id"]}
    assert {r["slot"] for r in inner} == {1, 2}
    # A span with no player of its own takes its parent's.
    assert spans["leaf0"]["slot"] == 1 and spans["leaf1"]["slot"] == 2
    assert spans["outer"]["slot"] is None


async def test_reads_count_against_the_innermost_span(dev, tmp_path, stopped):
    path = run_trace.start(lambda c: None, tmp_path)
    reader = _reader()
    with run_trace.span("outer"):
        await reader.read_bytes(0x1000, 8)
        with run_trace.span("inner"):
            for _ in range(3):
                await reader.read_bytes(0x1000, 8)
    run_trace.stop()

    spans = _spans(_records(path))
    assert spans["outer"]["reads"] == 1
    assert spans["inner"]["reads"] == 3


async def test_stopping_puts_wizwalkers_own_methods_back(dev, tmp_path, stopped):
    originals = (MemoryReader.read_bytes, Client.send_key, MouseHandler.click, Window.get_windows_with_name)
    run_trace.start(lambda c: None, tmp_path)
    assert MemoryReader.read_bytes is not originals[0]
    run_trace.stop()
    assert (MemoryReader.read_bytes, Client.send_key, MouseHandler.click, Window.get_windows_with_name) == originals


async def test_a_key_names_the_bartlebot_function_that_pressed_it(dev, tmp_path, stopped, monkeypatch):
    sent = []

    async def timed_send_key(hwnd, key, seconds):
        sent.append(key)

    monkeypatch.setattr("wizwalker.client.utils.timed_send_key", timed_send_key)
    client = object.__new__(Client)
    client.window_handle = 1
    key = SimpleNamespace(name="P")
    # A function whose module is in bartlebot, as deck_io's deck_page_open is.
    namespace = {"__name__": "bartlebot.combat.example", "key": key}
    exec("async def open_page(client):\n    await client.send_key(key)\n", namespace)

    path = run_trace.start(lambda c: 3 if c is client else None, tmp_path)
    with run_trace.span("deck.match_check"):
        await namespace["open_page"](client)
    run_trace.stop()

    records = _records(path)
    (press,) = [r for r in records if r["kind"] == "key"]
    assert sent == [key]
    assert press["key"] == "P"
    assert press["slot"] == 3
    assert press["caller"] == "combat.example.open_page:2"
    assert press["span"] == _spans(records)["deck.match_check"]["id"]


async def test_through_an_extension_both_ends_of_its_part_are_named(dev, tmp_path, stopped, monkeypatch):
    async def timed_send_key(hwnd, key, seconds):
        pass

    monkeypatch.setattr("wizwalker.client.utils.timed_send_key", timed_send_key)
    client = object.__new__(Client)
    client.window_handle = 1
    # bartlebot -> an extension's entry point -> the extension function that presses.
    inner = {"__name__": "wizwalker.extensions.wizsprinter.inner"}
    exec("async def press(client):\n    await client.send_key('P')\n", inner)
    outer = {"__name__": "wizwalker.extensions.wizsprinter.outer", "press": inner["press"]}
    exec("async def handle_round(client):\n    await press(client)\n", outer)
    ours = {"__name__": "bartlebot.nodes.example", "handle_round": outer["handle_round"]}
    exec("async def fight(client):\n    await handle_round(client)\n", ours)

    path = run_trace.start(lambda c: 1, tmp_path)
    await ours["fight"](client)
    run_trace.stop()

    (press,) = [r for r in _records(path) if r["kind"] == "key"]
    assert press["caller"] == "nodes.example.fight:2"
    assert press["via"] == "wizsprinter.outer.handle_round:2 > wizsprinter.inner.press:2"


async def test_a_click_on_a_window_is_one_click_named_by_the_window(dev, tmp_path, stopped, monkeypatch):
    clicks = []

    async def click(self, x, y, **kwargs):
        clicks.append((x, y))

    monkeypatch.setattr(MouseHandler, "click", click)
    window = SimpleNamespace(
        scale_to_client=lambda: _resolved(SimpleNamespace(center=lambda: (10, 20))),
        name=lambda: _resolved("btnInvite"),
    )
    handler = object.__new__(MouseHandler)
    handler.client = object()

    path = run_trace.start(lambda c: 2, tmp_path)
    await handler.click_window(window)
    await handler.click(5, 6)
    run_trace.stop()

    events = [r for r in _records(path) if r["kind"] == "click"]
    assert clicks == [(10, 20), (5, 6)]
    assert [(e.get("window"), e.get("at")) for e in events] == [("btnInvite", None), (None, [5, 6])]


async def _resolved(value):
    return value


class _FakeWindow(Window):
    def __init__(self, name: str, children=()):
        self._name = name
        self._children = list(children)
        self.hook_handler = SimpleNamespace(client=None)

    async def name(self):
        return self._name

    async def children(self):
        return self._children


async def test_a_search_of_the_ui_tree_is_one_walk(dev, tmp_path, stopped):
    root = _FakeWindow("root", [_FakeWindow("a", [_FakeWindow("Hand")]), _FakeWindow("Hand")])
    path = run_trace.start(lambda c: None, tmp_path)
    found = await root.get_windows_with_name("Hand")
    run_trace.stop()

    walks = [r for r in _records(path) if r["kind"] == "walk"]
    assert len(found) == 2
    assert [(w["name"], w["found"]) for w in walks] == [("Hand", 2)]


async def test_a_blocked_event_loop_is_a_stall(dev, tmp_path, stopped):
    path = run_trace.start(lambda c: None, tmp_path)
    await asyncio.sleep(run_trace.HEARTBEAT_INTERVAL * 2)
    time.sleep(run_trace.STALL_THRESHOLD * 3)  # the stall: nothing else runs
    await asyncio.sleep(run_trace.HEARTBEAT_INTERVAL * 2)
    run_trace.stop()

    stalls = [r for r in _records(path) if r["kind"] == "stall"]
    assert any(s["dur"] >= run_trace.STALL_THRESHOLD * 2 for s in stalls)


async def test_a_span_open_when_the_trace_stops_is_not_written(dev, tmp_path, stopped):
    path = run_trace.start(lambda c: None, tmp_path)
    with run_trace.span("cut short"):
        run_trace.stop()
    assert "cut short" not in _spans(_records(path))


async def test_old_traces_are_pruned(dev, tmp_path, stopped):
    for i in range(run_trace.KEEP_TRACES + 3):
        (tmp_path / f"run_old_{i:02}.jsonl").write_text("", encoding="utf-8")
    run_trace.start(lambda c: None, tmp_path)
    run_trace.stop()
    assert len(list(tmp_path.glob("run_*.jsonl"))) == run_trace.KEEP_TRACES
