"""Unified bot canvas — single QGraphicsScene with Run and Edit modes.

BotCanvas  — top-level widget; owns the scene, toolbars, and properties panel.
"""

import enum
import json
import logging
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from PyQt6.QtCore import QEvent, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QIcon,
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
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from bartlebot import card_art
from bartlebot.bot.schema import BotConfig, SubgraphDefinition, SubgraphInput, SubgraphInputBinding
from bartlebot.bot_folders import ALL_BADGE, folder_badge, folder_caption
from bartlebot.bot_options import compose_options, orphaned_flag_options
from bartlebot.bot_tags import BOT_TAGS, TAG_GROUPS, normalize_tags, tag_group
from bartlebot.game_sizing import BOTCANVAS_MIN_HEIGHT, BOTCANVAS_MIN_WIDTH
from bartlebot.gui.canvas.bot_header import BotHeader
from bartlebot.gui.canvas.edge_item import EdgeItem
from bartlebot.gui.canvas.edit_scene import (
    EditScene,
    _EditView,
    _node_returns,
    _reachable_states,
    _validate_node,
)
from bartlebot.gui.canvas.favorites_bar import FavoritesBar
from bartlebot.gui.canvas.graph_view import _MinimapView
from bartlebot.gui.canvas.node_item import NodeItem
from bartlebot.gui.canvas.param_panel import _ParamPanel
from bartlebot.gui.canvas.subgraph_widgets import EditSubgraphCrumb, ExposeInputDialog, SubgraphMetaDialog
from bartlebot.gui.canvas.undo_manager import UndoManager
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin
from bartlebot.gui.widgets.card_art_tile import art_icon
from bartlebot.gui.widgets.icon_button import IconButton
from bartlebot.gui.widgets.picker_popup import SECTION_ICON_PX
from bartlebot.gui.widgets.search_field import SearchLineEdit
from bartlebot.gui.widgets.tag_picker import TagPickerButton
from bartlebot.gui.widgets.tag_pill import NeutralPill, TagChip, TagPill
from bartlebot.logging_setup import GUI_ONLY
from bartlebot.nodes.registry import NODE_PARAM_SCHEMAS, NODE_SPECS
from bartlebot.paths import project_root
from bartlebot.settings_manager import SettingsManager
from bartlebot.strategy_store import rekey_bot_strategy_states
from bartlebot.subgraph_store import SubgraphLibrary
from bartlebot.subgraphs import (
    ComposeReport,
    added_member_ids,
    clear_label_override,
    clear_param_override,
    compose_bot,
    contains_subgraph,
    convert_region_to_instance,
    definition_values,
    drop_states,
    extract_definition,
    free_keys,
    local_id,
    mint_instance_id,
    push_back,
    rename_states,
    reresolve_member_params,
    set_param_override,
    sync_free_overrides,
    taken_ids,
)

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
# Gap *within* a cluster, and between clusters.  Set explicitly on every toolbar row:
# the style's default spacing is 6 px on Windows, so a row that only called
# addSpacing(4) got 16 px between its buttons and overflowed the fixed width it was
# given -- which is what was clipping the Save button off the right-hand end.
_BTN_GAP = 4
_GROUP_GAP = 10
_PAIR_WIDTH = _BTN_SIZE.width() * 2 + _BTN_GAP


def _icon_btn(icon_name: str, tooltip: str) -> IconButton:
    """One toolbar button, at the toolbar's size.

    Still a factory rather than a subclass: the only thing it adds to ``IconButton``
    is the pair of sizes every button on this row shares, and those are facts about
    the toolbar rather than about a kind of button.
    """
    return IconButton(icon_name, tooltip=tooltip, size=_BTN_SIZE.width(), icon_size=_ICON_SIZE.width())


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


#: The category list's geometry. Wide enough for the longest caption the shipped folders
#: have ("Housing Gauntlet") beside a 32px badge with the scrollbar showing -- which it
#: does once icons make every row 42px tall. ``test_bot_folders`` measures it.
CATEGORY_LIST_WIDTH = 180
CATEGORY_PAD_X = 10
CATEGORY_PAD_Y = 5


def _badge_icon(badge: str | None) -> QIcon | None:
    """The extracted icon for a badge category, or None -- which most installs get for all of them."""
    return None if badge is None else _art_icon(card_art.badge_icon_path(badge))


def _art_icon(path: Path | None) -> QIcon | None:
    """A category row's icon from an extracted PNG, or None.

    Decoration over a caption that already carries the meaning, the same rule the zone
    picker's world headers follow, so every failure to resolve is silent.
    """
    return None if path is None else art_icon(path)


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


class _BotCard(ThemedMixin, QWidget):
    """Single clickable bot entry shown in the library's right panel."""

    open_requested = pyqtSignal(str)  # emits path string

    def __init__(self, meta: "_BotMeta", matched_drops: "list[str] | None" = None, parent=None) -> None:
        super().__init__(parent)
        self._meta = meta
        self.setObjectName("BotCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # Every *plain* label the card builds is held, because each carries a THEME
        # colour that apply_theme_styles has to re-derive. Several are conditional, so
        # the attributes are declared up front rather than only where they are built.
        # The pills are absent from this list deliberately: TagPill and NeutralPill own
        # their own colour, so there is nothing here to re-derive for them.
        self._hovered = False
        self._desc_lbl: QLabel | None = None
        self._meta_lbl: QLabel | None = None
        self._build()
        self.set_matched_drops(matched_drops)
        self.refresh_theme_styles()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(2)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)

        self._name_lbl = QLabel(self._meta.name or "(untitled)")
        f = self._name_lbl.font()
        f.setBold(True)
        self._name_lbl.setFont(f)
        header.addWidget(self._name_lbl, 0)

        # Neutral grey rather than an accent, and NeutralPill rather than a label this
        # card restyles: see its docstring for both halves of the reasoning.
        if self._meta.version:
            header.addWidget(NeutralPill(f"v{self._meta.version}"), 0, Qt.AlignmentFlag.AlignVCenter)

        # One palette for a tag everywhere it appears: the dot in the picker, the chip
        # in the filter bar, this pill, and the ones in Bot Info. TagPill owns it.
        for tag in self._meta.tags:
            header.addWidget(TagPill(tag), 0, Qt.AlignmentFlag.AlignVCenter)

        header.addStretch()
        layout.addLayout(header)

        if self._meta.description:
            self._desc_lbl = QLabel(self._meta.description)
            self._desc_lbl.setWordWrap(True)
            layout.addWidget(self._desc_lbl)

        parts: list[str] = []
        if self._meta.authors:
            parts.append(f"by {self._meta.authors}")
        n = len(self._meta.drops)
        if n:
            parts.append(f"{n} drop{'s' if n != 1 else ''}")
        if parts:
            self._meta_lbl = QLabel("  ·  ".join(parts))
            layout.addWidget(self._meta_lbl)

        # Always built, and hidden while nothing matched: the library keeps its cards
        # across searches, so this is the one line a new query has to be able to change.
        self._match_lbl = QLabel()
        self._match_lbl.setWordWrap(True)
        layout.addWidget(self._match_lbl)

    def set_matched_drops(self, drops: "list[str] | None") -> None:
        """Show which drops the current search matched, or nothing when none did."""
        self._matched_drops = drops
        if drops:
            text = ", ".join(drops[:3])
            if len(drops) > 3:
                text += f" +{len(drops) - 3} more"
            self._match_lbl.setText(f"Drops: {text}")
        self._match_lbl.setVisible(bool(drops))

    @property
    def matched_drops_text(self) -> str | None:
        """The "Drops: …" line as shown, or None while it is hidden."""
        return self._match_lbl.text() if self._matched_drops else None

    def apply_theme_styles(self) -> None:
        """Restyle the frame and every label on it.

        Hover is state rather than a QSS pseudo-class because the frame and the
        labels are restyled by the same pass; ``_hovered`` picks which frame colours
        that pass uses. Before this was one method, hover was the *only* thing that
        re-read THEME, so a theme switch left a card's text in the old palette until
        the mouse crossed it — and even then only the frame followed.
        """
        # Use #BotCard selector so border/background don't bleed into child labels
        bg = THEME.node_fill if self._hovered else THEME.surface
        border = THEME.accent if self._hovered else THEME.border
        self.setStyleSheet(f"#BotCard {{ background: {bg}; border: 1px solid {border}; border-radius: 4px; }}")

        self._name_lbl.setStyleSheet(f"color: {THEME.text}; background: transparent;")
        for lbl, color in (
            (self._desc_lbl, THEME.text_muted),
            (self._meta_lbl, THEME.text_subtle),
            (self._match_lbl, THEME.accent),
        ):
            if lbl is not None:
                lbl.setStyleSheet(f"color: {color}; font-size: {THEME.font_subtext_px}px; background: transparent;")

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.refresh_theme_styles()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.refresh_theme_styles()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        self.open_requested.emit(str(self._meta.path.resolve()))
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# _BotLibraryPanel — shown when no bot file is open
# ---------------------------------------------------------------------------


class _BotLibraryPanel(ThemedMixin, QWidget):
    """Bot library panel shown in BotCanvas when no bot file is open.

    Two-panel layout: left category list (Recent, All, then the folders under bots/),
    right scrollable bot cards. Search bar filters across all categories.

    Each category row carries its *key* in ``Qt.UserRole`` -- the folder name, or one of
    the two sentinels below -- and the caption is only what it shows. The caption used to
    be the key as well, which tied the lookup to wording that now comes from the game.
    """

    new_bot_requested = pyqtSignal()
    open_bot_requested = pyqtSignal()
    open_recent_requested = pyqtSignal(str)  # path

    _RECENT = "Recent"
    _ALL = "All"
    #: Folder-name keys for the rows that are not folders. A folder can never be named
    #: with a leading NUL, so these cannot collide with one.
    _OTHER = "Other"
    _RECENT_KEY = "\0recent"
    _ALL_KEY = "\0all"
    _OTHER_KEY = "\0other"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._all_metas: list[_BotMeta] = []
        #: Folder name -> its bots, in folder-name order.
        self._category_map: dict[str, list[_BotMeta]] = {}
        self._recent_metas: list[_BotMeta] = []
        #: Bot path -> its card, built the first time a filter shows it and kept until the
        #: next refresh(). Rebuilding every card per keystroke made the search box lag:
        #: ~200ms a keystroke for a 70-bot library, where reusing them is ~40ms.
        self._cards: dict[Path, _BotCard] = {}
        # Listed on first show, not here. This is BotCanvas's placeholder, so a canvas
        # that opens a bot straight away hides it before it is ever painted -- and
        # listing costs a read of every file in bots/ plus a card widget for each one
        # that survives the filter, which is the whole library on a fresh profile.
        self._listed = False
        self._build_ui()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._listed:
            self.refresh()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top bar: search + action buttons ────────────────────────────
        self._top_bar = top = QWidget()
        top.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
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

        self._sep = QFrame()
        self._sep.setFrameShape(QFrame.Shape.VLine)
        top_layout.addWidget(self._sep)

        # Same shape and height as the pickers, so the bar looks like one set of
        # controls — but New Bot keeps the accent, because a bar with four identical
        # buttons has undone the grouping the separator just drew.
        # (button, is_primary) — apply_theme_styles needs to know which is which on
        # every restyle, not only on the first.
        self._action_btns: list[tuple[QPushButton, bool]] = []
        for label, signal_name, primary in (
            ("New Bot", "new_bot_requested", True),
            # Not "Open…": in this panel every card already opens a bot, so the name
            # has to say what is different about this one, which is that it takes a
            # file from anywhere rather than an entry in the library.
            ("Open Bot File…", "open_bot_requested", False),
        ):
            btn = QPushButton(label)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(getattr(self, signal_name))
            top_layout.addWidget(btn)
            self._action_btns.append((btn, primary))

        root.addWidget(top)

        # ── Active tag filters, shown as chips you can click to remove ───
        # Only the *selected* tags live here; the vocabulary itself is behind the
        # picker. Eighteen chips laid out at once needed 1648px and pinned the
        # panel's minimum width above what a 1366px laptop can give it.
        self._chip_bar = QWidget()
        self._chip_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
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
        self._cat_list.setFixedWidth(CATEGORY_LIST_WIDTH)
        # Only rows that have an icon reserve room for one, so an install with no
        # extracted art keeps its compact rows.
        self._cat_list.setIconSize(QSize(SECTION_ICON_PX, SECTION_ICON_PX))
        body.addWidget(self._cat_list)

        # Right: scrollable bot cards
        self._cards_area = QScrollArea()
        self._cards_area.setWidgetResizable(True)
        self._cards_area.setFrameShape(QFrame.Shape.NoFrame)
        self._cards_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._cards_container = QWidget()
        self._cards_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
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

        # Both are rebuilt on filter changes and absent much of the time, but have to
        # follow a theme switch whenever they exist.
        self._clear_btn: QPushButton | None = None
        self._empty_hint: QLabel | None = None
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        """Restyle the panel chrome, all of which outlives a theme switch.

        Qt does not recompute a stylesheet set on a widget earlier, and nothing here
        is rebuilt on its own, which is why the search bar and the category list used
        to keep the old palette until the app was restarted.
        """
        panel = f"background: {THEME.surface}; border-bottom: 1px solid {THEME.border};"
        self._top_bar.setStyleSheet(panel)
        self._chip_bar.setStyleSheet(panel)
        self._sep.setStyleSheet(f"color: {THEME.border};")

        for btn, primary in self._action_btns:
            # New Bot keeps the accent: a bar of identical buttons would undo the
            # grouping the separator just drew.
            border = THEME.accent if primary else THEME.border
            color = THEME.accent if primary else THEME.text
            btn.setStyleSheet(
                f"QPushButton {{ background: {THEME.surface}; color: {color};"
                f"  border: 1px solid {border}; border-radius: 3px; padding: 3px 8px; }}"
                f"QPushButton:hover {{ border-color: {THEME.accent}; color: {THEME.accent}; }}"
            )

        self._cat_list.setStyleSheet(
            # outline: none drops the focus rectangle Qt draws around the clicked
            # row's text; the selection fill already marks it.
            f"QListWidget {{ background: {THEME.surface}; border: none; outline: none;"
            f"  border-right: 1px solid {THEME.border}; font-size: {THEME.font_small_px}px; }}"
            f"QListWidget::item {{ padding: {CATEGORY_PAD_Y}px {CATEGORY_PAD_X}px; color: {THEME.text_muted};"
            f"  border-bottom: 1px solid {THEME.border}; }}"
            f"QListWidget::item:selected {{ background: {THEME.node_fill_selected}; color: {THEME.text}; }}"
        )
        self._cards_area.setStyleSheet(f"background: {THEME.bg};")
        self._cards_container.setStyleSheet(f"background: {THEME.bg};")

        if self._clear_btn is not None:
            self._clear_btn.setStyleSheet(
                f"QPushButton {{ color: {THEME.text_muted}; border: none;"
                f"  padding: 2px 6px; font-size: {THEME.font_subtext_px}px; }}"
                f"QPushButton:hover {{ color: {THEME.text}; }}"
            )
        if self._empty_hint is not None:
            self._empty_hint.setStyleSheet(f"color: {THEME.text_muted}; font-size: {THEME.font_small_px}px;")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Rescan recent files and bots/ directory, rebuild UI."""
        self._listed = True
        sm = SettingsManager.instance()
        recent_paths = sm.recent_bot_files

        # Scan bots/ subdirectories
        self._all_metas = []
        self._category_map = {}

        bots_dir = _resolve_bots_dir(sm.get("canvas", "bot_library_path", default="bots"), project_root())
        if bots_dir.is_dir():
            # By folder name, so the numbers set the order -- the same order Explorer
            # shows, which is the game's badge page order (see bot_folders).
            for subdir in sorted(bots_dir.iterdir()):
                if not subdir.is_dir():
                    continue
                metas = [m for f in sorted(subdir.glob("*.json")) if (m := _read_bot_meta(f))]
                if metas:
                    self._category_map[subdir.name] = metas
                    self._all_metas.extend(metas)
            root_metas = [m for f in sorted(bots_dir.glob("*.json")) if (m := _read_bot_meta(f))]
            if root_metas:
                self._category_map[self._OTHER_KEY] = root_metas
                self._all_metas.extend(root_metas)

        # Recent files
        self._recent_metas = [m for p in recent_paths if (m := _read_bot_meta(Path(p)))]

        # The files were just re-read, so a kept card may describe an old version.
        # _show_cards below takes them out of the layout.
        for card in self._cards.values():
            card.hide()
            card.deleteLater()
        self._cards.clear()

        self._refresh_tag_counts()
        self._rebuild_chips()

        # Rebuild category list
        self._cat_list.blockSignals(True)
        self._cat_list.clear()
        # "Recent" first, because opening the library is usually the first step of
        # going back to a bot you ran yesterday; "All" is the fallback the row below
        # it, and is always present. Without "All" there was no way to ask the library
        # a question that crossed worlds — which is exactly what a tag filter is for —
        # and no way back to the whole list once a world was clicked.
        #
        # Selection is row 0 either way (see refresh), so this ordering *is* the
        # default: Recent when there is one, All on a first run.
        if self._recent_metas:
            self.add_category_row(self._RECENT_KEY, self._RECENT, _art_icon(card_art.icon_path(card_art.RECENT_ICON)))
        self.add_category_row(self._ALL_KEY, self._ALL, _badge_icon(ALL_BADGE))
        for folder in self._category_map:
            if folder == self._OTHER_KEY:
                self.add_category_row(folder, self._OTHER)
            else:
                self.add_category_row(folder, folder_caption(folder), _badge_icon(folder_badge(folder)))
        self._cat_list.blockSignals(False)

        # Select first category. ``clear()`` above left the current row at -1, so this
        # always changes it and the currentRowChanged connection builds the cards --
        # calling _on_category_row_changed by hand as well built every card twice, the
        # first set torn down again before anything could paint it.
        if self._cat_list.count() > 0:
            self._cat_list.setCurrentRow(0)
        else:
            self._show_cards([])

    def add_category_row(self, key: str, caption: str, icon: QIcon | None = None) -> None:
        """Append a row to the category list, looked up later by *key* rather than *caption*."""
        item = QListWidgetItem(caption)
        item.setData(Qt.ItemDataRole.UserRole, key)
        if icon is not None:
            item.setIcon(icon)
        self._cat_list.addItem(item)

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
            # Coloured by group, matching the dot in the popup and the button it came
            # from — with two pickers feeding one chip bar, a chip has to say which.
            chip = TagChip(tag, tooltip=f"Remove the {tag} filter")
            chip.removed.connect(self._remove_tag)
            self._chip_layout.insertWidget(self._chip_layout.count() - 1, chip)

        self._clear_btn = None
        if selected:
            self._clear_btn = clear = QPushButton("Clear")
            clear.setCursor(Qt.CursorShape.PointingHandCursor)
            clear.setFlat(True)
            clear.clicked.connect(lambda: self._set_tag_selection([]))
            self._chip_layout.insertWidget(self._chip_layout.count() - 1, clear)
        self.refresh_theme_styles()

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
        key = self._cat_list.item(row).data(Qt.ItemDataRole.UserRole)
        if key == self._ALL_KEY:
            return self._all_metas
        if key == self._RECENT_KEY:
            return self._recent_metas
        return self._category_map.get(key, [])

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
        # Take every widget out, keeping the trailing stretch (last item). Cards are
        # only hidden -- self._cards keeps them for the next filter -- and the empty
        # hint is the one thing deleted. hide() comes first so it disappears now rather
        # than on the next event loop cycle.
        while layout.count() > 1:
            item = layout.takeAt(0)
            if w := item.widget():
                w.hide()
                if w is self._empty_hint:
                    w.deleteLater()
        self._empty_hint = None

        if not metas:
            # "All" is always in the list now, so its presence no longer distinguishes
            # "nothing matched" from "the library is empty". The metas do.
            self._empty_hint = hint = QLabel(
                "No bots match these filters." if self._all_metas else "No bots found in bots/ directory."
            )
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.insertWidget(0, hint)
            self.refresh_theme_styles()
            return

        for i, meta in enumerate(metas):
            card = self._cards.get(meta.path)
            if card is None:
                card = self._cards[meta.path] = _BotCard(meta)
                card.open_requested.connect(self.open_recent_requested)
            card.set_matched_drops((matched_drops or {}).get(meta.path))
            layout.insertWidget(i, card)
            card.show()


# ---------------------------------------------------------------------------
# _SearchBar — floating quick-search overlay (Ctrl+F)
# ---------------------------------------------------------------------------


class _SearchBar(ThemedMixin, QWidget):
    """Floating search bar overlaid on the graph view (Ctrl+F to open)."""

    text_changed = pyqtSignal(str)
    next_match = pyqtSignal()
    prev_match = pyqtSignal()
    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._input = SearchLineEdit("Search states…")
        self._input.setFixedWidth(200)
        self._input.installEventFilter(self)
        self._input.textChanged.connect(self.text_changed)

        self._count_lbl = QLabel()

        close_btn = IconButton("dismiss", tooltip="Close the search bar", size=22, icon_size=12)
        close_btn.clicked.connect(self.closed)

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 6, 4)
        row.setSpacing(6)
        row.addWidget(self._input)
        row.addWidget(self._count_lbl)
        row.addWidget(close_btn)
        self.adjustSize()
        self.setVisible(False)
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        """One sheet on self, reaching the children by type selector.

        Styling self is exactly the case the mixin's re-entrancy guard exists for:
        setStyleSheet here delivers StyleChange straight back to changeEvent.
        """
        self.setStyleSheet(
            f"QWidget {{ background-color: {THEME.surface}; border: 1px solid {THEME.border}; border-radius: 6px; }}"
            f"QLineEdit {{ border: none; background: transparent; color: {THEME.text}; font-size: {THEME.font_body_px + 1}px; }}"
            f"QPushButton {{ border: none; background: transparent; color: {THEME.text_muted}; font-size: {THEME.font_body_px + 2}px; }}"
            f"QPushButton:hover {{ color: {THEME.text}; }}"
            f"QLabel {{ border: none; color: {THEME.text_muted}; font-size: {THEME.font_small_px}px; }}"
        )

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
    # Emitted with "log" or "properties" to switch a sidebar tab.  "log" now lands on
    # the *right* sidebar; the canvas does not know or care which one holds it.
    tab_switch = pyqtSignal(str)
    # (css_color, status, state_label, elapsed_text).  Bar B is written to directly;
    # this is for everyone else — the menu actions, and Bar A's active-node caption.
    bot_status_changed = pyqtSignal(str, str, str, str)
    # Undo/redo state for MainWindow menu: (can_undo, can_redo, undo_desc, redo_desc)
    undo_state_changed = pyqtSignal(bool, bool, str, str)
    # Placeholder quick-start signals
    new_bot_requested = pyqtSignal()
    open_bot_requested = pyqtSignal()
    open_recent_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        # The floor the bottom pane may be dragged to, set explicitly so that the header
        # and the favourites bar -- which hint 536px and 844px wide -- cannot raise the
        # window's own minimum past what the screen-fit arithmetic budgeted for the
        # canvas. See "The panel floors" in game_sizing.py.
        self.setMinimumSize(BOTCANVAS_MIN_WIDTH, BOTCANVAS_MIN_HEIGHT)
        self._config: BotConfig | None = None
        self._mode = BotMode.RUN
        self._run_state = _IDLE
        self._start_time: float | None = None
        self._active_state: str | None = None
        self._run_externally_enabled: bool = False
        self._n_validation_errors: int = 0
        # Per-linked-region outcome of the last compose, set by the controller after
        # setup() and again before a run. Empty for a bot with no linked regions.
        self._compose_report = ComposeReport()
        # The session's subgraph library, handed in by the controller. Read for the
        # region page and for re-resolving a member's params; never written here.
        self._subgraph_library: SubgraphLibrary | None = None
        # The linked region being edited in place, and the working copy of its
        # definition that Expose-as-input writes into. Neither is in the bot config,
        # so neither is undoable -- docs/SUBGRAPHS.md, *Authoring*.
        self._editing_region_id: str | None = None
        self._editing_defn: SubgraphDefinition | None = None

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

        # ── Centre zone: the transport, in Edit mode as well ─────────
        # A transport is a landmark: people aim at it without looking, so it stays put
        # and is *disabled* when it cannot be used rather than swapped for something
        # else. Add State and Auto-Layout used to occupy these exact pixels in Edit
        # mode, which made the same target do two different things with no cue at the
        # target saying which -- and they are graph-editing tools, so they belong with
        # the mode toggle that summons them, not in the run's seat.
        run_controls = QWidget()
        run_row = QHBoxLayout(run_controls)
        run_row.setContentsMargins(0, 0, 0, 0)
        run_row.setSpacing(_BTN_GAP)
        run_row.addWidget(self._run_pause_btn)
        run_row.addWidget(self.stop_button)

        # ── Edit tools: add a state, tidy the graph ──────────────────
        edit_tools = QWidget()
        edit_tools_row = QHBoxLayout(edit_tools)
        edit_tools_row.setContentsMargins(0, 0, 0, 0)
        edit_tools_row.setSpacing(_BTN_GAP)
        edit_tools_row.addWidget(self._add_state_btn)
        edit_tools_row.addWidget(self._layout_btn)

        self._edit_tools_stack = QStackedWidget()
        self._edit_tools_stack.setFixedWidth(_PAIR_WIDTH)
        self._edit_tools_stack.addWidget(QWidget())  # _TB_RUN  = 0 -- deliberately empty
        self._edit_tools_stack.addWidget(edit_tools)  # _TB_EDIT = 1

        # ── View tools (fit + failure-edge toggle) ───────────────────
        # These act on the *view*, not on the bot, so they sit with the mode toggle
        # on the right rather than among the metadata on the left.
        view_tools = QWidget()
        view_tools.setFixedWidth(_PAIR_WIDTH)
        view_row = QHBoxLayout(view_tools)
        view_row.setContentsMargins(0, 0, 0, 0)
        view_row.setSpacing(_BTN_GAP)
        view_row.addWidget(self._fit_btn)
        view_row.addWidget(self._failure_edges_btn)

        # ── Right mode-toggle zone ───────────────────────────────────
        # Run mode page: single icon right-aligned inside the pair's width
        run_right = QWidget()
        run_right_row = QHBoxLayout(run_right)
        run_right_row.setContentsMargins(0, 0, 0, 0)
        run_right_row.setSpacing(_BTN_GAP)
        run_right_row.addStretch(1)
        run_right_row.addWidget(self._edit_btn)

        # Edit mode page: two icons, filling the pair's width exactly
        edit_right = QWidget()
        edit_right_row = QHBoxLayout(edit_right)
        edit_right_row.setContentsMargins(0, 0, 0, 0)
        edit_right_row.setSpacing(_BTN_GAP)
        edit_right_row.addWidget(self._done_btn)
        edit_right_row.addWidget(self._save_done_btn)

        self._right_stack = QStackedWidget()
        self._right_stack.setFixedWidth(_PAIR_WIDTH)
        self._right_stack.addWidget(run_right)  # _TB_RUN  = 0
        self._right_stack.addWidget(edit_right)  # _TB_EDIT = 1

        # ── The right-hand end, grouped by what each cluster acts on ─
        # Widening scope, left to right: the document, then the view of it, then the
        # way out of the mode.  Every cluster is a fixed pair-width and the empty page
        # of the edit stack reserves its slot in Run mode, so nothing here moves when
        # the mode changes -- only the mode toggle is anchored by the right margin.
        tools = QWidget()
        tools_row = QHBoxLayout(tools)
        tools_row.setContentsMargins(0, 0, 0, 0)
        tools_row.setSpacing(_GROUP_GAP)
        tools_row.addWidget(self._edit_tools_stack)
        tools_row.addWidget(view_tools)
        tools_row.addWidget(self._right_stack)

        # ── Validation badge — shown left of center when errors/warnings exist ──
        # ── Persistent header row (Bar B) ─────────────────────────────
        # The two stretches this used to hold the clusters apart with are now the
        # bot's identity and its run status: the bar above the graph owns the bot.
        self._toolbar = BotHeader(run_controls, tools)
        toolbar = self._toolbar
        self._toolbar.metadata_changed.connect(self.config_changed)

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
        # what the pill on its own failed to do.
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
            f"QLabel {{ border: none; color: {THEME.text}; font-size: {THEME.font_body_px}px; padding: 0; }}"
            f"QPushButton {{ border: none; background: transparent; color: {THEME.text_muted}; font-size: {THEME.font_body_px + 2}px; }}"
            f"QPushButton:hover {{ color: {THEME.text}; }}"
        )
        self._focus_crumb_label = QLabel("Focus Mode")
        self._focus_hint_label = QLabel("Esc to exit")
        _close_focus_btn = IconButton("dismiss", tooltip="Exit Focus Mode", size=22, icon_size=12)
        _close_focus_btn.clicked.connect(self.exit_focus_mode)
        _crumb_row = QHBoxLayout(self._focus_crumb)
        _crumb_row.setContentsMargins(8, 4, 6, 4)
        _crumb_row.setSpacing(6)
        _crumb_row.addWidget(self._focus_crumb_label)
        _crumb_row.addWidget(self._focus_hint_label)
        _crumb_row.addWidget(_close_focus_btn)
        self._focus_crumb.adjustSize()
        self._focus_crumb.setVisible(False)

        # ── Editing-subgraph crumb — below the focus crumb when both show ────
        self._edit_crumb = EditSubgraphCrumb(self._view)
        self._edit_crumb.save_requested.connect(self.save_subgraph_to_library)
        self._edit_crumb.discard_requested.connect(self.discard_subgraph_edit)
        self._edit_crumb.setVisible(False)

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
        self._param_panel.override_toggled.connect(self._on_override_toggled)
        self._param_panel.region_property_changed.connect(self._on_region_property_changed)
        self._param_panel.detach_requested.connect(self._on_detach_requested)
        self._param_panel.edit_subgraph_requested.connect(self.begin_subgraph_edit)
        self._param_panel.expose_input_requested.connect(self._on_expose_input_requested)
        self._param_panel.free_toggled.connect(self.set_param_free)
        self._param_panel.label_override_reset.connect(self._on_label_override_reset)
        self._param_panel.definition_changed.connect(self._on_definition_changed)
        self._scene.save_region_as_subgraph_requested.connect(self.save_region_as_subgraph)
        self._scene.edit_subgraph_requested.connect(self.begin_subgraph_edit)
        self._scene.expose_input_requested.connect(self._on_expose_input_requested)
        self._scene.selectionChanged.connect(self._on_selection_changed)
        self._scene.focus_region_requested.connect(self.enter_focus_mode)

        # ── Placeholder — shown until the first bot is loaded ────────
        self._placeholder = _BotLibraryPanel(self)
        self._placeholder.new_bot_requested.connect(self.new_bot_requested)
        self._placeholder.open_bot_requested.connect(self.open_bot_requested)
        self._placeholder.open_recent_requested.connect(self.open_recent_requested)
        self._placeholder.show()
        # Bar B is the *bot's* header, so with no bot there is nothing for it to say
        # and nothing its buttons can act on. Shown anyway it read as a second strip
        # of chrome above the library, which is the one moment the library needs to
        # be the whole surface.
        self._toolbar.setVisible(False)

    # ------------------------------------------------------------------
    # Public API — bot loading
    # ------------------------------------------------------------------

    def setup(self, config: BotConfig) -> None:
        """Load a BotConfig. Enters RUN mode with the graph ready."""
        self._undo_mgr.clear()
        self._placeholder.setVisible(False)
        self._toolbar.setVisible(True)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        sm = SettingsManager.instance()
        self._minimap.setVisible(sm.get("canvas", "minimap_visible", default=True))
        show_failure = sm.get("canvas", "show_failure_edges", default=True)
        self._failure_edges_btn.setChecked(show_failure)
        self.exit_focus_mode()
        self._close_search()
        self._end_subgraph_edit()
        self._tab_idx = 0
        self._config = config
        self._compose_report = ComposeReport()
        self._toolbar.set_config(config)
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
        self._end_subgraph_edit()
        self._scene.teardown()
        self._compose_report = ComposeReport()
        self._update_validation_badge()
        self._config = None
        self._toolbar.set_config(None)
        self._active_state = None
        self._edit_btn.setEnabled(False)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._minimap.setVisible(False)
        self._toolbar.setVisible(False)
        self._placeholder.refresh()
        self._placeholder.setVisible(True)
        self._reposition_placeholder()

    def set_compose_report(self, report: ComposeReport) -> None:
        """Record how each linked region composed, and redraw the validation banner from it.

        A separate call rather than a ``setup`` parameter so every existing caller and
        test of ``setup`` is untouched. ``setup`` and ``close_bot`` clear it.
        """
        self._compose_report = report
        self._scene.refresh_region_states(report)
        self._update_validation_badge()

    @property
    def compose_report(self) -> ComposeReport:
        return self._compose_report

    def set_subgraph_library(self, library: SubgraphLibrary | None) -> None:
        """The library the region page reads definitions from and a pasted instance composes from."""
        self._subgraph_library = library
        self._scene.set_subgraph_library(library)

    def _definition_for(self, region) -> SubgraphDefinition | None:
        if self._subgraph_library is None or region is None or region.source is None:
            return None
        return self._subgraph_library.get(region.source.subgraph_id)

    def replace_config(self, config: BotConfig, desc: str | None = None) -> None:
        """Swap the loaded config for ``config`` and rebind every surface to it.

        The same path undo takes (``_restore``), which is what makes the swap safe:
        the scene, header and properties panel are all rebuilt from the new object
        and ``config_changed`` tells the controller and sidebars to follow. In EDIT
        mode a ``desc`` pushes the outgoing config onto the undo stack first, so the
        swap is one undoable step; in RUN mode there is no stack to push to.
        """
        if self._config is None:
            return
        if desc is not None:
            self._on_about_to_change(desc)
        self._restore(config)
        self._refresh_undo_actions()

    def set_bot_file(self, filename: str | None) -> None:
        """Name the file the loaded bot came from, shown in the header."""
        self._toolbar.set_file(filename)

    @property
    def header_visible(self) -> bool:
        """Whether Bar B is showing.  It is hidden while no bot is loaded."""
        return self._toolbar.isVisibleTo(self)

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
        self._reposition_placeholder()

    def _reposition_placeholder(self) -> None:
        """Fill whatever the header is not using.

        The placeholder is an overlay child rather than a page of a stack, so it is
        positioned by hand.  A hidden widget keeps its last geometry, so the header's
        height is only its own while it is showing -- otherwise the library would sit
        a bar's worth down the panel with nothing above it.
        """
        tb_h = self._toolbar.height() if self._toolbar.isVisibleTo(self) else 0
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
        if hasattr(self, "_edit_crumb"):
            self._edit_crumb.adjustSize()
            below = self._focus_crumb.height() + 6 if self._focus_crumb.isVisible() else 0
            self._edit_crumb.move(vx + 8, vy + 8 + below)
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
        """Show the run/edit state in Bar B, and announce it for everyone else.

        The header is written to directly rather than through the signal: the status
        lives here now, and routing it out to MainWindow and back would make the
        bar above the graph depend on a window that may not have connected yet.
        """
        if self._mode == BotMode.EDIT:
            self._publish_status(THEME.text_muted, "Editing", "", "")
            return
        color = _status_dot_color(self._run_state)
        if self._run_state == _IDLE:
            self._publish_status(color, "Idle", "", "")
        elif self._run_state == _RUNNING:
            self._publish_status(color, "Running", self._active_state_label(), self._elapsed_str())
        elif self._run_state == _PAUSED:
            self._publish_status(color, "Paused", self._active_state_label(), self._elapsed_str())

    def _publish_status(self, color: str, status: str, state_label: str, elapsed: str) -> None:
        self._toolbar.set_status(color, status, elapsed)
        self.bot_status_changed.emit(color, status, state_label, elapsed)

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
        self._param_panel.set_zone_suggestions_getter(fn)

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
            self._apply_pill_stylesheets()
            # The dot's colour is resolved here, not in the header, so a theme
            # switch has to hand it over again.
            self._emit_bot_status()
            if self._focus_region_id is not None and self._config is not None:
                region = next((r for r in self._config.regions if r.id == self._focus_region_id), None)
                if region is not None:
                    self._apply_focus_styles(THEME.region_color(region.color))

    def _set_run_icon(self, icon_name: str) -> None:
        """Set the run/pause button icon.

        Kept as a named method although it is now one call: four call sites say
        "the transport shows play again", and none of them should have to know
        that re-tinting on a theme switch is the button's own business.
        """
        self._run_pause_btn.set_icon_name(icon_name)

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
        # carried 9,589 of these — a fifth of the file.
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
        if not self.resolve_subgraph_edit():
            return
        self._apply_mode(BotMode.RUN)
        self.mode_changed.emit(BotMode.RUN)
        self.config_changed.emit()

    def _on_save_done_clicked(self) -> None:
        if not self.resolve_subgraph_edit():
            return
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
            f"QLabel {{ border: none; color: #ffffff; font-size: {THEME.font_body_px}px; font-weight: 600; padding: 0; }}"
            f"QPushButton {{ border: none; background: transparent; color: #ffffff; font-size: {THEME.font_body_px + 2}px; }}"
            "QPushButton:hover { color: #000000; }"
        )
        self._focus_hint_label.setStyleSheet(
            f"border: none; color: rgba(255, 255, 255, 190); font-size: {THEME.font_small_px}px; font-weight: 400; padding: 0;"
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
        # without explanation: the camera never moved, so a region that was small
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
        self._toolbar.set_editable(mode == BotMode.EDIT)
        if mode == BotMode.RUN:
            self._edit_tools_stack.setCurrentIndex(_TB_RUN)
            self._right_stack.setCurrentIndex(_TB_RUN)
            self._favorites_bar.setVisible(False)
            self._scene.set_interactive(False)
            self._param_panel.show_idle()
            self._undo_mgr.set_enabled(False)
            self._undo_mgr.cancel_debounce()
            # The transport is on show in both modes now, so returning from Edit has to
            # put its enabled state back -- _apply_run_btn_state is a no-op while the
            # mode is EDIT, so nothing else would.
            self._apply_run_btn_state()
            self._emit_bot_status()
        else:
            self._edit_tools_stack.setCurrentIndex(_TB_EDIT)
            self._right_stack.setCurrentIndex(_TB_EDIT)
            self._favorites_bar.setVisible(True)
            self._scene.set_interactive(True)
            self._undo_mgr.set_enabled(True)
            # Visible but not usable, and the tooltip says why rather than leaving a
            # dead button to be poked at.
            self._run_pause_btn.setEnabled(False)
            self._run_pause_btn.setToolTip("Finish editing to run")
            self.stop_button.setEnabled(False)
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

    def refresh_edge_flags(self) -> None:
        """Re-derive the dotted-pen indicator on every edge from the config."""
        self._scene.refresh_all_edge_flags()

    def _on_scene_changed(self) -> None:
        self._refresh_node_labels()
        self._refresh_node_returns()
        self._refresh_initial()
        self._refresh_terminal()
        self._refresh_recovery()
        self._refresh_errors()
        self._scene.refresh_region_states(self._compose_report)
        self._update_validation_badge()
        self._minimap.fit_to_scene()
        if self._search_bar.isVisible():
            self._on_search_text_changed(self._search_bar.current_text())
        self.config_changed.emit()
        self._refresh_undo_actions()

    def _on_initial_state_requested(self, state_id: str) -> None:
        """Move the initial-state role to *state_id*."""
        self._set_fsm_role("initial", state_id, "Change initial state")

    def _on_recovery_state_requested(self, state_id: str) -> None:
        """Move the recovery-state role to *state_id*."""
        self._set_fsm_role("recovery_state", state_id, "Change recovery state")

    def _set_fsm_role(self, field: str, state_id: str, undo_desc: str) -> None:
        """Assign a bot-wide FSM role field to *state_id*.

        The panel asks and the canvas assigns, because the panel holds no BotConfig
        and because the undo snapshot has to be pushed *before* the mutation.
        ``_on_scene_changed`` then does the rest: it moves the badge, revalidates
        (moving the initial state changes which nodes are reachable), and
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
        if self._param_panel.is_locked:
            self._sync_overrides_from_shown_state()
        # Refresh the dotted-pen flag indicator on the selected edge when flags change.
        if self._param_panel._transition is not None:
            self._scene.refresh_edge_flags(self._param_panel._transition)
            self._config.sort_transitions()
        # Emit config change immediately for autosave/other consumers, but defer the
        # expensive full validation pass (O(n²) with large graphs) until typing pauses.
        self.config_changed.emit()
        self._refresh_undo_actions()
        self._validation_timer.start()

    def _sync_overrides_from_shown_state(self) -> None:
        """Copy an edited override, free param or label of a locked member into its instance's ``source``.

        The panel writes ``state.params`` and ``state.label`` as for any node; on
        a locked member the editable rows are the overridden and free ones and
        the label, and their values also have to land in ``source`` or the next
        compose would put the definition's back. A free param or label is
        recorded only while it differs from the subgraph's (``sync_free_overrides``).
        """
        state = self._param_panel.shown_state
        region = self._scene.linked_region_of(state.id) if state is not None else None
        if state is None or region is None or region.source is None:
            return
        local = local_id(region.id, state.id)
        if local is None:
            return
        defn = self._definition_for(region)
        free = free_keys(defn, local) if defn is not None else frozenset()
        for key in list(region.source.param_overrides.get(local, {})):
            if key in state.params and key not in free:
                set_param_override(region.source, local, key, state.params[key])
        if defn is not None:
            sync_free_overrides(region, defn, state)

    def _on_label_override_reset(self, state_id: str) -> None:
        """Drop a member's label override and show the definition's label again. One undo step."""
        config = self._scene.config
        region = self._scene.linked_region_of(state_id)
        if config is None or region is None or region.source is None:
            return
        local = local_id(region.id, state_id)
        state = next((st for st in config.states if st.id == state_id), None)
        defn = self._definition_for(region)
        def_state = next((s for s in defn.states if s.id == local), None) if defn is not None else None
        if local is None or state is None:
            return
        self._undo_mgr.cancel_debounce()
        self._on_about_to_change("Reset label")
        clear_label_override(region.source, local)
        if def_state is not None:
            state.label = def_state.label
            node = self._scene.node_items.get(state_id)
            if node is not None:
                node.set_label(state.label)
        self._on_scene_changed()
        if self._param_panel.shown_region is region:
            self._show_region_page(region)
        elif self._param_panel.shown_state is state:
            self._show_state_page(state)

    def _on_override_toggled(self, state_id: str, key: str, active: bool) -> None:
        """Start or drop a per-instance override of one member param.

        Starting one records the member's current (definition) value, so nothing
        visible changes until the user edits the now-unlocked field. Dropping one
        re-resolves the member from the library so the definition's value shows
        again; without the library the value stays until the next compose.
        """
        config = self._scene.config
        region = self._scene.linked_region_of(state_id)
        if config is None or region is None or region.source is None:
            return
        local = local_id(region.id, state_id)
        state = next((st for st in config.states if st.id == state_id), None)
        if local is None or state is None:
            return
        self._undo_mgr.cancel_debounce()
        self._on_about_to_change("Override param" if active else "Reset param")
        if active:
            set_param_override(region.source, local, key, state.params.get(key))
        else:
            clear_param_override(region.source, local, key)
            defn = self._definition_for(region)
            if defn is not None:
                reresolve_member_params(config, region, defn)
        self._on_scene_changed()
        if self._param_panel.shown_state is state:
            self._show_state_page(state)
        elif self._param_panel.shown_region is region:
            self._show_region_page(region)

    def _on_region_property_changed(self, debounced: bool) -> None:
        """The region page wrote label, colour or inputs into the shown region."""
        region = self._param_panel.shown_region
        config = self._scene.config
        if region is None or config is None:
            return
        if debounced and self._config is not None and self._mode == BotMode.EDIT:
            self._undo_mgr.begin_debounce(self._config, "Edit region")
        ri = self._scene.region_items.get(region.id)
        if ri is not None:
            ri.set_label(region.label)
            ri.set_color(region.color)
        defn = self._definition_for(region)
        if defn is not None:
            reresolve_member_params(config, region, defn)
        self.config_changed.emit()
        self._refresh_undo_actions()
        self._validation_timer.start()

    def _on_detach_requested(self, region_id: str) -> None:
        self._undo_mgr.cancel_debounce()
        self._scene.detach_region(region_id)
        self._on_selection_changed()

    # ------------------------------------------------------------------
    # Subgraph authoring: save-as, edit in place, push, discard, expose
    # ------------------------------------------------------------------

    @property
    def editing_region_id(self) -> str | None:
        """The linked region being edited in place, or None."""
        return self._editing_region_id

    @property
    def editing_definition(self) -> SubgraphDefinition | None:
        """The working copy of the definition being edited (inputs and metadata), or None."""
        return self._editing_defn

    @property
    def edit_crumb_text(self) -> str:
        return self._edit_crumb.text

    def save_region_as_subgraph(self, region_id: str, *, name: str | None = None, description: str | None = None):
        """Extract an ordinary region into the user library and link the region to it.

        Asks for a name and description unless given. The region's members are
        renamed to the derived form and every combat assignment of this bot that
        keyed on an old id is re-keyed through the strategy store, so nothing the
        user configured against those states falls silent. One undo step.
        """
        config = self._scene.config
        library = self._subgraph_library
        if config is None or library is None or self._mode != BotMode.EDIT:
            return False
        region = next((r for r in config.regions if r.id == region_id), None)
        if region is None or region.source is not None:
            return False
        if name is None:
            dlg = SubgraphMetaDialog(self, name=region.label)
            if not dlg.exec():
                return False
            name, description = dlg.name, dlg.description
        work = config.model_copy(deep=True)
        try:
            defn, source = extract_definition(
                work, region_id, new_id=str(uuid4()), name=name, description=description, schemas=NODE_PARAM_SCHEMAS
            )
        except ValueError as exc:
            QMessageBox.critical(self, "Save Region as Subgraph", str(exc))
            return False
        library.save(defn)
        instance_id = mint_instance_id(name, taken_ids(work))
        id_map = convert_region_to_instance(work, region_id, defn, source, instance_id=instance_id)
        composed, report = compose_bot(work, library)
        self._undo_mgr.cancel_debounce()
        self.replace_config(composed, "Save region as subgraph")
        self.set_compose_report(report)
        try:
            rekeyed = rekey_bot_strategy_states(config.id, id_map)
        except Exception:
            logger.exception("Re-keying combat assignments for subgraph %s failed", defn.id)
            rekeyed = 0
        logger.info(
            "Saved region '%s' to the library as subgraph %s; %d combat strategies re-keyed", name, defn.id, rekeyed
        )
        return True

    def begin_subgraph_edit(self, region_id: str) -> bool:
        """Unlock one instance for editing in place, showing the definition's own values.

        Inputs and overrides are the instance's, so they are set aside for the edit
        and the members show what will be pushed; the compose after Save or Discard
        puts them back. The rewrite is one undo step; the editing state itself is
        not in the config and is not undoable.
        """
        config = self._scene.config
        if config is None or self._mode != BotMode.EDIT:
            return False
        if self._editing_region_id is not None and not self.resolve_subgraph_edit():
            return False
        region = next((r for r in config.regions if r.id == region_id), None)
        if region is None or region.source is None:
            return False
        defn = self._definition_for(region)
        if defn is None:
            QMessageBox.warning(
                self,
                "Edit Subgraph",
                "The library has no definition for this instance, so there is nothing to edit.\n\n"
                "Detach it from the library to make its states your own.",
            )
            return False
        self._undo_mgr.cancel_debounce()
        self._on_about_to_change("Edit subgraph")
        definition_values(config, region, defn)
        self._editing_region_id = region_id
        self._editing_defn = defn.model_copy(deep=True)
        self._scene.set_editing_region(region_id)
        self._edit_crumb.set_subgraph_name(defn.name)
        self._edit_crumb.setVisible(True)
        self._reposition_banner()
        self._on_scene_changed()
        self._on_selection_changed()
        return True

    def save_subgraph_to_library(self) -> bool:
        """Push the edited region back as the next version, then recompose every instance of it here.

        A member added while editing is renamed to the derived form first, so the
        compose that follows finds it in place rather than reading its external
        edges as an update that removes it. A definition that would contain itself
        is refused before anything is written. One undo step.
        """
        config = self._scene.config
        library = self._subgraph_library
        rid = self._editing_region_id
        previous = self._editing_defn
        if config is None or library is None or rid is None or previous is None:
            return False
        work = config.model_copy(deep=True)
        region = next((r for r in work.regions if r.id == rid), None)
        if region is None:
            self._end_subgraph_edit()
            return False
        rename_states(work, added_member_ids(region))
        try:
            pushed, warnings = push_back(work, rid, previous, schemas=NODE_PARAM_SCHEMAS)
        except ValueError as exc:
            QMessageBox.critical(self, "Save to Library", str(exc))
            return False
        if contains_subgraph(pushed, pushed.id, library):
            QMessageBox.critical(
                self,
                "Save to Library",
                f"Subgraph '{pushed.name}' would contain itself, which can never finish composing. "
                "Remove the nested instance of it first.",
            )
            return False
        for warning in warnings:
            logger.warning("Saving subgraph '%s': %s", pushed.name, warning)
        library.save(pushed)
        self._end_subgraph_edit()
        composed, report = compose_bot(work, library)
        self._undo_mgr.cancel_debounce()
        self.replace_config(composed, "Save subgraph to library")
        self.set_compose_report(report)
        logger.info("Saved subgraph '%s' version %d to the library", pushed.name, pushed.version)
        return True

    def discard_subgraph_edit(self) -> None:
        """Drop the in-place edit: members added go, and the region recomposes from the library."""
        config = self._scene.config
        library = self._subgraph_library
        rid = self._editing_region_id
        if config is None or library is None or rid is None:
            self._end_subgraph_edit()
            return
        work = config.model_copy(deep=True)
        region = next((r for r in work.regions if r.id == rid), None)
        if region is not None:
            drop_states(work, set(added_member_ids(region)))
        self._end_subgraph_edit()
        composed, report = compose_bot(work, library)
        self._undo_mgr.cancel_debounce()
        self.replace_config(composed, "Discard subgraph edit")
        self.set_compose_report(report)

    def resolve_subgraph_edit(self) -> bool:
        """Settle an open edit with Save / Discard / Cancel. False means the user cancelled.

        Synchronous, for the same reason the run slot's dialogs are: every caller
        is a Qt slot, never an asyncio task.
        """
        if self._editing_region_id is None:
            return True
        name = self._editing_defn.name if self._editing_defn is not None else "subgraph"
        reply = QMessageBox.question(
            self,
            "Editing Subgraph",
            f"Save your changes to subgraph '{name}' to the library?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return False
        if reply == QMessageBox.StandardButton.Save:
            return self.save_subgraph_to_library()
        self.discard_subgraph_edit()
        return True

    def _end_subgraph_edit(self) -> None:
        """Clear the editing state without touching the config. The callers decide what the config does."""
        self._editing_region_id = None
        self._editing_defn = None
        if hasattr(self, "_edit_crumb"):
            self._edit_crumb.setVisible(False)
        self._scene.set_editing_region(None)

    def expose_input(
        self,
        state_id: str,
        key: str,
        *,
        existing: str | None = None,
        new_input: SubgraphInput | None = None,
    ) -> bool:
        """Bind a member param of the region being edited to an input of the working definition.

        ``existing`` names an input already declared; otherwise ``new_input`` is
        appended. The binding's state is the member's local id -- a state added
        while editing keeps its plain id as its local id, as push_back does.
        """
        defn = self._editing_defn
        rid = self._editing_region_id
        if defn is None or rid is None or state_id not in self._scene.editing_members():
            return False
        local = local_id(rid, state_id) or state_id
        binding = SubgraphInputBinding(state=local, param=key)
        if existing is not None:
            target = next((i for i in defn.inputs if i.name == existing), None)
            if target is None:
                return False
            if not any(b.state == local and b.param == key for b in target.bindings):
                target.bindings.append(binding)
        elif new_input is not None:
            if any(i.name == new_input.name for i in defn.inputs):
                return False
            defn.inputs.append(new_input.model_copy(update={"bindings": [binding]}))
        else:
            return False
        self._on_definition_changed()
        return True

    def set_param_free(self, state_id: str, key: str, free: bool) -> bool:
        """Mark one param of a member of the region being edited as free (consumers set it) or locked.

        Written to the working definition, like Expose; it reaches the library
        with the push. False when no edit is open or ``state_id`` is not a member.
        """
        defn = self._editing_defn
        rid = self._editing_region_id
        if defn is None or rid is None or state_id not in self._scene.editing_members():
            return False
        local = local_id(rid, state_id) or state_id
        keys = [k for k in defn.free_params.get(local, []) if k != key]
        if free:
            keys.append(key)
        if keys:
            defn.free_params[local] = keys
        else:
            defn.free_params.pop(local, None)
        return True

    def _on_expose_input_requested(self, state_id: str, key: str) -> None:
        config = self._scene.config
        defn = self._editing_defn
        if config is None or defn is None or state_id not in self._scene.editing_members():
            return
        state = next((st for st in config.states if st.id == state_id), None)
        if state is None:
            return
        field = next((f for f in NODE_PARAM_SCHEMAS.get(state.node_type, []) if f.key == key), None)
        if field is None:
            return
        dlg = ExposeInputDialog(
            defn.inputs,
            state_local_id=local_id(self._editing_region_id or "", state_id) or state_id,
            param_key=key,
            param_type=field.type,
            param_label=field.label,
            current_value=state.params.get(key, field.default),
            parent=self,
        )
        if not dlg.exec():
            return
        if dlg.choice is not None:
            self.expose_input(state_id, key, existing=dlg.choice)
        else:
            self.expose_input(state_id, key, new_input=dlg.new_input())

    def _on_definition_changed(self) -> None:
        """The working definition's inputs changed: the region page, if up, shows the new list."""
        region = self._param_panel.shown_region
        if region is not None and region.id == self._editing_region_id:
            self._show_region_page(region)

    def _show_state_page(self, state) -> None:
        config = self._scene.config
        if config is None:
            return
        region = self._scene.linked_region_of(state.id)
        locked = self._scene.is_locked(state.id)
        exposable = state.id in self._scene.editing_members()
        override_keys: frozenset = frozenset()
        free: frozenset = frozenset()
        bound: dict[str, str] = {}
        name = None
        if locked and region is not None and region.source is not None:
            local = local_id(region.id, state.id)
            override_keys = frozenset(region.source.param_overrides.get(local, {})) if local else frozenset()
            defn = self._definition_for(region)
            name = defn.name if defn is not None else region.label
            if defn is not None and local is not None:
                free = free_keys(defn, local)
        elif exposable and self._editing_defn is not None and self._editing_region_id is not None:
            local = local_id(self._editing_region_id, state.id) or state.id
            free = free_keys(self._editing_defn, local)
            bound = {b.param: i.name for i in self._editing_defn.inputs for b in i.bindings if b.state == local}
        self._param_panel.show_state(
            state,
            all_states=list(config.states),
            all_transitions=config.transitions,
            initial_id=config.initial,
            recovery_id=config.recovery_state,
            locked=locked,
            override_keys=override_keys,
            subgraph_name=name,
            exposable=exposable,
            free_keys=free,
            bound_inputs=bound,
        )

    def _show_region_page(self, region) -> None:
        config = self._scene.config
        if config is None:
            return
        editing = region.id == self._editing_region_id and self._editing_defn is not None
        self._param_panel.show_region(
            region,
            self._editing_defn if editing else self._definition_for(region),
            self._compose_report.regions.get(region.id),
            {st.id: (st.label or st.id) for st in config.states},
            editing=editing,
        )

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
        # Undo hands back a freshly-deserialised BotConfig, so every surface holding
        # the old object has to be re-bound or its edits go nowhere.
        self._toolbar.set_config(config)
        self._toolbar.set_editable(self._mode == BotMode.EDIT)
        self._scene.setup(config)
        self._apply_failure_edge_visibility(self._failure_edges_btn.isChecked())
        self._param_panel.show_idle()
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
        warning_lines.extend(self._orphan_flag_lines(config))
        sg_errors, sg_warnings = self._subgraph_lines(config)
        error_lines.extend(sg_errors)
        warning_lines.extend(sg_warnings)
        return error_lines, warning_lines

    def _subgraph_lines(self, config: BotConfig) -> tuple[list[str], list[str]]:
        """One line per linked region the last compose could not simply link.

        A ``cycle`` is an error (nothing runnable exists for it); ``held``, ``missing``
        and ``invalid`` are warnings, since the snapshot in the file still runs; a
        stale override on a ``linked`` region is a warning too. A region deleted since
        the report was taken is skipped rather than reported on.
        """
        labels = {r.id: (r.label or r.id) for r in config.regions if r.source is not None}
        errors: list[str] = []
        warnings: list[str] = []
        for region_id, status in self._compose_report.regions.items():
            label = labels.get(region_id)
            if label is None:
                continue
            if status.kind == "cycle":
                errors.extend(f"Region {label}: subgraph cycle {r}" for r in status.reasons)
            elif status.kind in ("held", "missing", "invalid"):
                warnings.extend(f"Region {label}: needs attention \u2014 {r}" for r in status.reasons)
            else:
                warnings.extend(f"Region {label}: {r}" for r in status.reasons)
        return errors, warnings

    def _orphan_flag_lines(self, config: BotConfig) -> list[str]:
        """One warning per transition guarding on an option nothing declares.

        Declarations are derived from the nodes in the graph, so cutting a node
        can take an option out from under a transition that reads it -- and
        ``_flag_<name>`` then quietly evaluates False rather than erroring. The
        bot still runs; it just stops taking that edge, which is the kind of
        thing that is only noticed hours into a run.
        """
        labels = {s.id: (s.label or s.id) for s in config.states}
        lines: list[str] = []
        for trans, option in orphaned_flag_options(config):
            where = f"{labels.get(trans.source, trans.source)} → {labels.get(trans.dest, trans.dest)}"
            lines.append(f"{where}: condition reads option '{option}', which nothing declares")
        return lines

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
        config = self._scene.config
        # A whole region's members and nothing else: the region page, unless the
        # press landed on one member of a locked instance, whose own page it is.
        if config is not None and all(isinstance(i, NodeItem) for i in selected):
            selected_ids = {i.state_id for i in selected}
            region = next((r for r in config.regions if r.members and set(r.members) == selected_ids), None)
            if region is not None:
                pressed = self._scene.pressed_member_id
                pressed_state = next((st for st in config.states if st.id == pressed), None) if pressed else None
                if pressed_state is not None and pressed in selected_ids and self._scene.is_locked(pressed):
                    self._show_state_page(pressed_state)
                else:
                    self._show_region_page(region)
                self.tab_switch.emit("properties")
                return
        if isinstance(item, NodeItem) and config is not None:
            state = next((s for s in config.states if s.id == item.state_id), None)
            if state:
                self._show_state_page(state)
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
                        option_names = [opt.name for opt in compose_options(self._scene._config) if opt.type == "bool"]
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
