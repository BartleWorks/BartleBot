"""Every surface #316 lists names a page, and each dialog with one wears the ? (epic #25, Stage 5)."""

from __future__ import annotations

import ast
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest

from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.gui import context_help
from bartlebot.gui.canvas.node_browser_dialog import NodeBrowserDialog
from bartlebot.gui.context_help import ContextHelp, help_topic, help_url_for
from bartlebot.gui.settings.settings_dialog import SettingsDialog
from bartlebot.packaging.help_topics import HELP_TOPICS, help_url, node_help_url
from bartlebot.paths import project_root

HINT = Qt.WindowType.WindowContextHelpButtonHint

#: Each surface the issue lists that names its page in its constructor: module, class, topic.
SURFACES = [
    ("gui/canvas/node_browser_dialog.py", "NodeBrowserDialog", "node_browser"),
    ("gui/combat_strategies/dialog.py", "CombatStrategyDialog", "combat_strategies"),
    ("gui/dialogs/templates_dialog.py", "TemplatesDialog", "templates"),
    ("gui/dialogs/new_bot_dialog.py", "NewBotDialog", "new_bot"),
    ("gui/dialogs/import_dialog.py", "ImportDialog", "import"),
    ("gui/dialogs/export_dialog.py", "ExportDialog", "export"),
    ("gui/dialogs/safety_warning_dialog.py", "SafetyWarningDialog", "safety_check"),
    ("gui/dialogs/entity_browser.py", "EntityBrowserDialog", "entity_browser"),
    ("gui/dialogs/ui_tree_browser.py", "UITreeBrowserDialog", "ui_tree_browser"),
    ("gui/sidebar/sections/option_values_section.py", "OptionValuesSection", "options"),
    ("gui/sidebar/sections/options_section.py", "OptionsSection", "options_edit"),
    ("gui/sidebar/sections/options_section.py", "_AddOptionDialog", "options_edit"),
    ("gui/sidebar/sections/variables_section.py", "VariablesSection", "variables"),
    ("gui/sidebar/sections/variables_section.py", "VariableDialog", "variables"),
]


def _topics_set_in(path: str, name: str) -> list[tuple[str, str]]:
    """``(target, topic)`` for each ``set_help_topic`` call in *name*'s body (a class or a method)."""
    tree = ast.parse((project_root() / "bartlebot" / path).read_text(encoding="utf-8"))
    body = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef | ast.FunctionDef) and n.name == name)
    return [
        (ast.unparse(call.args[0]), call.args[1].value)
        for call in ast.walk(body)
        if isinstance(call, ast.Call)
        and getattr(call.func, "id", None) == "set_help_topic"
        and isinstance(call.args[1], ast.Constant)
    ]


@pytest.mark.parametrize(("path", "name", "topic"), SURFACES, ids=[s[1] for s in SURFACES])
def test_every_listed_surface_names_its_page(path, name, topic):
    assert _topics_set_in(path, name) == [("self", topic)]
    assert topic in HELP_TOPICS


def test_setup_names_its_page_when_it_is_opened():
    assert ("dialog", "setup") in _topics_set_in("app_controller.py", "_open_setup")


# ---------------------------------------------------------------------------
# The dialogs as built
# ---------------------------------------------------------------------------


def _without_its_load(module, factory):
    """Build a browser dialog without the game fetch its ``__init__`` schedules (see test_inspector_button_weights)."""
    with patch.object(module.asyncio, "ensure_future", lambda coro: coro.close()):
        return factory()


def _built_dialogs():
    from bartlebot.gui.dialogs import entity_browser, ui_tree_browser  # noqa: PLC0415
    from bartlebot.gui.dialogs.safety_warning_dialog import SafetyWarningDialog  # noqa: PLC0415
    from bartlebot.gui.sidebar.sections.options_section import _AddOptionDialog  # noqa: PLC0415
    from bartlebot.gui.sidebar.sections.variables_section import VariableDialog  # noqa: PLC0415

    bot = BotConfig(
        id="bot",
        name="Bot",
        initial="a",
        recovery_state="a",
        states=[StateModel(id="a", node_type="sleep", position={"x": 0.0, "y": 0.0})],
        transitions=[],
    )
    return {
        "node_browser": NodeBrowserDialog(mode="view"),
        "settings_general": SettingsDialog(),
        "safety_check": SafetyWarningDialog([]),
        "entity_browser": _without_its_load(entity_browser, lambda: entity_browser.EntityBrowserDialog(MagicMock())),
        "ui_tree_browser": _without_its_load(ui_tree_browser, lambda: ui_tree_browser.UITreeBrowserDialog(MagicMock())),
        "options_edit": _AddOptionDialog([]),
        "variables": VariableDialog(bot),
    }


def test_each_dialog_as_built_names_its_page_and_wears_the_question_mark(qapp):
    for topic, dialog in _built_dialogs().items():
        assert help_topic(dialog) == topic, type(dialog).__name__
        assert dialog.windowFlags() & HINT, type(dialog).__name__
        dialog.deleteLater()


@pytest.fixture
def opened(monkeypatch) -> list[str]:
    urls: list[str] = []
    monkeypatch.setattr(context_help, "open_url", urls.append)
    return urls


def test_f1_in_the_node_browser_opens_the_shown_nodes_reference_entry(qtbot, opened):
    owner = NodeBrowserDialog(mode="view", initial_node_type="collect_wisps")
    qtbot.addWidget(owner)
    ContextHelp(owner)

    QTest.keyClick(owner._list, Qt.Key.Key_F1)

    assert opened == [node_help_url("collect_wisps")]
    assert context_help.help_from_title_bar(owner) == node_help_url("collect_wisps")


def test_the_node_browser_with_no_node_shown_opens_adding_a_node(qtbot):
    dialog = NodeBrowserDialog(mode="view", allowed_node_types=set())
    qtbot.addWidget(dialog)

    assert help_url_for(dialog) == help_url("node_browser")


def test_each_settings_tab_names_its_page_and_the_dialog_follows_the_current_tab(qtbot):
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    tabs = dialog.tabs

    topics = [help_topic(tabs.widget(i)) for i in range(tabs.count())]
    assert topics == [
        f"settings_{name}" for name in ("general", "bot", "combat", "canvas", "authoring", "theme", "game")
    ]
    assert set(topics) <= set(HELP_TOPICS)
    for index, topic in enumerate(topics):
        tabs.setCurrentIndex(index)
        assert help_url_for(tabs.tabBar()) == help_url(topic)
        assert context_help.help_from_title_bar(dialog) == help_url(topic)
