"""``dialog_footer()`` — the one ``[extras…] <stretch> [Cancel] [Commit]`` row.

Two things brought this together. The visible one is that a user meets both of this
app's footers within minutes of each other: the Combat Strategy dialog's ``ActionButton``
row, and ``QDialogButtonBox``'s Fusion chrome on nine other dialogs. Two footers that
disagree is worse than one that is uniformly old. The quieter one is that the
``ActionButton`` footer had already been hand-copied four times, which warrants a helper
whatever it looks like.

WHAT ``QDialogButtonBox`` WAS ACTUALLY DOING FOR US
---------------------------------------------------

The box is not only chrome, and a naive replacement silently regresses the keyboard in
every dialog it touches. Three jobs, and this helper takes over all three:

- **Order.** The box reorders its buttons per platform; on Windows that is
  accept-before-reject. Laid out by hand, the order is the order they are added, so it
  is written out here once: extras, stretch, Cancel, Commit.
- **The default button.** Qt hands "default" to the first ``autoDefault`` push button
  *constructed* in the dialog, which is a real trap — ``settings/profile_editor.py``
  carries a comment about its Add button having stolen Enter from OK. So the helper
  does not merely set the commit button default: it sweeps every other ``QPushButton``
  in the dialog and clears ``autoDefault``, which is what makes Enter mean the commit
  and not whichever button happened to be built first.

  That sweep runs **on the dialog's first show**, not at build time, and the reason is
  worth knowing: a widget added to a free-standing layout has no parent until that
  layout is installed on the dialog, so at the moment the footer is built most of the
  dialog's buttons are not its children yet and ``findChildren`` cannot see them. The
  region color picker is the clearest case — six swatch buttons, none of them a child
  of anything until the last line of ``__init__``.
- **Esc.** ``QDialog`` rejects on Esc by itself; the box never added that. What can take
  it away is a button holding ``autoDefault``, so the same sweep is what keeps it.

Every button is built ``fill=False``. This is the rule ``action_button.py`` documents and
the one most easily lost in a copy: these expand by default, and an expanding button
placed *after* a stretch eats the stretch and spreads the pair across the whole footer.
Extras are normalized too, rather than trusted, since they arrive already constructed.

THE CANCEL SLOT IS THE REJECT SIDE, NOT THE WORD "CANCEL"
----------------------------------------------------------

``recovery_dialog``'s reject-side button is *Discard All*, which permanently drops every
recovered unsaved bot file — the most destructive button in the app. It takes the same
slot and the ``CAUTION`` weight, because the slot is "the button that does not commit",
not "the quiet one". Hence *cancel_style* being a parameter at all.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from bartleby.qt.widgets.buttons.action_button import ActionButton, ActionStyle
from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QSizePolicy

#: Spacing between the footer's buttons, matching the worked example in
#: ``combat_strategies/dialog.py``.
_FOOTER_SPACING = 6


@dataclass(frozen=True)
class DialogFooter:
    """The built row, plus the buttons a caller has to reach back for.

    Several dialogs disable the commit until a selection exists (``wiki_import_dialog``
    starts on ``Add 0 items``), and one retitles it as the count changes, so handing the
    buttons back is not a convenience — it is the reason this returns an object rather
    than a bare layout.
    """

    layout: QHBoxLayout
    commit: ActionButton
    cancel: ActionButton
    extras: tuple[ActionButton, ...] = ()


def dialog_footer(
    dialog: QDialog,
    *,
    commit_text: str = "OK",
    commit_icon: str = "checkmark",
    commit_style: ActionStyle = ActionStyle.PRIMARY,
    on_commit: Callable[[], None] | None = None,
    cancel_text: str = "Cancel",
    cancel_icon: str = "dismiss",
    cancel_style: ActionStyle = ActionStyle.TERTIARY,
    on_cancel: Callable[[], None] | None = None,
    extras: Sequence[ActionButton] = (),
) -> DialogFooter:
    """Build *dialog*'s footer row and wire its keyboard behavior.

    *on_commit* defaults to ``dialog.accept`` and *on_cancel* to ``dialog.reject``; pass
    one where the dialog validates first, or where rejecting means something more than
    closing (``recovery_dialog`` unchecks every row on the way out).

    *extras* are buttons that sit **left of the stretch** — a second way forward that is
    not the commit. They are re-normalized to ``fill=False`` and stripped of
    ``autoDefault`` here, so a caller cannot accidentally hand one the Enter key.

    The order the footer is built in does not matter: the sweep that hands Enter to the
    commit runs on the dialog's first show, by which time every widget is a child.
    """
    row = QHBoxLayout()
    row.setSpacing(_FOOTER_SPACING)

    for extra in extras:
        _quiet(extra)
        row.addWidget(extra)
    row.addStretch()

    cancel = ActionButton(cancel_icon, cancel_text, style=cancel_style, fill=False)
    cancel.clicked.connect(on_cancel if on_cancel is not None else dialog.reject)
    row.addWidget(cancel)

    commit = ActionButton(commit_icon, commit_text, style=commit_style, fill=False)
    commit.clicked.connect(on_commit if on_commit is not None else dialog.accept)
    row.addWidget(commit)

    _quiet(cancel)
    claim_default(dialog, commit)
    # And again on the way up, when the rest of the dialog is finally a child of it.
    dialog.installEventFilter(_DefaultOnShow(dialog, commit))

    return DialogFooter(layout=row, commit=commit, cancel=cancel, extras=tuple(extras))


def claim_default(dialog: QDialog, commit: QPushButton) -> None:
    """Make *commit* the only button in *dialog* that Enter can reach.

    Every other button gives up its claim first, which is what makes the order buttons
    happen to be constructed in stop mattering. Separate from :func:`dialog_footer` so a
    dialog that grows a button after the footer — none today — can re-run it.
    """
    for button in dialog.findChildren(QPushButton):
        if button is not commit:
            button.setAutoDefault(False)
            button.setDefault(False)
    commit.setAutoDefault(True)
    commit.setDefault(True)


class _DefaultOnShow(QObject):
    """Re-runs the sweep the first time *dialog* is shown.

    Parented to the dialog so it lives exactly as long as what it watches, and it stays
    installed rather than removing itself on the first show: a dialog reopened with
    ``exec()`` costs one cheap pass over its buttons, and a filter that uninstalls itself
    mid-delivery is the fiddlier of the two.
    """

    def __init__(self, dialog: QDialog, commit: QPushButton) -> None:
        super().__init__(dialog)
        self._commit = commit

    def eventFilter(self, obj: QObject | None, event: QEvent | None) -> bool:
        if event is not None and event.type() == QEvent.Type.Show and isinstance(obj, QDialog):
            claim_default(obj, self._commit)
        return False


def _quiet(button: QPushButton) -> None:
    """Take *button* out of the running for Enter, and out of the stretch's way.

    Both halves of what a footer button that is not the commit must be. Applied to
    extras because they arrive built by the caller, and to Cancel because the sweep that
    clears the dialog's other buttons runs before it would otherwise be reached.
    """
    button.setAutoDefault(False)
    button.setDefault(False)
    policy = button.sizePolicy()
    if policy.horizontalPolicy() is QSizePolicy.Policy.Expanding:
        policy.setHorizontalPolicy(QSizePolicy.Policy.Preferred)
        button.setSizePolicy(policy)
