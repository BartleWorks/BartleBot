"""Non-modal window (or modal picker dialog) for browsing and editing Combat Strategies."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.strategy_models import DuelCombatStrategy, PlayerCombatStrategy
from bartlebot.gui.strategy_library.duel_strategy_editor import DuelStrategyEditor
from bartlebot.gui.strategy_library.player_strategy_editor import PlayerStrategyEditor
from bartlebot.gui.theme import THEME
from bartlebot.strategy_store import (
    delete_duel_strategy,
    delete_player_strategy,
    list_duel_strategies,
    list_player_strategies,
    save_duel_strategy,
    save_player_strategy,
)


def _apply_list_style(list_widget: QListWidget) -> None:
    """Apply accent-border selection style to a plain QListWidget."""
    list_widget.setStyleSheet(
        f"QListWidget {{ border: 1px solid {THEME.border}; background: {THEME.bg}; }}"
        f"QListWidget::item {{ padding: 4px 6px; color: {THEME.text}; }}"
        f"QListWidget::item:hover {{ background: {THEME.node_fill}; }}"
        f"QListWidget::item:selected {{ background: {THEME.node_fill};"
        f" border-left: 2px solid {THEME.accent}; color: {THEME.text}; padding-left: 4px; }}"
        f"QListWidget::item:selected:hover {{ background: {THEME.node_fill}; }}"
    )


class StrategyLibraryWindow(QDialog):
    """Browse / edit window for PlayerCombatStrategy and DuelCombatStrategy files.

    Two modes:

    - ``mode="browse"`` (default): free-floating non-modal window, stays open, no
      selection buttons.  Used by the pencil/plus buttons in the Combat tab.
    - ``mode="pick"``: modal dialog with "Use selected", "Use built-in default" (when
      ``allow_none=True``), and "Cancel" buttons.  Call ``exec()`` and read
      ``selected_id()`` on accept.  Strategies may be created/edited freely while the
      dialog is open.

    Emits ``strategies_changed`` whenever any strategy is saved or deleted, so the
    Combat tab can refresh its chip labels.
    """

    strategies_changed = pyqtSignal()

    def __init__(
        self,
        mode: str = "browse",
        current_id: str | None = None,
        allow_none: bool = False,
        none_label: str = "BartleBot Default Combat Strategy",
        parent=None,
    ):
        if mode == "browse":
            super().__init__(parent, Qt.WindowType.Window)
        else:
            super().__init__(parent)

        self._mode = mode
        self._selected_id: str | None = current_id
        self._closed = False

        self.setWindowTitle("Strategy Library" if mode == "browse" else "Select Duel Strategy")
        self.setMinimumSize(620, 400)
        self.resize(720, 480)

        self._tabs = QTabWidget()
        self._pcs_tab = self._build_pcs_tab()
        self._dcs_tab = self._build_dcs_tab()
        self._tabs.addTab(self._pcs_tab, "Player Strategies")
        self._tabs.addTab(self._dcs_tab, "Duel Strategies")
        self._tabs.currentChanged.connect(self._on_tab_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addWidget(self._tabs)

        if mode != "browse":
            self._use_btn = QPushButton("Use selected")
            self._use_btn.setDefault(True)
            self._use_btn.setEnabled(False)
            self._use_btn.setStyleSheet(
                f"background: {THEME.accent}; color: #ffffff; padding: 5px 18px; border-radius: 4px; font-weight: bold;"
            )
            self._use_btn.clicked.connect(self._on_use)

            cancel_btn = QPushButton("Cancel")
            cancel_btn.clicked.connect(self.reject)

            btn_row = QHBoxLayout()
            if allow_none:
                none_btn = QPushButton("Use built-in default")
                none_btn.setToolTip(none_label)
                none_btn.clicked.connect(self._on_use_none)
                btn_row.addWidget(none_btn)
            btn_row.addStretch()
            btn_row.addWidget(cancel_btn)
            btn_row.addWidget(self._use_btn)
            layout.addLayout(btn_row)

            # Enable Use button only when a DCS item is highlighted; double-click accepts
            self._dcs_list.currentItemChanged.connect(self._on_pick_selection_changed)
            self._dcs_list.itemDoubleClicked.connect(lambda _item: self._on_use())

        self._reload_pcs()
        self._reload_dcs()

        if mode != "browse":
            # Switch to DCS tab and pre-select the current strategy
            self._tabs.setCurrentWidget(self._dcs_tab)
            if current_id:
                self.focus_duel_strategy(current_id)

    # ------------------------------------------------------------------
    # Pick-mode API
    # ------------------------------------------------------------------

    def selected_id(self) -> str | None:
        """Return the UUID accepted by the user, or None for built-in default."""
        return self._selected_id

    def _on_use(self) -> None:
        item = self._dcs_list.currentItem()
        if item:
            self._selected_id = item.data(0x100)
            self.accept()

    def _on_use_none(self) -> None:
        self._selected_id = None
        self.accept()

    def _on_pick_selection_changed(self, current: QListWidgetItem | None, _previous) -> None:
        self._use_btn.setEnabled(current is not None)

    # ------------------------------------------------------------------
    # PCS tab
    # ------------------------------------------------------------------

    def _build_pcs_tab(self) -> QWidget:
        tab = QWidget()
        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)

        # Left: list + buttons
        left = QWidget()
        left.setMinimumWidth(180)
        left.setMaximumWidth(280)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        self._pcs_filter = self._make_filter()
        self._pcs_filter.textChanged.connect(self._rebuild_pcs_list)
        left_layout.addWidget(self._pcs_filter)

        self._pcs_list = QListWidget()
        self._pcs_list.currentItemChanged.connect(self._on_pcs_selected)
        _apply_list_style(self._pcs_list)
        left_layout.addWidget(self._pcs_list, 1)

        btn_row = QHBoxLayout()
        new_btn = QPushButton("+ New")
        new_btn.clicked.connect(self._on_new_pcs)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self._on_delete_pcs)
        btn_row.addWidget(new_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        left_layout.addLayout(btn_row)

        splitter.addWidget(left)

        # Right: editor
        self._pcs_editor = PlayerStrategyEditor()
        self._pcs_editor.changed.connect(self._on_pcs_saved)
        self._pcs_empty_lbl = QLabel("Select a strategy to edit")
        self._pcs_empty_lbl.setAlignment(self._pcs_empty_lbl.alignment())

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.addWidget(self._pcs_empty_lbl)
        right_layout.addWidget(self._pcs_editor)
        self._pcs_editor.hide()
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.addWidget(splitter)
        return tab

    def _reload_pcs(self) -> None:
        self._all_pcs = list_player_strategies()
        self._rebuild_pcs_list()

    def _rebuild_pcs_list(self) -> None:
        query = self._pcs_filter.text().strip().lower()
        current_id = self._current_pcs_id()
        self._pcs_list.clear()
        for pcs in self._all_pcs:
            if query and query not in pcs.name.lower():
                continue
            item = QListWidgetItem(pcs.name)
            item.setData(0x100, pcs.id)
            self._pcs_list.addItem(item)
            if pcs.id == current_id:
                self._pcs_list.setCurrentItem(item)

    def _current_pcs_id(self) -> str | None:
        item = self._pcs_list.currentItem()
        return item.data(0x100) if item else None

    def _on_pcs_selected(self, item: QListWidgetItem | None) -> None:
        self._pcs_editor.flush()
        if item is None:
            self._pcs_editor.hide()
            self._pcs_empty_lbl.show()
            return
        uuid = item.data(0x100)
        for pcs in self._all_pcs:
            if pcs.id == uuid:
                self._pcs_editor.load(pcs)
                self._pcs_editor.show()
                self._pcs_empty_lbl.hide()
                return

    def _on_pcs_saved(self) -> None:
        current_item = self._pcs_list.currentItem()
        if current_item is None:
            return
        uuid = current_item.data(0x100)
        for pcs in self._all_pcs:
            if pcs.id == uuid:
                save_player_strategy(pcs)
                current_item.setText(pcs.name)
                self.strategies_changed.emit()
                return

    def _on_new_pcs(self) -> None:
        name, ok = QInputDialog.getText(self, "New Player Strategy", "Name:")
        if not ok or not name.strip():
            return
        pcs = PlayerCombatStrategy(name=name.strip(), dsl="")
        save_player_strategy(pcs)
        self._all_pcs.append(pcs)
        self._rebuild_pcs_list()
        # Select newly created item
        for i in range(self._pcs_list.count()):
            item = self._pcs_list.item(i)
            if item and item.data(0x100) == pcs.id:
                self._pcs_list.setCurrentItem(item)
                break
        self.strategies_changed.emit()

    def _on_delete_pcs(self) -> None:
        item = self._pcs_list.currentItem()
        if item is None:
            return
        uuid = item.data(0x100)
        ans = QMessageBox.question(
            self,
            "Delete Player Strategy",
            f"Delete '{item.text()}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        delete_player_strategy(uuid)
        self._all_pcs = [p for p in self._all_pcs if p.id != uuid]
        self._rebuild_pcs_list()
        self._pcs_editor.hide()
        self._pcs_empty_lbl.show()
        self.strategies_changed.emit()

    # ------------------------------------------------------------------
    # DCS tab
    # ------------------------------------------------------------------

    def _build_dcs_tab(self) -> QWidget:
        tab = QWidget()
        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)

        left = QWidget()
        left.setMinimumWidth(180)
        left.setMaximumWidth(280)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        self._dcs_filter = self._make_filter()
        self._dcs_filter.textChanged.connect(self._rebuild_dcs_list)
        left_layout.addWidget(self._dcs_filter)

        self._dcs_list = QListWidget()
        self._dcs_list.currentItemChanged.connect(self._on_dcs_selected)
        _apply_list_style(self._dcs_list)
        left_layout.addWidget(self._dcs_list, 1)

        btn_row = QHBoxLayout()
        new_btn = QPushButton("+ New")
        new_btn.clicked.connect(self._on_new_dcs)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self._on_delete_dcs)
        btn_row.addWidget(new_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        left_layout.addLayout(btn_row)

        splitter.addWidget(left)

        self._dcs_editor = DuelStrategyEditor()
        self._dcs_editor.changed.connect(self._on_dcs_saved)
        self._dcs_empty_lbl = QLabel("Select a strategy to edit")

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.addWidget(self._dcs_empty_lbl)
        right_layout.addWidget(self._dcs_editor)
        self._dcs_editor.hide()
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.addWidget(splitter)
        return tab

    def _reload_dcs(self) -> None:
        self._all_dcs = list_duel_strategies()
        self._rebuild_dcs_list()
        # DCS editor needs PCS list for assignment pickers
        if hasattr(self, "_dcs_editor"):
            self._dcs_editor.set_pcs_list(self._all_pcs)

    def _rebuild_dcs_list(self) -> None:
        query = self._dcs_filter.text().strip().lower()
        current_id = self._current_dcs_id()
        self._dcs_list.clear()
        for dcs in self._all_dcs:
            if query and query not in dcs.name.lower():
                continue
            item = QListWidgetItem(dcs.name)
            item.setData(0x100, dcs.id)
            self._dcs_list.addItem(item)
            if dcs.id == current_id:
                self._dcs_list.setCurrentItem(item)

    def _current_dcs_id(self) -> str | None:
        item = self._dcs_list.currentItem()
        return item.data(0x100) if item else None

    def _on_dcs_selected(self, item: QListWidgetItem | None) -> None:
        self._dcs_editor.flush()
        if item is None:
            self._dcs_editor.hide()
            self._dcs_empty_lbl.show()
            return
        uuid = item.data(0x100)
        for dcs in self._all_dcs:
            if dcs.id == uuid:
                self._dcs_editor.load(dcs, self._all_pcs)
                self._dcs_editor.show()
                self._dcs_empty_lbl.hide()
                return

    def _on_dcs_saved(self) -> None:
        current_item = self._dcs_list.currentItem()
        if current_item is None:
            return
        uuid = current_item.data(0x100)
        for dcs in self._all_dcs:
            if dcs.id == uuid:
                save_duel_strategy(dcs)
                current_item.setText(dcs.name)
                self.strategies_changed.emit()
                return

    def _on_new_dcs(self) -> None:
        name, ok = QInputDialog.getText(self, "New Duel Strategy", "Name:")
        if not ok or not name.strip():
            return
        dcs = DuelCombatStrategy(name=name.strip())
        save_duel_strategy(dcs)
        self._all_dcs.append(dcs)
        self._rebuild_dcs_list()
        for i in range(self._dcs_list.count()):
            item = self._dcs_list.item(i)
            if item and item.data(0x100) == dcs.id:
                self._dcs_list.setCurrentItem(item)
                break
        self.strategies_changed.emit()

    def _on_delete_dcs(self) -> None:
        item = self._dcs_list.currentItem()
        if item is None:
            return
        uuid = item.data(0x100)
        ans = QMessageBox.question(
            self,
            "Delete Duel Strategy",
            f"Delete '{item.text()}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        delete_duel_strategy(uuid)
        self._all_dcs = [d for d in self._all_dcs if d.id != uuid]
        self._rebuild_dcs_list()
        self._dcs_editor.hide()
        self._dcs_empty_lbl.show()
        self.strategies_changed.emit()

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _on_tab_changed(self, index: int) -> None:
        # When switching to DCS tab, ensure PCS list is up-to-date for assignment pickers
        if index == 1:
            self._dcs_editor.set_pcs_list(self._all_pcs)

    def _make_filter(self):
        from PyQt6.QtWidgets import QLineEdit  # noqa: PLC0415

        w = QLineEdit()
        w.setPlaceholderText("Filter…")
        return w

    def focus_duel_strategy(self, dcs_id: str) -> None:
        """Switch to the Duel Strategies tab and select the given strategy."""
        self._tabs.setCurrentWidget(self._dcs_tab)
        for i in range(self._dcs_list.count()):
            item = self._dcs_list.item(i)
            if item and item.data(0x100) == dcs_id:
                self._dcs_list.setCurrentItem(item)
                return

    def focus_player_strategy(self, pcs_id: str) -> None:
        """Switch to the Player Strategies tab and select the given strategy."""
        self._tabs.setCurrentWidget(self._pcs_tab)
        for i in range(self._pcs_list.count()):
            item = self._pcs_list.item(i)
            if item and item.data(0x100) == pcs_id:
                self._pcs_list.setCurrentItem(item)
                return

    def refresh(self) -> None:
        """Reload all strategy files from disk. Called when external changes occur."""
        self._reload_pcs()
        self._reload_dcs()

    def closeEvent(self, event) -> None:
        self._closed = True
        # Flush any pending edits before closing
        if hasattr(self, "_pcs_editor"):
            self._pcs_editor.flush()
        if hasattr(self, "_dcs_editor"):
            self._dcs_editor.flush()
        super().closeEvent(event)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            _apply_list_style(self._pcs_list)
            _apply_list_style(self._dcs_list)
