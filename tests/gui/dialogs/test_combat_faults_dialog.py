"""The pre-run dialog for combat faults: Go back is its default, and only Run anyway runs."""

from PyQt6.QtWidgets import QMessageBox

from bartlebot.gui.dialogs import combat_faults_dialog
from bartlebot.gui.dialogs.combat_faults_dialog import GO_BACK, RUN_ANYWAY, TITLE, confirm_run, fault_box, fault_text

_FAULTS = ["Combat DSL in 'Boss' (P1) does not parse: Unexpected token"]


def test_go_back_is_the_default_and_the_escape(qapp):
    box, run_anyway = fault_box(None, _FAULTS)

    assert box.windowTitle() == TITLE
    assert box.defaultButton().text() == GO_BACK
    assert box.escapeButton() is box.defaultButton()
    assert run_anyway.text() == RUN_ANYWAY


def test_the_text_lists_every_fault():
    text = fault_text(["one", "two"])
    assert "• one\n• two" in text


def _answer(monkeypatch, label: str | None):
    """Make exec() press the button labeled *label*, or the default one for None."""

    def exec_(box: QMessageBox) -> int:
        target = box.defaultButton() if label is None else next(b for b in box.buttons() if b.text() == label)
        target.click()
        return 0

    monkeypatch.setattr(combat_faults_dialog.QMessageBox, "exec", exec_)


def test_pressing_the_default_does_not_run(qapp, monkeypatch):
    _answer(monkeypatch, None)
    assert confirm_run(None, _FAULTS) is False


def test_run_anyway_runs(qapp, monkeypatch):
    _answer(monkeypatch, RUN_ANYWAY)
    assert confirm_run(None, _FAULTS) is True
