"""Settings tab: Accounts (credential management with aliases)."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)

from bartlebot.credentials import CredentialStore
from bartlebot.settings_manager import SettingsManager


class _AccountDialog(QDialog):
    """Small dialog for adding or editing an account."""

    def __init__(self, username: str = "", alias: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Account")
        self.setMinimumWidth(300)

        form = QFormLayout()
        self._username_edit = QLineEdit(username)
        self._alias_edit    = QLineEdit(alias)
        self._alias_edit.setPlaceholderText("Optional — shown instead of username")
        self._password_edit = QLineEdit()
        self._password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        if username:
            self._password_edit.setPlaceholderText("Leave blank to keep existing password")

        form.addRow("Username:", self._username_edit)
        form.addRow("Alias:", self._alias_edit)
        form.addRow("Password:", self._password_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
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
    """Small dialog for adding or editing a launch profile."""

    def __init__(
        self,
        name: str = "",
        selected_usernames: list[str] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Launch Profile")
        self.setMinimumWidth(320)

        form = QFormLayout()
        self._name_edit = QLineEdit(name)
        form.addRow("Profile name:", self._name_edit)

        self._account_list = QListWidget()
        for acct in SettingsManager.instance().accounts:
            username = acct.get("username", "")
            alias    = acct.get("alias") or None
            item = QListWidgetItem(CredentialStore.display_name(username, alias))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setData(Qt.ItemDataRole.UserRole, username)
            item.setCheckState(
                Qt.CheckState.Checked
                if username in (selected_usernames or [])
                else Qt.CheckState.Unchecked
            )
            self._account_list.addItem(item)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("Accounts:"))
        layout.addWidget(self._account_list, 1)
        layout.addWidget(buttons)

    @property
    def name(self) -> str:
        return self._name_edit.text().strip()

    @property
    def selected_usernames(self) -> list[str]:
        result = []
        for i in range(self._account_list.count()):
            item = self._account_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                result.append(item.data(Qt.ItemDataRole.UserRole))
        return result


class AccountsTab(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        layout.addWidget(QLabel("Accounts (passwords stored in Windows Credential Manager):"))

        self._list = QListWidget()
        layout.addWidget(self._list, 1)

        btn_row = QHBoxLayout()
        self._add_btn    = QPushButton("Add")
        self._edit_btn   = QPushButton("Edit")
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

        self._profile_list = QListWidget()
        layout.addWidget(self._profile_list, 1)

        profile_btn_row = QHBoxLayout()
        self._profile_add_btn    = QPushButton("Add")
        self._profile_edit_btn   = QPushButton("Edit")
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

    def _refresh(self) -> None:
        self._list.clear()
        for acct in SettingsManager.instance().accounts:
            username = acct.get("username", "")
            alias    = acct.get("alias") or None
            display  = CredentialStore.display_name(username, alias)
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
        alias    = acct.get("alias", "")
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
            self, "Delete account",
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
            item = QListWidgetItem(f"{p['name']} ({n} account{'s' if n != 1 else ''})")
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
        profiles.append({"name": name, "accounts": dlg.selected_usernames})
        SettingsManager.instance().launch_profiles = profiles
        self._refresh_profiles()

    def _on_profile_edit(self) -> None:
        row = self._profile_list.currentRow()
        if row < 0:
            return
        profile  = self._profile_list.item(row).data(Qt.ItemDataRole.UserRole)
        old_name = profile.get("name", "")
        dlg = _ProfileDialog(
            name=old_name,
            selected_usernames=profile.get("accounts", []),
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
                p["name"]     = new_name
                p["accounts"] = dlg.selected_usernames
                break
        SettingsManager.instance().launch_profiles = profiles
        self._refresh_profiles()

    def _on_profile_delete(self) -> None:
        row = self._profile_list.currentRow()
        if row < 0:
            return
        profile = self._profile_list.item(row).data(Qt.ItemDataRole.UserRole)
        name    = profile.get("name", "")
        reply   = QMessageBox.question(
            self, "Delete profile",
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
