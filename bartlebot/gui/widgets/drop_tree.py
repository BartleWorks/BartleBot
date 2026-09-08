"""Shared pieces for the two tree views that list a bot's drops.

The Possible Drops sidebar and the quicksell keep-list picker show the same rows
from different angles, so they agree on how a drop sorts and on how a missing
category reads.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QTreeWidgetItem

#: Shown in the Category column for a drop that has none — every drop imported
#: before categories existed, and anything added by hand.
UNCATEGORIZED_DISPLAY = "—"


class CaseInsensitiveTreeItem(QTreeWidgetItem):
    """Tree row that sorts by text without regard to case.

    Qt's default compares raw strings, which files every lowercase name after
    every uppercase one — "amber" lands below "Zafaria" in a list of item names.
    """

    def __lt__(self, other: QTreeWidgetItem) -> bool:
        tree = self.treeWidget()
        column = tree.sortColumn() if tree is not None else 0
        return self.text(column).casefold() < other.text(column).casefold()


class CategoryThenNameTreeItem(CaseInsensitiveTreeItem):
    """Row that breaks a Category tie by Item name, so the table reads as groups.

    Sorting by category alone leaves the rows inside each group in whatever order
    they were inserted, which across a 600-entry drop table is no order at all —
    you can see where a category starts but not find anything in it. Comparing
    ``(category, name)`` makes the column do the grouping it exists for.

    Uncategorized rows sort as a group of their own, last in ascending order,
    rather than wherever the em dash happens to land in the collation order.

    Only the category column is affected: clicking Item still sorts by name alone,
    exactly as before.

    The two drop tables put these columns in different places, so the indices are
    class attributes for subclasses to override.
    """

    NAME_COLUMN = 0
    CATEGORY_COLUMN = 1

    def sort_key(self) -> tuple[int, str, str]:
        """(uncategorized-last, category, name), all case-insensitive."""
        category = self.text(self.CATEGORY_COLUMN)
        return (
            1 if category == UNCATEGORIZED_DISPLAY else 0,
            category.casefold(),
            self.text(self.NAME_COLUMN).casefold(),
        )

    def __lt__(self, other: QTreeWidgetItem) -> bool:
        tree = self.treeWidget()
        column = tree.sortColumn() if tree is not None else 0
        if column == self.CATEGORY_COLUMN and isinstance(other, CategoryThenNameTreeItem):
            return self.sort_key() < other.sort_key()
        return super().__lt__(other)
