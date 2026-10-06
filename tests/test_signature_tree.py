"""The signature page's inputs tree: its rows, what a drop may do, and what Alt+Up/Down does."""

from __future__ import annotations

from bartlebot import signature_tree as st
from bartlebot.bot.schema import SubgraphInput, SubgraphPanel
from bartlebot.signature_tree import GROUP, INPUT, NODE, TreeRow


class _Meta:
    def __init__(self, inputs, panels):
        self.inputs = inputs
        self.panels = panels


def _meta() -> _Meta:
    """Two ungrouped inputs, a promoted node, a three-input group, a one-input group, a promoted node."""

    def inp(name, panel=None):
        return SubgraphInput(name=name, type="ratio", panel=panel)

    return _Meta(
        [
            inp("heal"),
            inp("mana"),
            inp("safe", "wisps"),
            inp("hw", "wisps"),
            inp("mw", "wisps"),
            inp("give_up", "limits"),
        ],
        [
            SubgraphPanel(id="enter", kind="node", member="state_2"),
            SubgraphPanel(id="wisps", label="Wisps"),
            SubgraphPanel(id="limits", label="Limits"),
            SubgraphPanel(id="sync", kind="node", member="state_3"),
        ],
    )


def _keys(rows):
    return [("  " if r.member else "") + r.key for r in rows]


def test_rows_list_the_ungrouped_inputs_then_each_group_with_its_members():
    m = _meta()
    assert _keys(st.tree_rows(m.inputs, m.panels)) == [
        "heal",
        "mana",
        "enter",
        "wisps",
        "  safe",
        "  hw",
        "  mw",
        "limits",
        "  give_up",
        "sync",
    ]


def test_an_input_naming_a_missing_group_is_drawn_ungrouped():
    m = _meta()
    m.inputs[0].panel = "gone"
    rows = st.tree_rows(m.inputs, m.panels)
    assert rows[0] == TreeRow(INPUT, "heal")


def test_a_group_carries_its_members_and_a_node_carries_nothing():
    m = _meta()
    rows = st.tree_rows(m.inputs, m.panels)
    assert st.carried(rows, 3) == 3  # wisps
    assert st.carried(rows, 7) == 1  # limits
    assert st.carried(rows, 2) == 0  # enter, a promoted node
    assert st.carried(rows, 0) == 0  # an input


def test_an_input_may_move_into_a_group():
    m = _meta()
    rows = st.tree_rows(m.inputs, m.panels)
    mana = rows[1]
    after = [rows[0], rows[2], rows[3], rows[4], mana, *rows[5:]]
    assert st.allowed(rows, after, [mana])


def test_an_input_may_not_land_under_a_promoted_node():
    m = _meta()
    rows = st.tree_rows(m.inputs, m.panels)
    heal = rows[0]
    after = [rows[1], rows[2], heal, *rows[3:]]
    assert not st.allowed(rows, after, [heal])


def test_a_group_may_not_split_another_group():
    m = _meta()
    rows = st.tree_rows(m.inputs, m.panels)
    limits = rows[7:9]
    # Limits dropped between Wisps' first and second inputs would take hw and mw with it.
    after = rows[:5] + limits + rows[5:7] + rows[9:]
    assert not st.allowed(rows, after, limits)


def test_a_group_may_not_land_above_the_ungrouped_inputs():
    m = _meta()
    rows = st.tree_rows(m.inputs, m.panels)
    wisps = rows[3:7]
    after = wisps + rows[:3] + rows[7:]
    assert not st.allowed(rows, after, wisps)


def test_alt_up_on_a_groups_first_input_steps_out_past_a_promoted_node():
    m = _meta()
    rows = st.tree_rows(m.inputs, m.panels)
    after = st.step(rows, 4, -1)  # safe, the first of Wisps
    assert _keys(after)[:5] == ["heal", "mana", "safe", "enter", "wisps"]
    assert after[2].member is False


def test_alt_down_on_the_last_ungrouped_input_steps_into_the_first_input_group():
    m = _meta()
    rows = st.tree_rows(m.inputs, m.panels)
    after = st.step(rows, 1, 1)  # mana; Enter dungeon is a node, so it lands at the top of Wisps
    assert _keys(after)[:5] == ["heal", "enter", "wisps", "  mana", "  safe"]


def test_alt_up_on_an_input_swaps_it_with_the_one_above():
    m = _meta()
    rows = st.tree_rows(m.inputs, m.panels)
    assert _keys(st.step(rows, 5, -1))[3:7] == ["wisps", "  hw", "  safe", "  mw"]


def test_alt_down_on_a_group_steps_over_the_whole_next_group():
    m = _meta()
    rows = st.tree_rows(m.inputs, m.panels)
    after = st.step(rows, 3, 1)  # wisps
    assert _keys(after) == ["heal", "mana", "enter", "limits", "  give_up", "wisps", "  safe", "  hw", "  mw", "sync"]


def test_alt_up_on_the_first_row_goes_nowhere():
    m = _meta()
    rows = st.tree_rows(m.inputs, m.panels)
    assert st.step(rows, 0, -1) is None
    assert st.step(rows, 2, -1) is None  # the first heading cannot go above the ungrouped inputs


def test_alt_down_on_the_last_input_goes_nowhere_when_only_a_node_is_below():
    m = _meta()
    rows = st.tree_rows(m.inputs, m.panels)
    assert st.step(rows, 8, 1) is None  # give_up: below it is Sync in zone, and then nothing


def test_move_to_group_puts_the_input_last_in_that_group():
    m = _meta()
    rows = st.tree_rows(m.inputs, m.panels)
    assert _keys(st.move_to_group(rows, "heal", "limits"))[-3:] == ["  give_up", "  heal", "sync"]
    assert _keys(st.move_to_group(rows, "hw", None))[:3] == ["heal", "mana", "hw"]


def test_reorder_writes_the_order_and_the_membership_back():
    m = _meta()
    rows = st.step(st.tree_rows(m.inputs, m.panels), 3, 1)  # Wisps below Limits
    rows = st.move_to_group(rows, "heal", "wisps")
    st.reorder(m, rows)
    assert [p.id for p in m.panels] == ["enter", "limits", "wisps", "sync"]
    assert [(i.name, i.panel) for i in m.inputs] == [
        ("mana", None),
        ("give_up", "limits"),
        ("safe", "wisps"),
        ("hw", "wisps"),
        ("mw", "wisps"),
        ("heal", "wisps"),
    ]
    # And the tree drawn from what was written is the tree that was asked for.
    assert st.tree_rows(m.inputs, m.panels) == rows


def test_an_empty_group_takes_an_input_dropped_under_it():
    m = _meta()
    m.panels.insert(1, SubgraphPanel(id="empty", label="Empty"))
    rows = st.tree_rows(m.inputs, m.panels)
    assert [r.kind for r in rows[2:5]] == [NODE, GROUP, GROUP]
    after = st.step(rows, 1, 1)  # mana skips the node and lands in the empty group
    assert st.groups_of(after)["mana"] == TreeRow(GROUP, "empty")
