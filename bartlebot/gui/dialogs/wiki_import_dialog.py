"""Dialog for importing drop items from a Wizard101 Central Wiki page."""

from __future__ import annotations

import asyncio
import logging

from bartleby.qt.theming.theme import THEME
from bartleby.qt.theming.themed import ThemedMixin
from bartleby.qt.widgets.buttons.action_button import ActionButton, ActionStyle
from bartleby.qt.widgets.labels.themed_label import ThemedTextLabel
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from bartlebot.bot.schema import DropModel
from bartlebot.game.wiki_browser import BrowserFetch, is_challenge
from bartlebot.game.wiki_drops import match_name_for
from bartlebot.game.wiki_pages import WIKI_HOST as _WIKI_HOST
from bartlebot.game.wiki_pages import page_title, wiki_page_path
from bartlebot.gui.widgets.layout.dialog_footer import dialog_footer

logger = logging.getLogger(__name__)

_WIKI_PREFIX = f"https://{_WIKI_HOST}/wiki/"

# Wiki namespaces that represent obtainable in-game items.
# "Item" covers most gear on Wizard101 Central Wiki (hats, robes, boots, athames, rings, wands…).
# "Jewel" covers socket jewels. "House" covers housing items and plantable seeds.
_ITEM_NAMESPACES = {
    "Item",
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
    "House",
    "Housing",
    "Jewel",
    "Reagent",
    "Snack",
    "TreasureCard",
    "TC",
    "Fish",
    "Seed",
    "Recipe",
}


# ---------------------------------------------------------------------------
# Background fetch
# ---------------------------------------------------------------------------


def _fetch_drops(url: str) -> dict[str, list[str]]:
    """Fetch and parse a wiki page: ``{category: [item_name, ...]}``. Blocking, so run off the GUI thread.

    An ``asyncio.to_thread`` call rather than a ``QThread``, as game art does: Nuitka
    documents Qt threading as unsupported under PyQt6, and the packaged build must not
    depend on it (``docs/RELEASING.md``).

    Raises :class:`WikiChallenged` when Cloudflare asks for a check this fetch cannot pass.
    """
    from curl_cffi import requests as cf_requests

    resp = cf_requests.get(url, timeout=15, impersonate="chrome124")
    if is_challenge(resp.status_code, resp.headers, resp.text):
        raise WikiChallenged(f"HTTP {resp.status_code}, cf-ray {resp.headers.get('cf-ray', '?')}")
    resp.raise_for_status()
    return _parse_drops(resp.text)


class WikiChallenged(Exception):
    """Cloudflare answered the fetch with a check only a person can pass (#278)."""


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------


def _is_item_link(href: str) -> bool:
    """Return True if *href* links to an obtainable in-game item page."""
    path = wiki_page_path(href)
    if path is None:
        return False
    page = path[len("/wiki/") :]
    ns = page.split(":")[0] if ":" in page else ""
    return ns in _ITEM_NAMESPACES


def _text_before_first_link(tag) -> str:
    """Return the text content of *tag* up to (not including) its first <a> child."""
    if tag is None:
        return ""
    from bs4 import NavigableString

    text = ""
    for child in tag.children:
        if hasattr(child, "name") and child.name == "a":
            break
        text += str(child) if isinstance(child, NavigableString) else child.get_text()
    return text.strip()


def _parse_drops(html: str) -> dict[str, list[str]]:
    """Parse a wiki creature page; return drops grouped by category.

    Tries the Wizard101 Central Wiki ``div.drop-category`` template first (where the
    category label sits in the next-sibling of the *preceding* drop-category div).
    Falls back to grouping items under the nearest preceding h2/h3/h4 heading for
    other wiki page formats.
    """
    from bs4 import BeautifulSoup, NavigableString, Tag

    soup = BeautifulSoup(html, "html.parser")
    content = soup.find("div", id="mw-content-text") or soup
    result: dict[str, list[str]] = {}

    # --- Primary: Wizard101 Central Wiki drop-category template ---
    drop_divs = content.find_all("div", class_="drop-category")
    if drop_divs:
        for i, div in enumerate(drop_divs):
            # The category label is the text-before-first-link in the next sibling
            # of the *previous* drop-category div (offset-by-one pattern in the wiki template).
            # For the first div use its own previous sibling as the label source.
            label_source = drop_divs[i - 1].find_next_sibling() if i > 0 else div.find_previous_sibling()
            cat_name = _text_before_first_link(label_source) or "Other"

            for a in div.find_all("a", href=True):
                name = a.get_text(strip=True)
                if name and _is_item_link(a.get("href", "")):
                    bucket = result.setdefault(cat_name, [])
                    if name not in bucket:
                        bucket.append(name)

        return {k: v for k, v in result.items() if v}

    # --- Fallback: heading-based traversal ---
    current_category = "Other"
    for elem in content.descendants:
        if isinstance(elem, NavigableString) or not isinstance(elem, Tag):
            continue
        if elem.name in ("h2", "h3", "h4", "h5"):
            text = elem.get_text(" ", strip=True).replace("[edit]", "").replace("[edit source]", "").strip()
            if text:
                current_category = text
        elif elem.name == "a":
            name = elem.get_text(strip=True)
            if name and _is_item_link(elem.get("href", "")):
                bucket = result.setdefault(current_category, [])
                if name not in bucket:
                    bucket.append(name)

    return result


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------


class WikiImportDialog(ThemedMixin, QDialog):
    """Modal dialog: paste a wiki URL, fetch the drop list, choose items to add."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import Drops from Wiki")
        self.setMinimumSize(480, 500)
        self.resize(540, 580)
        self._task: asyncio.Task | None = None
        self._browser: BrowserFetch | None = None
        self._closed = False
        self._selected: list[DropModel] = []
        self._page: str | None = None
        self._build_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def selected_items(self) -> list[str]:
        """Return the list of checked item names after the dialog is accepted."""
        return [entry.name for entry in self._selected]

    def selected_entries(self) -> list[DropModel]:
        """Return the checked items with the wiki category each was listed under."""
        return list(self._selected)

    def selected_page(self) -> str | None:
        """The title of the page these drops came from, for the caller to record.

        A title rather than the URL the user pasted — see bartlebot/game/wiki_pages.py.
        """
        return self._page

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # URL row
        url_row = QHBoxLayout()
        url_lbl = ThemedTextLabel("Wiki URL:", css=f"font-size: {THEME.font_small_px}px; font-weight: 600;")

        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText(f"{_WIKI_PREFIX}Creature:YourCreature")
        # A real second way forward rather than the commit — the commit is "Add N items"
        # below — so Fetch is the one SECONDARY on this dialog. The bare ``setDefault(False)``
        # this replaces said the same thing by negation. Not in the footer, so it is built
        # here rather than handed over as an extra.
        self._btn_fetch = ActionButton("download", "Fetch", style=ActionStyle.SECONDARY, fill=False)
        url_row.addWidget(url_lbl)
        url_row.addWidget(self._url_edit, 1)
        url_row.addWidget(self._btn_fetch)
        layout.addLayout(url_row)

        # Status
        # The color says whether the last message was an error, so the flag is what
        # apply_theme_styles re-derives from — not the stylesheet already on the label.
        self._status_error = False
        self._status_lbl = QLabel(f"Enter a {_WIKI_PREFIX}Creature:… URL and click Fetch.")
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
        # A pair of equal reads on the tree below, so neither takes a weight, and both
        # stay sized to their own text at the left of the row.
        self._btn_all = ActionButton("checkmark", "Select All", fill=False)
        self._btn_none = ActionButton("dismiss", "Select None", fill=False)
        sel_row.addWidget(self._btn_all)
        sel_row.addWidget(self._btn_none)
        sel_row.addStretch()
        layout.addLayout(sel_row)

        # Dialog buttons. The commit counts what is checked, so it starts disabled on
        # "Add 0 items" and is retitled by _on_item_changed.
        footer = dialog_footer(self, commit_text="Add 0 items", commit_icon="plus", on_commit=self._on_accept)
        self._ok_btn = footer.commit
        self._ok_btn.setEnabled(False)
        layout.addLayout(footer.layout)

        # Connections
        self._url_edit.returnPressed.connect(self._on_fetch)
        self._btn_fetch.clicked.connect(self._on_fetch)
        self._btn_all.clicked.connect(self._select_all)
        self._btn_none.clicked.connect(self._select_none)
        self._tree.itemChanged.connect(self._on_item_changed)

        self.refresh_theme_styles()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        # The thread runs on to the end of its request, but nothing it returns is shown.
        self._closed = True
        # A window open for the wiki's check closes with the dialog.
        if self._browser is not None:
            self._browser.cancel()
        if self._task is not None:
            self._task.cancel()
            self._task = None
        super().closeEvent(event)

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

        self._page = page_title(url)
        self._set_status("Fetching…")
        self._btn_fetch.setEnabled(False)
        self._tree.clear()
        self._ok_btn.setEnabled(False)
        self._ok_btn.setText("Add 0 items")

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._task = loop.create_task(self._fetch(url))

    async def _fetch(self, url: str) -> None:
        try:
            try:
                drops = await asyncio.to_thread(_fetch_drops, url)
            except WikiChallenged as exc:
                logger.info("The wiki's Cloudflare check challenged the fetch of %s (%s); opening a window", url, exc)
                drops = await self._fetch_in_window(url)
        except Exception as exc:
            logger.exception("Wiki fetch failed for %s", url)
            if not self._closed:
                self._on_fetch_error(str(exc))
            return
        if self._closed:
            return
        if drops is None:
            self._btn_fetch.setEnabled(True)
            self._set_status("The wiki window was closed before the page loaded. Click Fetch to try again.", error=True)
            return
        self._on_fetch_done(drops)

    async def _fetch_in_window(self, url: str) -> dict[str, list[str]] | None:
        """The drops from *url* loaded in a window where the user passes the check; None if closed first."""
        self._set_status(
            "The wiki asked to check that you're human. Finish its check in the window that opened, "
            "and the drops will load here."
        )
        self._browser = BrowserFetch(url)
        try:
            html = await asyncio.to_thread(self._browser.run)
        finally:
            self._browser = None
        return None if html is None else await asyncio.to_thread(_parse_drops, html)

    def _on_fetch_done(self, drops: dict[str, list[str]]) -> None:
        self._btn_fetch.setEnabled(True)
        if not drops:
            self._set_status("No recognizable item drops found on this page.", error=True)
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
            cat_item.setData(0, Qt.ItemDataRole.UserRole, category)
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
            # The header carries a count suffix for display; the category is stored
            # separately so it survives back into the bot's drop table.
            category = cat.data(0, Qt.ItemDataRole.UserRole)
            for j in range(cat.childCount()):
                child = cat.child(j)
                if child.checkState(0) == Qt.CheckState.Checked:
                    name = child.text(0)
                    # The wiki disambiguates variants the game shows identically, so
                    # record the in-game form now — the category is only in hand here.
                    self._selected.append(
                        DropModel(name=name, category=category, match_name=match_name_for(name, category))
                    )
        self.accept()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_url(url: str) -> str | None:
        """Return an error string, or None if the URL is acceptable.

        The fetch that follows impersonates Chrome against whatever this allows, so
        the host check has to be exact — see :func:`wiki_page_path`.
        """
        url = url.strip()
        if not url.lower().startswith("https://"):
            return "URL must start with https://"
        if wiki_page_path(url) is None:
            return f"URL must be an https://{_WIKI_HOST}/wiki/… page"
        return None

    def _set_status(self, msg: str, *, error: bool = False) -> None:
        self._status_error = error
        self._status_lbl.setText(msg)
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        color = THEME.error if self._status_error else THEME.text_muted
        self._status_lbl.setStyleSheet(f"color: {color}; font-size: {THEME.font_subtext_px}px;")
