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
        self._refresh()

    def apply(self) -> None:
        """No-op: AccountsTab saves immediately on each action."""
