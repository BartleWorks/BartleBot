"""The #120 window tracer: what counts as a stray window, and whose windows are logged."""

import logging
import os

from PyQt6.QtWidgets import QDialog, QMainWindow, QMenu, QWidget

from bartlebot.gui.window_show_trace import WindowShowTracer, is_stray_window, should_report


def test_a_parentless_plain_widget_is_stray(qapp):
    assert is_stray_window(QWidget())


def test_a_parented_widget_is_not(qapp):
    parent = QWidget()
    assert not is_stray_window(QWidget(parent))


def test_windows_that_are_meant_to_be_windows_are_not(qapp):
    """A dialog, a menu and the main window are top-levels on purpose."""
    assert not is_stray_window(QDialog())
    assert not is_stray_window(QMenu())
    assert not is_stray_window(QMainWindow())


def test_only_our_own_process_and_the_game_are_reported():
    assert should_report("python.exe", 10, own_pid=10)
    assert should_report("WizardGraphicalClient.exe", 20, own_pid=10)
    assert should_report("wizardgraphicalclient.exe", 20, own_pid=10)
    assert not should_report("explorer.exe", 30, own_pid=10)


def test_showing_a_parentless_widget_logs_a_warning_with_its_stack(qapp, caplog):
    tracer = WindowShowTracer(qapp)
    try:
        with caplog.at_level(logging.WARNING, logger="bartlebot.gui.window_show_trace"):
            stray = QWidget()
            stray.show()
            stray.hide()
    finally:
        tracer.close()

    [record] = [r for r in caplog.records if "Stray Qt window" in r.getMessage()]
    assert "QWidget" in record.getMessage()
    assert "test_showing_a_parentless_widget" in record.getMessage()  # the stack names the caller


def test_a_dialog_shown_is_not_logged(qapp, caplog):
    tracer = WindowShowTracer(qapp)
    try:
        with caplog.at_level(logging.WARNING, logger="bartlebot.gui.window_show_trace"):
            dialog = QDialog()
            dialog.show()
            dialog.hide()
    finally:
        tracer.close()

    assert not [r for r in caplog.records if "Stray Qt window" in r.getMessage()]


def test_the_process_name_lookup_finds_this_process(qapp):
    tracer = WindowShowTracer(qapp)
    try:
        assert tracer.exe_name(os.getpid()).lower().startswith("python")
    finally:
        tracer.close()
