"""The Log tab's minimum-level filter applies to lines already shown (#259)."""

import logging

from bartlebot.gui.sidebar.log_panel import LogPanel


def _shown(panel: LogPanel) -> list[str]:
    return panel._log.toPlainText().splitlines()


def _fill(panel: LogPanel) -> None:
    panel.append_log_formatted("debug line", logging.DEBUG)
    panel.append_log_formatted("info line", logging.INFO)
    panel.append_log_formatted("warn line", logging.WARNING)
    panel.append_log_formatted("error line", logging.ERROR)


def test_defaults_to_warning(qapp):
    panel = LogPanel()
    _fill(panel)
    assert _shown(panel) == ["warn line", "error line"]


def test_widening_the_filter_brings_back_hidden_lines(qapp):
    panel = LogPanel()
    _fill(panel)
    panel._log_level_combo.setCurrentText("DEBUG")
    assert _shown(panel) == ["debug line", "info line", "warn line", "error line"]


def test_narrowing_the_filter_hides_shown_lines(qapp):
    panel = LogPanel()
    panel._log_level_combo.setCurrentText("DEBUG")
    _fill(panel)
    panel._log_level_combo.setCurrentText("ERROR")
    assert _shown(panel) == ["error line"]


def test_clear_drops_filtered_lines_too(qapp):
    panel = LogPanel()
    _fill(panel)
    panel.clear()
    panel._log_level_combo.setCurrentText("DEBUG")
    assert _shown(panel) == []
