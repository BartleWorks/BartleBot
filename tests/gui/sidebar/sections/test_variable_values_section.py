"""The Variables section on the Run-mode Options tab: values as the run has them (#188).

The marking rule itself is tested without Qt in test_variable_watch.py; this covers what
the section draws, how it follows the sidebar, and a real run feeding it.
"""

from __future__ import annotations

import asyncio

from bartleby.qt.theming.theme import THEME
from PyQt6.QtWidgets import QWidget

from bartlebot.bot.machine import BotMachine
from bartlebot.bot.schema import BotConfig, FlagCondition, StateModel, TransitionModel, VariableModel
from bartlebot.gui.sidebar.left_sidebar import LeftSidebar
from bartlebot.gui.sidebar.sections.variable_values_section import VariableValuesSection
from bartlebot.nodes.registry import NODE_REGISTRY, NODE_SPECS


def _bot(variables=None) -> BotConfig:
    """count adds 1 to swords, which (a branch) goes to rack while swords < 3, and rack back to count."""
    return BotConfig(
        id="bot-vv",
        name="Variable values",
        initial="count",
        recovery_state="count",
        variables=variables
        if variables is not None
        else [
            VariableModel(name="swords", type="int"),
            VariableModel(name="found", type="bool", initial=True, description="Whether a sword turned up."),
            VariableModel(name="stage", type="enum", values=["Sun", "Moon"]),
        ],
        states=[
            StateModel(
                id="count",
                node_type="set_variables",
                label="Count",
                params={"assignments": [{"variable": "swords", "op": "add", "value": 1}]},
            ),
            StateModel(id="which", node_type="branch", label="Which"),
            StateModel(id="rack", node_type="work", label="Rack"),
            StateModel(id="end", node_type="done", label="End"),
        ],
        transitions=[
            TransitionModel(trigger="advance", source="count", dest="which", when="SUCCESS"),
            TransitionModel(
                trigger="advance",
                source="which",
                dest="rack",
                when="SUCCESS",
                flags=[FlagCondition(variable="swords", compare="<", value=3)],
            ),
            TransitionModel(trigger="advance", source="which", dest="end", when="SUCCESS"),
            TransitionModel(trigger="advance", source="rack", dest="count", when="SUCCESS"),
        ],
    )


def _section(config: BotConfig | None = None) -> tuple[VariableValuesSection, QWidget]:
    host = QWidget()
    section = VariableValuesSection(parent=host)
    section.set_config(config if config is not None else _bot())
    return section, host


# ---------------------------------------------------------------------------
# Before a run
# ---------------------------------------------------------------------------


def test_before_a_run_each_row_shows_where_its_variable_starts(qapp, parentless_shows):
    section, _host = _section()
    assert section.row_names == ["swords", "found", "stage"]
    assert [section.value_text(n) for n in section.row_names] == ["0", "true", "Sun"]
    assert not any(section.is_marked(n) for n in section.row_names)
    assert section.value_label("swords").toolTip() == "Starts each run at 0"
    assert parentless_shows == []


def test_a_bot_without_variables_hides_the_section(qapp):
    section, _host = _section(_bot(variables=[]))
    assert section.row_names == []
    assert section.isHidden()
    section.set_config(_bot())
    assert not section.isHidden()


# ---------------------------------------------------------------------------
# During a run
# ---------------------------------------------------------------------------


def test_a_write_shows_at_once_drawn_in_the_accent_with_what_it_was(qapp):
    section, _host = _section()
    section.run_started()
    section.on_variable_change("swords", 0, 1)
    label = section.value_label("swords")
    assert section.value_text("swords") == "1"
    assert section.is_marked("swords")
    assert THEME.accent in label.styleSheet()
    assert label.font().bold()
    assert label.toolTip() == "Just changed: it was 0"
    assert THEME.accent not in section.value_label("found").styleSheet()


def test_the_mark_goes_once_the_game_node_after_the_write_has_run(qapp):
    section, _host = _section()
    section.run_started()
    section.on_variable_change("swords", 0, 1)
    section.on_node_started("branch")
    section.on_node_started("interact")
    assert section.is_marked("swords")
    section.on_node_started("interact")
    label = section.value_label("swords")
    assert not section.is_marked("swords")
    assert section.value_text("swords") == "1"
    assert THEME.accent not in label.styleSheet() and not label.font().bold()
    assert label.toolTip() == "Starts each run at 0"


def test_a_run_starts_every_value_again(qapp):
    section, _host = _section()
    section.run_started()
    section.on_variable_change("stage", "Sun", "Moon")
    section.run_finished()
    assert section.value_text("stage") == "Moon", "after the run its values stay to be read"
    section.run_started()
    assert section.value_text("stage") == "Sun"
    assert not section.is_marked("stage")


def test_an_equal_config_swapped_in_during_a_run_keeps_the_values(qapp):
    """Undo snapshots and the pre-run recompose hand over a new object with the same declarations."""
    section, _host = _section()
    section.run_started()
    section.on_variable_change("swords", 0, 2)
    section.set_config(_bot())
    assert section.value_text("swords") == "2"
    assert section.is_marked("swords")


def test_another_bot_after_a_run_starts_from_its_own_values(qapp):
    section, _host = _section()
    section.run_started()
    section.on_variable_change("swords", 0, 2)
    section.run_finished()
    section.set_config(_bot())
    assert section.value_text("swords") == "0"


def test_changed_declarations_rebuild_the_rows(qapp):
    section, _host = _section()
    section.set_config(_bot(variables=[VariableModel(name="laps", type="int", initial=4)]))
    assert section.row_names == ["laps"]
    assert section.value_text("laps") == "4"


# ---------------------------------------------------------------------------
# On the tab
# ---------------------------------------------------------------------------


def test_it_sits_under_option_values_on_the_run_mode_tab_and_follows_the_run(qapp, parentless_shows):
    sidebar = LeftSidebar(QWidget())
    sidebar.set_config(_bot())
    sidebar.set_editable(False)
    page = sidebar.widget(0)
    assert page.isAncestorOf(sidebar.option_values)
    assert page.isAncestorOf(sidebar.variable_values)
    assert sidebar.variable_values.row_names == ["swords", "found", "stage"]

    sidebar.set_bot_running(True)
    sidebar.variable_values.on_variable_change("swords", 0, 1)
    sidebar.set_bot_running(False)
    assert sidebar.variable_values.value_text("swords") == "1"
    sidebar.set_bot_running(True)
    assert sidebar.variable_values.value_text("swords") == "0"

    sidebar.set_editable(True)
    assert not sidebar.widget(0).isAncestorOf(sidebar.variable_values), "Edit mode shows the declarations instead"
    assert parentless_shows == []


# ---------------------------------------------------------------------------
# A real run feeding it, wired as AppController wires it
# ---------------------------------------------------------------------------


async def test_every_write_of_a_run_shows_while_it_is_live(qapp):
    section, _host = _section()
    seen: list[tuple[str, str | None, bool]] = []
    machine_ref: list[BotMachine] = []

    async def work(machine, clients, params):
        # A game node: the value its write led to is on screen, and marked, while it runs.
        seen.append(("rack", section.value_text("swords"), section.is_marked("swords")))
        await asyncio.sleep(0)
        return "SUCCESS"

    async def done(machine, clients, params):
        machine.stop()
        return "SUCCESS"

    def on_state(state_id):
        section.on_node_started(machine_ref[0].node_type_of(state_id))

    bot = _bot()
    registry = {**NODE_REGISTRY, "work": work, "done": done}
    machine = BotMachine(
        clients=[],
        config=bot.to_machine_config(node_specs=NODE_SPECS),
        node_registry=registry,
        bot_config=bot,
        on_state_change=on_state,
        on_variable_change=section.on_variable_change,
        stop_grace_period=0.1,
    )
    machine_ref.append(machine)
    section.run_started()
    await asyncio.wait_for(machine.run(), 2.0)

    assert seen == [("rack", "1", True), ("rack", "2", True)]
    assert section.value_text("swords") == "3"
    assert dict(machine.variables)["swords"] == 3


async def test_a_section_that_raises_never_stops_the_run(qapp, monkeypatch, caplog):
    section, _host = _section()

    def boom(*_):
        raise RuntimeError("section gone")

    monkeypatch.setattr(section, "on_variable_change", boom)

    async def done(machine, clients, params):
        machine.stop()
        return "SUCCESS"

    async def work(machine, clients, params):
        return "SUCCESS"

    bot = _bot()
    machine = BotMachine(
        clients=[],
        config=bot.to_machine_config(node_specs=NODE_SPECS),
        node_registry={**NODE_REGISTRY, "work": work, "done": done},
        bot_config=bot,
        on_variable_change=lambda *a: section.on_variable_change(*a),
        stop_grace_period=0.1,
    )
    await asyncio.wait_for(machine.run(), 2.0)
    assert machine.state == "end"
    assert machine.variables["swords"] == 3
