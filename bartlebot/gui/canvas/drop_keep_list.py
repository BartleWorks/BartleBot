"""Quicksell keep-list picker: the bot's drop table as a filterable, checkable table.

Rendered for the quicksell node's ``keep_list`` param (``ParamField.type ==
"drop_keep_list"``). The keyword line edit is the stored value; the table below it
shows what those keywords do to each drop the bot can farm.

Drop tables are large — the biggest shipped bot lists 597 items — so the table is
built for search-and-filter rather than scrolling, and it never rebuilds rows for a
filter change.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.schema import DropModel
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.drop_tree import UNCATEGORIZED_DISPLAY, CaseInsensitiveTreeItem
from bartlebot.nodes.utility import quicksell

# Bulk-checking more than this many rows asks first. A drop table can hold hundreds
# of names, and there is no undo inside the properties panel.
DROP_BULK_CONFIRM_THRESHOLD = 25

_COL_KEEP = 0
_COL_ITEM = 1
_COL_CATEGORY = 2

_TABLE_MAX_HEIGHT = 220


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------


def row_visible(
    name: str,
    category: str,
    kept: bool,
    query: str,
    category_filter: str | None,
    kept_only: bool,
) -> bool:
    """Whether one drop row survives the current filters.

    The filters compose rather than override: a query narrows within the selected
    category, and "show only kept" narrows within both.

    *category* is "" for an uncategorized drop. *category_filter* is None for
    "all categories", so "" is a selectable filter meaning uncategorized only.

    Categories quicksell never sells are left out unless the filter names that
    category specifically. They are a third of every categorized drop and 45% of
    the worst bot, and there is no decision to make about them — but selecting
    "Reagents" in the dropdown still shows them, so nothing is unreachable.

    An *unknown* category is never hidden this way: most of the library predates
    categories, and unknown is not the same as unsellable.

    The query test is deliberately the same case-insensitive substring rule as
    ``quicksell.matching_keyword``: the rows a search shows are exactly the drops
    that search text would keep if typed into the keep list.
    """
    if category in quicksell.NEVER_QUICKSOLD_CATEGORIES and category_filter != category:
        return False
    if kept_only and not kept:
        return False
    if category_filter is not None and category != category_filter:
        return False
    query = query.strip().lower()
    return not query or query in name.lower()


def bulk_needs_confirmation(count: int) -> bool:
    """Whether changing *count* rows at once is big enough to ask about first."""
    return count > DROP_BULK_CONFIRM_THRESHOLD


class _DropItem(CaseInsensitiveTreeItem):
    """Tree row that sorts by check state in the Keep column, by text elsewhere.

    Sorting by Keep is how "show me what I am keeping" works without a filter.
    """

    def __lt__(self, other: QTreeWidgetItem) -> bool:
        tree = self.treeWidget()
        if tree is not None and tree.sortColumn() == _COL_KEEP:
            return (self.checkState(_COL_KEEP) == Qt.CheckState.Checked) < (
                other.checkState(_COL_KEEP) == Qt.CheckState.Checked
            )
        return super().__lt__(other)


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------


class DropKeepListWidget(QWidget):
    """Keyword line edit plus the bot's drop table, checkable and filterable.

    Rows kept by something a checkbox cannot express — a partial keyword like
    "Amber" sparing "Amber Ring", or one of the sell_* bools — are checked but
    disabled, with a tooltip naming the keyword responsible.
    """

    changed = pyqtSignal()

    def __init__(self, get_drops: Callable[[], list[DropModel]], parent=None):
        super().__init__(parent)
        self._get_drops = get_drops
        # Sibling params (the sell_* bools) — supplied by the form on every edit,
        # since they contribute implied keywords to the keep list.
        self._context: dict[str, Any] = {}
        self._suppress = False
        self._rows: list[_DropItem] = []
        self._kept_total = (0, 0, 0)  # kept, sellable, never-quicksold

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)

        self._edit = QLineEdit()
        self._edit.setPlaceholderText("e.g. ItemA, ItemB")
        self._edit.textChanged.connect(self._on_text_edited)
        outer.addWidget(self._edit)

        # Two lines: the rule, which never changes, and the live count under it.
        self._rule = QLabel()
        self._rule.setWordWrap(True)
        outer.addWidget(self._rule)

        self._summary = QLabel()
        self._summary.setWordWrap(True)
        outer.addWidget(self._summary)

        outer.addLayout(self._build_filter_row())

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Keep", "Item", "Category"])
        self._tree.setRootIsDecorated(False)
        self._tree.setUniformRowHeights(True)
        self._tree.setSortingEnabled(True)
        self._tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._tree.setMaximumHeight(_TABLE_MAX_HEIGHT)
        header = self._tree.header()
        header.setSectionResizeMode(_COL_KEEP, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(_COL_ITEM, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_COL_CATEGORY, QHeaderView.ResizeMode.Fixed)
        self._tree.setColumnWidth(_COL_KEEP, 42)
        self._tree.setColumnWidth(_COL_CATEGORY, 92)
        self._tree.itemChanged.connect(self._on_item_changed)
        outer.addWidget(self._tree)

        self._empty = QLabel()
        self._empty.setWordWrap(True)
        self._empty.setVisible(False)
        outer.addWidget(self._empty)

        outer.addLayout(self._build_bulk_row())

        self._apply_styles()

    def _build_filter_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search drops…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        row.addWidget(self._search, 1)

        self._category = QComboBox()
        self._category.currentIndexChanged.connect(self._apply_filter)
        row.addWidget(self._category)

        self._kept_only = QCheckBox("Kept only")
        self._kept_only.setToolTip("Show only the drops this quicksell node keeps.")
        self._kept_only.checkStateChanged.connect(self._apply_filter)
        row.addWidget(self._kept_only)
        return row

    def _build_bulk_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self._btn_check_all = QPushButton("Check all shown")
        self._btn_uncheck_all = QPushButton("Uncheck all shown")
        for btn in (self._btn_check_all, self._btn_uncheck_all):
            btn.setFlat(True)
            row.addWidget(btn)
        self._btn_check_all.clicked.connect(lambda: self._on_bulk(keep=True))
        self._btn_uncheck_all.clicked.connect(lambda: self._on_bulk(keep=False))
        row.addStretch()
        return row

    # ------------------------------------------------------------------
    # Form integration
    # ------------------------------------------------------------------

    def get_value(self) -> list[str]:
        return [x.strip().strip("'\"") for x in self._edit.text().strip("[] ").split(",") if x.strip().strip("'\"")]

    def set_value(self, value: list[str]) -> None:
        self._suppress = True
        self._edit.setText(", ".join(str(v) for v in value) if isinstance(value, list) else str(value or ""))
        self._suppress = False
        self.refresh()

    def set_context(self, params: dict) -> None:
        """Supply the node's other params, whose sell_* bools imply extra keywords."""
        self._context = dict(params)
        self.refresh()

    def refresh(self) -> None:
        """Bring the table in line with the current drop table and keep list.

        Rows are only rebuilt when the drop table itself changes; otherwise their
        state is updated in place, so a filter or a tick never costs a teardown.
        """
        entries = self._get_drops()
        if [(row.text(_COL_ITEM), row.data(_COL_CATEGORY, Qt.ItemDataRole.UserRole)) for row in self._rows] != [
            (entry.name, entry.category or "") for entry in entries
        ]:
            self._rebuild_rows(entries)
        self._apply_states(entries)
        self._apply_filter()

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------

    def _rebuild_rows(self, entries: list[DropModel]) -> None:
        self._suppress = True
        self._tree.setSortingEnabled(False)
        self._tree.clear()
        self._rows = []
        for entry in entries:
            category = entry.category or ""
            row = _DropItem(["", entry.name, category or UNCATEGORIZED_DISPLAY])
            row.setData(_COL_CATEGORY, Qt.ItemDataRole.UserRole, category)
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(_COL_KEEP, Qt.CheckState.Unchecked)
            self._tree.addTopLevelItem(row)
            self._rows.append(row)
        self._tree.setSortingEnabled(True)
        self._suppress = False
        self._rebuild_category_filter(entries)

    def _rebuild_category_filter(self, entries: list[DropModel]) -> None:
        """Repopulate the category dropdown, keeping the current selection if it survives."""
        previous = self._category.currentData()
        counts: dict[str, int] = {}
        for entry in entries:
            counts[entry.category or ""] = counts.get(entry.category or "", 0) + 1

        # The default entry counts what it actually shows — never-sold categories are
        # filtered out of it, so labelling it with the full drop total would lie.
        never = sum(n for c, n in counts.items() if c in quicksell.NEVER_QUICKSOLD_CATEGORIES)
        default_label = f"All sellable ({len(entries) - never})" if never else f"All categories ({len(entries)})"

        self._category.blockSignals(True)
        self._category.clear()
        self._category.addItem(default_label, None)
        for category in sorted(counts, key=lambda c: (c == "", c.casefold())):
            label = category or "Uncategorized"
            self._category.addItem(f"{label} ({counts[category]})", category)
        index = self._category.findData(previous)
        self._category.setCurrentIndex(max(index, 0))
        self._category.blockSignals(False)

    def _apply_states(self, entries: list[DropModel]) -> None:
        has_drops = bool(entries)
        self._tree.setVisible(has_drops)
        self._rule.setVisible(has_drops)
        for widget in (self._search, self._category, self._kept_only, self._btn_check_all, self._btn_uncheck_all):
            widget.setVisible(has_drops)
        if not has_drops:
            self._summary.setText("This bot has no drops listed. Add some in Possible Drops to pick them here.")
            self._empty.setVisible(False)
            return
        # A bot whose drops are all uncategorized has nothing to choose between:
        # "All categories" plus one entry is not a choice.
        self._category.setVisible(self._category.count() > 2)

        states = quicksell.drop_keep_states(
            [entry.name for entry in entries], {**self._context, "keep_list": self.get_value()}
        )
        by_name = {state.name: state for state in states}

        self._suppress = True
        kept = sellable = 0
        for row in self._rows:
            state = by_name.get(row.text(_COL_ITEM))
            if state is None:
                continue
            never_reason = quicksell.NEVER_QUICKSOLD_CATEGORIES.get(
                row.data(_COL_CATEGORY, Qt.ItemDataRole.UserRole) or ""
            )
            if never_reason:
                # Hidden by default; shown checked and inert if the user filters to
                # this category, because the item does survive — it is just never
                # the keep list's doing.
                row.setCheckState(_COL_KEEP, Qt.CheckState.Checked)
                row.setFlags(row.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                row.setToolTip(_COL_ITEM, never_reason)
                continue

            sellable += 1
            kept += bool(state.kept)
            row.setCheckState(_COL_KEEP, Qt.CheckState.Checked if state.kept else Qt.CheckState.Unchecked)
            locked = state.kept and not state.exact
            flags = row.flags()
            row.setFlags(flags & ~Qt.ItemFlag.ItemIsEnabled if locked else flags | Qt.ItemFlag.ItemIsEnabled)
            if locked:
                row.setToolTip(
                    _COL_ITEM, f"Kept by the keyword {state.keyword!r}, not by its own name — change that above."
                )
            else:
                row.setToolTip(_COL_ITEM, "Sell this item." if state.kept else "Keep this item when quickselling.")
        self._suppress = False

        self._kept_total = (kept, sellable, len(states) - sellable)
        self._render_summary()

    def _render_summary(self) -> None:
        kept, sellable, never = self._kept_total
        # The denominator is the sellable drops, not every drop: counting items
        # quicksell can never touch would make the number meaningless on bots
        # where they are half the table.
        text = f"Currently keeping {self._emphasis(kept)} out of {self._emphasis(sellable)} sellable drops"
        if never:
            text += f" · {self._emphasis(never)} never quicksold"
        shown = sum(1 for row in self._rows if not row.isHidden())
        if shown != sellable:
            text += f" · {self._emphasis(shown)} shown"
        self._summary.setText(text)

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def _apply_filter(self, *_) -> None:
        query = self._search.text()
        category_filter = self._category.currentData()
        kept_only = self._kept_only.isChecked()
        for row in self._rows:
            row.setHidden(
                not row_visible(
                    row.text(_COL_ITEM),
                    row.data(_COL_CATEGORY, Qt.ItemDataRole.UserRole) or "",
                    row.checkState(_COL_KEEP) == Qt.CheckState.Checked,
                    query,
                    category_filter,
                    kept_only,
                )
            )
        if self._rows:
            self._render_summary()
            nothing_shown = all(row.isHidden() for row in self._rows)
            self._empty.setVisible(nothing_shown)
            self._empty.setText("No drops match these filters.")
            self._tree.setVisible(not nothing_shown)

    def visible_rows(self) -> list[str]:
        """Names of the drops currently passing the filters — public for tests."""
        return [row.text(_COL_ITEM) for row in self._rows if not row.isHidden()]

    # ------------------------------------------------------------------
    # Editing
    # ------------------------------------------------------------------

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._suppress or column != _COL_KEEP:
            return
        keep = item.checkState(_COL_KEEP) == Qt.CheckState.Checked
        self._set_keep_list(quicksell.toggle_drop_in_keep_list(self.get_value(), item.text(_COL_ITEM), keep))

    def _on_bulk(self, *, keep: bool) -> None:
        """Check or uncheck every visible, unlocked row.

        Locked rows are skipped: their fate is decided by a keyword or a sell_*
        bool, which a checkbox cannot rewrite. Hidden rows are never touched.
        """
        names = [
            row.text(_COL_ITEM)
            for row in self._rows
            if not row.isHidden()
            and bool(row.flags() & Qt.ItemFlag.ItemIsEnabled)
            and (row.checkState(_COL_KEEP) == Qt.CheckState.Checked) != keep
        ]
        if not names or not self._confirm_bulk(len(names), keep=keep):
            return
        self._set_keep_list(quicksell.set_drops_in_keep_list(self.get_value(), names, keep))

    def _confirm_bulk(self, count: int, *, keep: bool) -> bool:
        """Ask before a large bulk change. Safe to block: this runs in a Qt slot.

        Never call this from inside an async function — see CLAUDE.md's qasync
        re-entrancy rule. It is reached only from a button's clicked signal.
        """
        if not bulk_needs_confirmation(count):
            return True
        verb = "Keep" if keep else "Sell"
        box = QMessageBox(self)
        box.setWindowTitle(f"{verb} all {count} drops?")
        box.setText(f"This {'adds' if keep else 'removes'} {count} entries {'to' if keep else 'from'} the keep list.")
        if keep:
            box.setInformativeText(
                "Searching for a word these items share and typing it in the Keep List keeps them all "
                "with one keyword, and survives a wiki re-import."
            )
        box.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.button(QMessageBox.StandardButton.Ok).setText(f"{verb} all {count}")
        return box.exec() == QMessageBox.StandardButton.Ok

    def _set_keep_list(self, keep_list: list[str]) -> None:
        self._suppress = True
        self._edit.setText(", ".join(keep_list))
        self._suppress = False
        self._apply_states(self._get_drops())
        self._apply_filter()
        self.changed.emit()

    def _on_text_edited(self) -> None:
        if self._suppress:
            return
        self.refresh()
        self.changed.emit()

    # ------------------------------------------------------------------
    # Theming
    # ------------------------------------------------------------------

    @staticmethod
    def _emphasis(text: object) -> str:
        """Bold *text* at full text colour, for use inside the muted summary lines.

        The colour has to be inline: a Qt style sheet selects widgets, so it cannot
        reach the <b> spans inside a QLabel's rich text.
        """
        return f'<b style="color: {THEME.text}">{text}</b>'

    def _apply_styles(self) -> None:
        line = f"color: {THEME.text_muted}; font-size: 10px;"
        self._rule.setStyleSheet(line)
        self._summary.setStyleSheet(line)
        self._empty.setStyleSheet(line)
        self._search.setStyleSheet(
            f"padding: 2px 6px; border: 1px solid {THEME.border}; border-radius: 4px; font-size: 11px;"
        )
        self._tree.setStyleSheet(
            f"QTreeWidget {{ border: 1px solid {THEME.border}; font-size: 11px; background: {THEME.bg};"
            f" alternate-background-color: {THEME.surface}; }}"
            " QTreeWidget::item { padding: 2px 4px; }"
        )
        btn_style = (
            f"QPushButton {{ color: {THEME.accent}; font-size: 10px; padding: 0; border: none; }}"
            f"QPushButton:hover {{ color: {THEME.text}; }}"
        )
        self._btn_check_all.setStyleSheet(btn_style)
        self._btn_uncheck_all.setStyleSheet(btn_style)
        self._rule.setText(f"Checked drops are {self._emphasis('kept')}, unchecked drops are {self._emphasis('sold')}")

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._apply_styles()
            # The summary's bold spans carry an inline colour, so re-render them too.
            self.refresh()
