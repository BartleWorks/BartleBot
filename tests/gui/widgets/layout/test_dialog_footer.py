"""The ten dialogs converted onto core's ``dialog_footer()`` (issue #89).

``QDialogButtonBox`` was not only chrome: it owned the platform button order, the Esc
wiring and — the one that bites — the default button. Qt hands "default" to the first
``autoDefault`` button *constructed*, so replacing the box by hand can leave Enter on
whichever button a dialog happened to build first, with nothing visible to show for it.
That is the regression this module exists to catch, which is why it is keyboard tests
over the real dialogs. The helper's own tests are core's
(``tests/qt/widgets/layout/test_dialog_footer.py``), since Stage 6 of #167.

The per-dialog tests disconnect every button before pressing Enter and assert which one
was *reached*. Running a dialog's real commit would drag in its side effects (a message
box, a settings write) and test them instead of the routing, which is the part that
broke.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from bartleby.qt.widgets.buttons.action_button import ActionButton, ActionStyle
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog, QPushButton

# ---------------------------------------------------------------------------
# The converted dialogs — Enter and Esc
# ---------------------------------------------------------------------------


def _settings_dialog():
    from bartlebot.gui.settings.settings_dialog import SettingsDialog

    return SettingsDialog()


def _account_dialog():
    from bartlebot.gui.settings import accounts_tab

    return accounts_tab._AccountDialog()


def _profile_dialog():
    from bartlebot.gui.settings.profile_editor import ProfileDialog

    return ProfileDialog()


def _bot_strategy_picker():
    from bartlebot.gui.combat_strategies.bot_strategy_picker import BotStrategyPicker

    return BotStrategyPicker([], current_id=None)


def _subgraph_meta_dialog():
    from bartlebot.gui.canvas.subgraph_widgets import SubgraphMetaDialog

    return SubgraphMetaDialog(name="Regroup at the hub")


def _link_input_dialog():
    from bartlebot.bot.schema import SubgraphInput
    from bartlebot.gui.canvas.subgraph_widgets import LinkInputDialog

    return LinkInputDialog(
        [SubgraphInput(name="world", type="str")],
        param_key="zone",
        param_type="str",
        param_label="Zone",
        current_value="X/Hub",
    )


def _color_picker_dialog():
    from bartleby.qt.theming.theme import THEME

    from bartlebot.gui.canvas.edit_scene import _ColorPickerDialog

    return _ColorPickerDialog(THEME.REGION_PALETTE[0])._dialog


def _recovery_dialog():
    from bartlebot.gui.dialogs.recovery_dialog import RecoveryDialog

    return RecoveryDialog([{"autosave_path": None, "original_path": None, "modified": datetime.now()}])


def _safety_warning_dialog():
    from bartlebot.gui.dialogs.safety_warning_dialog import SafetyWarning, SafetyWarningDialog

    return SafetyWarningDialog([SafetyWarning(message="This bot sells drops.")])


def _rewards_tag_dialog():
    from bartlebot.gui.dialogs.rewards_tag_dialog import RewardsTagDialog

    # With a suggestion, so Save starts enabled and Enter has somewhere to go.
    return RewardsTagDialog(["Reagents"])


def _wiki_import_dialog():
    from bartlebot.gui.dialogs.wiki_import_dialog import WikiImportDialog

    return WikiImportDialog()


def _new_bot_dialog():
    from bartlebot.gui.dialogs.new_bot_dialog import NewBotDialog

    # No templates and no library: the Blank bot card alone, on the page Enter leaves by Next.
    return NewBotDialog([], "no-such-library")


def _first_run_dialog():
    from bartlebot.gui.dialogs.first_run_dialog import FirstRunDialog, RealmAndDelayPage

    # A page every user is asked: the combat question is only a team's.
    return FirstRunDialog([RealmAndDelayPage()])


def _variable_dialog():
    from bartlebot.bot.schema import BotConfig, VariableModel
    from bartlebot.gui.sidebar.sections.variables_section import VariableDialog

    config = BotConfig(
        id="b",
        name="b",
        initial="",
        recovery_state="",
        states=[],
        transitions=[],
        variables=[VariableModel(name="n", type="int")],
    )
    return VariableDialog(config, existing=config.variables[0])


#: (name, factory, the label Enter must reach). ``wiki_import`` is absent on purpose —
#: its commit starts disabled and has a test of its own below.
CONVERTED = [
    ("settings", _settings_dialog, "OK"),
    ("account", _account_dialog, "OK"),
    ("profile", _profile_dialog, "OK"),
    ("bot_strategy_picker", _bot_strategy_picker, "OK"),
    ("subgraph_meta", _subgraph_meta_dialog, "Save"),
    ("link_input", _link_input_dialog, "OK"),
    ("region_color", _color_picker_dialog, "OK"),
    ("recovery", _recovery_dialog, "OK"),
    ("safety_warning", _safety_warning_dialog, "Run Bot"),
    ("rewards_tag", _rewards_tag_dialog, "Save"),
    ("new_bot", _new_bot_dialog, "Next"),
    ("variable", _variable_dialog, "Save"),
    ("first_run", _first_run_dialog, "Next"),
]


def _press_enter(dialog: QDialog) -> list[QPushButton]:
    """Show *dialog*, press Enter, and return the buttons that were clicked.

    Every button is disconnected from its real slot first: what is under test is which
    button Enter reaches, and running a commit for real would test the dialog's own
    side effects instead — a message box on an empty selection, a settings write.
    """
    dialog.show()
    fired: list[QPushButton] = []
    for button in dialog.findChildren(QPushButton):
        try:
            button.clicked.disconnect()
        except TypeError:
            pass  # never connected to anything
        button.clicked.connect(lambda _checked=False, b=button: fired.append(b))
    QTest.keyClick(dialog, Qt.Key.Key_Return)
    return fired


@pytest.mark.parametrize(("name", "factory", "commit_text"), CONVERTED, ids=[c[0] for c in CONVERTED])
def test_enter_activates_the_commit_button(qapp, name, factory, commit_text):
    dialog = factory()

    fired = _press_enter(dialog)

    assert [b.text() for b in fired] == [commit_text]


@pytest.mark.parametrize(
    ("name", "factory", "commit_text"),
    [*CONVERTED, ("wiki_import", _wiki_import_dialog, "Add 0 items")],
    ids=[c[0] for c in CONVERTED] + ["wiki_import"],
)
def test_the_commit_is_the_only_button_that_can_take_enter(qapp, name, factory, commit_text):
    """The sharper half of the Enter test. ``autoDefault`` hands Enter to whichever
    button has focus, so a rival only shows itself once the user has tabbed onto it —
    which is exactly the regression that would otherwise ship unnoticed."""
    dialog = factory()
    dialog.show()

    default = [b for b in dialog.findChildren(QPushButton) if b.isDefault()]
    rivals = [b.text() for b in dialog.findChildren(QPushButton) if b.autoDefault() and not b.isDefault()]

    assert [b.text() for b in default] == [commit_text]
    assert rivals == []


@pytest.mark.parametrize(("name", "factory", "commit_text"), CONVERTED, ids=[c[0] for c in CONVERTED])
def test_escape_rejects(qapp, name, factory, commit_text):
    dialog = factory()
    dialog.show()

    QTest.keyClick(dialog, Qt.Key.Key_Escape)

    assert dialog.result() == QDialog.DialogCode.Rejected


def test_enter_does_nothing_while_the_commit_is_disabled(qapp):
    """``wiki_import_dialog`` opens on ``Add 0 items`` with nothing fetched, and Enter
    there must not close the dialog on an empty import."""
    dialog = _wiki_import_dialog()
    assert not dialog._ok_btn.isEnabled()

    fired = _press_enter(dialog)

    assert fired == []
    assert not dialog.isHidden()


def test_the_wiki_commit_still_takes_enter_once_it_is_enabled(qapp):
    dialog = _wiki_import_dialog()
    dialog._ok_btn.setEnabled(True)

    fired = _press_enter(dialog)

    assert [b.text() for b in fired] == ["Add 0 items"]


def test_fetch_is_the_second_way_forward_not_the_commit(qapp):
    """It was already saying so by negation — a bare ``setDefault(False)`` whose only job
    was "I am prominent but I am not the commit."""
    dialog = _wiki_import_dialog()

    assert dialog._btn_fetch.action_style is ActionStyle.SECONDARY
    assert not dialog._btn_fetch.isDefault()


def test_discard_all_carries_the_caution_weight(qapp):
    """It unchecks every candidate and rejects — i.e. permanently drops every recovered
    unsaved bot file. The app's most destructive button must not render as its quietest."""
    dialog = _recovery_dialog()

    discard = next(b for b in dialog.findChildren(ActionButton) if b.text() == "Discard All")

    assert discard.action_style is ActionStyle.CAUTION


def test_run_bot_deliberately_declines_the_accent(qapp):
    """The one place the commit does not take the weight: this dialog exists to make the
    user hesitate, and filling the button that dismisses it argues with its own purpose.
    Raise it on issue #89 rather than switching it silently."""
    dialog = _safety_warning_dialog()

    run = next(b for b in dialog.findChildren(ActionButton) if b.text() == "Run Bot")

    assert run.action_style is ActionStyle.TERTIARY
    assert run.isDefault(), "still what Enter reaches — it just does not advertise itself"
