"""Settings tab: Game (accounts, launch profiles, and game embed resolution).

Also the home of the *card actions* — the module-level ``add_``/``edit_``/``delete_``/
``duplicate_`` functions below, which are what every surface that manages an account or
a launch profile calls (#194). They live here because the editors they wrap do, not
because the tab owns them: none of them needs an ``AccountsTab`` to exist, which is the
whole point. The launch screen's cards reached the profile editor by building the entire
Settings dialog underneath it until #193, and the Add cards (#195) and the Game tab's own
buttons would each have grown a second copy of the delete cascade otherwise.

Each returns whether anything was written, which is every caller's cue to refresh
whatever it draws accounts or profiles with.

Each also *saves*. That is not incidental: ``SettingsManager.set`` only writes memory,
and the one ``save()`` these used to reach was ``SettingsDialog._on_accept``. A card on
the launch screen has no Settings dialog behind it, so an account added there survived
until the process exited and no further -- and the tab's own ``apply()`` has always
claimed it "saves immediately on each action", which was true of the reorders alone.

Every one of them runs ``exec()`` on a dialog or a message box, so every one must be
called from a synchronous Qt slot: Python 3.13 forbids re-entering Qt's event loop from
inside a running asyncio task. See the qasync section of CLAUDE.md.
"""

from __future__ import annotations

from copy import deepcopy

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from bartlebot import character_roster
from bartlebot.credentials import CredentialStore
from bartlebot.gui import screen_fit
from bartlebot.gui.menu_utils import popup_menu
from bartlebot.gui.widgets.themed_label import ThemedTextLabel
from bartlebot.settings_manager import VALID_RESOLUTIONS, SettingsManager


def _mark_unfittable_resolutions(
    combo: QComboBox,
    client_count: int,
    widget: QWidget,
    live_area: tuple[int, int] | None = None,
) -> None:
    """Grey out every entry in *combo* that *client_count* clients could not fit at.

    Disabled and explained rather than dropped from the list, which is the same rule the
    launch surfaces follow: a user who cannot find 1920x1080 in the list learns nothing,
    while one who sees it greyed out learns that their screen is the reason and that
    fewer clients would bring it back.

    The item's own resolution is read from its ``userData``, so the "Use global setting"
    entry — which carries None — is skipped: what it resolves to is the global combo's
    business, and that combo polices itself.
    """
    model = combo.model()
    for index in range(combo.count()):
        resolution = combo.itemData(index)
        if resolution is None:
            continue
        reason = screen_fit.fit_problem(tuple(resolution), client_count, widget, live_area)
        item = model.item(index)
        if item is not None:
            item.setEnabled(reason is None)
        combo.setItemData(index, reason or "", Qt.ItemDataRole.ToolTipRole)


class _ReorderableList(QListWidget):
    """QListWidget with internal-move drag-and-drop that preserves UserRole data.

    Qt's default InternalMove serialises items to MIME (text only), losing any
    UserRole data set on each item.  This subclass overrides dropEvent to move
    the actual QListWidgetItem object via takeItem/insertItem instead, so all
    item data survives the drag.  A ``reordered`` signal is emitted after each
    successful drop so callers can persist the new order.
    """

    reordered = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def dropEvent(self, event) -> None:
        src_row = self.currentRow()
        if src_row < 0:
            event.ignore()
            return

        dest_index = self.indexAt(event.position().toPoint())
        if dest_index.isValid():
            dest_row = dest_index.row()
            if self.dropIndicatorPosition() == QAbstractItemView.DropIndicatorPosition.BelowItem:
                dest_row += 1
        else:
            dest_row = self.count()

        # No-op: dropping onto current position or the gap immediately after it
        if dest_row == src_row or dest_row == src_row + 1:
            event.ignore()
            return

        item = self.takeItem(src_row)
        if src_row < dest_row:
            dest_row -= 1
        self.insertItem(dest_row, item)
        self.setCurrentRow(dest_row)
        # Report IgnoreAction so QAbstractItemView::startDrag does not attempt
        # its own source-row deletion — we already moved the item manually.
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()
        self.reordered.emit()


class _AccountDialog(QDialog):
    """Small dialog for adding or editing an account.

    A *username* opens it in edit mode, which differs in two ways: the password may be
    left blank to keep the stored one, and the username itself is read-only. The username
    is this account's key everywhere — the Credential Manager entry, the character roster
    file, and every launch profile's membership list — so it is not something an alias
    field's neighbour can quietly change. It used to be editable and silently ignored,
    which is the worse of the two: the user types a correction, presses OK, and the list
    comes back unchanged with nothing saying why. The alias is the field for what the
    account is *called*.
    """

    def __init__(self, username: str = "", alias: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Account")
        self.setMinimumWidth(300)

        form = QFormLayout()
        self._username_edit = QLineEdit(username)
        self._alias_edit = QLineEdit(alias)
        self._alias_edit.setPlaceholderText("Optional — shown instead of username")
        self._password_edit = QLineEdit()
        self._password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        if username:
            self._username_edit.setReadOnly(True)
            self._username_edit.setToolTip("An account's username cannot be changed. Set an alias instead.")
            self._password_edit.setPlaceholderText("Leave blank to keep existing password")

        form.addRow("Username:", self._username_edit)
        form.addRow("Alias:", self._alias_edit)
        form.addRow("Password:", self._password_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    @property
    def username(self) -> str:
        return self._username_edit.text().strip()

    @property
    def alias(self) -> str:
        return self._alias_edit.text().strip()

    @property
    def password(self) -> str:
        return self._password_edit.text()


def add_account(parent: QWidget | None = None) -> bool:
    """Open an empty account editor; store the credential and the record if accepted.

    A module function for the same reason as ``edit_launch_profile``: the Add affordance
    is a card at the foot of the launch screen's Accounts column (#195) as well as a
    button under the Game tab's list, and the card must not have to build a Settings
    dialog to reach the one editor it wants.

    Returns whether an account was written, which is every caller's cue to redraw
    whatever it draws accounts with. False covers all four ways nothing was saved: the
    editor cancelled, an empty username, an empty password, and a duplicate.
    """
    dlg = _AccountDialog(parent=parent)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    username = dlg.username
    if not username:
        QMessageBox.warning(parent, "Invalid", "Username cannot be empty.")
        return False
    if not dlg.password:
        QMessageBox.warning(parent, "Invalid", "Password cannot be empty for a new account.")
        return False
    accounts = SettingsManager.instance().accounts
    if any(a["username"] == username for a in accounts):
        QMessageBox.warning(parent, "Duplicate", f"Account '{username}' already exists.")
        return False
    CredentialStore.store(username, dlg.password)
    accounts.append({"username": username, "alias": dlg.alias or ""})
    SettingsManager.instance().accounts = accounts
    SettingsManager.instance().save()
    return True


def edit_account(username: str, parent: QWidget | None = None) -> bool:
    """Open the editor for the account *username*; store what it returns.

    The password is optional here and only written when one was typed: the field is blank
    on open, and an empty one means "keep the stored credential" rather than "clear it".
    Reading a password back out of the Credential Manager to prefill the field is what
    that alternative would cost.

    Returns whether anything was written. False covers no such account and a cancelled
    editor -- there is nothing else to refuse, since the username cannot change and an
    alias may legitimately be blank.
    """
    account = next((a for a in SettingsManager.instance().accounts if a.get("username") == username), None)
    if account is None:
        return False
    dlg = _AccountDialog(username=username, alias=account.get("alias") or "", parent=parent)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    if dlg.password:
        CredentialStore.store(username, dlg.password)
    accounts = SettingsManager.instance().accounts
    for a in accounts:
        if a.get("username") == username:
            a["alias"] = dlg.alias
            break
    SettingsManager.instance().accounts = accounts
    SettingsManager.instance().save()
    return True


def delete_account(username: str, parent: QWidget | None = None) -> bool:
    """Confirm, then remove the account *username* and everything keyed to it.

    Four things go, and the confirm names three of them because they are not recoverable
    by adding the account back: the record, its password in the Credential Manager, and
    its character roster. The fourth is the cascade -- the username is stripped from every
    launch profile that launches it, because a profile naming an account that no longer
    exists is a launch that fails at the login screen with nothing on the card saying why.

    A roster is wizard names, which is exactly the data ``character_roster`` is careful
    about; leaving one behind for an account the user has just removed is the case it
    exists for.

    Returns whether anything was written. False covers no such account and a declined
    confirm.
    """
    account = next((a for a in SettingsManager.instance().accounts if a.get("username") == username), None)
    if account is None:
        return False
    display = CredentialStore.display_name(username, account.get("alias") or None)
    reply = QMessageBox.question(
        parent,
        "Delete account",
        f"Remove '{display}', its stored password, and its known characters?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    if reply != QMessageBox.StandardButton.Yes:
        return False
    CredentialStore.delete(username)
    character_roster.forget(username)
    sm = SettingsManager.instance()
    sm.accounts = [a for a in sm.accounts if a.get("username") != username]
    profiles = sm.launch_profiles
    for p in profiles:
        p["accounts"] = [u for u in p.get("accounts", []) if u != username]
    sm.launch_profiles = profiles
    sm.save()
    return True


class _ProfileDialog(QDialog):
    """Dialog for adding or editing a launch profile.

    Shows only the accounts already included in the profile.  Adding/removing
    accounts is done via the Add ▾ dropdown and Remove button; order is
    controlled with the ↑/↓ buttons.  A per-profile resolution override can
    be selected, or left at "Use global setting".
    """

    def __init__(
        self,
        name: str = "",
        selected_usernames: list[str] | None = None,
        resolution: tuple[int, int] | None = None,
        parent=None,
        live_game_area: tuple[int, int] | None = None,
    ) -> None:
        super().__init__(parent)
        # The game area behind the settings dialog, so this picker and the Launch menu
        # agree about which resolutions can be launched. None falls back to the
        # configured layout alone — see gui/screen_fit.budget_for.
        self._live_game_area = live_game_area
        self.setWindowTitle("Launch Profile")
        self.setMinimumWidth(340)

        layout = QVBoxLayout(self)

        # --- Profile name ---
        form = QFormLayout()
        self._name_edit = QLineEdit(name)
        form.addRow("Profile name:", self._name_edit)
        layout.addLayout(form)

        # --- Account list (ordered, no checkboxes) ---
        layout.addWidget(QLabel("Accounts (in player order):"))

        list_and_buttons = QHBoxLayout()

        self._account_list = _ReorderableList()
        self._account_list.currentRowChanged.connect(self._on_selection_changed)
        list_and_buttons.addWidget(self._account_list, 1)

        side_buttons = QVBoxLayout()

        # Add — QToolButton with a popup menu (InstantPopup adds its own arrow)
        self._add_btn = QToolButton()
        self._add_btn.setText("Add")
        self._add_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._add_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._add_menu = QMenu(self)
        self._add_btn.setMenu(self._add_menu)
        self._add_menu.aboutToShow.connect(self._populate_add_menu)

        self._remove_btn = QPushButton("Remove")
        self._remove_btn.setEnabled(False)
        self._remove_btn.clicked.connect(self._on_remove)

        self._up_btn = QPushButton("↑")
        self._up_btn.setEnabled(False)
        self._up_btn.clicked.connect(self._on_move_up)

        self._down_btn = QPushButton("↓")
        self._down_btn.setEnabled(False)
        self._down_btn.clicked.connect(self._on_move_down)

        side_buttons.addWidget(self._add_btn)
        side_buttons.addWidget(self._remove_btn)
        side_buttons.addSpacing(4)
        side_buttons.addWidget(self._up_btn)
        side_buttons.addWidget(self._down_btn)
        side_buttons.addStretch()

        list_and_buttons.addLayout(side_buttons)
        layout.addLayout(list_and_buttons, 1)

        # --- Per-profile resolution ---
        res_form = QFormLayout()
        self._res_combo = QComboBox()
        self._res_combo.addItem("Use global setting", userData=None)
        for w, h in VALID_RESOLUTIONS:
            self._res_combo.addItem(f"{w}×{h}", userData=(w, h))

        if resolution is not None:
            for i in range(self._res_combo.count()):
                if self._res_combo.itemData(i) == resolution:
                    self._res_combo.setCurrentIndex(i)
                    break

        res_form.addRow("Resolution:", self._res_combo)
        layout.addLayout(res_form)

        self._res_hint = ThemedTextLabel("", css="font-size: 11px;")
        self._res_hint.setWordWrap(True)
        layout.addWidget(self._res_hint)

        # --- OK / Cancel ---
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Populate list with pre-selected accounts
        for username in selected_usernames or []:
            self._add_account_item(username)
        self._refresh_resolution_fit()

    def _refresh_resolution_fit(self) -> None:
        """Re-judge which resolutions this profile could launch at, and say what fits.

        Judged against the profile's *own* account count, which is the number of clients
        this resolution will actually have to hold. Re-run on every change to the list,
        because adding a fourth account is exactly what turns a fitting profile into one
        that has to come down a tier.
        """
        count = max(1, self._account_list.count())
        _mark_unfittable_resolutions(self._res_combo, count, self, self._live_game_area)
        largest = screen_fit.largest_fitting_resolution(count, self, self._live_game_area)
        clients = f"{count} client{'s' if count != 1 else ''}"
        self._res_hint.setText(
            f"Largest that fits {clients} on this screen: {largest[0]}×{largest[1]}."
            if largest is not None
            else f"No offered resolution fits {clients} on this screen — remove an account, "
            "or make the sidebars and bot canvas smaller in Settings → General."
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _add_account_item(self, username: str) -> None:
        accounts_meta = {a["username"]: a for a in SettingsManager.instance().accounts}
        meta = accounts_meta.get(username, {})
        alias = meta.get("alias") or None
        display = CredentialStore.display_name(username, alias)
        item = QListWidgetItem(display)
        item.setData(Qt.ItemDataRole.UserRole, username)
        self._account_list.addItem(item)

    def _populate_add_menu(self) -> None:
        self._add_menu.clear()
        already_in = {
            self._account_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self._account_list.count())
        }
        available = [a for a in SettingsManager.instance().accounts if a["username"] not in already_in]
        if not available:
            action = self._add_menu.addAction("(no more accounts)")
            action.setEnabled(False)
            return
        for acct in available:
            username = acct["username"]
            alias = acct.get("alias") or None
            display = CredentialStore.display_name(username, alias)
            action = self._add_menu.addAction(display)
            action.triggered.connect(lambda _checked, u=username: self._on_add_account(u))

    def _on_add_account(self, username: str) -> None:
        self._add_account_item(username)
        self._refresh_resolution_fit()

    def _on_remove(self) -> None:
        row = self._account_list.currentRow()
        if row >= 0:
            self._account_list.takeItem(row)
            self._refresh_resolution_fit()

    def _on_move_up(self) -> None:
        row = self._account_list.currentRow()
        if row <= 0:
            return
        item = self._account_list.takeItem(row)
        self._account_list.insertItem(row - 1, item)
        self._account_list.setCurrentRow(row - 1)

    def _on_move_down(self) -> None:
        row = self._account_list.currentRow()
        if row < 0 or row >= self._account_list.count() - 1:
            return
        item = self._account_list.takeItem(row)
        self._account_list.insertItem(row + 1, item)
        self._account_list.setCurrentRow(row + 1)

    def _on_selection_changed(self, row: int) -> None:
        has_sel = row >= 0
        self._remove_btn.setEnabled(has_sel)
        self._up_btn.setEnabled(has_sel and row > 0)
        self._down_btn.setEnabled(has_sel and row < self._account_list.count() - 1)

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._name_edit.text().strip()

    @property
    def selected_usernames(self) -> list[str]:
        return [self._account_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self._account_list.count())]

    @property
    def resolution(self) -> tuple[int, int] | None:
        return self._res_combo.currentData()


def add_launch_profile(
    parent: QWidget | None = None,
    live_game_area: tuple[int, int] | None = None,
) -> bool:
    """Open an empty profile editor; save the profile if accepted.

    The counterpart to :func:`add_account`, and lifted out of ``AccountsTab`` for the
    same reason -- see that function, and ``edit_launch_profile`` below for what
    *live_game_area* is for.

    Returns whether a profile was written. False covers the three ways nothing was:
    the editor cancelled, an empty name, and a duplicate.
    """
    dlg = _ProfileDialog(parent=parent, live_game_area=live_game_area)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    name = dlg.name
    if not name:
        QMessageBox.warning(parent, "Invalid", "Profile name cannot be empty.")
        return False
    profiles = SettingsManager.instance().launch_profiles
    if any(p["name"] == name for p in profiles):
        QMessageBox.warning(parent, "Duplicate", f"Profile '{name}' already exists.")
        return False
    profile: dict = {"name": name, "accounts": dlg.selected_usernames}
    if dlg.resolution is not None:
        profile["resolution"] = list(dlg.resolution)
    profiles.append(profile)
    SettingsManager.instance().launch_profiles = profiles
    SettingsManager.instance().save()
    return True


def edit_launch_profile(
    old_name: str,
    parent: QWidget | None = None,
    live_game_area: tuple[int, int] | None = None,
) -> bool:
    """Open the editor for the launch profile named *old_name*; save it if accepted.

    A module function rather than a method because the two places that edit a profile —
    the Game tab's Edit button and the gear on a launch card — have nothing else in
    common, and routing the card through the tab meant building the whole Settings dialog
    to reach one editor (#193). What the card needs is the editor and the save; what it
    does not need is a second OK/Cancel around them, applying to every tab.

    Returns whether the profile was actually written, which is the caller's cue to
    refresh whatever it draws profiles with. False covers all three ways nothing was
    saved: no such profile, the editor cancelled, and a name the save refused.

    *live_game_area* is passed through so the editor's resolution picker judges a launch
    the same way the surface behind it does — see gui/screen_fit.budget_for.
    """
    profile = next((p for p in SettingsManager.instance().launch_profiles if p.get("name") == old_name), None)
    if profile is None:
        return False
    raw_res = profile.get("resolution")
    existing_res = tuple(raw_res) if raw_res else None
    dlg = _ProfileDialog(
        name=old_name,
        selected_usernames=profile.get("accounts", []),
        resolution=existing_res,
        parent=parent,
        live_game_area=live_game_area,
    )
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    new_name = dlg.name
    if not new_name:
        QMessageBox.warning(parent, "Invalid", "Profile name cannot be empty.")
        return False
    profiles = SettingsManager.instance().launch_profiles
    if new_name != old_name and any(p["name"] == new_name for p in profiles):
        QMessageBox.warning(parent, "Duplicate", f"Profile '{new_name}' already exists.")
        return False
    for p in profiles:
        if p["name"] == old_name:
            p["name"] = new_name
            p["accounts"] = dlg.selected_usernames
            if dlg.resolution is not None:
                p["resolution"] = list(dlg.resolution)
            else:
                p.pop("resolution", None)
            break
    SettingsManager.instance().launch_profiles = profiles
    SettingsManager.instance().save()
    return True


def _free_profile_name(base: str, taken: set[str]) -> str:
    """The first of "Foo (copy)", "Foo (copy 2)", "Foo (copy 3)"... that *taken* lacks.

    Counting from 2 on the second copy rather than 1 on the first, so duplicating once
    reads "Farm Team (copy)" and not "Farm Team (copy 1)" -- the numberless form is the
    common case and should be the one that looks unremarkable.

    Split out and given the taken set rather than reading settings, because what makes a
    name free is a question with an answer a test can ask directly.
    """
    candidate = f"{base} (copy)"
    n = 2
    while candidate in taken:
        candidate = f"{base} (copy {n})"
        n += 1
    return candidate


def duplicate_launch_profile(name: str) -> bool:
    """Copy the profile *name* under a free name, directly after the original.

    No dialog, and so no *parent*: there is nothing to ask. A duplicate is the cheap way
    to build the profile that is this one plus an account, or this one at a smaller
    resolution, and putting an editor in front of it would make it as expensive as
    starting from Add.

    Inserted after the original rather than appended, so the copy lands where the user is
    looking. Appending puts it at the foot of a column they may have to scroll to find,
    beside an Add card, which reads as something that was created somewhere else.

    Deep-copied. A shallow one would share the ``accounts`` list with the original, so
    the first edit to the copy's membership would silently rewrite the profile it came
    from -- and both would then be written back by the same save.

    Returns whether anything was written; False only if no profile has that name.
    """
    profiles = SettingsManager.instance().launch_profiles
    index = next((i for i, p in enumerate(profiles) if p.get("name") == name), None)
    if index is None:
        return False
    copy = deepcopy(profiles[index])
    copy["name"] = _free_profile_name(name, {p.get("name", "") for p in profiles})
    profiles.insert(index + 1, copy)
    SettingsManager.instance().launch_profiles = profiles
    SettingsManager.instance().save()
    return True


def delete_launch_profile(name: str, parent: QWidget | None = None) -> bool:
    """Confirm, then remove the launch profile *name*.

    The confirm is lighter than ``delete_account``'s on purpose, and says only what goes:
    a profile is a list of accounts and a resolution, so deleting one destroys no
    credential and no roster, and rebuilding it is a minute in the editor.

    Returns whether anything was written. False covers no such profile and a declined
    confirm.
    """
    profiles = SettingsManager.instance().launch_profiles
    if not any(p.get("name") == name for p in profiles):
        return False
    reply = QMessageBox.question(
        parent,
        "Delete profile",
        f"Remove launch profile '{name}'?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    if reply != QMessageBox.StandardButton.Yes:
        return False
    SettingsManager.instance().launch_profiles = [p for p in profiles if p.get("name") != name]
    SettingsManager.instance().save()
    return True


class AccountsTab(QWidget):
    def __init__(self, parent=None, live_game_area: tuple[int, int] | None = None) -> None:
        super().__init__(parent)
        # Set before the resolution section is built: its own fit pass reads it.
        self._live_game_area = live_game_area
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ------------------------------------------------------------------
        # Global resolution section
        # ------------------------------------------------------------------
        res_form = QFormLayout()
        res_form.setSpacing(8)
        self._res_combo = QComboBox()
        for w, h in VALID_RESOLUTIONS:
            self._res_combo.addItem(f"{w}×{h}", userData=(w, h))

        current = SettingsManager.instance().game_resolution
        for i in range(self._res_combo.count()):
            if self._res_combo.itemData(i) == current:
                self._res_combo.setCurrentIndex(i)
                break

        res_form.addRow("Default game resolution:", self._res_combo)
        layout.addLayout(res_form)

        hint = ThemedTextLabel(
            "Set Wizard101 to Windowed mode in Options → Graphics, with this resolution. "
            "At smaller sizes the game's own UI is scaled down to match while BartleBot runs, "
            "and put back when it closes. Widescreen sizes keep a larger UI than 4:3 ones of "
            "the same height.",
            css="font-size: 11px;",
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._res_combo.currentIndexChanged.connect(self._on_resolution_changed)
        self._refresh_resolution_fit()

        # ------------------------------------------------------------------
        # Accounts section
        # ------------------------------------------------------------------
        layout.addWidget(QLabel("Accounts (passwords stored in Windows Credential Manager):"))

        self._list = _ReorderableList()
        self._list.reordered.connect(self._on_accounts_reordered)
        layout.addWidget(self._list, 1)

        btn_row = QHBoxLayout()
        self._add_btn = QPushButton("Add")
        self._edit_btn = QPushButton("Edit")
        self._delete_btn = QPushButton("Delete")
        self._edit_btn.setEnabled(False)
        self._delete_btn.setEnabled(False)
        btn_row.addWidget(self._add_btn)
        btn_row.addWidget(self._edit_btn)
        btn_row.addWidget(self._delete_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._add_btn.clicked.connect(self._on_add)
        self._edit_btn.clicked.connect(self._on_edit)
        self._delete_btn.clicked.connect(self._on_delete)
        self._list.currentRowChanged.connect(self._on_selection_changed)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_account_context_menu)

        self._refresh()

        # ------------------------------------------------------------------
        # Launch Profiles section
        # ------------------------------------------------------------------
        layout.addWidget(QLabel("Launch Profiles:"))

        self._profile_list = _ReorderableList()
        self._profile_list.reordered.connect(self._on_profiles_reordered)
        layout.addWidget(self._profile_list, 1)

        profile_btn_row = QHBoxLayout()
        self._profile_add_btn = QPushButton("Add")
        self._profile_edit_btn = QPushButton("Edit")
        self._profile_delete_btn = QPushButton("Delete")
        self._profile_edit_btn.setEnabled(False)
        self._profile_delete_btn.setEnabled(False)
        profile_btn_row.addWidget(self._profile_add_btn)
        profile_btn_row.addWidget(self._profile_edit_btn)
        profile_btn_row.addWidget(self._profile_delete_btn)
        profile_btn_row.addStretch()
        layout.addLayout(profile_btn_row)

        self._profile_add_btn.clicked.connect(self._on_profile_add)
        self._profile_edit_btn.clicked.connect(self._on_profile_edit)
        self._profile_delete_btn.clicked.connect(self._on_profile_delete)
        self._profile_list.currentRowChanged.connect(self._on_profile_selection_changed)
        self._profile_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._profile_list.customContextMenuRequested.connect(self._on_profile_context_menu)

        self._refresh_profiles()

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def _on_resolution_changed(self) -> None:
        resolution = self._res_combo.currentData()
        if resolution:
            SettingsManager.instance().game_resolution = resolution
            SettingsManager.instance().save()

    def _refresh_resolution_fit(self) -> None:
        """Grey out the resolutions a *single* client could not fit at.

        One client, deliberately, even though this setting is also what a profile without
        its own resolution launches at. Sizing it against the largest such profile is
        defensible and was what this did, but it greys out most of the list for anyone who
        happens to keep a four-account profile around — and a user running several clients
        is a user who has a launch profile, which carries its own resolution combo sized
        against its own accounts. Judging the global default by the rarer case made the
        common one look broken.

        Only a hint either way, not a gate: this value is set for more than a running
        fleet, and the launch surfaces refuse an unfittable launch on their own — a profile
        that inherits a resolution too large for its account count is still stopped there,
        with a reason naming both numbers.
        """
        _mark_unfittable_resolutions(self._res_combo, 1, self, self._live_game_area)

    # ------------------------------------------------------------------
    # Accounts
    # ------------------------------------------------------------------

    def _on_accounts_reordered(self) -> None:
        accounts = [self._list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self._list.count())]
        SettingsManager.instance().accounts = accounts
        SettingsManager.instance().save()

    def _on_profiles_reordered(self) -> None:
        profiles = [
            self._profile_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self._profile_list.count())
        ]
        SettingsManager.instance().launch_profiles = profiles
        SettingsManager.instance().save()

    def _refresh(self) -> None:
        self._list.clear()
        for acct in SettingsManager.instance().accounts:
            username = acct.get("username", "")
            alias = acct.get("alias") or None
            display = CredentialStore.display_name(username, alias)
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, acct)
            self._list.addItem(item)

    def _on_selection_changed(self, row: int) -> None:
        has_sel = row >= 0
        self._edit_btn.setEnabled(has_sel)
        self._delete_btn.setEnabled(has_sel)

    def _selected_username(self) -> str:
        """The account the buttons and the context menu act on, "" if none is selected."""
        row = self._list.currentRow()
        if row < 0:
            return ""
        return (self._list.item(row).data(Qt.ItemDataRole.UserRole) or {}).get("username", "")

    def _on_add(self) -> None:
        if add_account(self):
            self._refresh()

    def _on_edit(self) -> None:
        if (username := self._selected_username()) and edit_account(username, self):
            self._refresh()

    def _on_delete(self) -> None:
        if (username := self._selected_username()) and delete_account(username, self):
            # Both lists: deleting an account strips it from every profile that launched
            # it, and the profile rows carry an account count.
            self._refresh()
            self._refresh_profiles()

    def _on_account_context_menu(self, pos) -> None:
        """Right-click on an account row: the same two verbs the buttons below carry.

        Text only, where the launch screen's card menus carry a pencil and a trash. Those
        icons are there because the card face has the pencil on it and the menu has to
        read as the same verb; a list row has no icon of its own for a menu to echo, and
        the entries here duplicate two buttons that are already just words. They come with
        the icons when the rows become cards (#192).

        The row under the cursor is selected first. Right-clicking one row while another
        is selected would otherwise run the verb against the selection rather than the
        row that was aimed at -- and for Delete that is an account and a roster gone on a
        mis-aim.
        """
        item = self._list.itemAt(pos)
        if item is None:
            return
        self._list.setCurrentItem(item)
        menu = popup_menu(self._list)
        menu.addAction("Edit").triggered.connect(self._on_edit)
        menu.addAction("Delete").triggered.connect(self._on_delete)
        menu.exec(self._list.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------
    # Launch Profiles
    # ------------------------------------------------------------------

    def _refresh_profiles(self) -> None:
        self._refresh_resolution_fit()
        self._profile_list.clear()
        for p in SettingsManager.instance().launch_profiles:
            n = len(p.get("accounts", []))
            count_str = f"{n} account{'s' if n != 1 else ''}"
            res = p.get("resolution")
            res_str = f", {res[0]}×{res[1]}" if res else ""
            item = QListWidgetItem(f"{p['name']} ({count_str}{res_str})")
            item.setData(Qt.ItemDataRole.UserRole, p)
            self._profile_list.addItem(item)

    def _on_profile_selection_changed(self, row: int) -> None:
        has_sel = row >= 0
        self._profile_edit_btn.setEnabled(has_sel)
        self._profile_delete_btn.setEnabled(has_sel)

    def _selected_profile_name(self) -> str:
        """The profile the buttons and the context menu act on, "" if none is selected."""
        row = self._profile_list.currentRow()
        if row < 0:
            return ""
        return (self._profile_list.item(row).data(Qt.ItemDataRole.UserRole) or {}).get("name", "")

    def _on_profile_add(self) -> None:
        if add_launch_profile(self, self._live_game_area):
            self._refresh_profiles()

    def _on_profile_edit(self) -> None:
        if (name := self._selected_profile_name()) and edit_launch_profile(name, self, self._live_game_area):
            self._refresh_profiles()

    def _on_profile_duplicate(self) -> None:
        if (name := self._selected_profile_name()) and duplicate_launch_profile(name):
            self._refresh_profiles()

    def _on_profile_delete(self) -> None:
        if (name := self._selected_profile_name()) and delete_launch_profile(name, self):
            self._refresh_profiles()

    def _on_profile_context_menu(self, pos) -> None:
        """Right-click on a profile row. Duplicate lives here and nowhere else.

        It is the third verb on a card type that has three, and a fourth button under the
        list makes the two that matter harder to find. See ``_on_account_context_menu``
        for why the row under the cursor is selected first.
        """
        item = self._profile_list.itemAt(pos)
        if item is None:
            return
        self._profile_list.setCurrentItem(item)
        menu = popup_menu(self._profile_list)
        menu.addAction("Edit").triggered.connect(self._on_profile_edit)
        menu.addAction("Duplicate").triggered.connect(self._on_profile_duplicate)
        menu.addAction("Delete").triggered.connect(self._on_profile_delete)
        menu.exec(self._profile_list.viewport().mapToGlobal(pos))

    def apply(self) -> None:
        """No-op: AccountsTab saves immediately on each action."""
