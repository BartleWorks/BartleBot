"""``dialog_footer()`` and the ten dialogs converted onto it (issue #89).

``QDialogButtonBox`` was not only chrome: it owned the platform button order, the Esc
wiring and — the one that bites — the default button. Qt hands "default" to the first
``autoDefault`` button *constructed*, so replacing the box by hand can leave Enter on
whichever button a dialog happened to build first, with nothing visible to show for it.
That is the regression this module exists to catch, which is why most of it is keyboard
tests over the real dialogs rather than assertions about the helper.

The per-dialog tests disconnect every button before pressing Enter and assert which one
was *reached*. Running a dialog's real commit would drag in its side effects (a message
box, a settings write) and test them instead of the routing, which is the part that
broke.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout

from bartlebot.gui.widgets.action_button import ActionButton, ActionStyle
from bartlebot.gui.widgets.dialog_footer import dialog_footer

# ---------------------------------------------------------------------------
# The helper itself
# ---------------------------------------------------------------------------


def _footer_texts(footer) -> list[str]:
    """The button labels in the order they sit in the row, with the stretch marked."""
    row = footer.layout
    out = []
    for i in range(row.count()):
        item = row.itemAt(i)
        widget = item.widget()
        out.append(widget.text() if widget is not None else "<stretch>")
    return out


def test_the_row_reads_extras_stretch_cancel_commit(qapp):
    """The order ``QDialogButtonBox`` used to supply per platform, written out once."""
    dialog = QDialog()
    extra = ActionButton("revert", "Use default", style=ActionStyle.SECONDARY)

    footer = dialog_footer(dialog, commit_text="Save", extras=[extra])

    assert _footer_texts(footer) == ["Use default", "<stretch>", "Cancel", "Save"]


def test_an_extra_sits_left_of_the_stretch(qapp):
    """An extra is a second way forward, not a second commit: it stays on the far side."""
    dialog = QDialog()
    footer = dialog_footer(dialog, extras=[ActionButton("revert", "Use default")])

    texts = _footer_texts(footer)

    assert texts.index("Use default") < texts.index("<stretch>")


def test_every_button_in_the_footer_declines_to_fill(qapp):
    """An expanding button placed after a stretch eats the stretch and spreads the pair
    across the whole footer — see ``action_button.py``."""
    dialog = QDialog()
    extra = ActionButton("revert", "Use default")
    assert extra.sizePolicy().horizontalPolicy() is QSizePolicy.Policy.Expanding, "built filling"

    footer = dialog_footer(dialog, extras=[extra])

    for button in (footer.commit, footer.cancel, *footer.extras):
        assert button.sizePolicy().horizontalPolicy() is not QSizePolicy.Policy.Expanding


def test_exactly_one_button_is_the_default(qapp):
    dialog = QDialog()
    footer = dialog_footer(dialog, extras=[ActionButton("revert", "Use default")])

    defaults = [b for b in (footer.commit, footer.cancel, *footer.extras) if b.isDefault()]

    assert defaults == [footer.commit]


def test_the_commit_is_the_one_primary(qapp):
    """At most one ``PRIMARY`` per surface — the standing rule in ``gui/CLAUDE.md``."""
    footer = dialog_footer(QDialog())

    assert footer.commit.action_style is ActionStyle.PRIMARY
    assert footer.cancel.action_style is ActionStyle.TERTIARY


def test_the_cancel_slot_can_carry_the_caution_weight(qapp):
    """The slot is "the button that does not commit", not "the quiet one" — the recovery
    dialog's Discard All permanently drops every recovered file."""
    footer = dialog_footer(QDialog(), cancel_text="Discard All", cancel_style=ActionStyle.CAUTION)

    assert footer.cancel.action_style is ActionStyle.CAUTION


def test_the_commit_calls_accept_and_the_cancel_reject(qapp):
    dialog = QDialog()
    footer = dialog_footer(dialog)

    footer.cancel.click()
    assert dialog.result() == QDialog.DialogCode.Rejected

    footer.commit.click()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_either_side_can_be_given_a_slot_of_its_own(qapp):
    """``recovery_dialog`` rejects *and* unchecks; ``settings_dialog`` validates first."""
    dialog = QDialog()
    seen: list[str] = []
    footer = dialog_footer(dialog, on_commit=lambda: seen.append("commit"), on_cancel=lambda: seen.append("cancel"))

    footer.commit.click()
    footer.cancel.click()

    assert seen == ["commit", "cancel"]
    assert dialog.result() == QDialog.DialogCode.Rejected, "untouched: neither slot closed it"


def test_a_button_built_before_the_footer_gives_up_its_claim_on_enter(qapp):
    """``profile_editor``'s Add button is the real case: built first, it held Enter, so
    Enter opened a popup on top of the dialog instead of accepting it."""
    dialog = QDialog()
    add = QPushButton("Add", dialog)
    assert add.autoDefault(), "Qt's own default for a button in a dialog"

    footer = dialog_footer(dialog)

    assert not add.autoDefault()
    assert not add.isDefault()
    assert footer.commit.isDefault()


def test_the_claim_is_renewed_when_the_dialog_is_shown(qapp):
    """The half a build-time sweep alone gets wrong. A widget in a free-standing layout
    has no parent until that layout is installed, so at footer-build time most of a
    dialog's buttons are not its children yet — the region color picker's six swatches
    are none of them. Worse, ``autoDefault`` is *acquired* on reparenting: Qt reports it
    for any button whose parent is a dialog, so a button the sweep could not see does not
    merely keep an old answer, it turns into a rival for Enter afterwards."""
    dialog = QDialog()
    orphan_row = QHBoxLayout()
    swatch = QPushButton("swatch")
    orphan_row.addWidget(swatch)

    footer = dialog_footer(dialog)

    layout = QVBoxLayout(dialog)
    layout.addLayout(orphan_row)
    layout.addLayout(footer.layout)
    assert swatch.autoDefault(), "acquired on reparenting, after the build-time sweep ran"

    dialog.show()

    assert not swatch.autoDefault()
    assert footer.commit.isDefault()


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
    from bartlebot.gui.canvas.edit_scene import _ColorPickerDialog
    from bartlebot.gui.theme import THEME

    return _ColorPickerDialog(THEME.REGION_PALETTE[0])._dialog


def _recovery_dialog():
    from bartlebot.gui.recovery_dialog import RecoveryDialog

    return RecoveryDialog([{"autosave_path": None, "original_path": None, "modified": datetime.now()}])


def _safety_warning_dialog():
    from bartlebot.gui.safety_warning_dialog import SafetyWarning, SafetyWarningDialog

    return SafetyWarningDialog([SafetyWarning(message="This bot sells drops.")])


def _rewards_tag_dialog():
    from bartlebot.gui.rewards_tag_dialog import RewardsTagDialog

    # With a suggestion, so Save starts enabled and Enter has somewhere to go.
    return RewardsTagDialog(["Reagents"])


def _wiki_import_dialog():
    from bartlebot.gui.wiki_import_dialog import WikiImportDialog

    return WikiImportDialog()


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
