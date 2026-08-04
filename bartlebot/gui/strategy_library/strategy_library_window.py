"""Non-modal window (or modal picker dialog) for browsing and editing Combat Strategies."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.strategy_models import DeckPreset, DuelCombatStrategy, PlayerCombatStrategy
from bartlebot.gui.strategy_library.duel_strategy_editor import DuelStrategyEditor
from bartlebot.gui.strategy_library.player_strategy_editor import PlayerStrategyEditor
from bartlebot.gui.theme import THEME
from bartlebot.strategy_store import (
    delete_deck_preset,
    delete_duel_strategy,
    delete_player_strategy,
    list_deck_presets,
    list_duel_strategies,
    list_player_strategies,
    save_deck_preset,
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


class _DeckDetailPanel(QWidget):
    """Read-only panel showing the card contents of a DeckPreset, with an editable name."""

    renamed = pyqtSignal(str)  # new name

    def __init__(self, parent=None):
        super().__init__(parent)
        self._preset: DeckPreset | None = None
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(500)
        self._debounce.timeout.connect(self._flush_rename)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(8)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit()
        self._name_edit.textEdited.connect(lambda _: self._debounce.start())
        name_row.addWidget(self._name_edit, 1)
        layout.addLayout(name_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._cards_widget = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_widget)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(4)
        scroll.setWidget(self._cards_widget)
        layout.addWidget(scroll, 1)

    def load(self, preset) -> None:
        self._preset = preset
        self._name_edit.setText(preset.name)
        self._rebuild_cards()

    def _rebuild_cards(self) -> None:
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        if self._preset is None:
            return

        # Treasure cards are omitted — never captured or applied, so showing
        # them would misrepresent what the preset restores.
        deck = self._preset.deck
        any_section = False
        for section_name, cards in (("Normal Cards", deck.normal), ("Item Cards", deck.item)):
            if not cards:
                continue
            any_section = True
            self._cards_layout.addWidget(QLabel(f"<b>{section_name}</b>"))
            for name, count in sorted(cards.items()):
                self._cards_layout.addWidget(QLabel(f"  {name} × {count}"))

        if not any_section:
            self._cards_layout.addWidget(QLabel("(empty deck)"))

        self._cards_layout.addStretch()

    def _flush_rename(self) -> None:
        name = self._name_edit.text().strip()
        if name and self._preset is not None and name != self._preset.name:
            self.renamed.emit(name)


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
        self._decks_tab = self._build_decks_tab()
        self._tabs.addTab(self._pcs_tab, "Player Strategies")
        self._tabs.addTab(self._dcs_tab, "Duel Strategies")
        self._tabs.addTab(self._decks_tab, "Deck Presets")
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
        self._reload_decks()

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

    def _on_pcs_saved(self, uuid: str) -> None:
        """Persist the strategy the editor was editing, identified by ``uuid``.

        Deliberately does *not* consult ``currentItem()``: selecting a different
        strategy flushes the editor, and by then the list has already moved on,
        so the pending edits would be written to the newly selected file.
        """
        pcs = next((p for p in self._all_pcs if p.id == uuid), None)
        if pcs is None:
            return  # deleted out from under a pending save — nothing to resurrect
        save_player_strategy(pcs)
        item = self._find_item(self._pcs_list, uuid)
        if item is not None:
            item.setText(pcs.name)
        self.strategies_changed.emit()

    def _on_new_pcs(self) -> None:
        name, ok = QInputDialog.getText(self, "New Player Strategy", "Name:")
        if not ok or not name.strip():
            return
        pcs = PlayerCombatStrategy(name=name.strip(), dsl="")
        save_player_strategy(pcs)
        self._all_pcs.append(pcs)
        self._rebuild_pcs_list()
        item = self._find_item(self._pcs_list, pcs.id)
        if item is not None:
            self._pcs_list.setCurrentItem(item)
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
        # Detach the editor first: a debounce timer still pending here would fire
        # after the unlink and write the file straight back.
        self._pcs_editor.clear()
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
        self._dcs_editor.presets_changed.connect(self._on_presets_changed)
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

    def _on_presets_changed(self) -> None:
        """A DCS assignment row promoted its inline DSL into a new preset."""
        self._reload_pcs()
        self._dcs_editor.set_pcs_list(self._all_pcs)
        self.strategies_changed.emit()

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

    def _on_dcs_saved(self, uuid: str) -> None:
        """Persist the strategy the editor was editing — see ``_on_pcs_saved``."""
        dcs = next((d for d in self._all_dcs if d.id == uuid), None)
        if dcs is None:
            return  # deleted out from under a pending save
        save_duel_strategy(dcs)
        item = self._find_item(self._dcs_list, uuid)
        if item is not None:
            item.setText(dcs.name)
        self.strategies_changed.emit()

    def _on_new_dcs(self) -> None:
        name, ok = QInputDialog.getText(self, "New Duel Strategy", "Name:")
        if not ok or not name.strip():
            return
        dcs = DuelCombatStrategy(name=name.strip())
        save_duel_strategy(dcs)
        self._all_dcs.append(dcs)
        self._rebuild_dcs_list()
        item = self._find_item(self._dcs_list, dcs.id)
        if item is not None:
            self._dcs_list.setCurrentItem(item)
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
        # See _on_delete_pcs — detach before unlinking so no pending save resurrects it.
        self._dcs_editor.clear()
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

    @staticmethod
    def _find_item(list_widget: QListWidget, uuid: str) -> QListWidgetItem | None:
        """Return the row carrying ``uuid``, or None if it is filtered out or gone."""
        for i in range(list_widget.count()):
            item = list_widget.item(i)
            if item is not None and item.data(0x100) == uuid:
                return item
        return None

    def _make_filter(self):
        from PyQt6.QtWidgets import QLineEdit  # noqa: PLC0415

        w = QLineEdit()
        w.setPlaceholderText("Filter…")
        return w

    def focus_duel_strategy(self, dcs_id: str) -> None:
        """Switch to the Duel Strategies tab and select the given strategy."""
        self._tabs.setCurrentWidget(self._dcs_tab)
        item = self._find_item(self._dcs_list, dcs_id)
        if item is not None:
            self._dcs_list.setCurrentItem(item)

    def focus_player_strategy(self, pcs_id: str) -> None:
        """Switch to the Player Strategies tab and select the given strategy."""
        self._tabs.setCurrentWidget(self._pcs_tab)
        item = self._find_item(self._pcs_list, pcs_id)
        if item is not None:
            self._pcs_list.setCurrentItem(item)

    def refresh(self) -> None:
        """Reload all strategy files from disk. Called when external changes occur."""
        self._reload_pcs()
        self._reload_dcs()
        self._reload_decks()

    def reload_decks(self) -> None:
        """Reload only the deck presets list. Called after an import/save from the Combat tab."""
        self._reload_decks()

    # ------------------------------------------------------------------
    # Deck Presets tab
    # ------------------------------------------------------------------

    def _build_decks_tab(self) -> QWidget:
        tab = QWidget()
        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)

        left = QWidget()
        left.setMinimumWidth(180)
        left.setMaximumWidth(280)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        self._decks_filter = self._make_filter()
        self._decks_filter.textChanged.connect(self._rebuild_decks_list)
        left_layout.addWidget(self._decks_filter)

        self._decks_list = QListWidget()
        self._decks_list.currentItemChanged.connect(self._on_deck_selected)
        _apply_list_style(self._decks_list)
        left_layout.addWidget(self._decks_list, 1)

        btn_row = QHBoxLayout()
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self._on_delete_deck)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        left_layout.addLayout(btn_row)

        splitter.addWidget(left)

        # Right: deck details panel
        self._deck_detail = _DeckDetailPanel()
        self._deck_detail.renamed.connect(self._on_deck_renamed)
        self._deck_empty_lbl = QLabel("Select a deck preset to view")
        self._deck_empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.addWidget(self._deck_empty_lbl)
        right_layout.addWidget(self._deck_detail)
        self._deck_detail.hide()
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.addWidget(splitter)
        return tab

    def _reload_decks(self) -> None:
        self._all_decks = list_deck_presets()
        self._rebuild_decks_list()

    def _rebuild_decks_list(self) -> None:
        query = self._decks_filter.text().strip().lower()
        current_id = self._current_deck_id()
        self._decks_list.clear()
        for preset in self._all_decks:
            if query and query not in preset.name.lower():
                continue
            total = preset.deck.total_cards()
            label = f"{preset.name}  ({total} cards)" if total else preset.name
            item = QListWidgetItem(label)
            item.setData(0x100, preset.id)
            self._decks_list.addItem(item)
            if preset.id == current_id:
                self._decks_list.setCurrentItem(item)

    def _current_deck_id(self) -> str | None:
        item = self._decks_list.currentItem()
        return item.data(0x100) if item else None

    def _on_deck_selected(self, item: QListWidgetItem | None) -> None:
        if item is None:
            self._deck_detail.hide()
            self._deck_empty_lbl.show()
            return
        uuid = item.data(0x100)
        for preset in self._all_decks:
            if preset.id == uuid:
                self._deck_detail.load(preset)
                self._deck_detail.show()
                self._deck_empty_lbl.hide()
                return

    def _on_deck_renamed(self, new_name: str) -> None:
        item = self._decks_list.currentItem()
        if item is None:
            return
        uuid = item.data(0x100)
        for preset in self._all_decks:
            if preset.id == uuid:
                preset.name = new_name
                save_deck_preset(preset)
                self._rebuild_decks_list()
                self.strategies_changed.emit()
                return

    def _on_delete_deck(self) -> None:
        item = self._decks_list.currentItem()
        if item is None:
            return
        uuid = item.data(0x100)
        ans = QMessageBox.question(
            self,
            "Delete Deck Preset",
            f"Delete '{item.text().split('  (')[0]}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        delete_deck_preset(uuid)
        self._all_decks = [d for d in self._all_decks if d.id != uuid]
        self._rebuild_decks_list()
        self._deck_detail.hide()
        self._deck_empty_lbl.show()
        self.strategies_changed.emit()

    def focus_deck_preset(self, preset_id: str) -> None:
        """Switch to the Deck Presets tab and select the given preset."""
        self._tabs.setCurrentWidget(self._decks_tab)
        item = self._find_item(self._decks_list, preset_id)
        if item is not None:
            self._decks_list.setCurrentItem(item)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def flush_editors(self) -> None:
        """Commit any debounced edits in either strategy editor immediately.

        Public so the owner can flush before the app quits — closing the window
        is not the only way edits go out of scope.
        """
        if hasattr(self, "_pcs_editor"):
            self._pcs_editor.flush()
        if hasattr(self, "_dcs_editor"):
            self._dcs_editor.flush()

    def closeEvent(self, event) -> None:
        self._closed = True
        self.flush_editors()
        super().closeEvent(event)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            _apply_list_style(self._pcs_list)
            _apply_list_style(self._dcs_list)
            _apply_list_style(self._decks_list)
