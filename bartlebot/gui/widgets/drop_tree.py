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
