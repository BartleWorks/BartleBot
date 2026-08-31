"""Unified bot canvas — single QGraphicsScene with Run and Edit modes.

BotCanvas  — top-level widget; owns the scene, toolbars, and properties panel.
"""

import enum
import json
import logging
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QEvent, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QKeySequence,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.schema import BotConfig
from bartlebot.bot_tags import BOT_TAGS, TAG_GROUPS, normalize_tags, tag_group
from bartlebot.gui.canvas.edge_item import EdgeItem
from bartlebot.gui.canvas.edit_scene import (
    EditScene,
    _EditView,
    _inject_required_options,
    _node_returns,
    _reachable_states,
    _validate_node,
)
from bartlebot.gui.canvas.favorites_bar import FavoritesBar
from bartlebot.gui.canvas.graph_view import _MinimapView
from bartlebot.gui.canvas.node_item import NodeItem
from bartlebot.gui.canvas.param_panel import _ParamPanel
from bartlebot.gui.canvas.undo_manager import UndoManager
from bartlebot.gui.icon_utils import svg_icon
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.search_field import SearchLineEdit
from bartlebot.gui.widgets.tag_picker import TagPickerButton, group_accent
from bartlebot.logging_setup import GUI_ONLY
from bartlebot.nodes.registry import NODE_SPECS
from bartlebot.paths import project_root
from bartlebot.settings_manager import SettingsManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TB_RUN = 0  # toolbar stack index for Run mode
_TB_EDIT = 1  # toolbar stack index for Edit mode

_IDLE = "idle"
_RUNNING = "running"
_PAUSED = "paused"


def _status_dot_color(state: str) -> str:
    if state == _RUNNING:
        return THEME.success
    if state == _PAUSED:
        return THEME.status_paused
    return THEME.text_muted  # idle / fallback


_ICON_SIZE = QSize(20, 20)
_BTN_SIZE = QSize(32, 32)


def _icon_btn(icon_name: str, tooltip: str) -> QPushButton:
    """Create a fixed-size icon-only toolbar button."""
    btn = QPushButton()
    btn.setIcon(svg_icon(icon_name))
    btn.setIconSize(_ICON_SIZE)
    btn.setFixedSize(_BTN_SIZE)
    btn.setToolTip(tooltip)
    return btn


class BotMode(enum.Enum):
    RUN = "run"
    EDIT = "edit"


# ---------------------------------------------------------------------------
# Bot library helpers — used by _BotLibraryPanel
# ---------------------------------------------------------------------------


@dataclass
class _BotMeta:
    name: str
    description: str
    authors: str
    drops: list[str]
    tags: list[str]
    start_location: str
    version: str
    path: Path


def _drop_names(raw: object) -> list[str]:
    """Drop names from a bot file's raw ``drops``, in either storage form.

    Read outside Pydantic, so it has to handle the pre-category ``["Amber"]`` shape
    as well as ``[{"name": "Amber", "category": "Reagents"}]``. The library card only
    counts and searches names, so the category is not carried here.
    """
    if not isinstance(raw, list):
        return []
    names = []
    for entry in raw:
        if isinstance(entry, str):
            names.append(entry)
        elif isinstance(entry, dict) and isinstance(entry.get("name"), str):
            names.append(entry["name"])
    return names


def _read_bot_meta(path: Path) -> "_BotMeta | None":
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return _BotMeta(
            name=data.get("name") or path.stem,
            description=data.get("description") or "",
            authors=data.get("authors") or "",
            drops=_drop_names(data.get("drops")),
            tags=normalize_tags(data.get("tags")),
            start_location=data.get("start_location") or "",
            version=data.get("version") or "",
            path=path,
        )
    except Exception:
        return None


def _resolve_bots_dir(configured: str, root: Path) -> Path:
    """Resolve the configured bot library path against the project root.

    The setting (default ``"bots"``) is meant relative to the project, not to
    wherever the process happened to be launched from — resolving it against
    the cwd silently emptied the library for anyone who launched elsewhere.
    An absolute override is returned unchanged.
    """
    configured_path = Path(configured)
    return configured_path if configured_path.is_absolute() else root / configured_path


def _format_category(dir_name: str) -> str:
    """'01_wizard_city' → 'Wizard City'."""
    name = re.sub(r"^\d+_", "", dir_name)
    return name.replace("_", " ").title()


def filter_bot_metas(metas: list[_BotMeta], query: str, tags: set[str]) -> tuple[list[_BotMeta], dict[Path, list[str]]]:
    """Narrow *metas* by free text and by selected tag chips.

    Returns the surviving metas and, for each one the text matched by drop name, the
    drops that matched — the library card shows those so a hit on a 300-entry drop
    list explains itself.

    Tags **OR within a group and AND across groups**: "Gear or Jewels, *and* Dungeon".
    That is the faceted model two labelled buttons imply, and it is the only one that
    stays useful — the groups answer different questions (:mod:`bartlebot.bot_tags`:
    Rewards is what a run yields, Activity is what it does), so ANDing across them
    narrows, while ANDing *within* one asks for a bot that drops both Gear and Jewels
    and usually returns nothing. Cross-group pairs like "Dungeon + Spellements" behave
    exactly as they did when every tag ANDed.

    The text query is an OR across the fields it searches, tags included, and
    intersects whatever the tags left.

    Pure, and at module level, so the filtering can be tested without a QApplication
    — same reasoning as ``_resolve_bots_dir``.
    """
    q = query.strip().casefold()
    wanted: dict[str, set[str]] = {}
    for t in tags:
        wanted.setdefault(tag_group(t), set()).add(t)

    results: list[_BotMeta] = []
    matched_drops: dict[Path, list[str]] = {}
    for m in metas:
        if any(group_tags.isdisjoint(m.tags) for group_tags in wanted.values()):
            continue
        drop_matches = [d for d in m.drops if q in d.casefold()]
        if q and not (
            q in m.name.casefold()
            or q in m.description.casefold()
            or q in m.authors.casefold()
            or any(q in t.casefold() for t in m.tags)
            or drop_matches
        ):
            continue
        results.append(m)
        if q and drop_matches:
            matched_drops[m.path] = drop_matches
    return results, matched_drops


def _pill(text: str, bg: str, fg: str) -> QLabel:
    """Small rounded caption used for a bot card's version and tags."""
    lbl = QLabel(text)
    lbl.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    lbl.setStyleSheet(
        f"background: {bg}; color: {fg};font-size: 9px; font-weight: bold; border-radius: 7px; padding: 1px 6px;"
    )
    lbl.setFixedHeight(14)
    return lbl


class _BotCard(QWidget):
    """Single clickable bot entry shown in the library's right panel."""

    open_requested = pyqtSignal(str)  # emits path string

    def __init__(self, meta: "_BotMeta", matched_drops: "list[str] | None" = None, parent=None) -> None:
        super().__init__(parent)
        self._meta = meta
        self._matched_drops = matched_drops
        self.setObjectName("BotCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._set_normal_style()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(2)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)

        name_lbl = QLabel(self._meta.name or "(untitled)")
        f = name_lbl.font()
        f.setBold(True)
        name_lbl.setFont(f)
        name_lbl.setStyleSheet(f"color: {THEME.text}; background: transparent;")
        header.addWidget(name_lbl, 0)

        # Neutral grey, not the accent it used to be. Colour on this card now means
        # "tag group", and a version is not one — spending the accent on it made the
        # loudest pill the one carrying the least information.
        if self._meta.version:
            pill = _pill(f"v{self._meta.version}", THEME.chip_neutral_bg, THEME.pill_text)
            header.addWidget(pill, 0, Qt.AlignmentFlag.AlignVCenter)

        # One palette for a tag everywhere it appears: the dot in the picker, the chip
        # in the filter bar, and this pill. Someone who filters by Rewards and scans
        # the results should be matching a colour, not re-reading every pill.
        for tag in self._meta.tags:
            header.addWidget(
                _pill(tag, group_accent(tag_group(tag)), THEME.pill_text),
                0,
                Qt.AlignmentFlag.AlignVCenter,
            )

        header.addStretch()
        layout.addLayout(header)

        if self._meta.description:
            desc_lbl = QLabel(self._meta.description)
            desc_lbl.setWordWrap(True)
            desc_lbl.setStyleSheet(f"color: {THEME.text_muted}; font-size: 10px; background: transparent;")
            layout.addWidget(desc_lbl)

        parts: list[str] = []
        if self._meta.authors:
            parts.append(f"by {self._meta.authors}")
        n = len(self._meta.drops)
        if n:
            parts.append(f"{n} drop{'s' if n != 1 else ''}")
        if parts:
            meta_lbl = QLabel("  ·  ".join(parts))
            meta_lbl.setStyleSheet(f"color: {THEME.text_subtle}; font-size: 10px; background: transparent;")
            layout.addWidget(meta_lbl)

        if self._matched_drops:
            shown = self._matched_drops[:3]
            text = ", ".join(shown)
            if len(self._matched_drops) > 3:
                text += f" +{len(self._matched_drops) - 3} more"
            match_lbl = QLabel(f"Drops: {text}")
            match_lbl.setWordWrap(True)
            match_lbl.setStyleSheet(f"color: {THEME.accent}; font-size: 10px; background: transparent;")
            layout.addWidget(match_lbl)

    def _set_normal_style(self) -> None:
        # Use #BotCard selector so border/background don't bleed into child labels
        self.setStyleSheet(
            f"#BotCard {{ background: {THEME.surface}; border: 1px solid {THEME.border}; border-radius: 4px; }}"
        )

    def _set_hover_style(self) -> None:
        self.setStyleSheet(
            f"#BotCard {{ background: {THEME.node_fill}; border: 1px solid {THEME.accent}; border-radius: 4px; }}"
        )

    def enterEvent(self, event) -> None:
        self._set_hover_style()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._set_normal_style()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        self.open_requested.emit(str(self._meta.path.resolve()))
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# _BotLibraryPanel — shown when no bot file is open
# ---------------------------------------------------------------------------


class _BotLibraryPanel(QWidget):
    """Bot library panel shown in BotCanvas when no bot file is open.

    Two-panel layout: left category list (Recent + world subdirs from bots/),
    right scrollable bot cards. Search bar filters across all categories.
    """

    new_bot_requested = pyqtSignal()
    open_bot_requested = pyqtSignal()
    open_recent_requested = pyqtSignal(str)  # path

    _ALL = "All"
    _RECENT = "Recent"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._all_metas: list[_BotMeta] = []
        self._category_map: dict[str, list[_BotMeta]] = {}
        self._recent_metas: list[_BotMeta] = []
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top bar: search + action buttons ────────────────────────────
        top = QWidget()
        top.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        top.setStyleSheet(f"background: {THEME.surface}; border-bottom: 1px solid {THEME.border};")
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(8, 6, 8, 6)
        top_layout.setSpacing(6)

        # Filters first, then search, then actions — the bar reads left to right as
        # "narrow the list … then act on it". The tag picker used to sit between the
        # search box and these buttons, where it was read as a third action and was
        # the last place anyone looked for a filter.
        self._pickers: dict[str, TagPickerButton] = {}
        for group in TAG_GROUPS:
            picker = TagPickerButton(title=group, placeholder=group, group=group)
            picker.setToolTip(f"Filter by {group.lower()} tag")
            picker.selection_changed.connect(self._on_tag_selection_changed)
            self._pickers[group] = picker
            top_layout.addWidget(picker, 0)

        # Stretch 1, not a fixed width flanked by two stretches: the search field then
        # fills whatever the two clusters leave, so it *sits* centred without being
        # anchored there, and narrowing the window shortens it instead of colliding.
        self._search = SearchLineEdit("Search bots by name, author, or drop…")
        self._search.setClearButtonEnabled(True)
        top_layout.addWidget(self._search, 1)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"color: {THEME.border};")
        top_layout.addWidget(sep)

        # Same shape and height as the pickers, so the bar looks like one set of
        # controls — but New Bot keeps the accent, because a bar with four identical
        # buttons has undone the grouping the separator just drew.
        for label, signal_name, primary in (
            ("New Bot", "new_bot_requested", True),
            # Not "Open…": in this panel every card already opens a bot, so the name
            # has to say what is different about this one, which is that it takes a
            # file from anywhere rather than an entry in the library.
            ("Open Bot File…", "open_bot_requested", False),
        ):
            btn = QPushButton(label)
            border = THEME.accent if primary else THEME.border
            color = THEME.accent if primary else THEME.text
            btn.setStyleSheet(
                f"QPushButton {{ background: {THEME.surface}; color: {color};"
                f"  border: 1px solid {border}; border-radius: 3px; padding: 3px 8px; }}"
                f"QPushButton:hover {{ border-color: {THEME.accent}; color: {THEME.accent}; }}"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(getattr(self, signal_name))
            top_layout.addWidget(btn)

        root.addWidget(top)

        # ── Active tag filters, shown as chips you can click to remove ───
        # Only the *selected* tags live here; the vocabulary itself is behind the
        # picker. Eighteen chips laid out at once needed 1648px and pinned the
        # panel's minimum width above what a 1366px laptop can give it.
        self._chip_bar = QWidget()
        self._chip_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._chip_bar.setStyleSheet(f"background: {THEME.surface}; border-bottom: 1px solid {THEME.border};")
        self._chip_layout = QHBoxLayout(self._chip_bar)
        self._chip_layout.setContentsMargins(8, 5, 8, 5)
        self._chip_layout.setSpacing(4)
        self._chip_layout.addStretch()
        root.addWidget(self._chip_bar)

        # ── Body: left category list + right card area ───────────────────
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # Left: category list
        self._cat_list = QListWidget()
        self._cat_list.setFixedWidth(155)
        self._cat_list.setStyleSheet(
            f"QListWidget {{ background: {THEME.surface}; border: none;"
            f"  border-right: 1px solid {THEME.border}; font-size: 11px; }}"
            f"QListWidget::item {{ padding: 5px 10px; color: {THEME.text_muted}; }}"
            f"QListWidget::item:selected {{ background: {THEME.node_fill_selected}; color: {THEME.text}; }}"
        )
        body.addWidget(self._cat_list)

        # Right: scrollable bot cards
        self._cards_area = QScrollArea()
        self._cards_area.setWidgetResizable(True)
        self._cards_area.setFrameShape(QFrame.Shape.NoFrame)
        self._cards_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._cards_area.setStyleSheet(f"background: {THEME.bg};")
        self._cards_container = QWidget()
        self._cards_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._cards_container.setStyleSheet(f"background: {THEME.bg};")
        self._cards_layout = QVBoxLayout(self._cards_container)
        self._cards_layout.setContentsMargins(10, 10, 10, 10)
        self._cards_layout.setSpacing(6)
        self._cards_layout.addStretch()
        self._cards_area.setWidget(self._cards_container)
        body.addWidget(self._cards_area, 1)

        body_w = QWidget()
        body_w.setLayout(body)
        root.addWidget(body_w, 1)

        self._search.textChanged.connect(lambda _text: self._apply_filter())
        self._cat_list.currentRowChanged.connect(self._on_category_row_changed)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Rescan recent files and bots/ directory, rebuild UI."""
        sm = SettingsManager.instance()
        recent_paths = sm.recent_bot_files

        # Scan bots/ subdirectories
        self._all_metas = []
        self._category_map = {}

        bots_dir = _resolve_bots_dir(sm.get("canvas", "bot_library_path", default="bots"), project_root())
        if bots_dir.is_dir():
            for subdir in sorted(bots_dir.iterdir()):
                if not subdir.is_dir():
                    continue
                cat_name = _format_category(subdir.name)
                metas = [m for f in sorted(subdir.glob("*.json")) if (m := _read_bot_meta(f))]
                if metas:
                    self._category_map[cat_name] = metas
                    self._all_metas.extend(metas)
            root_metas = [m for f in sorted(bots_dir.glob("*.json")) if (m := _read_bot_meta(f))]
            if root_metas:
                self._category_map.setdefault("Other", []).extend(root_metas)
                self._all_metas.extend(root_metas)

        # Recent files
        self._recent_metas = [m for p in recent_paths if (m := _read_bot_meta(Path(p)))]

        self._refresh_tag_counts()
        self._rebuild_chips()

        # Rebuild category list
        self._cat_list.blockSignals(True)
        self._cat_list.clear()
        # "All" first and always present. Without it there was no way to ask the
        # library a question that crossed worlds, which is exactly what a tag filter
        # is for — and no way back to the whole list once a world was clicked.
        self._cat_list.addItem(self._ALL)
        if self._recent_metas:
            self._cat_list.addItem(self._RECENT)
        for cat in self._category_map:
            self._cat_list.addItem(cat)
        self._cat_list.blockSignals(False)

        # Select first category (triggers card refresh)
        if self._cat_list.count() > 0:
            self._cat_list.setCurrentRow(0)
            self._on_category_row_changed(0)
        else:
            self._show_cards([])

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _refresh_tag_counts(self) -> None:
        """Feed the picker how many bots carry each tag.

        A tag with no bots still appears, showing 0 — the picker is the only place
        the vocabulary is visible, and a silently absent row reads as a bug rather
        than as an answer.
        """
        counts = Counter(t for m in self._all_metas for t in m.tags)
        per_tag = {tag: counts.get(tag, 0) for tag in BOT_TAGS}
        for picker in self._pickers.values():
            picker.set_counts(per_tag)

    def _rebuild_chips(self) -> None:
        """Show one removable chip per *selected* tag; hide the bar when none are."""
        while self._chip_layout.count() > 1:
            item = self._chip_layout.takeAt(0)
            if w := item.widget():
                w.hide()
                w.deleteLater()

        selected = self._selected_tag_list()
        for tag in selected:
            chip = QPushButton(f"{tag}  ✕")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip(f"Remove the {tag} filter")
            # Coloured by group, matching the dot in the popup and the button it came
            # from — with two pickers feeding one chip bar, a chip has to say which.
            chip.setStyleSheet(
                f"QPushButton {{ background: {group_accent(tag_group(tag))}; color: {THEME.pill_text};"
                f"  border: none; border-radius: 9px;"
                f"  padding: 2px 9px; font-size: 10px; font-weight: bold; }}"
                f"QPushButton:hover {{ background: {THEME.edge_selected}; }}"
            )
            chip.clicked.connect(lambda _checked=False, t=tag: self._remove_tag(t))
            self._chip_layout.insertWidget(self._chip_layout.count() - 1, chip)

        if selected:
            clear = QPushButton("Clear")
            clear.setCursor(Qt.CursorShape.PointingHandCursor)
            clear.setFlat(True)
            clear.setStyleSheet(
                f"QPushButton {{ color: {THEME.text_muted}; border: none;"
                f"  padding: 2px 6px; font-size: 10px; }}"
                f"QPushButton:hover {{ color: {THEME.text}; }}"
            )
            clear.clicked.connect(lambda: self._set_tag_selection([]))
            self._chip_layout.insertWidget(self._chip_layout.count() - 1, clear)

        self._chip_bar.setVisible(bool(selected))

    def _remove_tag(self, tag: str) -> None:
        self._set_tag_selection([t for t in self._selected_tag_list() if t != tag])

    def _set_tag_selection(self, tags: list[str]) -> None:
        """Change the selection and re-filter — set_selection does not emit.

        Takes the whole selection across both pickers and routes each tag back to the
        group it belongs to, so a chip's ✕ and the Clear button do not have to know
        which button produced which chip.
        """
        for group, picker in self._pickers.items():
            picker.set_selection([t for t in tags if tag_group(t) == group])
        self._rebuild_chips()
        self._apply_filter()

    def _on_tag_selection_changed(self, _keys: list) -> None:
        self._rebuild_chips()
        self._apply_filter()

    def _selected_tag_list(self) -> list[str]:
        """Every selected tag across the pickers, in ``BOT_TAGS`` order."""
        chosen = self._selected_tags()
        return [t for t in BOT_TAGS if t in chosen]

    def _selected_tags(self) -> set[str]:
        return {t for picker in self._pickers.values() for t in picker.tags()}

    def _on_category_row_changed(self, row: int) -> None:
        """Switch category, keeping the filters.

        This used to clear them, because a filter *replaced* the listing rather than
        narrowing it, so a category click with a tag still selected would have looked
        like it did nothing. Now that the two compose, throwing away a selection the
        user built is just losing their work — and with ``All`` present there is an
        obvious way to ask the same question across every world.
        """
        if row < 0:
            return
        self._apply_filter()

    def _category_metas(self) -> "list[_BotMeta]":
        """The bots the selected category offers, before search and tags narrow them."""
        row = self._cat_list.currentRow()
        if row < 0:
            return []
        cat = self._cat_list.item(row).text()
        if cat == self._ALL:
            return self._all_metas
        if cat == self._RECENT:
            return self._recent_metas
        return self._category_map.get(cat, [])

    def _apply_filter(self) -> None:
        """The single place cards are chosen.

        Category, search and tags all **narrow**, in that order: the category picks
        the set, and the other two cut it down. That is the faceted model the left
        list already implies — a world is one more facet — and it is what makes
        ``All`` meaningful rather than decorative.
        """
        results, matched_drops = filter_bot_metas(self._category_metas(), self._search.text(), self._selected_tags())
        self._show_cards(results, matched_drops)

    def _show_cards(self, metas: "list[_BotMeta]", matched_drops: "dict | None" = None) -> None:
        layout = self._cards_layout
        # Remove all widgets, keep the trailing stretch (last item).
        # hide() is called before deleteLater() so the widget disappears immediately
        # rather than remaining visible until the next event loop cycle.
        while layout.count() > 1:
            item = layout.takeAt(0)
            if w := item.widget():
                w.hide()
                w.deleteLater()

        if not metas:
            # "All" is always in the list now, so its presence no longer distinguishes
            # "nothing matched" from "the library is empty". The metas do.
            hint = QLabel("No bots match these filters." if self._all_metas else "No bots found in bots/ directory.")
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint.setStyleSheet(f"color: {THEME.text_muted}; font-size: 11px;")
            layout.insertWidget(0, hint)
            return

        for i, meta in enumerate(metas):
            drops = (matched_drops or {}).get(meta.path)
            card = _BotCard(meta, matched_drops=drops)
            card.open_requested.connect(self.open_recent_requested)
            layout.insertWidget(i, card)


# ---------------------------------------------------------------------------
# _SearchBar — floating quick-search overlay (Ctrl+F)
# ---------------------------------------------------------------------------


class _SearchBar(QWidget):
    """Floating search bar overlaid on the graph view (Ctrl+F to open)."""

    text_changed = pyqtSignal(str)
    next_match = pyqtSignal()
    prev_match = pyqtSignal()
    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"QWidget {{ background-color: {THEME.surface}; border: 1px solid {THEME.border}; border-radius: 6px; }}"
            f"QLineEdit {{ border: none; background: transparent; color: {THEME.text}; font-size: 13px; }}"
            f"QPushButton {{ border: none; background: transparent; color: {THEME.text_muted}; font-size: 14px; }}"
            f"QPushButton:hover {{ color: {THEME.text}; }}"
            f"QLabel {{ border: none; color: {THEME.text_muted}; font-size: 11px; }}"
        )

        self._input = SearchLineEdit("Search states…")
        self._input.setFixedWidth(200)
        self._input.installEventFilter(self)
        self._input.textChanged.connect(self.text_changed)

        self._count_lbl = QLabel()

        close_btn = QPushButton("×")
        close_btn.setFixedSize(22, 22)
        close_btn.clicked.connect(self.closed)

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 6, 4)
        row.setSpacing(6)
        row.addWidget(self._input)
        row.addWidget(self._count_lbl)
        row.addWidget(close_btn)
        self.adjustSize()
        self.setVisible(False)

    def set_count(self, current: int, total: int) -> None:
        self._count_lbl.setText(
            "" if total == 0 and not self._input.text() else "no matches" if total == 0 else f"{current}/{total}"
        )
        self.adjustSize()

    def focus_input(self) -> None:
        self._input.setFocus()
        self._input.selectAll()

    def current_text(self) -> str:
        return self._input.text()

    def eventFilter(self, obj, event) -> bool:
        if obj is self._input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.closed.emit()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Down):
                self.next_match.emit()
                return True
            if key == Qt.Key.Key_Up:
                self.prev_match.emit()
                return True
        return super().eventFilter(obj, event)


# ---------------------------------------------------------------------------
# BotCanvas — unified widget
# ---------------------------------------------------------------------------


class BotCanvas(QWidget):
    """Single canvas widget covering both Run and Edit modes."""

    config_changed = pyqtSignal()
    mode_changed = pyqtSignal(BotMode)
    run_requested = pyqtSignal()
    pause_requested = pyqtSignal()
    resume_requested = pyqtSignal()
    save_done = pyqtSignal()
    # Emitted with "log", "properties", or "bot" to switch the sidebar tab
    tab_switch = pyqtSignal(str)
    # (css_color, status, state_label, elapsed_text) — consumed by MainWindow title bar
    bot_status_changed = pyqtSignal(str, str, str, str)
    # Undo/redo state for MainWindow menu: (can_undo, can_redo, undo_desc, redo_desc)
    undo_state_changed = pyqtSignal(bool, bool, str, str)
    # Placeholder quick-start signals
    new_bot_requested = pyqtSignal()
    open_bot_requested = pyqtSignal()
    open_recent_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config: BotConfig | None = None
        self._mode = BotMode.RUN
        self._run_state = _IDLE
        self._start_time: float | None = None
        self._active_state: str | None = None
        self._run_externally_enabled: bool = False
        self._n_validation_errors: int = 0

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._tick)

        self._validation_timer = QTimer(self)
        self._validation_timer.setSingleShot(True)
        self._validation_timer.setInterval(250)
        self._validation_timer.timeout.connect(self._deferred_validation)

        self._tab_idx: int = 0
        self._search_matches: list[str] = []
        self._search_match_idx: int = 0
        self._focus_region_id: str | None = None
        # Camera to put back when focus mode exits; None when not focused.
        self._focus_camera: tuple | None = None
        # Region colour the focus overlays are currently painted in, so a theme change
        # can repaint them without the caller having to know which region is focused.
        self._focus_color: QColor | None = None

        self._undo_mgr = UndoManager()

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._scene = EditScene()
        self._view = _EditView(self._scene)
        self._param_panel = _ParamPanel()
        self._param_panel.set_drops_getter(lambda: list(self._config.drops) if self._config else [])

        # ── Buttons ───────────────────────────────────────────────────
        self._run_pause_btn = _icon_btn("play", "Run bot")
        self._run_pause_btn.setEnabled(False)
        self._run_pause_btn.clicked.connect(self._on_run_pause_clicked)

        self.stop_button = _icon_btn("stop", "Stop bot")
        self.stop_button.setEnabled(False)  # enabled only when running/paused

        self._edit_btn = _icon_btn("pencil", "Enter Edit Mode")
        self._edit_btn.setEnabled(False)
        self._edit_btn.clicked.connect(self._on_edit_clicked)

        self._fit_btn = _icon_btn("fit", "Fit to Canvas (Ctrl+0)")
        self._fit_btn.clicked.connect(self._view.fit_scene)

        self._failure_edges_btn = _icon_btn("failure_edges", "Toggle Failure/False Edges")
        self._failure_edges_btn.setCheckable(True)
        self._failure_edges_btn.setChecked(True)
        self._failure_edges_btn.toggled.connect(self._on_failure_edges_toggled)

        self._add_state_btn = _icon_btn("plus", "Add State")
        self._add_state_btn.clicked.connect(self._on_add_state)

        self._layout_btn = _icon_btn("layout", "Auto-Layout states")
        self._layout_btn.clicked.connect(self._on_layout_clicked)

        self._done_btn = _icon_btn("checkmark", "Apply changes and return to Run mode (does not save to disk)")
        self._done_btn.clicked.connect(self._on_done_clicked)

        self._save_done_btn = _icon_btn("save_done", "Save to disk and return to Run mode")
        self._save_done_btn.clicked.connect(self._on_save_done_clicked)

        # Track (button → current icon name) so changeEvent can re-tint on theme switch.
        # _run_pause_btn entry is updated by _set_run_icon() whenever the icon changes.
        self._btn_icons: dict[QPushButton, str] = {
            self._run_pause_btn: "play",
            self.stop_button: "stop",
            self._edit_btn: "pencil",
            self._fit_btn: "fit",
            self._failure_edges_btn: "failure_edges",
            self._add_state_btn: "plus",
            self._layout_btn: "layout",
            self._done_btn: "checkmark",
            self._save_done_btn: "save_done",
        }

        # ── Swappable center zone ─────────────────────────────────────
        run_center = QWidget()
        run_center_row = QHBoxLayout(run_center)
        run_center_row.setContentsMargins(0, 0, 0, 0)
        run_center_row.addWidget(self._run_pause_btn)
        run_center_row.addSpacing(4)
        run_center_row.addWidget(self.stop_button)

        edit_center = QWidget()
        edit_center_row = QHBoxLayout(edit_center)
        edit_center_row.setContentsMargins(0, 0, 0, 0)
        edit_center_row.addWidget(self._add_state_btn)
        edit_center_row.addSpacing(4)
        edit_center_row.addWidget(self._layout_btn)

        self._center_stack = QStackedWidget()
        self._center_stack.addWidget(run_center)  # _TB_RUN  = 0
        self._center_stack.addWidget(edit_center)  # _TB_EDIT = 1

        # ── Left section (68 px fixed: fit + failure-edge toggle) ─
        left_section = QWidget()
        left_section.setFixedWidth(68)
        left_row = QHBoxLayout(left_section)
        left_row.setContentsMargins(0, 0, 0, 0)
        left_row.setSpacing(4)
        left_row.addWidget(self._fit_btn)
        left_row.addWidget(self._failure_edges_btn)

        # ── Right mode-toggle zone (68 px fixed) ─────────────────────
        # Run mode page: single icon right-aligned inside 68 px
        run_right = QWidget()
        run_right_row = QHBoxLayout(run_right)
        run_right_row.setContentsMargins(0, 0, 0, 0)
        run_right_row.addStretch(1)
        run_right_row.addWidget(self._edit_btn)

        # Edit mode page: two icons, fills 68 px naturally (32+4+32)
        edit_right = QWidget()
        edit_right_row = QHBoxLayout(edit_right)
        edit_right_row.setContentsMargins(0, 0, 0, 0)
        edit_right_row.addWidget(self._done_btn)
        edit_right_row.addSpacing(4)
        edit_right_row.addWidget(self._save_done_btn)

        self._right_stack = QStackedWidget()
        self._right_stack.setFixedWidth(68)
        self._right_stack.addWidget(run_right)  # _TB_RUN  = 0
        self._right_stack.addWidget(edit_right)  # _TB_EDIT = 1

        # ── Validation badge — shown left of center when errors/warnings exist ──
        # ── Persistent toolbar row ────────────────────────────────────
        self._toolbar = QWidget()
        toolbar = self._toolbar
        toolbar_row = QHBoxLayout(toolbar)
        toolbar_row.setContentsMargins(4, 4, 4, 4)
        toolbar_row.addWidget(left_section)
        toolbar_row.addStretch(1)
        toolbar_row.addWidget(self._center_stack)
        toolbar_row.addStretch(1)
        toolbar_row.addWidget(self._right_stack)

        # ── Validation banner — right-aligned below toolbar ───────────
        # Hidden when valid (takes no space); appears under the edit/save
        # button whose right_stack sits at the 4 px right margin of the toolbar.
        self._error_pill = QLabel()
        self._error_pill.setVisible(False)

        self._warning_pill = QPushButton()
        self._warning_pill.setVisible(False)
        self._apply_pill_stylesheets()
        self._warning_pill.clicked.connect(self._cleanup_orphans)

        # ── Focus frame — a border round the whole viewport while focused ──
        # Created before the other overlays so it stacks beneath them, and made
        # transparent to the mouse so it never eats a click on the canvas.  A corner
        # pill only exists where the user happens to be looking; a frame is in
        # peripheral vision wherever they look and however far they scroll, which is
        # what the pill on its own failed to do (#55).
        self._focus_frame = QWidget(self._view)
        self._focus_frame.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._focus_frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._focus_frame.setVisible(False)

        self._validation_banner = QWidget(self._view)
        self._validation_banner.setStyleSheet("background: transparent;")
        _banner_row = QHBoxLayout(self._validation_banner)
        _banner_row.setContentsMargins(4, 4, 4, 4)
        _banner_row.setSpacing(4)
        _banner_row.addWidget(self._error_pill)
        _banner_row.addWidget(self._warning_pill)
        self._validation_banner.setVisible(False)

        # ── Minimap overlay — bottom-right corner of the graph view ──────
        self._minimap = _MinimapView(self._scene, self._view, parent=self._view)
        self._minimap.setVisible(SettingsManager.instance().get("canvas", "minimap_visible", default=True))
        self._failure_edges_btn.setChecked(SettingsManager.instance().get("canvas", "show_failure_edges", default=True))

        # ── Favorites bar — shown only in Edit mode ───────────────────
        self._favorites_bar = FavoritesBar()
        self._favorites_bar.node_requested.connect(self._add_node_at_viewport_center)
        self._favorites_bar.setVisible(False)

        # ── Main layout: toolbar + favorites bar + graph view ─────────
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(toolbar)
        outer.addWidget(self._favorites_bar)
        outer.addWidget(self._view, 1)

        # ── Search bar overlay — top-center of the graph view ────────
        self._search_bar = _SearchBar(parent=self._view)
        self._search_bar.text_changed.connect(self._on_search_text_changed)
        self._search_bar.next_match.connect(self._on_search_next)
        self._search_bar.prev_match.connect(self._on_search_prev)
        self._search_bar.closed.connect(self._close_search)

        # ── Focus breadcrumb overlay — top-left of the graph view ────
        self._focus_crumb = QWidget(self._view)
        self._focus_crumb.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._focus_crumb.setStyleSheet(
            f"QWidget {{ background-color: {THEME.surface}; border: 1px solid {THEME.border}; border-radius: 6px; }}"
            f"QLabel {{ border: none; color: {THEME.text}; font-size: 12px; padding: 0; }}"
            f"QPushButton {{ border: none; background: transparent; color: {THEME.text_muted}; font-size: 14px; }}"
            f"QPushButton:hover {{ color: {THEME.text}; }}"
        )
        self._focus_crumb_label = QLabel("Focus Mode")
        self._focus_hint_label = QLabel("Esc to exit")
        _close_focus_btn = QPushButton("×")
        _close_focus_btn.setFixedSize(22, 22)
        _close_focus_btn.clicked.connect(self.exit_focus_mode)
        _crumb_row = QHBoxLayout(self._focus_crumb)
        _crumb_row.setContentsMargins(8, 4, 6, 4)
        _crumb_row.setSpacing(6)
        _crumb_row.addWidget(self._focus_crumb_label)
        _crumb_row.addWidget(self._focus_hint_label)
        _crumb_row.addWidget(_close_focus_btn)
        self._focus_crumb.adjustSize()
        self._focus_crumb.setVisible(False)

        # ── Keyboard shortcuts ────────────────────────────────────────
        fit_shortcut = QShortcut(QKeySequence("Ctrl+0"), self)
        fit_shortcut.activated.connect(self._view.fit_scene)

        search_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        search_shortcut.activated.connect(self._show_search_bar)

        failure_edges_shortcut = QShortcut(QKeySequence("F"), self._view)
        failure_edges_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        failure_edges_shortcut.activated.connect(
            lambda: self._failure_edges_btn.setChecked(not self._failure_edges_btn.isChecked())
        )

        record_shortcut = QShortcut(QKeySequence("Ctrl+R"), self)
        record_shortcut.activated.connect(self._param_panel.toggle_record)

        # ── Hotkey slots 1–9 and 0 for the favorites bar ─────────────
        for _i in range(10):
            _key = str(_i + 1) if _i < 9 else "0"
            _sc = QShortcut(QKeySequence(_key), self)
            _sc.setContext(Qt.ShortcutContext.WindowShortcut)
            _sc.activated.connect(lambda _slot=_i: self._on_hotkey_slot(_slot))

        # ── Reposition banner overlay when the view or its viewport resizes ─
        self._view.installEventFilter(self)
        self._view.viewport().installEventFilter(self)

        # ── Wire internal signals ─────────────────────────────────────
        self._scene.about_to_change.connect(self._on_about_to_change)
        self._scene.graph_changed.connect(self._on_scene_changed)
        self._scene.node_type_chosen.connect(self._on_node_type_chosen)
        self._param_panel.structural_property_changed.connect(self._on_about_to_change)
        self._param_panel.structural_property_applied.connect(self._on_structural_property_applied)
        self._param_panel.properties_changed.connect(self._on_param_changed)
        self._param_panel.label_committed.connect(self._on_node_label_committed)
        self._param_panel.edge_endpoints_changed.connect(self._scene.change_edge_endpoints)
        self._param_panel.edge_when_changed.connect(self._scene.change_edge_when)
        self._param_panel.select_edge_requested.connect(self._on_select_edge_requested)
        self._param_panel.select_state_requested.connect(self._on_select_state_requested)
        self._param_panel.initial_state_requested.connect(self._on_initial_state_requested)
        self._param_panel.recovery_state_requested.connect(self._on_recovery_state_requested)
        self._scene.selectionChanged.connect(self._on_selection_changed)
        self._scene.focus_region_requested.connect(self.enter_focus_mode)

        # ── Placeholder — shown until the first bot is loaded ────────
        self._placeholder = _BotLibraryPanel(self)
        self._placeholder.new_bot_requested.connect(self.new_bot_requested)
        self._placeholder.open_bot_requested.connect(self.open_bot_requested)
        self._placeholder.open_recent_requested.connect(self.open_recent_requested)
        self._placeholder.show()

    # ------------------------------------------------------------------
    # Public API — bot loading
    # ------------------------------------------------------------------

    def setup(self, config: BotConfig) -> None:
        """Load a BotConfig. Enters RUN mode with the graph ready."""
        self._undo_mgr.clear()
        self._placeholder.setVisible(False)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        sm = SettingsManager.instance()
        self._minimap.setVisible(sm.get("canvas", "minimap_visible", default=True))
        show_failure = sm.get("canvas", "show_failure_edges", default=True)
        self._failure_edges_btn.setChecked(show_failure)
        self.exit_focus_mode()
        self._close_search()
        self._tab_idx = 0
        _inject_required_options(config)
        self._config = config
        self._param_panel.refresh_drops()
        # Auto-layout when no positions are saved and the setting is enabled
        if (
            config.states
            and all(s.position is None for s in config.states)
            and SettingsManager.instance().get("canvas", "auto_layout_on_load", default=True)
        ):
            from bartlebot.gui.canvas.layout import auto_layout

            auto_layout(config)
        self._scene.setup(config)
        _sm = SettingsManager.instance()
        self._scene.set_snap_enabled(_sm.get("canvas", "snap_to_grid", default=False))
        self._scene.set_grid_style(_sm.get("canvas", "grid_style", default="dots"))
        self._apply_failure_edge_visibility(self._failure_edges_btn.isChecked())
        self._param_panel.show_idle()
        self._view.fit_scene()
        self._minimap.fit_to_scene()
        self._reposition_banner()
        self._apply_mode(BotMode.RUN)
        self._edit_btn.setEnabled(True)
        self._update_validation_badge()
        self.mode_changed.emit(BotMode.RUN)

    def close_bot(self) -> None:
        """Unload the current bot and return to the placeholder."""
        self._undo_mgr.clear()
        self._refresh_undo_actions()
        self.exit_focus_mode()
        self._close_search()
        self._scene.teardown()
        self._update_validation_badge()
        self._config = None
        self._active_state = None
        self._edit_btn.setEnabled(False)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._minimap.setVisible(False)
        self._placeholder.refresh()
        self._placeholder.setVisible(True)

    def refresh_placeholder(self) -> None:
        """Rebuild the recent files list in the placeholder."""
        self._placeholder.refresh()

    def open_library_dialog(self) -> None:
        """Open the bot library as a modal dialog (useful when a bot is already loaded)."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout

        dlg = QDialog(self.window())
        dlg.setWindowTitle("Bot Library")
        dlg.resize(720, 520)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(0, 0, 0, 0)
        panel = _BotLibraryPanel(dlg)
        panel.new_bot_requested.connect(dlg.reject)
        panel.new_bot_requested.connect(self.new_bot_requested)
        panel.open_bot_requested.connect(dlg.reject)
        panel.open_bot_requested.connect(self.open_bot_requested)
        panel.open_recent_requested.connect(dlg.accept)
        panel.open_recent_requested.connect(self.open_recent_requested)
        lay.addWidget(panel)
        dlg.exec()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        tb_h = self._toolbar.height()
        self._placeholder.setGeometry(0, tb_h, self.width(), self.height() - tb_h)

    # ------------------------------------------------------------------
    # Public API — run state management
    # ------------------------------------------------------------------

    def set_run_enabled(self, enabled: bool) -> None:
        """Enable/disable the Run button (only when idle and in RUN mode)."""
        self._run_externally_enabled = enabled
        self._apply_run_btn_state()

    def _apply_run_btn_state(self) -> None:
        """Sync Run button enabled/tooltip from external readiness + validation errors."""
        if self._run_state != _IDLE or self._mode != BotMode.RUN:
            return
        if self._n_validation_errors > 0:
            self._run_pause_btn.setEnabled(False)
            noun = "error" if self._n_validation_errors == 1 else "errors"
            self._run_pause_btn.setToolTip(f"Cannot run: fix {self._n_validation_errors} {noun} first")
        elif self._run_externally_enabled:
            self._run_pause_btn.setEnabled(True)
            self._run_pause_btn.setToolTip("Bot is valid — click to run")
        else:
            self._run_pause_btn.setEnabled(False)
            self._run_pause_btn.setToolTip("")

    def eventFilter(self, obj, event) -> bool:
        if obj is self._view.viewport() and event.type() == QEvent.Type.Resize:
            self._reposition_banner()
        elif obj is self._view:
            if event.type() == QEvent.Type.Resize:
                self._reposition_banner()
            elif event.type() == QEvent.Type.KeyPress:
                key = event.key()
                mods = event.modifiers()
                if key == Qt.Key.Key_Escape and self._focus_region_id is not None:
                    self.exit_focus_mode()
                    return True
                if key == Qt.Key.Key_Tab and not (mods & Qt.KeyboardModifier.ShiftModifier):
                    self._tab_navigate(1)
                    return True
                if key in (Qt.Key.Key_Backtab,) or (key == Qt.Key.Key_Tab and mods & Qt.KeyboardModifier.ShiftModifier):
                    self._tab_navigate(-1)
                    return True
        return super().eventFilter(obj, event)

    def _reposition_banner(self) -> None:
        """Pin overlays: validation banner top-right, minimap bottom-right, search bar top-center, focus crumb top-left."""
        vp = self._view.viewport()
        vx, vy, vw, vh = vp.x(), vp.y(), vp.width(), vp.height()
        self._validation_banner.adjustSize()
        self._validation_banner.move(vx + vw - self._validation_banner.width() - 4, vy + 4)
        self._minimap.move(vx + vw - self._minimap.width() - 8, vy + vh - self._minimap.height() - 8)
        if hasattr(self, "_search_bar"):
            self._search_bar.adjustSize()
            self._search_bar.move(vx + (vw - self._search_bar.width()) // 2, vy + 8)
        if hasattr(self, "_focus_crumb"):
            self._focus_crumb.adjustSize()
            self._focus_crumb.move(vx + 8, vy + 8)
        if hasattr(self, "_focus_frame"):
            self._focus_frame.setGeometry(vx, vy, vw, vh)

    def _apply_canvas_settings(self) -> None:
        """Re-read canvas settings and update live widgets. Called after Settings dialog OK."""
        sm = SettingsManager.instance()
        self._minimap.setVisible(sm.get("canvas", "minimap_visible", default=True))
        self._scene.set_snap_enabled(sm.get("canvas", "snap_to_grid", default=False))
        self._scene.set_grid_style(sm.get("canvas", "grid_style", default="dots"))
        show_failure = sm.get("canvas", "show_failure_edges", default=True)
        self._failure_edges_btn.setChecked(show_failure)
        self._apply_failure_edge_visibility(show_failure)
        self._reposition_banner()

    def _apply_failure_edge_visibility(self, visible: bool) -> None:
        for item in self._scene.items():
            if isinstance(item, EdgeItem) and item.when in ("FAILURE", "FALSE"):
                item.setVisible(visible)

    def _on_failure_edges_toggled(self, checked: bool) -> None:
        SettingsManager.instance().set("canvas", "show_failure_edges", value=checked)
        self._apply_failure_edge_visibility(checked)

    def _active_state_label(self) -> str:
        """Return the display label for the current active state, falling back to its id."""
        if not self._active_state:
            return ""
        if self._config:
            state = next((s for s in self._config.states if s.id == self._active_state), None)
            if state and state.label:
                return state.label
        return self._active_state

    def _emit_bot_status(self) -> None:
        """Emit bot_status_changed with current run/edit state."""
        if self._mode == BotMode.EDIT:
            self.bot_status_changed.emit(THEME.text_muted, "Editing", "", "")
            return
        color = _status_dot_color(self._run_state)
        if self._run_state == _IDLE:
            self.bot_status_changed.emit(color, "Idle", "", "")
        elif self._run_state == _RUNNING:
            self.bot_status_changed.emit(color, "Running", self._active_state_label(), self._elapsed_str())
        elif self._run_state == _PAUSED:
            self.bot_status_changed.emit(color, "Paused", self._active_state_label(), self._elapsed_str())

    @property
    def config(self) -> "BotConfig | None":
        return self._config

    @property
    def mode(self) -> BotMode:
        """The canvas's current mode (RUN or EDIT)."""
        return self._mode

    @property
    def scene(self) -> "EditScene":
        """The EditScene holding the node/edge graph."""
        return self._scene

    @property
    def param_panel(self) -> QWidget:
        """The _ParamPanel widget — placed in the left sidebar Properties tab."""
        return self._param_panel

    def set_clients_getter(self, fn: Callable[[], list]) -> None:
        """Pass a callable that returns the current list of hooked clients."""
        self._param_panel.set_clients_getter(fn)

    def refresh_player_availability(self) -> None:
        """Re-grey player checkboxes for the current client count."""
        self._param_panel.refresh_player_availability()

    def set_zone_getter(self, fn: Callable[[], list[str]]) -> None:
        """Pass a callable that returns zone suggestions (current player zones + history)."""
        self._param_panel._param_form._get_zone_suggestions = fn

    def _apply_pill_stylesheets(self) -> None:
        _pill_base = "border-radius: 4px; padding: 1px 7px;"
        self._error_pill.setStyleSheet(
            f"QLabel {{ background-color: {THEME.error}; color: {THEME.pill_text}; {_pill_base} }}"
        )
        self._warning_pill.setStyleSheet(
            f"QPushButton {{ background-color: {THEME.warning}; color: {THEME.pill_text}; {_pill_base} border: none; }}"
            f"QPushButton:hover {{ background-color: {THEME.pill_warning_hover}; }}"
        )

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            for btn, icon_name in self._btn_icons.items():
                btn.setIcon(svg_icon(icon_name))
            self._apply_pill_stylesheets()
            if self._focus_region_id is not None and self._config is not None:
                region = next((r for r in self._config.regions if r.id == self._focus_region_id), None)
                if region is not None:
                    self._apply_focus_styles(THEME.region_color(region.color))

    def _set_run_icon(self, icon_name: str) -> None:
        """Set the run/pause button icon and record it for live theme updates."""
        self._btn_icons[self._run_pause_btn] = icon_name
        self._run_pause_btn.setIcon(svg_icon(icon_name))

    def set_running(self) -> None:
        self._run_state = _RUNNING
        self._set_run_icon("pause")
        self._run_pause_btn.setEnabled(True)
        self.stop_button.setEnabled(True)
        self._edit_btn.setEnabled(False)
        self._start_time = time.monotonic()
        self._tick_timer.start()
        self._emit_bot_status()
        self.tab_switch.emit("log")
        logger.info("=== Bot started ===")

    def set_paused(self) -> None:
        self._run_state = _PAUSED
        self._set_run_icon("play")
        self._run_pause_btn.setEnabled(True)
        self._emit_bot_status()
        logger.info("=== Paused ===")

    def set_idle(self) -> None:
        self._run_state = _IDLE
        self._set_run_icon("play")
        self._run_pause_btn.setEnabled(False)  # re-enabled by set_run_enabled
        self.stop_button.setEnabled(False)
        self._edit_btn.setEnabled(self._config is not None)
        self._tick_timer.stop()
        if self._active_state and self._active_state in self._scene._node_items:
            self._scene._node_items[self._active_state].set_active(False)
        self._active_state = None
        self._emit_bot_status()
        logger.info("=== Bot stopped ===")

    def set_state(self, state_id: str) -> None:
        """Highlight the active FSM state node."""
        if self._active_state and self._active_state in self._scene._node_items:
            self._scene._node_items[self._active_state].set_active(False)
        self._active_state = state_id
        if state_id in self._scene._node_items:
            node = self._scene._node_items[state_id]
            node.set_active(True)
            self._view.smooth_center_on(node)
        self._emit_bot_status()
        # GUI_ONLY: BotMachine logs the same transition, in more detail, a moment
        # either side of these two. Sending them to the session file as well made a
        # state change cost four lines to say two things, and one representative run
        # carried 9,589 of these — a fifth of the file (#156).
        logger.info("Entered: %s", state_id, extra=GUI_ONLY)

    def on_transition(self, prev_state: str, result: str, next_state: str) -> None:
        if prev_state == next_state:
            logger.info("Result: %s → (retry %s)", result, next_state, extra=GUI_ONLY)
        else:
            logger.info("Result: %s → %s", result, next_state, extra=GUI_ONLY)

    # ------------------------------------------------------------------
    # Toolbar button handlers
    # ------------------------------------------------------------------

    def _on_run_pause_clicked(self) -> None:
        if self._run_state == _IDLE:
            self.run_requested.emit()
        elif self._run_state == _RUNNING:
            self.set_paused()
            self.pause_requested.emit()
        elif self._run_state == _PAUSED:
            self._run_state = _RUNNING
            self._set_run_icon("pause")
            self._emit_bot_status()
            logger.info("=== Resumed ===")
            self.resume_requested.emit()

    def enter_edit_mode(self) -> None:
        """Switch to Edit mode (no-op if the bot is currently running)."""
        if self._run_state != _IDLE:
            return
        self._apply_mode(BotMode.EDIT)
        self.mode_changed.emit(BotMode.EDIT)

    def _on_edit_clicked(self) -> None:
        self.enter_edit_mode()

    def _on_done_clicked(self) -> None:
        self._apply_mode(BotMode.RUN)
        self.mode_changed.emit(BotMode.RUN)
        self.config_changed.emit()

    def _on_save_done_clicked(self) -> None:
        self._apply_mode(BotMode.RUN)
        self.mode_changed.emit(BotMode.RUN)
        self.save_done.emit()

    def _canvas_origin(self):
        """Return cursor scene position if the cursor is over the canvas, else viewport center.

        Used as the search origin for _find_empty_grid_cell so all add-node paths share the
        same placement logic.  When the user clicks a toolbar button the cursor is outside the
        canvas, so this degrades gracefully to viewport center.
        """
        cursor_local = self._view.viewport().mapFromGlobal(QCursor.pos())
        if self._view.viewport().rect().contains(cursor_local):
            return self._view.mapToScene(self._view.mapFromGlobal(QCursor.pos()))
        return self._view.mapToScene(self._view.viewport().rect().center())

    def _on_add_state(self) -> None:
        self._scene.add_state(self._scene.find_empty_grid_cell(self._canvas_origin()))

    def _on_node_type_chosen(self, node_type: str, pos: object) -> None:
        """Slot for EditScene.node_type_chosen — creates the state after dialog confirmation."""
        from PyQt6.QtCore import QPointF as _QPointF

        if isinstance(pos, _QPointF):
            self._scene.create_state_at(node_type, pos)

    def _on_hotkey_slot(self, slot: int) -> None:
        """Handle a bare number-key press for the given favorites slot."""
        focused = QApplication.focusWidget()
        if isinstance(focused, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
            return
        if self._mode != BotMode.EDIT or self._scene._config is None:
            return
        node_type = SettingsManager.instance().favorited_hotkey_nodes[slot]
        if not node_type:
            return
        pos = self._scene.find_empty_grid_cell(self._canvas_origin())
        self._scene.create_state_at(node_type, pos)
        SettingsManager.instance().add_recently_used_node(node_type)

    def _add_node_at_viewport_center(self, node_type: str) -> None:
        """Add a node of the given type near the canvas origin, avoiding occupied grid cells."""
        if self._mode != BotMode.EDIT or self._scene._config is None:
            return
        pos = self._scene.find_empty_grid_cell(self._canvas_origin())
        self._scene.create_state_at(node_type, pos)
        SettingsManager.instance().add_recently_used_node(node_type)

    def _on_layout_clicked(self) -> None:
        if self._config is None or self._focus_region_id is not None:
            return
        from bartlebot.gui.canvas.layout import auto_layout

        self._scene._flush_positions()
        self._undo_mgr.push(self._config, "Auto Layout")
        auto_layout(self._config)
        self._scene.setup(self._config)
        _sm = SettingsManager.instance()
        self._scene.set_snap_enabled(_sm.get("canvas", "snap_to_grid", default=False))
        self._scene.set_grid_style(_sm.get("canvas", "grid_style", default="dots"))
        self._apply_failure_edge_visibility(self._failure_edges_btn.isChecked())
        self._view.fit_scene()
        self._minimap.fit_to_scene()
        self.config_changed.emit()

    # ------------------------------------------------------------------
    # Focus Mode
    # ------------------------------------------------------------------

    def _apply_focus_styles(self, color: QColor) -> None:
        """Paint the focus overlays in *color*, the focused region's own palette slot.

        Held as state and re-applied from changeEvent, because these are built from
        THEME tokens and Qt does not refresh a stylesheet set earlier when the theme
        changes underneath it.
        """
        self._focus_color = color
        name = color.name()
        self._focus_frame.setStyleSheet(
            f"QWidget {{ border: 3px solid {name}; border-radius: 2px; background: transparent; }}"
        )
        self._focus_crumb.setStyleSheet(
            f"QWidget {{ background-color: {name}; border: 1px solid {name}; border-radius: 6px; }}"
            "QLabel { border: none; color: #ffffff; font-size: 12px; font-weight: 600; padding: 0; }"
            "QPushButton { border: none; background: transparent; color: #ffffff; font-size: 14px; }"
            "QPushButton:hover { color: #000000; }"
        )
        self._focus_hint_label.setStyleSheet(
            "border: none; color: rgba(255, 255, 255, 190); font-size: 11px; font-weight: 400; padding: 0;"
        )

    @property
    def focus_region_id(self) -> str | None:
        """The region currently focused, or None when not in focus mode."""
        return self._focus_region_id

    @property
    def focus_crumb_text(self) -> str:
        """Text on the focus-mode pill."""
        return self._focus_crumb_label.text()

    @property
    def focus_hint_text(self) -> str:
        """The pill's how-to-leave hint."""
        return self._focus_hint_label.text()

    @property
    def focus_color(self) -> QColor | None:
        """Colour the focus overlays are painted in, or None if never focused."""
        return self._focus_color

    @property
    def focus_frame(self) -> QWidget:
        """The border overlay drawn round the viewport while focused."""
        return self._focus_frame

    @property
    def view(self) -> QWidget:
        """The graph view. Exposed for camera assertions and viewport geometry."""
        return self._view

    def enter_focus_mode(self, region_id: str) -> None:
        """Isolate a region's nodes in the view; hide everything else."""
        if self._config is None:
            return
        region = next((r for r in self._config.regions if r.id == region_id), None)
        if region is None:
            return
        self.exit_focus_mode()  # clean up any existing focus first
        self._focus_region_id = region_id
        member_set = set(region.members)

        for sid, node in self._scene._node_items.items():
            node.setVisible(sid in member_set)
        for s, _, d, edge in self._scene._edge_items:
            edge.setVisible(s in member_set and d in member_set)
        for rid, ri in self._scene.region_items.items():
            ri.setVisible(rid == region_id)

        self._focus_crumb_label.setText(f"Focus Mode — {region.label}")
        self._apply_focus_styles(THEME.region_color(region.color))
        self._focus_crumb.setVisible(True)
        self._focus_frame.setVisible(True)
        self._reposition_banner()

        # Frame the region. Hiding everything else is what made the canvas look empty
        # without explanation (#55): the camera never moved, so a region that was small
        # or off to one side stayed small and off to one side while the rest vanished.
        # Moving the camera is also the clearest signal that anything happened at all.
        region_item = self._scene.region_items.get(region_id)
        if region_item is not None:
            self._focus_camera = self._view.camera_state()
            self._view.fit_rect(region_item.scene_bounds)

    def exit_focus_mode(self) -> None:
        """Restore all nodes/edges/regions to visible; hide the breadcrumb."""
        if self._focus_region_id is None:
            return
        self._focus_region_id = None
        for node in self._scene._node_items.values():
            node.setVisible(True)
        for *_, edge in self._scene._edge_items:
            edge.setVisible(True)
        for ri in self._scene.region_items.values():
            ri.setVisible(True)
        if hasattr(self, "_focus_crumb"):
            self._focus_crumb.setVisible(False)
        if hasattr(self, "_focus_frame"):
            self._focus_frame.setVisible(False)
        if self._focus_camera is not None:
            self._view.restore_camera(self._focus_camera)
            self._focus_camera = None

    # ------------------------------------------------------------------
    # Tab navigation
    # ------------------------------------------------------------------

    def _tab_navigate(self, direction: int) -> None:
        """Cycle node selection by BFS order and center the view on the node."""
        order = self._scene._get_tab_order()
        if not order:
            return
        selected = [item for item in self._scene.selectedItems() if isinstance(item, NodeItem)]
        if selected and selected[0].state_id in order:
            cur_idx = order.index(selected[0].state_id)
        else:
            cur_idx = self._tab_idx - direction  # step will land on _tab_idx
        next_idx = (cur_idx + direction) % len(order)
        self._tab_idx = next_idx
        node = self._scene._node_items.get(order[next_idx])
        if node:
            self._scene.clearSelection()
            if self._mode == BotMode.EDIT:
                node.setSelected(True)
            self._view.centerOn(node)

    # ------------------------------------------------------------------
    # Quick search
    # ------------------------------------------------------------------

    def _show_search_bar(self) -> None:
        self._search_bar.setVisible(True)
        self._reposition_banner()
        self._search_bar.focus_input()
        self._on_search_text_changed(self._search_bar.current_text())

    def _close_search(self) -> None:
        if hasattr(self, "_search_bar"):
            self._search_bar.setVisible(False)
        self._search_matches.clear()
        self._search_match_idx = 0
        for node in self._scene._node_items.values():
            node.set_search_match(False)

    def _on_search_text_changed(self, text: str) -> None:
        config = self._scene._config
        if not config:
            self._search_matches.clear()
            self._search_bar.set_count(0, 0)
            return
        q = text.strip().lower()
        matches: list[str] = []
        if q:
            for state in config.states:
                if q in (state.label or "").lower() or q in state.id.lower() or q in state.node_type.lower():
                    matches.append(state.id)
        self._search_matches = matches
        self._search_match_idx = 0
        # Apply highlights
        for sid, node in self._scene._node_items.items():
            node.set_search_match(sid in matches, is_current=(bool(matches) and sid == matches[0]))
        if matches:
            first = self._scene._node_items.get(matches[0])
            if first:
                self._view.centerOn(first)
        self._search_bar.set_count(1 if matches else 0, len(matches))
        self._reposition_banner()

    def _on_search_next(self) -> None:
        if not self._search_matches:
            return
        old = self._search_matches[self._search_match_idx]
        self._search_match_idx = (self._search_match_idx + 1) % len(self._search_matches)
        new = self._search_matches[self._search_match_idx]
        if old != new:
            old_node = self._scene._node_items.get(old)
            if old_node:
                old_node.set_search_match(True, is_current=False)
        new_node = self._scene._node_items.get(new)
        if new_node:
            new_node.set_search_match(True, is_current=True)
            self._view.centerOn(new_node)
        self._search_bar.set_count(self._search_match_idx + 1, len(self._search_matches))
        self._reposition_banner()

    def _on_search_prev(self) -> None:
        if not self._search_matches:
            return
        old = self._search_matches[self._search_match_idx]
        self._search_match_idx = (self._search_match_idx - 1) % len(self._search_matches)
        new = self._search_matches[self._search_match_idx]
        if old != new:
            old_node = self._scene._node_items.get(old)
            if old_node:
                old_node.set_search_match(True, is_current=False)
        new_node = self._scene._node_items.get(new)
        if new_node:
            new_node.set_search_match(True, is_current=True)
            self._view.centerOn(new_node)
        self._search_bar.set_count(self._search_match_idx + 1, len(self._search_matches))
        self._reposition_banner()

    # ------------------------------------------------------------------
    # Mode switching
    # ------------------------------------------------------------------

    def _apply_mode(self, mode: BotMode) -> None:
        self.exit_focus_mode()
        self._mode = mode
        if mode == BotMode.RUN:
            self._center_stack.setCurrentIndex(_TB_RUN)
            self._right_stack.setCurrentIndex(_TB_RUN)
            self._favorites_bar.setVisible(False)
            self._scene.set_interactive(False)
            self._param_panel.show_idle()
            self._undo_mgr.set_enabled(False)
            self._undo_mgr.cancel_debounce()
            self._emit_bot_status()
        else:
            self._center_stack.setCurrentIndex(_TB_EDIT)
            self._right_stack.setCurrentIndex(_TB_EDIT)
            self._favorites_bar.setVisible(True)
            self._scene.set_interactive(True)
            self._undo_mgr.set_enabled(True)
            self._emit_bot_status()
        self._refresh_undo_actions()

    # ------------------------------------------------------------------
    # Scene change callbacks
    # ------------------------------------------------------------------

    def refresh_initial(self, initial_id: str) -> None:
        """Propagate initial state change to the canvas node indicator."""
        self._scene.refresh_initial(initial_id)
        self._refresh_errors()
        self._update_validation_badge()

    def refresh_recovery(self, recovery_state_id: str | None) -> None:
        """Propagate recovery state change to the canvas node indicator."""
        self._scene.refresh_recovery(recovery_state_id)

    def _on_scene_changed(self) -> None:
        if self._scene._config is not None:
            _inject_required_options(self._scene._config)
        self._refresh_node_labels()
        self._refresh_node_returns()
        self._refresh_initial()
        self._refresh_terminal()
        self._refresh_recovery()
        self._refresh_errors()
        self._update_validation_badge()
        self._minimap.fit_to_scene()
        if self._search_bar.isVisible():
            self._on_search_text_changed(self._search_bar.current_text())
        self.config_changed.emit()
        self._refresh_undo_actions()

    def _on_initial_state_requested(self, state_id: str) -> None:
        """Move the initial-state role to *state_id* (#71)."""
        self._set_fsm_role("initial", state_id, "Change initial state")

    def _on_recovery_state_requested(self, state_id: str) -> None:
        """Move the recovery-state role to *state_id* (#71)."""
        self._set_fsm_role("recovery_state", state_id, "Change recovery state")

    def _set_fsm_role(self, field: str, state_id: str, undo_desc: str) -> None:
        """Assign a bot-wide FSM role field to *state_id*.

        The panel asks and the canvas assigns, because the panel holds no BotConfig
        and because the undo snapshot has to be pushed *before* the mutation.
        ``_on_scene_changed`` then does the rest: it moves the badge, revalidates
        (moving the initial state changes which nodes are reachable -- #53), and
        emits ``config_changed``.
        """
        config = self._scene.config
        if config is None or getattr(config, field) == state_id:
            return
        self._on_about_to_change(undo_desc)
        setattr(config, field, state_id)
        self._on_scene_changed()
        # The role moved off some other node; if that node is the one on screen, its
        # tick has to come off too.
        self._param_panel.refresh_role_checks(config.initial, config.recovery_state)

    def _on_about_to_change(self, desc: str) -> None:
        if self._config is not None and self._mode == BotMode.EDIT:
            self._undo_mgr.cancel_debounce()
            self._undo_mgr.push(self._config, desc)

    def _on_structural_property_applied(self) -> None:
        """A node type switch or an edge rewire landed in the model from the panel.

        These change the graph itself — a node's type sub-label and output ports, an
        edge's endpoints — so they need the same full refresh a scene-side edit gets,
        not the param-edit path, which only revalidates.  The undo snapshot was
        already pushed by structural_property_changed, so this must not open a
        debounced one on top of it.
        """
        if self._scene.config is not None:
            self._scene.config.sort_transitions()
        self._on_scene_changed()

    def _on_node_label_committed(self) -> None:
        state = self._param_panel._state
        if state is None:
            return
        node = self._scene._node_items.get(state.id)
        if node is not None:
            node.set_label(state.label)

    def _on_param_changed(self) -> None:
        if self._config is not None and self._mode == BotMode.EDIT:
            desc = "Edit transition" if self._param_panel._transition is not None else "Edit params"
            self._undo_mgr.begin_debounce(self._config, desc)
        # Refresh the dotted-pen flag indicator on the selected edge when flags change.
        if self._param_panel._transition is not None:
            self._scene.refresh_edge_flags(self._param_panel._transition)
            self._config.sort_transitions()
        # Emit config change immediately for autosave/other consumers, but defer the
        # expensive full validation pass (O(n²) with large graphs) until typing pauses.
        self.config_changed.emit()
        self._refresh_undo_actions()
        self._validation_timer.start()

    def _deferred_validation(self) -> None:
        """Run the expensive validation pass after a short debounce from param edits."""
        self._refresh_errors()
        self._update_validation_badge()

    def _on_select_edge_requested(self, transition) -> None:
        """Select the edge on the canvas that corresponds to *transition* and centre it."""
        self._scene.clearSelection()
        for s, w, d, edge in self._scene._edge_items:
            if s == transition.source and d == transition.dest and w == transition.when:
                edge.setSelected(True)
                self._view.centerOn(edge)
                break

    def select_state(self, state_id: str) -> None:
        """Select the node for *state_id* and centre the view on it.

        Selecting is what loads the state into the properties panel, via
        ``_on_selection_changed``.
        """
        self._scene.clearSelection()
        node = self._scene._node_items.get(state_id)
        if node is not None:
            node.setSelected(True)
            self._view.centerOn(node)

    def _on_select_state_requested(self, state_id: str) -> None:
        """Select the node on the canvas that corresponds to *state_id* and centre it."""
        self.select_state(state_id)

    def undo(self) -> None:
        """Undo the last edit. Wired to the Edit menu's Undo action."""
        self._on_undo()

    def redo(self) -> None:
        """Redo the last undone edit. Wired to the Edit menu's Redo action."""
        self._on_redo()

    def _on_undo(self) -> None:
        if self._config is None or self._mode != BotMode.EDIT:
            return
        self._undo_mgr.cancel_debounce()
        self._undo_mgr.set_enabled(False)
        try:
            restored = self._undo_mgr.pop_undo(self._config)
            if restored is None:
                return
            self._restore(restored)
        finally:
            self._undo_mgr.set_enabled(True)
        self._refresh_undo_actions()

    def _on_redo(self) -> None:
        if self._config is None or self._mode != BotMode.EDIT:
            return
        self._undo_mgr.cancel_debounce()
        self._undo_mgr.set_enabled(False)
        try:
            restored = self._undo_mgr.pop_redo(self._config)
            if restored is None:
                return
            self._restore(restored)
        finally:
            self._undo_mgr.set_enabled(True)
        self._refresh_undo_actions()

    def _restore(self, config: BotConfig) -> None:
        self._config = config
        self._scene.setup(config)
        self._apply_failure_edge_visibility(self._failure_edges_btn.isChecked())
        self._param_panel.show_idle()
        _inject_required_options(config)
        self._on_scene_changed()

    def prepare_for_shutdown(self) -> None:
        """Make the canvas safe to destroy. Idempotent, and safe to call twice.

        Two things outlive the widget otherwise:

        ``_validation_timer`` is single-shot and re-armed on every graph change, so a
        teardown can always land with a validation pass still queued.

        A QGraphicsScene destroyed while items are still selected corrupts the heap on
        the way out -- a 0xC0000409 fail-fast at interpreter finalization, with no
        Python traceback to say so.  Measured at 8 crashes in 40 runs of a script that
        selects a few nodes and exits; clearing the selection first takes it to 0 in 40.

        Signals are blocked over the clear because ``selectionChanged`` runs
        ``_on_selection_changed``, which reaches into the properties panel.  Nothing
        should be repainting a panel on the way to being deleted.
        """
        self._tick_timer.stop()
        self._validation_timer.stop()
        was_blocked = self._scene.signalsBlocked()
        self._scene.blockSignals(True)
        try:
            self._scene.clearSelection()
        finally:
            self._scene.blockSignals(was_blocked)

    def closeEvent(self, event) -> None:
        self.prepare_for_shutdown()
        super().closeEvent(event)

    def can_undo(self) -> bool:
        """Whether there is an edit to undo. Mirrors what the Edit menu is told."""
        return self._undo_mgr.can_undo() and self._mode == BotMode.EDIT

    def _refresh_undo_actions(self) -> None:
        in_edit = self._mode == BotMode.EDIT
        self.undo_state_changed.emit(
            self._undo_mgr.can_undo() and in_edit,
            self._undo_mgr.can_redo() and in_edit,
            self._undo_mgr.undo_desc() or "",
            self._undo_mgr.redo_desc() or "",
        )

    def _validation_lines(self) -> tuple[list[str], list[str]]:
        """Return (error_lines, warning_lines) for the loaded graph, ``"<node>: <reason>"``."""
        config = self._scene.config
        if config is None:
            return [], []
        error_lines: list[str] = []
        warning_lines: list[str] = []
        reachable = _reachable_states(config)
        for state in config.states:
            name = state.label or state.id
            _, _, err_reasons, warn_reasons = _validate_node(state, config, reachable)
            error_lines.extend(f"{name}: {r}" for r in err_reasons)
            warning_lines.extend(f"{name}: {r}" for r in warn_reasons)
        return error_lines, warning_lines

    def validation_errors(self) -> list[str]:
        """The error lines currently behind the red badge, one per problem."""
        return self._validation_lines()[0]

    def validation_warnings(self) -> list[str]:
        """The warning lines currently behind the amber badge, one per problem."""
        return self._validation_lines()[1]

    def _update_validation_badge(self) -> None:
        if self._scene.config is None:
            self._validation_banner.setVisible(False)
            self._n_validation_errors = 0
            self._apply_run_btn_state()
            return

        error_lines, warning_lines = self._validation_lines()

        n_errors = len(error_lines)
        n_warnings = len(warning_lines)

        if error_lines:
            noun = "error" if n_errors == 1 else "errors"
            self._error_pill.setText(f"\u25cf {n_errors} {noun}")
            self._error_pill.setToolTip("\n".join(error_lines))
            self._error_pill.setVisible(True)
        else:
            self._error_pill.setVisible(False)

        if warning_lines:
            noun = "warning" if n_warnings == 1 else "warnings"
            self._warning_pill.setText(f"\u25b2 {n_warnings} {noun}")
            n_orphans = sum(1 for line in warning_lines if "Unreachable from initial state" in line)
            tip = "\n".join(warning_lines)
            if n_orphans:
                noun_o = "state" if n_orphans == 1 else "states"
                tip += f"\n\nClick to delete {n_orphans} orphaned {noun_o}"
            self._warning_pill.setToolTip(tip)
            self._warning_pill.setVisible(True)
        else:
            self._warning_pill.setVisible(False)

        self._validation_banner.setVisible(bool(error_lines or warning_lines))
        if error_lines or warning_lines:
            self._reposition_banner()
        self._n_validation_errors = n_errors
        self._apply_run_btn_state()

    def _refresh_initial(self) -> None:
        if self._scene._config is None:
            return
        self._scene.refresh_initial(self._scene._config.initial)

    def _refresh_terminal(self) -> None:
        self._scene.refresh_terminal()

    def _refresh_recovery(self) -> None:
        if self._scene._config is not None:
            self._scene.refresh_recovery(self._scene._config.recovery_state)

    def _refresh_errors(self) -> None:
        self._scene.refresh_errors()

    def _refresh_node_labels(self) -> None:
        if self._scene._config is None:
            return
        for state in self._scene._config.states:
            node = self._scene._node_items.get(state.id)
            if node:
                node.set_label(state.label)
                node.set_node_type(state.node_type)

    def _refresh_node_returns(self) -> None:
        if self._scene._config is None:
            return
        for state in self._scene._config.states:
            node = self._scene._node_items.get(state.id)
            if node:
                returns = _node_returns(state.node_type)
                if node._returns != returns:
                    node.set_returns(returns)
                    for s, _w, _d, edge in self._scene._edge_items:
                        if s == state.id:
                            edge._rebuild()

    def _cleanup_orphans(self) -> None:
        config = self._scene._config
        if config is None:
            return
        reachable = _reachable_states(config)
        orphan_ids = [s.id for s in config.states if s.id not in reachable]
        for sid in orphan_ids:
            self._scene.delete_state(sid, confirm=False)

    def _on_selection_changed(self) -> None:
        self._undo_mgr.cancel_debounce()
        try:
            selected = self._scene.selectedItems()
        except RuntimeError:
            return
        if not selected:
            self._param_panel.show_idle()
            return
        item = selected[0]
        if isinstance(item, NodeItem) and self._scene._config:
            state = next((s for s in self._scene._config.states if s.id == item.state_id), None)
            if state:
                self._param_panel.show_state(
                    state,
                    all_states=list(self._scene._config.states),
                    all_transitions=self._scene._config.transitions,
                    initial_id=self._scene._config.initial,
                    recovery_id=self._scene._config.recovery_state,
                )
                self.tab_switch.emit("properties")
        elif isinstance(item, EdgeItem) and self._scene._config:
            for s, w, d, ei in self._scene._edge_items:
                if ei is item:
                    trans = next(
                        (t for t in self._scene._config.transitions if t.source == s and t.dest == d and t.when == w),
                        None,
                    )
                    if trans:
                        src_state = next((st for st in self._scene._config.states if st.id == s), None)
                        src_returns: list[str] = []
                        if src_state:
                            spec = NODE_SPECS.get(src_state.node_type)
                            if spec:
                                src_returns = [r.name for r in spec.returns]
                        option_names = [opt.name for opt in self._scene._config.options if opt.type == "bool"]
                        self._param_panel.show_transition(
                            trans,
                            src_returns,
                            option_names,
                            states=list(self._scene._config.states),
                            all_transitions=self._scene._config.transitions,
                        )
                        self.tab_switch.emit("properties")
                    break
        else:
            self._param_panel.show_idle()

    def _tick(self) -> None:
        self._emit_bot_status()

    def _elapsed_str(self) -> str:
        if self._start_time is None:
            return "00:00:00"
        s = int(time.monotonic() - self._start_time)
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{sec:02d}"
