"""Dialog for importing drop items from a Wizard101 Central wiki page."""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from bartlebot.gui.theme import THEME

logger = logging.getLogger(__name__)

_WIKI_HOST = "wiki.wizard101central.com"
_WIKI_PREFIX = f"https://{_WIKI_HOST}/wiki/"

# Wiki namespaces that represent obtainable in-game items
_ITEM_NAMESPACES = {
    "Hat",
    "Robe",
    "Boot",
    "Boots",
    "Wand",
    "Athame",
    "Amulet",
    "Ring",
    "Deck",
    "Pet",
    "Mount",
    "Housing",
    "Reagent",
    "Snack",
    "TreasureCard",
    "TC",
    "Fish",
    "Seed",
    "Recipe",
}


# ---------------------------------------------------------------------------
# Background fetch worker
# ---------------------------------------------------------------------------


class _FetchWorker(QThread):
    """Fetches and parses a wiki page in a background thread."""

    finished = pyqtSignal(dict)  # {category: [item_name, ...]}
    error = pyqtSignal(str)

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self._url = url

    def run(self) -> None:
        try:
            import requests
            from bs4 import BeautifulSoup  # noqa: F401 — confirm dep available

            headers = {"User-Agent": "BartleBot/1.0 (wiki drop importer)"}
            resp = requests.get(self._url, timeout=15, headers=headers)
            resp.raise_for_status()
            drops = _parse_drops(resp.text)
            self.finished.emit(drops)
        except Exception as exc:
            logger.exception("Wiki fetch failed for %s", self._url)
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------


def _is_item_link(href: str) -> bool:
    """Return True if *href* links to an obtainable in-game item page."""
    path = href
    if path.startswith("http"):
        if _WIKI_HOST not in path:
            return False
        idx = path.find("/wiki/")
        if idx == -1:
            return False
        path = path[idx:]
    path = path.lstrip("/")
    if not path.startswith("wiki/"):
        return False
    page = path[len("wiki/") :]
    ns = page.split(":")[0] if ":" in page else ""
    return ns in _ITEM_NAMESPACES


def _parse_drops(html: str) -> dict[str, list[str]]:
    """Parse a wiki creature page; return drops grouped by nearest heading.

    Walks the MediaWiki content area collecting item links (detected by
    wiki namespace prefix) and groups them under the nearest preceding heading.
    Headings outside the Drops section that precede any items are kept;
    the caller's checklist lets the user filter unwanted groups.
    """
    from bs4 import BeautifulSoup, NavigableString, Tag

    soup = BeautifulSoup(html, "html.parser")
    content = soup.find("div", id="mw-content-text") or soup

    result: dict[str, list[str]] = {}
    current_category = "Other"

    for elem in content.descendants:
        if isinstance(elem, NavigableString):
            continue
        if not isinstance(elem, Tag):
            continue

        if elem.name in ("h2", "h3", "h4", "h5"):
            text = elem.get_text(" ", strip=True)
            # Strip MediaWiki edit-section links
            text = text.replace("[edit]", "").replace("[edit source]", "").strip()
            if text:
                current_category = text
        elif elem.name == "a":
            href = elem.get("href", "")
            name = elem.get_text(strip=True)
            if name and _is_item_link(href):
                bucket = result.setdefault(current_category, [])
                if name not in bucket:
                    bucket.append(name)

    return result


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------


class WikiImportDialog(QDialog):
    """Modal dialog: paste a wiki URL, fetch the drop list, choose items to add."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import Drops from Wiki")
        self.setMinimumSize(480, 500)
        self.resize(540, 580)
        self._worker: _FetchWorker | None = None
        self._selected: list[str] = []
        self._build_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def selected_items(self) -> list[str]:
        """Return the list of checked item names after the dialog is accepted."""
        return self._selected

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # URL row
        url_row = QHBoxLayout()
        url_lbl = QLabel("Wiki URL:")
        url_lbl.setStyleSheet(f"color: {THEME.text_muted}; font-size: 11px; font-weight: 600;")
        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText(f"{_WIKI_PREFIX}Creature:YourCreature")
        self._btn_fetch = QPushButton("Fetch")
        self._btn_fetch.setDefault(False)
        url_row.addWidget(url_lbl)
        url_row.addWidget(self._url_edit, 1)
        url_row.addWidget(self._btn_fetch)
        layout.addLayout(url_row)

        # Status
        self._status_lbl = QLabel(f"Enter a {_WIKI_PREFIX}Creature:… URL and click Fetch.")
        self._status_lbl.setStyleSheet(f"color: {THEME.text_muted}; font-size: 10px;")
        self._status_lbl.setWordWrap(True)
        layout.addWidget(self._status_lbl)

        # Category + item tree
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setRootIsDecorated(True)
        self._tree.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._tree)

        # Select all / none
        sel_row = QHBoxLayout()
        btn_style = (
            f"QPushButton {{ color: {THEME.accent}; font-size: 10px; padding: 0; border: none; }}"
            f"QPushButton:hover {{ color: {THEME.text}; }}"
        )
        self._btn_all = QPushButton("Select All")
        self._btn_none = QPushButton("Select None")
        for b in (self._btn_all, self._btn_none):
            b.setFlat(True)
            b.setStyleSheet(btn_style)
        sel_row.addWidget(self._btn_all)
        sel_row.addWidget(self._btn_none)
        sel_row.addStretch()
        layout.addLayout(sel_row)

        # Dialog buttons
        self._btn_box = QDialogButtonBox()
        self._ok_btn = self._btn_box.addButton("Add 0 items", QDialogButtonBox.ButtonRole.AcceptRole)
        self._btn_box.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._ok_btn.setEnabled(False)
        layout.addWidget(self._btn_box)

        # Connections
        self._url_edit.returnPressed.connect(self._on_fetch)
        self._btn_fetch.clicked.connect(self._on_fetch)
        self._btn_all.clicked.connect(self._select_all)
        self._btn_none.clicked.connect(self._select_none)
        self._tree.itemChanged.connect(self._on_item_changed)
        self._btn_box.accepted.connect(self._on_accept)
        self._btn_box.rejected.connect(self.reject)

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------

    def _on_fetch(self) -> None:
        url = self._url_edit.text().strip()
        if not url:
            return
        err = self._validate_url(url)
        if err:
            self._set_status(err, error=True)
            return

        self._set_status("Fetching…")
        self._btn_fetch.setEnabled(False)
        self._tree.clear()
        self._ok_btn.setEnabled(False)
        self._ok_btn.setText("Add 0 items")

        self._worker = _FetchWorker(url, parent=self)
        self._worker.finished.connect(self._on_fetch_done)
        self._worker.error.connect(self._on_fetch_error)
        self._worker.start()

    def _on_fetch_done(self, drops: dict[str, list[str]]) -> None:
        self._btn_fetch.setEnabled(True)
        if not drops:
            self._set_status("No recognisable item drops found on this page.", error=True)
            return
        total = sum(len(v) for v in drops.values())
        cats = len(drops)
        self._set_status(f"Found {total} item(s) across {cats} categor{'y' if cats == 1 else 'ies'}.")
        self._populate_tree(drops)

    def _on_fetch_error(self, msg: str) -> None:
        self._btn_fetch.setEnabled(True)
        self._set_status(f"Error: {msg}", error=True)

    # ------------------------------------------------------------------
    # Tree population and interaction
    # ------------------------------------------------------------------

    def _populate_tree(self, drops: dict[str, list[str]]) -> None:
        self._tree.blockSignals(True)
        self._tree.clear()
        for category, items in drops.items():
            cat_item = QTreeWidgetItem(self._tree, [f"{category}  ({len(items)})"])
            cat_item.setFlags(cat_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            cat_item.setCheckState(0, Qt.CheckState.Checked)
            for name in sorted(items, key=str.casefold):
                child = QTreeWidgetItem(cat_item, [name])
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(0, Qt.CheckState.Checked)
            cat_item.setExpanded(True)
        self._tree.blockSignals(False)
        self._update_ok_button()

    def _select_all(self) -> None:
        self._set_all_checked(Qt.CheckState.Checked)

    def _select_none(self) -> None:
        self._set_all_checked(Qt.CheckState.Unchecked)

    def _set_all_checked(self, state: Qt.CheckState) -> None:
        self._tree.blockSignals(True)
        for i in range(self._tree.topLevelItemCount()):
            cat = self._tree.topLevelItem(i)
            cat.setCheckState(0, state)
            for j in range(cat.childCount()):
                cat.child(j).setCheckState(0, state)
        self._tree.blockSignals(False)
        self._update_ok_button()

    def _on_item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        self._tree.blockSignals(True)
        if item.parent() is None:
            # Category toggled — propagate to children (skip PartiallyChecked, which is manual)
            state = item.checkState(0)
            if state != Qt.CheckState.PartiallyChecked:
                for j in range(item.childCount()):
                    item.child(j).setCheckState(0, state)
        else:
            # Child toggled — update parent to reflect children
            parent = item.parent()
            n = parent.childCount()
            checked = sum(1 for j in range(n) if parent.child(j).checkState(0) == Qt.CheckState.Checked)
            if checked == 0:
                parent.setCheckState(0, Qt.CheckState.Unchecked)
            elif checked == n:
                parent.setCheckState(0, Qt.CheckState.Checked)
            else:
                parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
        self._tree.blockSignals(False)
        self._update_ok_button()

    def _update_ok_button(self) -> None:
        n = self._count_checked()
        self._ok_btn.setText(f"Add {n} item{'s' if n != 1 else ''}")
        self._ok_btn.setEnabled(n > 0)

    def _count_checked(self) -> int:
        total = 0
        for i in range(self._tree.topLevelItemCount()):
            cat = self._tree.topLevelItem(i)
            for j in range(cat.childCount()):
                if cat.child(j).checkState(0) == Qt.CheckState.Checked:
                    total += 1
        return total

    def _on_accept(self) -> None:
        self._selected = []
        for i in range(self._tree.topLevelItemCount()):
            cat = self._tree.topLevelItem(i)
            for j in range(cat.childCount()):
                child = cat.child(j)
                if child.checkState(0) == Qt.CheckState.Checked:
                    self._selected.append(child.text(0))
        self.accept()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_url(url: str) -> str | None:
        """Return an error string, or None if the URL is acceptable."""
        if not url.startswith("https://"):
            return "URL must start with https://"
        if _WIKI_HOST not in url:
            return f"URL must be from {_WIKI_HOST}"
        if "/wiki/" not in url:
            return "URL must point to a wiki page (must contain /wiki/)"
        return None

    def _set_status(self, msg: str, *, error: bool = False) -> None:
        color = THEME.error if error else THEME.text_muted
        self._status_lbl.setStyleSheet(f"color: {color}; font-size: 10px;")
        self._status_lbl.setText(msg)
