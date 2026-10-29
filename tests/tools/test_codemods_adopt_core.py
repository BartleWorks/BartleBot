"""Tests for tools/codemods/adopt_core.py: every reference to a moved module points at core."""

import sys

from bartlebot.paths import project_root

sys.path.insert(0, str(project_root()))

from tools.codemods.adopt_core import moved, rewrite  # noqa: E402


def test_a_moved_module_and_its_package_s_submodules_map_to_core():
    assert moved("bartlebot.clients.game_keys") == "bartleby.runtime.clients.game_keys"
    assert moved("bartlebot.shared.account_store") == "bartleby.shared.account_store"
    assert moved("bartlebot.gui.theming.theme") == "bartleby.qt.theming.theme"
    assert moved("bartlebot.nodes.timing") == "bartleby.runtime.timing"


def test_a_module_bartlebot_keeps_is_not_moved():
    assert moved("bartlebot.clients.client_manager") is None
    assert moved("bartlebot.nodes.node_helpers") is None
    assert moved("bartlebot.paths") is None
    assert moved("bartlebot.gui.widgets.buttons.record_button") is None


def test_a_from_line_naming_a_moved_module_is_rewritten_with_its_comment():
    source = "    from bartlebot.gui.theming.theme import THEME  # noqa: PLC0415\n"

    assert rewrite(source) == "    from bartleby.qt.theming.theme import THEME  # noqa: PLC0415\n"


def test_a_from_line_importing_a_moved_module_beside_one_that_stays_is_split():
    source = "from bartlebot.clients import (\n    client_manager,\n    game_keys as keys,\n)\n"

    assert rewrite(source) == (
        "from bartlebot.clients import client_manager\nfrom bartleby.runtime.clients import game_keys as keys\n"
    )


def test_a_name_that_left_a_module_bartlebot_keeps_is_imported_from_core():
    source = "from bartlebot.settings_manager import VALID_RESOLUTIONS, SettingsManager\n"

    assert rewrite(source) == (
        "from bartleby.runtime.game.game_sizing import VALID_RESOLUTIONS\n"
        "from bartlebot.settings_manager import SettingsManager\n"
    )


def test_a_dotted_path_in_a_string_or_a_docstring_is_rewritten():
    source = 'monkeypatch.setattr("bartlebot.launcher.launch_profile.launch", fake)\n"""See bartlebot.bot.run_trace."""\n'

    assert rewrite(source) == (
        'monkeypatch.setattr("bartleby.runtime.launcher.launch_profile.launch", fake)\n'
        '"""See bartleby.runtime.run_trace."""\n'
    )


def test_a_slashed_path_in_a_comment_is_rewritten():
    assert rewrite("# see bartlebot/gui/streamer_mode.py\n") == "# see bartleby/qt/streamer_mode.py\n"


def test_a_longer_name_that_starts_like_a_moved_module_is_left_alone():
    source = "from bartlebot.nodes.timing_report import x\nimport bartlebot.paths\n"

    assert rewrite(source) == source


def test_line_endings_are_kept():
    source = "from bartlebot.shared import vendor\r\nx = 1\r\n"

    assert rewrite(source) == "from bartleby.shared import vendor\r\nx = 1\r\n"
