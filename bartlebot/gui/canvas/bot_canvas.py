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

from PyQt6.QtCore import QEvent, QRectF, QSize, Qt, QTimer, pyqtSignal
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
from bartlebot.bot.schema import (
    FALLTHROUGH_RETURNS,
    PORT_NODE_TYPES,
    BotConfig,
    SubgraphDefinition,
    SubgraphInput,
    SubgraphInputBinding,
    SubgraphPanel,
)
from bartlebot.bot_folders import ALL_BADGE, folder_badge, folder_caption
from bartlebot.bot_options import compose_options, orphaned_flag_options
from bartlebot.bot_tags import BOT_TAGS, TAG_GROUPS, normalize_tags, tag_group
from bartlebot.game_sizing import BOTCANVAS_MIN_HEIGHT, BOTCANVAS_MIN_WIDTH
from bartlebot.gui.canvas.bot_header import BotHeader
from bartlebot.gui.canvas.call_node_item import CallNodeItem, PortNodeItem
from bartlebot.gui.canvas.edge_item import EdgeItem
from bartlebot.gui.canvas.edit_scene import (
    EditScene,
    _EditView,
    _node_returns,
    _reachable_states,
    _validate_node,
    member_warnings,
)
from bartlebot.gui.canvas.favorites_bar import FavoritesBar
from bartlebot.gui.canvas.graph_view import _MinimapView
from bartlebot.gui.canvas.node_item import NodeItem
from bartlebot.gui.canvas.param_panel import _ParamPanel
from bartlebot.gui.canvas.subgraph_widgets import (
    Crumb,
    CrumbBar,
    CrumbMode,
    LinkInputDialog,
    SubgraphMetaDialog,
    ViewFrame,
)
from bartlebot.gui.canvas.undo_manager import UndoManager
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin
from bartlebot.gui.widgets.action_button import ActionButton, ActionStyle
from bartlebot.gui.widgets.card_art_tile import art_icon
from bartlebot.gui.widgets.icon_button import IconButton
from bartlebot.gui.widgets.picker_popup import SECTION_ICON_PX
from bartlebot.gui.widgets.search_field import SearchLineEdit
from bartlebot.gui.widgets.tag_picker import TagPickerButton
from bartlebot.gui.widgets.tag_pill import NeutralPill, TagChip, TagPill
from bartlebot.logging_setup import GUI_ONLY
from bartlebot.nodes.param_schema import narrowest_type
from bartlebot.nodes.registry import NODE_PARAM_SCHEMAS, NODE_SPECS
from bartlebot.paths import project_root
from bartlebot.settings_manager import SettingsManager
from bartlebot.strategy_store import rekey_bot_strategy_states
from bartlebot.subgraph_store import SubgraphLibrary
from bartlebot.subgraphs import (
    ComposeReport,
    compose_bot,
    convert_region_to_instance,
    definition_problems,
    extract_definition,
    mint_instance_id,
    node_panels,
    normalize_input_types,
    promoted_param_type,
    reresolve_member_params,
    sync_linked_values,
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


# What the failure-edge toggle cycles through. Three rather than two because a
# failure edge that merely goes to the recovery node is the one an author almost
# never needs to look at -- 28% of every edge in the shipped library before the
# default made them implicit -- while one going anywhere else is a real branch.
# See docs/RETURN_ROUTING.md.
FAILURE_EDGES_ALL = "all"
FAILURE_EDGES_HIDE_RECOVERY = "hide_recovery"
FAILURE_EDGES_NONE = "none"
_FAILURE_EDGE_CYCLE = (FAILURE_EDGES_ALL, FAILURE_EDGES_HIDE_RECOVERY, FAILURE_EDGES_NONE)
# The glyph carries the state, since the button has no label: a clear eye, an eye
# behind a broken slash, an eye behind a solid one.
_FAILURE_EDGE_ICONS = {
    FAILURE_EDGES_ALL: "failure_edges",
    FAILURE_EDGES_HIDE_RECOVERY: "failure_edges_some",
    FAILURE_EDGES_NONE: "failure_edges_none",
}
_FAILURE_EDGE_TOOLTIPS = {
    FAILURE_EDGES_ALL: "Failure/False edges: all shown (F)",
    FAILURE_EDGES_HIDE_RECOVERY: "Failure/False edges: recovery edges hidden (F)",
    FAILURE_EDGES_NONE: "Failure/False edges: all hidden (F)",
}


def _stored_failure_edge_mode(sm) -> str:
    """The saved mode, falling back to the bool this setting used to be.

    A settings.json written before the third state existed carries only
    ``show_failure_edges``, and reading it is the difference between honouring a
    user's hidden edges and silently turning them back on.
    """
    mode = sm.get("canvas", "failure_edge_mode", default=None)
    if mode in _FAILURE_EDGE_ICONS:
        return mode
    return FAILURE_EDGES_ALL if sm.get("canvas", "show_failure_edges", default=True) else FAILURE_EDGES_NONE


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


@dataclass
class CanvasDocument:
    """What the canvas holds for one open document, set aside while another is on top of it.

    A definition opened from a bot is a document of its own, with its own undo history;
    going back puts the bot's exactly as it was, camera included. ``take_document`` and
    ``restore_document`` are the handoff, and the controller's document stack holds these.
    """

    config: BotConfig
    undo: UndoManager
    camera: tuple | None
    report: ComposeReport
    mode: BotMode


@dataclass
class _ViewLevel:
    """One step in from the open document: a region focused, or a subgraph node's body viewed.

    ``kind`` is ``"region"`` (Focus Mode, either mode) or ``"instance"`` (Run mode
    only). ``camera`` is where the view was before this step, put back on the way out.
    """

    kind: str
    region_id: str
    camera: tuple


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
    #: Set on a card for a subgraph definition, which opens as a document rather than a file.
    subgraph_id: str | None = None


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
    Rewards is what a run yields, Activities is what it does), so ANDing across them
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
        self.open_requested.emit(self._meta.subgraph_id or str(self._meta.path.resolve()))
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
    open_subgraph_requested = pyqtSignal(str)  # subgraph id

    _RECENT = "Recent"
    _ALL = "All"
    #: Definitions from the subgraph library, listed after the folders. Not in All,
    #: which is the bots: a definition opens as a document to edit, never to run.
    _SUBGRAPHS = "Subgraphs"
    _SUBGRAPHS_KEY = "\0subgraphs"
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
        self._subgraph_metas: list[_BotMeta] = []
        self._subgraph_library: SubgraphLibrary | None = None
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
        # buttons has undone the grouping the separator just drew. That accent outline
        # is SECONDARY exactly, so the weight it already had is the weight it keeps: no
        # PRIMARY on this surface, since what the panel expects is that you open one of
        # the bots it is showing you.
        self._action_btns: list[ActionButton] = []
        for label, signal_name, icon, style in (
            ("New Bot", "new_bot_requested", "plus", ActionStyle.SECONDARY),
            # Not "Open…": in this panel every card already opens a bot, so the name
            # has to say what is different about this one, which is that it takes a
            # file from anywhere rather than an entry in the library.
            ("Open Bot File…", "open_bot_requested", "upload", ActionStyle.TERTIARY),
        ):
            btn = ActionButton(icon, label, style=style, fill=False)
            btn.clicked.connect(getattr(self, signal_name))
            top_layout.addWidget(btn)
            self._action_btns.append(btn)

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
        self._clear_btn: ActionButton | None = None
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

        if self._empty_hint is not None:
            self._empty_hint.setStyleSheet(f"color: {THEME.text_muted}; font-size: {THEME.font_small_px}px;")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_subgraph_library(self, library: SubgraphLibrary | None) -> None:
        """The library the Subgraphs folder lists. Read at the next refresh."""
        self._subgraph_library = library
        if self._listed:
            self.refresh()

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

        self._subgraph_metas = []
        if self._subgraph_library is not None:
            defns = sorted(
                (d for sid in self._subgraph_library.ids() if (d := self._subgraph_library.get(sid)) is not None),
                key=lambda d: d.name.casefold(),
            )
            self._subgraph_metas = [
                _BotMeta(
                    name=d.name,
                    description=d.description or "",
                    authors="",
                    drops=[],
                    tags=[],
                    version=str(d.version),
                    path=Path(f"{d.id}.json"),
                    subgraph_id=d.id,
                )
                for d in defns
            ]

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
        if self._subgraph_metas:
            self.add_category_row(self._SUBGRAPHS_KEY, self._SUBGRAPHS)
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
            # Sized to its own text at the end of the chip row, so fill=False.
            self._clear_btn = clear = ActionButton("dismiss", "Clear", fill=False)
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
        if key == self._SUBGRAPHS_KEY:
            return self._subgraph_metas
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
                card.open_requested.connect(
                    self.open_subgraph_requested if meta.subgraph_id else self.open_recent_requested
                )
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
    # (subgraph id): open a definition as a document -- from a call node, or from the
    # Subgraphs folder of the library. The controller owns the document stack.
    open_definition_requested = pyqtSignal(str)
    # A definition document's way out, and its save: (then go back).
    definition_back_requested = pyqtSignal()
    definition_save_requested = pyqtSignal(bool)
    # A crumb segment naming a document further down: (how many documents stay under it).
    document_back_requested = pyqtSignal(int)
    # The definition document's other two verbs: save a copy as a new definition, and
    # discard a modified built-in for the shipped one.
    definition_save_as_requested = pyqtSignal()
    definition_revert_requested = pyqtSignal()

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
        # call node's page and for re-resolving a member's params; written only by
        # Save Region as Subgraph.
        self._subgraph_library: SubgraphLibrary | None = None
        # The flat transitions behind the edge on the properties panel, when it is a
        # call node's: a flag edit on the first is copied to the rest.
        self._shown_edge_models: list = []

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
        # Each step in from the open document, innermost last: a region focused, or in
        # Run mode a subgraph node's body. The crumb bar's path is these, after the
        # documents under this one.
        self._levels: list[_ViewLevel] = []
        # The names of the documents under this one, outermost first: a definition
        # opened from a bot has the bot's. Handed in by the controller.
        self._doc_path: list[str] = []
        # Whether the open definition is a modified built-in, which the crumb offers to revert.
        self._can_revert = False
        # Region colour the focus overlays are painted in, or None if never focused.
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

        # Not checkable: three states, and the glyph rather than a checked look is
        # what reports which one -- see _FAILURE_EDGE_ICONS.
        self._failure_edge_mode = FAILURE_EDGES_ALL
        self._failure_edges_btn = _icon_btn("failure_edges", _FAILURE_EDGE_TOOLTIPS[FAILURE_EDGES_ALL])
        self._failure_edges_btn.clicked.connect(self._cycle_failure_edge_mode)

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
        #
        # A subgraph -- its body viewed in Run mode, or its definition open -- gets the
        # double line instead, in the accent: the call node's side bars echo it, so
        # "double means shared" holds everywhere. A region focused inside a definition
        # draws its single line just inside the double one.
        self._shared_frame = ViewFrame(self._view)
        self._shared_frame.set_shared()
        self._shared_frame.setVisible(False)
        self._focus_frame = ViewFrame(self._view)
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
        self._set_failure_edge_mode(_stored_failure_edge_mode(SettingsManager.instance()), persist=False)

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

        # ── Crumb bar — top-left of the graph view ───────────────────
        # Where the canvas is, and the way back out: Focus Mode on a region, a
        # subgraph node's body in Run mode, or a definition open as a document.
        self._crumb = CrumbBar(self._view)
        self._crumb.segment_clicked.connect(self._on_crumb_segment_clicked)
        self._crumb.close_requested.connect(self.step_back)
        self._crumb.save_requested.connect(lambda: self.definition_save_requested.emit(False))
        self._crumb.save_as_requested.connect(self.definition_save_as_requested)
        self._crumb.revert_requested.connect(self.definition_revert_requested)
        self._crumb.setVisible(False)

        # ── Keyboard shortcuts ────────────────────────────────────────
        fit_shortcut = QShortcut(QKeySequence("Ctrl+0"), self)
        fit_shortcut.activated.connect(self._view.fit_scene)

        search_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        search_shortcut.activated.connect(self._show_search_bar)

        # Alt+Left steps out one level, as a browser's Back does: out of a focused
        # region or a subgraph node's body, else out of a definition document.
        back_shortcut = QShortcut(QKeySequence("Alt+Left"), self)
        back_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        back_shortcut.activated.connect(self.step_back)

        failure_edges_shortcut = QShortcut(QKeySequence("F"), self._view)
        failure_edges_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        failure_edges_shortcut.activated.connect(self._cycle_failure_edge_mode)

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
        self._param_panel.edit_starting.connect(self._on_panel_edit_starting)
        self._param_panel.properties_changed.connect(self._on_param_changed)
        self._param_panel.label_committed.connect(self._on_node_label_committed)
        self._param_panel.edge_endpoints_changed.connect(self._scene.change_edge_endpoints)
        self._param_panel.edge_when_changed.connect(self._scene.change_edge_when)
        self._param_panel.select_edge_requested.connect(self._on_select_edge_requested)
        self._param_panel.select_state_requested.connect(self._on_select_state_requested)
        self._param_panel.initial_state_requested.connect(self._on_initial_state_requested)
        self._param_panel.recovery_state_requested.connect(self._on_recovery_state_requested)
        self._param_panel.region_property_changed.connect(self._on_region_property_changed)
        self._param_panel.detach_requested.connect(self._on_detach_requested)
        self._param_panel.open_definition_requested.connect(self.open_definition_of)
        self._param_panel.role_requested.connect(self._on_role_requested)
        self._param_panel.link_input_requested.connect(self._on_link_requested)
        self._param_panel.unlink_input_requested.connect(self.unlink_param)
        self._param_panel.promote_node_requested.connect(self._on_promote_node_requested)
        self._param_panel.definition_changed.connect(self._on_definition_changed)
        self._param_panel.add_port_requested.connect(self._on_add_port_requested)
        self._param_panel.delete_port_requested.connect(self._on_delete_port_requested)
        self._scene.save_region_as_subgraph_requested.connect(self.save_region_as_subgraph)
        self._scene.open_definition_requested.connect(self.open_definition_of)
        self._scene.selectionChanged.connect(self._on_selection_changed)
        self._scene.focus_region_requested.connect(self.enter_focus_mode)
        self._scene.view_instance_requested.connect(self.view_instance)

        # ── Placeholder — shown until the first bot is loaded ────────
        self._placeholder = _BotLibraryPanel(self)
        self._placeholder.new_bot_requested.connect(self.new_bot_requested)
        self._placeholder.open_bot_requested.connect(self.open_bot_requested)
        self._placeholder.open_recent_requested.connect(self.open_recent_requested)
        self._placeholder.open_subgraph_requested.connect(self.open_definition_requested)
        self._placeholder.show()
        # Bar B is the *bot's* header, so with no bot there is nothing for it to say
        # and nothing its buttons can act on. Shown anyway it read as a second strip
        # of chrome above the library, which is the one moment the library needs to
        # be the whole surface.
        self._toolbar.setVisible(False)

    # ------------------------------------------------------------------
    # Public API — bot loading
    # ------------------------------------------------------------------

    def setup(self, config: BotConfig, *, mode: BotMode = BotMode.RUN) -> None:
        """Load a BotConfig -- a bot, or a definition document -- and enter ``mode`` with the graph ready.

        Pass the mode here rather than calling ``enter_edit_mode`` afterwards: landing in
        Run mode first hid the favorites bar and re-laid out the whole canvas only for
        Edit mode to show it again, all inside one call that can take 200ms.
        """
        self._undo_mgr.clear()
        # Auto-layout when no positions are saved and the setting is enabled
        if (
            config.states
            and all(s.position is None for s in config.states)
            and SettingsManager.instance().get("canvas", "auto_layout_on_load", default=True)
        ):
            from bartlebot.gui.canvas.layout import auto_layout

            auto_layout(config)
        self._load(config, ComposeReport())
        self._view.fit_scene()
        self._minimap.fit_to_scene()
        self._apply_mode(mode)
        self.mode_changed.emit(mode)

    def _load(self, config: BotConfig, report: ComposeReport) -> None:
        """Bind every surface to ``config``: what ``setup`` and ``restore_document`` share."""
        self._placeholder.setVisible(False)
        self._toolbar.setVisible(True)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        sm = SettingsManager.instance()
        self._minimap.setVisible(sm.get("canvas", "minimap_visible", default=True))
        self._set_failure_edge_mode(_stored_failure_edge_mode(sm), persist=False)
        self.exit_focus_mode()
        self._close_search()
        self._tab_idx = 0
        self._config = config
        self._compose_report = report
        self._toolbar.set_config(config)
        self._scene.setup(config)
        self._scene.set_snap_enabled(sm.get("canvas", "snap_to_grid", default=False))
        self._scene.set_grid_style(sm.get("canvas", "grid_style", default="dots"))
        self._scene.refresh_region_states(report)
        self._apply_failure_edge_visibility()
        if not config.is_definition:
            self._doc_path = []
            self._can_revert = False
        self._refresh_crumb()
        self._show_default_page()
        self._reposition_banner()
        self._edit_btn.setEnabled(True)
        self._update_validation_badge()

    # ------------------------------------------------------------------
    # Documents: a definition opened on top of a bot
    # ------------------------------------------------------------------

    @property
    def is_definition(self) -> bool:
        """Whether the open document is a subgraph definition rather than a bot."""
        return self._config is not None and self._config.is_definition

    def take_document(self) -> CanvasDocument | None:
        """Set the open document aside, with its undo history and camera. The canvas is left to be ``setup`` again."""
        if self._config is None:
            return None
        # A focused region is not kept: the camera set aside is the one from before it.
        self.exit_focus_mode()
        self._undo_mgr.flush_debounce()
        self._scene.flush_positions()
        doc = CanvasDocument(
            config=self._config,
            undo=self._undo_mgr,
            camera=self._view.camera_state(),
            report=self._compose_report,
            mode=self._mode,
        )
        self._undo_mgr = UndoManager()
        return doc

    def restore_document(self, doc: CanvasDocument) -> None:
        """Put a document set aside by ``take_document`` back exactly as it was."""
        self._undo_mgr.cancel_debounce()
        self._undo_mgr = doc.undo
        self._load(doc.config, doc.report)
        if doc.camera is not None:
            self._view.restore_camera(doc.camera)
        self._minimap.fit_to_scene()
        self._apply_mode(doc.mode)
        self.mode_changed.emit(doc.mode)

    def set_definition_context(
        self, path: list[str], *, origin: str = "", used_by: int = 0, can_revert: bool = False
    ) -> None:
        """Name the documents under the open definition, and what the header says about it.

        ``path`` is their names, outermost first -- the crumb's segments before this
        one. ``can_revert`` says it is a modified built-in, which the crumb offers to
        put back.
        """
        if self._config is None or not self._config.is_definition:
            return
        self._doc_path = list(path)
        self._can_revert = can_revert
        self._refresh_crumb()
        # Re-read, since a save moved the version the header's pill shows.
        self._toolbar.set_config(self._config)
        self._toolbar.set_editable(self._mode == BotMode.EDIT)
        self._toolbar.set_definition_info(origin, used_by)
        self._reposition_banner()

    @property
    def crumb_bar(self) -> CrumbBar:
        return self._crumb

    def open_definition_of(self, region_id: str) -> None:
        """Ask for the definition a call node links to be opened as a document."""
        config = self._scene.config
        region = next((r for r in config.regions if r.id == region_id), None) if config is not None else None
        if region is None or region.source is None:
            return
        if self._definition_for(region) is None:
            QMessageBox.warning(
                self,
                "Open Definition",
                "The library has no definition for this subgraph, so there is nothing to open.\n\n"
                "Detach it from the library to make its states your own.",
            )
            return
        self.open_definition_requested.emit(region.source.subgraph_id)

    def close_bot(self) -> None:
        """Unload the current bot and return to the placeholder."""
        self._undo_mgr.clear()
        self._refresh_undo_actions()
        self.exit_focus_mode()
        self._close_search()
        self._doc_path = []
        self._scene.teardown()
        self._compose_report = ComposeReport()
        self._update_validation_badge()
        self._config = None
        self._refresh_crumb()
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
        self._placeholder.set_subgraph_library(library)

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
        panel.set_subgraph_library(self._subgraph_library)
        panel.open_subgraph_requested.connect(dlg.accept)
        panel.open_subgraph_requested.connect(self.open_definition_requested)
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
        if self.is_definition:
            self._run_pause_btn.setEnabled(False)
            self._run_pause_btn.setToolTip("A subgraph runs inside a bot: go back to run it")
        elif self._n_validation_errors > 0:
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
                if key == Qt.Key.Key_Escape and not mods and self._on_escape():
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
        if hasattr(self, "_crumb"):
            self._crumb.adjustSize()
            self._crumb.move(vx + 10, vy + 10)
        if hasattr(self, "_shared_frame"):
            self._shared_frame.setGeometry(vx, vy, vw, vh)
            # Inside the double line when both show, so a focused region reads as a
            # filter over the definition rather than in place of it.
            inset = 7 if self._shared_frame.isVisibleTo(self) else 0
            self._focus_frame.setGeometry(vx + inset, vy + inset, vw - 2 * inset, vh - 2 * inset)

    def _apply_canvas_settings(self) -> None:
        """Re-read canvas settings and update live widgets. Called after Settings dialog OK."""
        sm = SettingsManager.instance()
        self._minimap.setVisible(sm.get("canvas", "minimap_visible", default=True))
        self._scene.set_snap_enabled(sm.get("canvas", "snap_to_grid", default=False))
        self._scene.set_grid_style(sm.get("canvas", "grid_style", default="dots"))
        self._set_failure_edge_mode(_stored_failure_edge_mode(sm), persist=False)
        self._reposition_banner()

    def _apply_failure_edge_visibility(self) -> None:
        """Show or hide each failure edge according to the current mode.

        The middle mode asks where the edge *goes*, not what it is: an explicit
        failure edge into the recovery node says exactly what the default already
        does, so hiding it leaves a graph that reads like one written after the
        default existed. Every other failure edge is a real branch and stays.
        """
        mode = self._failure_edge_mode
        recovery = self._config.recovery_state if self._config else None
        shown = self._shown_ids()
        ends = {e: (src, dst) for src, _, dst, e in self._scene.edge_items} if shown is not None else {}
        for item in self._scene.items():
            # A call node's exit edge carries its port id as ``when``, so it is never
            # one of these: an exit is a named way out, not a failure fallthrough.
            if not isinstance(item, EdgeItem) or item.when not in FALLTHROUGH_RETURNS:
                continue
            if mode == FAILURE_EDGES_NONE:
                visible = False
            elif mode == FAILURE_EDGES_HIDE_RECOVERY:
                visible = any(t.dest != recovery for t in self._scene.transitions_of(item))
            else:
                visible = True
            if shown is not None:
                src, dst = ends.get(item, (None, None))
                visible = visible and src in shown and dst in shown
            item.setVisible(visible)

    @property
    def failure_edge_mode(self) -> str:
        """Which failure edges the canvas is currently drawing."""
        return self._failure_edge_mode

    def _cycle_failure_edge_mode(self) -> None:
        idx = _FAILURE_EDGE_CYCLE.index(self._failure_edge_mode)
        self._set_failure_edge_mode(_FAILURE_EDGE_CYCLE[(idx + 1) % len(_FAILURE_EDGE_CYCLE)])

    def _set_failure_edge_mode(self, mode: str, *, persist: bool = True) -> None:
        self._failure_edge_mode = mode if mode in _FAILURE_EDGE_ICONS else FAILURE_EDGES_ALL
        self._failure_edges_btn.set_icon_name(_FAILURE_EDGE_ICONS[self._failure_edge_mode])
        self._failure_edges_btn.setToolTip(_FAILURE_EDGE_TOOLTIPS[self._failure_edge_mode])
        if persist:
            sm = SettingsManager.instance()
            sm.set("canvas", "failure_edge_mode", value=self._failure_edge_mode)
            # Kept in step so a downgrade to a build that only knows the bool still
            # gets the closer of the two answers.
            sm.set("canvas", "show_failure_edges", value=self._failure_edge_mode != FAILURE_EDGES_NONE)
        self._apply_failure_edge_visibility()

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
            if self.focus_region_id is not None:
                self._focus_color = self._focus_frame.color()

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
        self._set_node_active(self._active_state, False)
        self._active_state = None
        self._emit_bot_status()
        logger.info("=== Bot stopped ===")

    def _set_node_active(self, state_id: str | None, active: bool):
        """Light or dim the item ``state_id`` is drawn as. A member lights its call node, titled with its name."""
        if not state_id:
            return None
        node = self._scene.node_items.get(state_id)
        if node is None:
            node = self._scene.call_node_of(state_id)
            if node is not None:
                state = next((s for s in self._config.states if s.id == state_id), None) if self._config else None
                node.set_active_member((state.label or state.id) if active and state is not None else None)
        if node is not None:
            node.set_active(active)
        return node

    def set_state(self, state_id: str) -> None:
        """Highlight the active FSM state node -- or, for a member of an instance, its call node."""
        self._set_node_active(self._active_state, False)
        self._active_state = state_id
        node = self._set_node_active(state_id, True)
        # Only a node on show: in a subgraph node's body this follows the run from
        # member to member, and a run that leaves the body is not chased out of it.
        if node is not None and node.isVisible():
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

    def exit_edit_mode(self) -> bool:
        """Switch back to Run mode, as the Done button does.

        Returns False, having changed nothing, in a definition document, which has no
        Run mode: its way out is back to the document it was opened from. The
        counterpart to :meth:`enter_edit_mode`, so that a caller outside the canvas can
        drive a round trip -- the layout probe and the height tests both do -- without
        reaching for the button's own slot.
        """
        if self.is_definition:
            return False
        self._apply_mode(BotMode.RUN)
        self.mode_changed.emit(BotMode.RUN)
        self.config_changed.emit()
        return True

    def _on_done_clicked(self) -> None:
        if self.is_definition:
            self.definition_back_requested.emit()
            return
        self.exit_edit_mode()

    def _on_save_done_clicked(self) -> None:
        if self.is_definition:
            self.definition_save_requested.emit(True)
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
        if self._mode != BotMode.EDIT or self._scene.config is None:
            return
        node_type = SettingsManager.instance().favorited_hotkey_nodes[slot]
        if not node_type:
            return
        pos = self._scene.find_empty_grid_cell(self._canvas_origin())
        self._scene.create_state_at(node_type, pos)
        SettingsManager.instance().add_recently_used_node(node_type)

    def _add_node_at_viewport_center(self, node_type: str) -> None:
        """Add a node of the given type near the canvas origin, avoiding occupied grid cells."""
        if self._mode != BotMode.EDIT or self._scene.config is None:
            return
        pos = self._scene.find_empty_grid_cell(self._canvas_origin())
        self._scene.create_state_at(node_type, pos)
        SettingsManager.instance().add_recently_used_node(node_type)

    def _on_layout_clicked(self) -> None:
        if self._config is None or self._levels:
            return
        from bartlebot.gui.canvas.layout import auto_layout

        self._scene.flush_positions()
        self._undo_mgr.push(self._config, "Auto Layout")
        auto_layout(self._config)
        self._scene.setup(self._config)
        _sm = SettingsManager.instance()
        self._scene.set_snap_enabled(_sm.get("canvas", "snap_to_grid", default=False))
        self._scene.set_grid_style(_sm.get("canvas", "grid_style", default="dots"))
        self._apply_failure_edge_visibility()
        self._view.fit_scene()
        self._minimap.fit_to_scene()
        self.config_changed.emit()

    # ------------------------------------------------------------------
    # Stepping in and out: Focus Mode, a subgraph node's body, the crumb bar
    # ------------------------------------------------------------------

    @property
    def focus_region_id(self) -> str | None:
        """The region currently focused, or None when not in focus mode."""
        top = self._levels[-1] if self._levels else None
        return top.region_id if top is not None and top.kind == "region" else None

    @property
    def viewed_instances(self) -> list[str]:
        """The subgraph nodes whose body is on show in Run mode, outermost first."""
        return [lv.region_id for lv in self._levels if lv.kind == "instance"]

    @property
    def level_depth(self) -> int:
        """How many steps in from the open document the view is."""
        return len(self._levels)

    @property
    def focus_crumb_text(self) -> str:
        """The crumb bar's path, as one string."""
        return self._crumb.text

    @property
    def focus_hint_text(self) -> str:
        """The crumb bar's how-to-leave hint."""
        return self._crumb.hint_text

    @property
    def focus_color(self) -> QColor | None:
        """Colour the focus overlays are painted in, or None if never focused."""
        return self._focus_color

    @property
    def focus_frame(self) -> QWidget:
        """The single-line border drawn round the viewport while a region is focused."""
        return self._focus_frame

    @property
    def shared_frame(self) -> ViewFrame:
        """The double-line border drawn round the viewport in a subgraph: its body, or its definition."""
        return self._shared_frame

    @property
    def view(self) -> QWidget:
        """The graph view. Exposed for camera assertions and viewport geometry."""
        return self._view

    @property
    def header(self) -> QWidget:
        """Bar B. Exposed for height assertions: the bar's height is resolved by this
        canvas's layout, so a measurement that means anything has to be taken here
        rather than off the header in isolation."""
        return self._toolbar

    def _region(self, region_id: str):
        if self._config is None:
            return None
        return next((r for r in self._config.regions if r.id == region_id), None)

    def enter_focus_mode(self, region_id: str) -> None:
        """Isolate a region's nodes in the view; hide everything else.

        Focusing a second region replaces the first rather than nesting in it, and
        keeps the camera from before the first, so stepping out lands where the
        user started.
        """
        if self._config is None:
            return
        region = self._region(region_id)
        if region is None or region.source is not None:
            return
        if self._levels and self._levels[-1].kind == "region":
            camera = self._levels.pop().camera
        else:
            camera = self._view.camera_state()
        self._levels.append(_ViewLevel("region", region_id, camera))
        self._apply_levels()
        # Frame the region. Hiding everything else is what made the canvas look empty
        # without explanation: the camera never moved, so a region that was small
        # or off to one side stayed small and off to one side while the rest vanished.
        # Moving the camera is also the clearest signal that anything happened at all.
        region_item = self._scene.region_items.get(region_id)
        if region_item is not None:
            self._view.fit_rect(region_item.scene_bounds)

    def view_instance(self, region_id: str) -> None:
        """Show a subgraph node's body, read-only: Run mode's way in.

        A running bot's config is already composed, so the members are real states:
        the scene draws them at their definition positions, and this is Focus Mode
        over them. The run highlight lands on them by id with nothing added. In Edit
        mode the way in is the definition document instead (``open_definition_of``).
        """
        if self._config is None or self._mode != BotMode.RUN or not self._scene.is_call_node(region_id):
            return
        self._levels.append(_ViewLevel("instance", region_id, self._view.camera_state()))
        self._scene.set_expanded(self.viewed_instances)
        self._after_scene_rebuild()
        rect = QRectF()
        for did in self._scene.members_shown_for(region_id):
            rect = rect.united(self._scene.node_items[did].sceneBoundingRect())
        if not rect.isNull():
            self._view.fit_rect(rect)

    def step_out(self) -> bool:
        """Leave the innermost focused region or subgraph body, camera and all. False when there is none."""
        if not self._levels:
            return False
        level = self._levels.pop()
        if level.kind == "instance":
            self._scene.set_expanded(self.viewed_instances)
            self._after_scene_rebuild()
        else:
            self._apply_levels()
        self._view.restore_camera(level.camera)
        return True

    def step_out_to(self, depth: int) -> None:
        """Step out until ``depth`` levels are left: 0 is the open document itself."""
        while len(self._levels) > max(depth, 0):
            self.step_out()

    def exit_focus_mode(self) -> None:
        """Leave every focused region and subgraph body, back to the whole document."""
        if hasattr(self, "_crumb"):
            self.step_out_to(0)

    def step_back(self) -> None:
        """One level out: the crumb's close button and Alt+Left. Out of a definition when nothing is focused."""
        if self.step_out():
            return
        if self.is_definition:
            self.definition_back_requested.emit()

    def _on_escape(self) -> bool:
        """Esc dismisses what is open before it leaves anything. True when it did something.

        The search bar first, then a focused region or subgraph body, then the
        selection, and only then the definition document -- so a stray Esc never
        lands on the Save / Discard prompt.
        """
        if self._search_bar.isVisibleTo(self):
            self._close_search()
            return True
        if self.step_out():
            return True
        if self._mode == BotMode.EDIT and self._scene.selectedItems():
            self._scene.clearSelection()
            return True
        if self.is_definition:
            self.definition_back_requested.emit()
            return True
        return False

    def _on_crumb_segment_clicked(self, index: int) -> None:
        """An earlier segment: a document further down, the open one, or a level inside it."""
        n_docs = len(self._doc_path)
        if index < n_docs:
            self.document_back_requested.emit(index)
            return
        self.step_out_to(index - n_docs)

    def _after_scene_rebuild(self) -> None:
        """Put back what a rebuilt scene forgot: the levels' filter, failure edges, badges, and the run highlight."""
        linked = {r.id for r in self._config.regions if r.source is not None} if self._config else set()
        kept: list[_ViewLevel] = []
        for lv in self._levels:
            region = self._region(lv.region_id)
            if region is None or (lv.kind == "instance" and lv.region_id not in linked):
                break  # gone with an undo or a recompose: so is everything inside it
            kept.append(lv)
        self._levels = kept
        if self._scene.expanded != self.viewed_instances:
            self._scene.set_expanded(self.viewed_instances)
        self._scene.refresh_region_states(self._compose_report)
        if self._active_state:
            self._set_node_active(self._active_state, True)
        self._apply_levels()

    def _shown_ids(self) -> set[str] | None:
        """The display ids the innermost level keeps on show, or None when nothing is filtered."""
        top = self._levels[-1] if self._levels else None
        if top is None:
            return None
        if top.kind == "instance":
            return self._scene.members_shown_for(top.region_id)
        region = self._region(top.region_id)
        return {self._scene.display_id(m) for m in region.members} if region is not None else set()

    def _apply_levels(self) -> None:
        """Show what the innermost level keeps, and redraw the crumb and frames to match."""
        shown = self._shown_ids()
        top = self._levels[-1] if self._levels else None
        for sid, node in self._scene.node_items.items():
            node.setVisible(shown is None or sid in shown)
        for s, _, d, edge in self._scene.edge_items:
            edge.setVisible(shown is None or (s in shown and d in shown))
        for rid, ri in self._scene.region_items.items():
            ri.setVisible(shown is None or (top is not None and top.kind == "region" and rid == top.region_id))
        # The failure-edge toggle hides some of what the filter shows.
        self._apply_failure_edge_visibility()
        self._refresh_crumb()

    def _refresh_crumb(self) -> None:
        """Draw the crumb bar and the frames for where the canvas is now.

        A bot with one region focused keeps Focus Mode's own look -- its colour, a
        single line, "Focus Mode — <region>". Anything involving a subgraph is a path
        in the accent with the double line: the documents under this one, this one,
        then each level in, a focused region's segment wearing its colour.
        """
        if not hasattr(self, "_crumb"):
            return
        top = self._levels[-1] if self._levels else None
        top_region = self._region(top.region_id) if top is not None and top.kind == "region" else None
        definition = self.is_definition
        if self._config is None or (not definition and not self._levels):
            self._crumb.setVisible(False)
            self._shared_frame.setVisible(False)
            self._focus_frame.setVisible(False)
            return
        if not definition and not self.viewed_instances and top_region is not None:
            self._crumb.show_state(
                CrumbMode.FOCUS,
                [Crumb(f"Focus Mode — {top_region.label}")],
                hint="Esc to exit",
                color_slot=top_region.color,
                close_tip="Exit Focus Mode",
            )
            self._shared_frame.setVisible(False)
        else:
            segments = [Crumb(name) for name in self._doc_path] + [Crumb(self._config.name)]
            for lv in self._levels:
                region = self._region(lv.region_id)
                label = region.label if region is not None else lv.region_id
                segments.append(Crumb(label, region.color if lv is top and top_region is not None else None))
            if definition:
                mode, hint = CrumbMode.DEFINITION, ""
                close_tip = "Exit Focus Mode" if self._levels else "Close the definition and go back"
            else:
                mode, hint, close_tip = CrumbMode.VIEW, "read-only · Esc to exit", "Step out"
            self._crumb.show_state(mode, segments, hint=hint, can_revert=self._can_revert, close_tip=close_tip)
            self._shared_frame.setVisible(True)
        if top_region is not None:
            self._focus_frame.set_region(top_region.color)
            self._focus_color = self._focus_frame.color()
        self._focus_frame.setVisible(top_region is not None)
        self._crumb.setVisible(True)
        self._crumb.raise_()
        self._reposition_banner()

    # ------------------------------------------------------------------
    # Tab navigation
    # ------------------------------------------------------------------

    def _tab_navigate(self, direction: int) -> None:
        """Cycle node selection by BFS order and center the view on the node."""
        order = self._scene.tab_order()
        if not order:
            return
        selected = [item for item in self._scene.selectedItems() if isinstance(item, NodeItem)]
        if selected and selected[0].state_id in order:
            cur_idx = order.index(selected[0].state_id)
        else:
            cur_idx = self._tab_idx - direction  # step will land on _tab_idx
        next_idx = (cur_idx + direction) % len(order)
        self._tab_idx = next_idx
        node = self._scene.node_items.get(order[next_idx])
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
        for node in self._scene.node_items.values():
            node.set_search_match(False)

    def _on_search_text_changed(self, text: str) -> None:
        config = self._scene.config
        if not config:
            self._search_matches.clear()
            self._search_bar.set_count(0, 0)
            return
        q = text.strip().lower()
        matches: list[str] = []
        if q:
            for state in config.states:
                if q in (state.label or "").lower() or q in state.id.lower() or q in state.node_type.lower():
                    # A member is found as the call node it is drawn as.
                    did = self._scene.display_id(state.id)
                    if did not in matches:
                        matches.append(did)
            for region in config.regions:
                if region.source is not None and region.id in self._scene.node_items and q in region.label.lower():
                    if region.id not in matches:
                        matches.append(region.id)
        self._search_matches = matches
        self._search_match_idx = 0
        # Apply highlights
        for sid, node in self._scene.node_items.items():
            node.set_search_match(sid in matches, is_current=(bool(matches) and sid == matches[0]))
        if matches:
            first = self._scene.node_items.get(matches[0])
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
            old_node = self._scene.node_items.get(old)
            if old_node:
                old_node.set_search_match(True, is_current=False)
        new_node = self._scene.node_items.get(new)
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
            old_node = self._scene.node_items.get(old)
            if old_node:
                old_node.set_search_match(True, is_current=False)
        new_node = self._scene.node_items.get(new)
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
            self._show_default_page()
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
            if not self._scene.selectedItems():
                self._show_default_page()
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
        config = self._scene.config
        if config is not None and config.is_definition:
            # Links may have moved: each input takes its params' strictest type, and
            # every linked param its input's value.
            normalize_input_types(config, NODE_PARAM_SCHEMAS)
            sync_linked_values(config)
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
        if self._param_panel.showing_call:
            self._param_panel.call_page.refresh_roles()

    def _on_role_requested(self, field: str, state_id: str) -> None:
        """The call node's page moved a role onto the member one of its entry ports leads to."""
        desc = "Change initial state" if field == "initial" else "Change recovery state"
        self._set_fsm_role(field, state_id, desc)

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
        state = self._param_panel.shown_state
        if state is None:
            return
        node = self._scene.node_items.get(state.id)
        if node is not None:
            node.set_label(state.label)

    def _on_panel_edit_starting(self, desc: str) -> None:
        """Open the debounced undo step for a typed edit, before it lands in the model."""
        if self._config is not None and self._mode == BotMode.EDIT:
            self._undo_mgr.begin_debounce(self._config, desc)

    def _on_param_changed(self) -> None:
        # In a definition a linked param is its input's default: an edit to one is an
        # edit to the input, and to every other param linked to it.
        shown_state = self._param_panel.shown_state
        if shown_state is not None and self._config is not None and self._config.is_definition:
            sync_linked_values(self._config, edited_state=shown_state.id)
        # Refresh the dotted-pen flag indicator on the selected edge when flags change.
        shown = self._param_panel.shown_transition
        if shown is not None:
            # A call node's edge stands for several transitions and shows the first:
            # its conditions are the edge's, so the rest take the same ones.
            for sibling in self._shown_edge_models:
                if sibling is not shown:
                    sibling.flags = [f.model_copy() for f in shown.flags]
                    sibling.flag_op = shown.flag_op
                    sibling.flag_groups = [g.model_copy(deep=True) for g in shown.flag_groups]
                    sibling.flag_groups_op = shown.flag_groups_op
            self._scene.refresh_edge_flags(shown)
            self._config.sort_transitions()
        # Emit config change immediately for autosave/other consumers, but defer the
        # expensive full validation pass (O(n²) with large graphs) until typing pauses.
        self.config_changed.emit()
        self._refresh_undo_actions()
        self._validation_timer.start()

    def _on_region_property_changed(self) -> None:
        """The region or call node page wrote into the shown region: label, colour, or an instance's inputs."""
        region = self._param_panel.shown_region
        config = self._scene.config
        if region is None or config is None:
            return
        if region.source is None:
            ri = self._scene.region_items.get(region.id)
            if ri is not None:
                ri.set_label(region.label)
                ri.set_color(region.color)
        else:
            defn = self._definition_for(region)
            if defn is not None:
                reresolve_member_params(config, region, defn)
            self._scene.refresh_call_nodes()
            self._scene.refresh_errors()
        self.config_changed.emit()
        self._refresh_undo_actions()
        self._validation_timer.start()

    def _on_detach_requested(self, region_id: str) -> None:
        self._undo_mgr.cancel_debounce()
        self._scene.detach_region(region_id)
        self._on_selection_changed()

    # ------------------------------------------------------------------
    # Subgraph authoring: save a region as one; a definition's signature
    # ------------------------------------------------------------------

    def save_region_as_subgraph(self, region_id: str, *, name: str | None = None, description: str | None = None):
        """Extract an ordinary region into the user library, link the region to it, and open the definition.

        Asks for a name and description unless given. The region's members are
        renamed to the derived form and every combat assignment of this bot that
        keyed on an old id is re-keyed through the strategy store, so nothing the
        user configured against those states falls silent. One undo step. The new
        definition opens as a document on top of the bot, since a region just made
        reusable usually wants its params linked to inputs next.
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
            defn, source = extract_definition(work, region_id, new_id=str(uuid4()), name=name, description=description)
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
        self.open_definition_requested.emit(defn.id)
        return True

    def _definition_state(self, state_id: str):
        config = self._scene.config
        if config is None or config.definition is None:
            return None
        return next((s for s in config.states if s.id == state_id), None)

    def link_param(
        self,
        state_id: str,
        key: str,
        *,
        existing: str | None = None,
        new_input: SubgraphInput | None = None,
        shared: tuple[str, str] | None = None,
    ) -> bool:
        """Link one param of a definition's node to a subgraph input. One undo step.

        ``existing`` names an input already declared, ``shared`` is ``(group id, key)``
        of a promoted node's param, and otherwise ``new_input`` is declared with this
        one link. A linked param *is* its input's default, so the param takes the
        input's value -- or, for an input with no default yet, gives it its own.
        """
        state = self._definition_state(state_id)
        if state is None:
            return False
        meta = self._scene.config.definition
        if any(pn.member == state_id for pn in node_panels(meta)):
            return False  # a promoted node's params are its input group's already
        binding = SubgraphInputBinding(state=state_id, param=key)
        if existing is not None:
            target = next((i for i in meta.inputs if i.name == existing), None)
            if target is None:
                return False
            self._begin_definition_edit("Link to subgraph input")
            if not any(b.state == state_id and b.param == key for b in target.bindings):
                target.bindings.append(binding)
            if target.default is not None:
                state.params[key] = target.default
        elif shared is not None:
            panel = next((pn for pn in node_panels(meta) if pn.id == shared[0]), None)
            if panel is None:
                return False
            self._begin_definition_edit("Link to subgraph input")
            extra = panel.bindings.setdefault(shared[1], [])
            if not any(b.state == state_id and b.param == key for b in extra):
                extra.append(binding)
        elif new_input is not None:
            if any(i.name == new_input.name for i in meta.inputs):
                return False
            self._begin_definition_edit("Link to subgraph input")
            meta.inputs.append(new_input.model_copy(update={"bindings": [binding]}))
        else:
            return False
        sync_linked_values(self._scene.config)
        self._end_definition_edit(state)
        return True

    def unlink_param(self, state_id: str, key: str) -> bool:
        """Unlink one param from its input or promoted param. The input itself stays. One undo step.

        A promoted node's own params cannot be unlinked one by one: they are its input
        group's, and demoting the node is how they let go.
        """
        state = self._definition_state(state_id)
        if state is None:
            return False
        meta = self._scene.config.definition

        def linked(b) -> bool:
            return b.state == state_id and b.param == key

        if not any(linked(b) for i in meta.inputs for b in i.bindings) and not any(
            linked(b) for pn in node_panels(meta) for extra in pn.bindings.values() for b in extra
        ):
            return False
        self._begin_definition_edit("Unlink param")
        for inp in meta.inputs:
            inp.bindings = [b for b in inp.bindings if not linked(b)]
        for panel in node_panels(meta):
            panel.bindings = {k: kept for k, v in panel.bindings.items() if (kept := [b for b in v if not linked(b)])}
        self._end_definition_edit(state)
        return True

    def linked_inputs_of(self, state_id: str) -> list[str]:
        """What the node's params are linked to, by label: what promoting it would replace."""
        config = self._scene.config
        if config is None or config.definition is None:
            return []
        meta = config.definition
        names = [i.label or i.name for i in meta.inputs if any(b.state == state_id for b in i.bindings)]
        for panel in node_panels(meta):
            if any(b.state == state_id for extra in panel.bindings.values() for b in extra):
                names.append(panel.label or panel.id)
        return list(dict.fromkeys(names))

    def set_node_promoted(self, state_id: str, promoted: bool) -> bool:
        """Promote a definition's node whole to an input group on the subgraph node, or demote it. One undo step.

        A promoted node's params are all its input group's, so any link one of them had
        goes: a param is set from one place only. The caller asks first (see
        ``_on_promote_node_requested``).
        """
        state = self._definition_state(state_id)
        if state is None:
            return False
        meta = self._scene.config.definition
        current = next((pn for pn in node_panels(meta) if pn.member == state_id), None)
        if promoted == (current is not None):
            return False
        if promoted:
            self._begin_definition_edit("Promote to input group")
            # As on a delete: an input only this node's params were linked to would sit
            # on every subgraph node setting nothing, so it goes; one linked elsewhere stays.
            emptied = set()
            for inp in meta.inputs:
                kept = [b for b in inp.bindings if b.state != state_id]
                if inp.bindings and not kept:
                    emptied.add(inp.name)
                inp.bindings = kept
            meta.inputs = [i for i in meta.inputs if i.name not in emptied]
            for panel in node_panels(meta):
                panel.bindings = {
                    k: kept for k, v in panel.bindings.items() if (kept := [b for b in v if b.state != state_id])
                }
            taken = {pn.id for pn in meta.panels}
            pid, n = f"node_{state_id}", 2
            while pid in taken:
                pid, n = f"node_{state_id}_{n}", n + 1
            label = state.label or state.node_type.replace("_", " ").title()
            meta.panels.append(SubgraphPanel(id=pid, kind="node", label=label, member=state_id))
        else:
            self._begin_definition_edit("Demote")
            meta.panels = [pn for pn in meta.panels if pn is not current]
            for inp in meta.inputs:
                if inp.panel == current.id:
                    inp.panel = None
        self._end_definition_edit(state)
        return True

    def _on_promote_node_requested(self, state_id: str, promoted: bool) -> None:
        if promoted:
            linked = self.linked_inputs_of(state_id)
            if linked:
                reply = QMessageBox.question(
                    self,
                    "Promote to Input Group",
                    "Some of this node's params are linked already: "
                    f"{', '.join(linked)}.\n\nPromoting the node replaces those links with its own input group. "
                    "An input linked only to this node is removed; one linked elsewhere too stays.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    state = self._definition_state(state_id)
                    if state is not None and self._param_panel.shown_state is state:
                        self._show_state_page(state)
                    return
        self.set_node_promoted(state_id, promoted)

    def _begin_definition_edit(self, desc: str) -> None:
        self._undo_mgr.cancel_debounce()
        self._on_about_to_change(desc)

    def _end_definition_edit(self, state=None) -> None:
        self._on_scene_changed()
        if state is not None and self._param_panel.shown_state is state:
            self._show_state_page(state)

    def _shareable_params(self, state_id: str, param_type: str) -> list[tuple[str, str, str]]:
        """``(label, group id, key)`` of every promoted node's param a ``param_type`` param can link to."""
        config = self._scene.config
        if config is None or config.definition is None:
            return []
        by_id = {s.id: s for s in config.states}
        out = []
        for panel in node_panels(config.definition):
            member = by_id.get(panel.member or "")
            if member is None or member.id == state_id:
                continue
            for field in NODE_PARAM_SCHEMAS.get(member.node_type, []):
                if narrowest_type([field.type, param_type]) is not None:
                    out.append(
                        (f"{panel.label or member.label or member.id} \u203a {field.label}", panel.id, field.key)
                    )
        return out

    def _on_link_requested(self, state_id: str, key: str) -> None:
        state = self._definition_state(state_id)
        if state is None:
            return
        field = next((f for f in NODE_PARAM_SCHEMAS.get(state.node_type, []) if f.key == key), None)
        if field is None:
            return
        dlg = LinkInputDialog(
            self._scene.config.definition.inputs,
            param_key=key,
            param_type=field.type,
            param_label=field.label,
            current_value=state.params.get(key, field.default),
            shared=self._shareable_params(state_id, field.type),
            groups=[(pn.id, pn.label or pn.id) for pn in self._scene.config.definition.panels if pn.kind == "inputs"],
            parent=self,
        )
        if not dlg.exec():
            return
        if dlg.choice is not None:
            self.link_param(state_id, key, existing=dlg.choice)
        elif dlg.shared_choice is not None:
            self.link_param(state_id, key, shared=dlg.shared_choice)
        else:
            self.link_param(state_id, key, new_input=dlg.new_input())

    def _on_definition_changed(self) -> None:
        """The signature page wrote into the document: a port renamed, an input or group changed.

        An input's default is every param linked to it, so a default edited there is
        written through to them -- which ``_on_scene_changed`` does.
        """
        self._on_scene_changed()

    def _on_add_port_requested(self, node_type: str) -> None:
        self._undo_mgr.cancel_debounce()
        pid = self._scene.add_port_node(node_type, self._scene.find_empty_grid_cell(self._canvas_origin()))
        if pid is not None and self._param_panel.showing_signature and self._config is not None:
            self._param_panel.show_signature(self._config)

    def _on_delete_port_requested(self, state_id: str) -> None:
        self._undo_mgr.cancel_debounce()
        # Undoable, and asked for by name on the signature page: no second confirmation.
        if (
            self._scene.delete_state(state_id, confirm=False)
            and self._config is not None
            and self._param_panel.showing_signature
        ):
            self._param_panel.show_signature(self._config)

    def _show_default_page(self) -> None:
        """What the panel shows with nothing selected: a definition's signature, or nothing."""
        if self._config is not None and self._config.is_definition and self._mode == BotMode.EDIT:
            self._param_panel.show_signature(self._config)
        else:
            self._param_panel.show_idle()

    def _show_state_page(self, state) -> None:
        config = self._scene.config
        if config is None:
            return
        meta = config.definition
        links: dict[str, str] = {}
        types: dict[str, str] = {}
        group: str | None = None
        if meta is not None:
            labels = {s.id: (s.label or s.id) for s in config.states}
            for inp in meta.inputs:
                for b in inp.bindings:
                    if b.state == state.id:
                        links[b.param] = inp.label or inp.name
                        types[b.param] = inp.type
            for panel in node_panels(meta):
                name = panel.label or labels.get(panel.member or "", "")
                if panel.member == state.id:
                    group = name
                    for f in NODE_PARAM_SCHEMAS.get(state.node_type, []):
                        if (t := promoted_param_type(config, panel, f.key, NODE_PARAM_SCHEMAS)) is not None:
                            types[f.key] = t
                for key, extra in panel.bindings.items():
                    for b in extra:
                        if b.state == state.id:
                            links[b.param] = f"{name} \u203a {key}"
                            if (t := promoted_param_type(config, panel, key, NODE_PARAM_SCHEMAS)) is not None:
                                types[b.param] = t
        self._param_panel.show_state(
            state,
            all_states=list(config.states),
            all_transitions=config.transitions,
            initial_id=config.initial,
            recovery_id=config.recovery_state,
            in_definition=meta is not None,
            links=links,
            promoted_group=group,
            field_types=types,
        )

    def _show_call_page(self, region) -> None:
        config = self._scene.config
        if config is None:
            return
        self._param_panel.show_call(
            region, self._definition_for(region), self._compose_report.regions.get(region.id), config
        )

    def _deferred_validation(self) -> None:
        """Run the expensive validation pass after a short debounce from param edits."""
        self._refresh_errors()
        self._update_validation_badge()

    def _on_select_edge_requested(self, transition) -> None:
        """Select the edge on the canvas that corresponds to *transition* and centre it."""
        self._scene.clearSelection()
        edge = self._scene.edge_for(transition)
        if edge is not None:
            edge.setSelected(True)
            self._view.centerOn(edge)

    def select_state(self, state_id: str) -> None:
        """Select the node for *state_id* and centre the view on it.

        Selecting is what loads the state into the properties panel, via
        ``_on_selection_changed``.
        """
        self._scene.clearSelection()
        node = self._scene.node_items.get(state_id) or self._scene.call_node_of(state_id)
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
        # Commit, never cancel: an edit made under 500ms ago is the step to undo, not one to drop.
        self._undo_mgr.flush_debounce()
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
        # Commit, never cancel: an edit made under 500ms ago is the step to undo, not one to drop.
        self._undo_mgr.flush_debounce()
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
        # The scene is rebuilt, so what was selected is re-selected by id: an undo is
        # about the node being worked on, and dropping it made every undo a lost place.
        try:
            keep = [i.state_id for i in self._scene.selectedItems() if isinstance(i, NodeItem)]
        except RuntimeError:
            keep = []
        self._scene.setup(config)
        self._after_scene_rebuild()
        self._show_default_page()
        self._on_scene_changed()
        for sid in keep:
            node = self._scene.node_items.get(sid)
            if node is not None:
                node.setSelected(True)

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
            call = self._scene.call_node_of(state.id)
            if call is not None:
                # Named through the call node it is drawn as, and without what is the
                # definition's business rather than this bot's -- see member_warnings.
                name = f"{call.label} \u203a {name}"
                warn_reasons = member_warnings(warn_reasons)
            error_lines.extend(f"{name}: {r}" for r in err_reasons)
            warning_lines.extend(f"{name}: {r}" for r in warn_reasons)
        warning_lines.extend(self._orphan_flag_lines(config))
        sg_errors, sg_warnings = self._subgraph_lines(config)
        error_lines.extend(sg_errors)
        warning_lines.extend(sg_warnings)
        if config.is_definition:
            def_errors, def_warnings = definition_problems(config, NODE_PARAM_SCHEMAS, self._subgraph_library)
            error_lines.extend(def_errors)
            warning_lines.extend(def_warnings)
        return error_lines, warning_lines

    def _subgraph_lines(self, config: BotConfig) -> tuple[list[str], list[str]]:
        """One line per linked region the last compose could not simply link.

        A ``cycle`` is an error (nothing runnable exists for it); ``held``, ``missing``
        and ``invalid`` are warnings, since the snapshot in the file still runs; any
        other reason on a ``linked`` region is a warning too. A region deleted since
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
        if self._scene.config is None:
            return
        self._scene.refresh_initial(self._scene.config.initial)

    def _refresh_terminal(self) -> None:
        self._scene.refresh_terminal()

    def _refresh_recovery(self) -> None:
        if self._scene.config is not None:
            self._scene.refresh_recovery(self._scene.config.recovery_state)

    def _refresh_errors(self) -> None:
        self._scene.refresh_errors()

    def _refresh_node_labels(self) -> None:
        if self._scene.config is None:
            return
        for state in self._scene.config.states:
            node = self._scene.node_items.get(state.id)
            if isinstance(node, PortNodeItem):
                node.set_label(state.label)
            elif node and not isinstance(node, CallNodeItem):
                node.set_label(state.label)
                node.set_node_type(state.node_type)
        self._scene.refresh_call_nodes()

    def _refresh_node_returns(self) -> None:
        if self._scene.config is None:
            return
        for state in self._scene.config.states:
            node = self._scene.node_items.get(state.id)
            if node and not isinstance(node, (CallNodeItem, PortNodeItem)):
                returns = _node_returns(state.node_type)
                if node._returns != returns:
                    node.set_returns(returns)
                    for s, _w, _d, edge in self._scene.edge_items:
                        if s == state.id:
                            edge._rebuild()

    def _cleanup_orphans(self) -> None:
        config = self._scene.config
        if config is None:
            return
        reachable = _reachable_states(config)
        # A member is the definition's to keep or not, and reached through its call
        # node; a port is part of a definition's signature. Neither is an orphan.
        orphan_ids = [
            s.id
            for s in config.states
            if s.id not in reachable and self._scene.call_node_of(s.id) is None and s.node_type not in PORT_NODE_TYPES
        ]
        for sid in orphan_ids:
            self._scene.delete_state(sid, confirm=False)

    def _on_selection_changed(self) -> None:
        self._undo_mgr.cancel_debounce()
        self._shown_edge_models = []
        try:
            selected = self._scene.selectedItems()
        except RuntimeError:
            return
        if not selected:
            self._show_default_page()
            return
        item = selected[0]
        config = self._scene.config
        if config is None:
            self._param_panel.show_idle()
            return
        nodes = [i for i in selected if isinstance(i, NodeItem)]
        # A whole plain region's members and nothing else: the region page. One call
        # node on its own is the instance's page, even where a region holds only it.
        if nodes and len(nodes) == len(selected) and not (len(nodes) == 1 and isinstance(item, CallNodeItem)):
            selected_ids = set(self._scene.state_ids_of(n.state_id for n in nodes))
            region = next(
                (r for r in config.regions if r.source is None and r.members and set(r.members) == selected_ids),
                None,
            )
            if region is not None:
                self._param_panel.show_region(region)
                self.tab_switch.emit("properties")
                return
        if isinstance(item, CallNodeItem):
            region = next((r for r in config.regions if r.id == item.state_id), None)
            if region is not None:
                self._show_call_page(region)
                self.tab_switch.emit("properties")
        elif isinstance(item, PortNodeItem):
            # A port is the signature's: its page is where it is named and ordered.
            self._param_panel.show_signature(config)
            self.tab_switch.emit("properties")
        elif isinstance(item, NodeItem):
            state = next((s for s in config.states if s.id == item.state_id), None)
            if state:
                self._show_state_page(state)
                self.tab_switch.emit("properties")
        elif isinstance(item, EdgeItem):
            models = self._scene.transitions_of(item)
            if not models:
                self._param_panel.show_idle()
                return
            trans = models[0]
            src_state = next((st for st in config.states if st.id == trans.source), None)
            src_returns: list[str] = []
            if src_state:
                spec = NODE_SPECS.get(src_state.node_type)
                if spec:
                    src_returns = [r.name for r in spec.returns]
            option_names = [opt.name for opt in compose_options(config) if opt.type == "bool"]
            # An edge on a call node is rewired on the canvas, where its port is: the
            # dropdowns name states, and its ends are a member and a port.
            on_call = isinstance(item.src_node, CallNodeItem) or isinstance(item.dst_node, CallNodeItem)
            self._shown_edge_models = models
            self._param_panel.show_transition(
                trans,
                src_returns,
                option_names,
                states=list(config.states),
                all_transitions=config.transitions,
                rewire=not on_call,
            )
            self.tab_switch.emit("properties")
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
