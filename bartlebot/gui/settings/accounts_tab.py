"""Settings tab: Game (accounts, launch profiles, and game embed resolution)."""

from __future__ import annotations

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

from bartlebot.credentials import CredentialStore
from bartlebot.gui.widgets.themed_label import ThemedTextLabel
from bartlebot.settings_manager import VALID_RESOLUTIONS, SettingsManager


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
    """Small dialog for adding or editing an account."""

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
    ) -> None:
        super().__init__(parent)
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

        # --- OK / Cancel ---
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Populate list with pre-selected accounts
        for username in selected_usernames or []:
            self._add_account_item(username)

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

    def _on_remove(self) -> None:
        row = self._account_list.currentRow()
        if row >= 0:
            self._account_list.takeItem(row)

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


class AccountsTab(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
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
            "Set Wizard101 to Windowed mode in Options → Graphics, with this resolution.",
            css="font-size: 11px;",
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._res_combo.currentIndexChanged.connect(self._on_resolution_changed)

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

        self._refresh_profiles()

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def _on_resolution_changed(self) -> None:
        resolution = self._res_combo.currentData()
        if resolution:
            SettingsManager.instance().game_resolution = resolution
            SettingsManager.instance().save()

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

    def _on_add(self) -> None:
        dlg = _AccountDialog(parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        username = dlg.username
        if not username:
            QMessageBox.warning(self, "Invalid", "Username cannot be empty.")
            return
        password = dlg.password
        if not password:
            QMessageBox.warning(self, "Invalid", "Password cannot be empty for a new account.")
            return
        accounts = SettingsManager.instance().accounts
        if any(a["username"] == username for a in accounts):
            QMessageBox.warning(self, "Duplicate", f"Account '{username}' already exists.")
            return
        CredentialStore.store(username, password)
        accounts.append({"username": username, "alias": dlg.alias or ""})
        SettingsManager.instance().accounts = accounts
        self._refresh()

    def _on_edit(self) -> None:
        row = self._list.currentRow()
        if row < 0:
            return
        acct = self._list.item(row).data(Qt.ItemDataRole.UserRole)
        username = acct.get("username", "")
        alias = acct.get("alias", "")
        dlg = _AccountDialog(username=username, alias=alias, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_password = dlg.password
        if new_password:
            CredentialStore.store(username, new_password)
        accounts = SettingsManager.instance().accounts
        for a in accounts:
            if a["username"] == username:
                a["alias"] = dlg.alias
                break
        SettingsManager.instance().accounts = accounts
        self._refresh()

    def _on_delete(self) -> None:
        row = self._list.currentRow()
        if row < 0:
            return
        acct = self._list.item(row).data(Qt.ItemDataRole.UserRole)
        username = acct.get("username", "")
        reply = QMessageBox.question(
            self,
            "Delete account",
            f"Remove '{username}' and its stored password?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        CredentialStore.delete(username)
        accounts = [a for a in SettingsManager.instance().accounts if a["username"] != username]
        SettingsManager.instance().accounts = accounts
        # Remove the deleted account from all profiles
        profiles = SettingsManager.instance().launch_profiles
        for p in profiles:
            p["accounts"] = [u for u in p.get("accounts", []) if u != username]
        SettingsManager.instance().launch_profiles = profiles
        self._refresh()
        self._refresh_profiles()

    # ------------------------------------------------------------------
    # Launch Profiles
    # ------------------------------------------------------------------

    def _refresh_profiles(self) -> None:
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

    def _on_profile_add(self) -> None:
        dlg = _ProfileDialog(parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name = dlg.name
        if not name:
            QMessageBox.warning(self, "Invalid", "Profile name cannot be empty.")
            return
        profiles = SettingsManager.instance().launch_profiles
        if any(p["name"] == name for p in profiles):
            QMessageBox.warning(self, "Duplicate", f"Profile '{name}' already exists.")
            return
        profile: dict = {"name": name, "accounts": dlg.selected_usernames}
        if dlg.resolution is not None:
            profile["resolution"] = list(dlg.resolution)
        profiles.append(profile)
        SettingsManager.instance().launch_profiles = profiles
        self._refresh_profiles()

    def _on_profile_edit(self) -> None:
        row = self._profile_list.currentRow()
        if row < 0:
            return
        profile = self._profile_list.item(row).data(Qt.ItemDataRole.UserRole)
        old_name = profile.get("name", "")
        raw_res = profile.get("resolution")
        existing_res = tuple(raw_res) if raw_res else None
        dlg = _ProfileDialog(
            name=old_name,
            selected_usernames=profile.get("accounts", []),
            resolution=existing_res,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_name = dlg.name
        if not new_name:
            QMessageBox.warning(self, "Invalid", "Profile name cannot be empty.")
            return
        profiles = SettingsManager.instance().launch_profiles
        if new_name != old_name and any(p["name"] == new_name for p in profiles):
            QMessageBox.warning(self, "Duplicate", f"Profile '{new_name}' already exists.")
            return
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
        self._refresh_profiles()

    def _on_profile_delete(self) -> None:
        row = self._profile_list.currentRow()
        if row < 0:
            return
        profile = self._profile_list.item(row).data(Qt.ItemDataRole.UserRole)
        name = profile.get("name", "")
        reply = QMessageBox.question(
            self,
            "Delete profile",
            f"Remove launch profile '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        profiles = [p for p in SettingsManager.instance().launch_profiles if p["name"] != name]
        SettingsManager.instance().launch_profiles = profiles
        self._refresh_profiles()

    def apply(self) -> None:
        """No-op: AccountsTab saves immediately on each action."""
