"""Tests for tools/run_trace_report.py: reading a run trace back into where the time went."""

import json
import sys

import pytest

from bartlebot.paths import project_root

sys.path.insert(0, str(project_root()))

from tools import run_trace_report as report  # noqa: E402


def _span(id, name, t, dur, *, parent=None, slot=None, reads=0, **attrs):
    record = {
        "kind": "span",
        "id": id,
        "parent": parent,
        "name": name,
        "slot": slot,
        "t": t,
        "dur": dur,
        "reads": reads,
        "read_s": reads * 0.0001,
    }
    if attrs:
        record["attrs"] = attrs
    return record


def _key(t, slot, key, caller, span=None, via=None):
    return {"kind": "key", "t": t, "dur": 0.01, "slot": slot, "span": span, "key": key, "caller": caller, "via": via}


@pytest.fixture
def trace(tmp_path):
    records = [
        {"kind": "start", "wall": "2026-10-23T09:00:00.000"},
        # The pre-run: the deck page opened for the check, then again for the write.
        _span(1, "pre_run", 0.0, 6.0),
        _span(2, "pre_run.decks", 0.0, 4.0, parent=1),
        _span(3, "deck.apply", 0.0, 4.0, parent=2, slot=2),
        # The tracer writes a child with its parent's player, as these are.
        _span(4, "deck.match_check", 0.1, 0.5, parent=3, slot=2),
        _span(5, "deck.write", 0.7, 3.2, parent=3, slot=2),
        _key(0.2, 2, "P", "combat.deck_io.deck_page_open:1926", span=4),
        _key(0.5, 2, "P", "combat.deck_io.deck_page_open:1934", span=4),
        _key(
            0.8, 2, "P", "combat.deck_io._open_and_write:2212", span=5, via="scripting.deck_builder.open_deck_page:40"
        ),
        # A fight under a node: two players, two rounds. P2 commits last in round 1.
        _span(10, "node.handle_combat", 10.0, 30.0, state="state_10"),
        _span(11, "combat.fight", 11.0, 20.0, parent=10, slot=1),
        _span(12, "combat.round", 12.0, 1.0, parent=11, slot=1, round=1),
        _span(13, "combat.round", 20.0, 0.5, parent=11, slot=1, round=2),
        _span(21, "combat.fight", 11.0, 20.0, parent=10, slot=2),
        _span(22, "combat.round", 12.2, 3.0, parent=21, slot=2, round=1),
        _span(23, "combat.priority", 12.3, 2.5, parent=22, slot=2, n=1, result="True"),
        _span(24, "combat.cast", 12.4, 0.6, parent=23, slot=2),
        _span(25, "combat.round", 20.1, 0.6, parent=21, slot=2, round=2),
        {
            "kind": "walk",
            "t": 12.35,
            "dur": 0.2,
            "slot": 2,
            "span": 23,
            "name": "CountText",
            "caller": "nodes.combat_runner.try_execute_config:740",
            "via": None,
            "found": 1,
        },
        {"kind": "stall", "t": 12.5, "dur": 0.3},
        {"kind": "tick", "t": 1.0, "reads": 40, "read_s": 0.004},
        {"kind": "tick", "t": 2.0, "reads": 60, "read_s": 0.006},
    ]
    path = tmp_path / "run_test.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return report.load(path)


def test_the_pre_run_lists_every_input_in_order_with_its_caller(trace):
    text = "\n".join(report.pre_run(trace))
    assert text.index("deck.match_check") < text.index("deck.write")
    lines = [line for line in text.splitlines() if "key P" in line]
    assert len(lines) == 3 + 3  # in order, then counted by caller
    assert "via scripting.deck_builder.open_deck_page:40" in lines[2]


def test_a_round_is_split_into_ours_and_the_games(trace):
    first, second = report.rounds(trace)
    assert (first.number, second.number) == (1, 2)
    assert first.last == 2
    # From P1 starting at 12.0 to P2 committing at 15.2.
    assert first.ours == pytest.approx(3.2)
    # From that commit to round 2's first start, at 20.0.
    assert first.game == pytest.approx(4.8)
    # The last round's game time runs to the end of the fight.
    assert second.game == pytest.approx(31.0 - 20.7)


def test_the_slowest_round_breaks_a_priority_into_its_casts_and_the_rest(trace):
    text = "\n".join(report.slowest_rounds(trace, 1))
    assert "round 1" in text and "last to commit P2" in text
    (row,) = [line for line in text.splitlines() if line.strip().startswith("#1")]
    assert row.split("casts")[1].split()[:2] == ["600", "ms"]
    assert row.split("rest")[1].split()[:2] == ["1.90", "s"]


def test_walks_are_grouped_by_what_was_looked_for_and_who_looked(trace):
    (header, row) = report.walks(trace)
    assert "name=CountText" in row and "try_execute_config" in row


def test_a_stall_names_the_innermost_spans_holding_the_loop(trace):
    text = "\n".join(report.stalls(trace))
    assert "combat.cast P2" in text
    assert "combat.round P1" in text
    assert "combat.fight" not in text


def test_nodes_total_their_reads_and_inputs(trace):
    (header, row) = report.nodes(trace)
    assert row.split()[:2] == ["handle_combat", "1"]


def test_the_whole_report_renders(trace):
    text = report.report(trace)
    for title in ("Summary", "Pre-run", "Nodes", "Combat", "slowest rounds", "UI-tree walks", "Stalls", "Background"):
        assert f"== {title}" in text or title in text
    assert "100 reads outside any span" in text
