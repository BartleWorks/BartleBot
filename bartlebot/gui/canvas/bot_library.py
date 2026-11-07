"""The bot library: the page the canvas shows when no bot is open, and its cards.

``BotLibraryPanel`` lists the bots and subgraphs, Official and the user's, by category
(Recent, All, then the folders under ``bots/``), with search and tag filters; a card
opens, exports, renames or deletes what it shows. ``BotCanvas`` shows one in place of
the graph, and the library dialog another. ``_read_bot_meta`` is what a card shows of a
bot file without loading it.
"""

import json
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from bartleby.qt.art_signal import follow_art
from bartleby.qt.menu_utils import popup_menu
from bartleby.qt.theming.theme import THEME
from bartleby.qt.theming.themed import ThemedMixin, set_style_sheet
from bartleby.qt.widgets.buttons.action_button import ActionButton, ActionStyle
from bartleby.qt.widgets.inputs.search_field import SearchLineEdit
from bartleby.qt.widgets.labels.elided_label import WrappingElidedLabel
from bartleby.qt.widgets.pickers.picker_popup import SECTION_ICON_PX
from bartleby.qt.widgets.pills.pill import NeutralPill
from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QIcon,
)
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bartlebot.combat.strategy_tags import SOURCE
from bartlebot.content import bot_roots, bot_summary
from bartlebot.content.bot_folders import ALL_BADGE, folder_badge, folder_caption
from bartlebot.content.bot_tags import BOT_TAGS, TAG_GROUPS, normalize_tags, tag_group
from bartlebot.content.store_roots import SOURCE_LABELS, Origin
from bartlebot.content.subgraphs.subgraph_store import SubgraphLibrary
from bartlebot.content.subgraphs.subgraph_tags import CATEGORY, SUBGRAPH_CATEGORIES, subgraph_tag_group
from bartlebot.content.templates import setup_steps, template_diff
from bartlebot.game import card_art
from bartlebot.gui.widgets.cards.card_art_tile import art_icon
from bartlebot.gui.widgets.pickers.tag_picker import (
    BOT_VOCABULARY,
    SUBGRAPH_VOCABULARY,
    TagPickerButton,
    source_vocabulary,
)
from bartlebot.gui.widgets.pills.source_pill import source_pill
from bartlebot.gui.widgets.pills.tag_pill import TagChip, TagPill
from bartlebot.settings_manager import SettingsManager

#: Why an Official subgraph's Delete is grayed out: the store only ever removes the user's copy.
_BUILT_IN_SUBGRAPH_TIP = "Official subgraphs can't be deleted. Open one and Save as… to make a copy of your own."

#: A library card's right-click, for a bot or a subgraph (#170).
EXPORT_TEXT = "Export…"

#: Why Delete Bot is grayed out on an Official bot: it ships in the install directory, so it would come back (#268).
_BUILT_IN_BOT_TIP = "Official bots can't be deleted. Make your own copy to have one you can remove."

#: Why an Official subgraph's Rename is grayed out: its name is the shipped file's (#160).
BUILT_IN_RENAME_TIP = "Official subgraphs can't be renamed. Open one and Save as… to make a copy of your own."


# ---------------------------------------------------------------------------
# Bot library helpers — used by BotLibraryPanel
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
    #: The sentence the graph implies, for a bot with no description; "" when it has one,
    #: or the graph implies nothing. See bartlebot/content/bot_summary.py.
    generated: str = ""
    #: Setup steps of the bot's template still to do; 0 for a bot made from none.
    setup_left: int = 0
    #: Whether the bot's template has released a version newer than the one it is based on.
    update_available: bool = False
    #: Where it comes from: Official (shipped) or Custom, for a bot; a subgraph can also be a
    #: modified Official one. Worked out from where the file is, never read from it (#149).
    source: Origin = Origin.USER
    #: For a subgraph overriding an Official one, whether the Official one has changed since
    #: (#169): the pill adds "Official updated", and the card offers Revert beside it.
    official_updated: bool = False
    #: The bot's own id, and for the user's copy of an Official bot that bot's id; "" when none.
    bot_id: str = ""
    forked_from_id: str = ""
    #: For the user's copy of an Official bot, that bot's name, filled in by the library
    #: once it has read every Official bot (:func:`name_forks`); "" for any other.
    forked_from: str = ""

    @property
    def source_text(self) -> str:
        """The Source filter's word for this entry: what its pill says."""
        return SOURCE_LABELS[self.source]


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


def setup_left_text(left: int) -> str:
    """A library row's "2 setup steps left"."""
    return f"{left} setup {'step' if left == 1 else 'steps'} left"


#: A library row's line for a bot whose template has moved on.
UPDATE_AVAILABLE_TEXT = "Template update available"


def forked_from_text(name: str) -> str:
    """A library row's words for the user's copy of the Official bot *name*."""
    return f"Copy of the Official {name}"


#: (file, template store) -> (the file's mtime, its setup steps left): see ``_setup_left``.
_setup_left_seen: dict[tuple[str, str], tuple[int, int]] = {}


def _setup_left(path: Path, data: dict, templates_root: "str | Path | None", library: "SubgraphLibrary | None") -> int:
    """The setup steps the bot file at *path* has left, remembered until the file changes.

    Counting them validates the whole file, and the library reads every bot each time it
    is listed. A released version's steps never change, so the answer is the file's
    alone -- unless a step asks for a named subgraph input, which the definition places,
    and then it is worked out each time.
    """
    if not isinstance(data.get("template"), dict):
        return 0
    if setup_steps.file_reads_library(data, templates_root):
        return setup_steps.left_in_file(data, templates_root, library=library)
    key = (str(path), str(templates_root or ""))
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return setup_steps.left_in_file(data, templates_root, library=library)
    seen = _setup_left_seen.get(key)
    if seen is None or seen[0] != stamp:
        seen = _setup_left_seen[key] = (stamp, setup_steps.left_in_file(data, templates_root, library=library))
    return seen[1]


def _read_bot_meta(
    path: Path,
    templates_root: "str | Path | None" = None,
    library: "SubgraphLibrary | None" = None,
    *,
    shipped: bool | None = None,
) -> "_BotMeta | None":
    """What a library card shows for the bot file at *path*, read without building a scene.

    *templates_root* is where the template store is, for the setup steps a bot made
    from a template has left; ``None`` is the project's. *library* is the subgraph
    library a step on a named subgraph input is read through, as the editor reads it.

    *shipped* says whether it is an Official bot, when the caller already knows;
    otherwise it is worked out from the path (``bot_roots.is_shipped``). An Official bot
    is never offered its template's update: it gets its updates from the app.
    """
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        description = data.get("description") or ""
        if shipped is None:
            shipped = bot_roots.is_shipped(path)
        forked = data.get("forked_from")
        forked_id = forked.get("id") if isinstance(forked, dict) else None
        return _BotMeta(
            name=data.get("name") or path.stem,
            description=description,
            authors=data.get("authors") or "",
            drops=_drop_names(data.get("drops")),
            tags=normalize_tags(data.get("tags")),
            version=data.get("version") or "",
            path=path,
            generated="" if description else (bot_summary.describe_data(data) or ""),
            setup_left=_setup_left(path, data, templates_root, library),
            update_available=not shipped and template_diff.behind_in_file(data, templates_root),
            source=Origin.SHIPPED if shipped else Origin.USER,
            bot_id=data.get("id") if isinstance(data.get("id"), str) else "",
            forked_from_id=forked_id if isinstance(forked_id, str) else "",
        )
    except Exception:
        return None


def name_forks(metas: "Iterable[_BotMeta]", official: "Iterable[_BotMeta]") -> None:
    """Fill in ``forked_from`` on each user's copy in *metas*, from the *official* bots' names.

    A copy whose Official bot is not listed -- one removed by a release, which the plan
    rules out, or a file from another install -- says nothing, rather than an id.
    """
    names = {m.bot_id: m.name for m in official if m.source is Origin.SHIPPED and m.bot_id}
    for meta in metas:
        meta.forked_from = names.get(meta.forked_from_id, "") if meta.forked_from_id else ""


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


def filter_bot_metas(
    metas: list[_BotMeta],
    query: str,
    tags: set[str],
    sources: "set[str] | frozenset[str]" = frozenset(),
    group_of: Callable[[str], str] = tag_group,
) -> tuple[list[_BotMeta], dict[Path, list[str]]]:
    """Narrow *metas* by free text, by selected tag chips and by Source.

    *sources* are Source words (``SOURCE_LABELS``): an entry passes when its own is one
    of them, or when none is selected. Source is one more group, so it ANDs with the tags.

    *group_of* says which group a tag is in: the bot tags' by default, and for the
    Subgraphs folder :func:`~bartlebot.content.subgraphs.subgraph_tags.subgraph_tag_group`, whose one
    stored group is Category (#172). A subgraph's ``tags`` are its categories.

    Returns the surviving metas and, for each one the text matched by drop name, the
    drops that matched — the library card shows those so a hit on a 300-entry drop
    list explains itself.

    Tags **OR within a group and AND across groups**: "Gear or Jewels, *and* Dungeon".
    That is the faceted model two labeled buttons imply, and it is the only one that
    stays useful — the groups answer different questions (:mod:`bartlebot.content.bot_tags`:
    Rewards is what a run yields, Activities is what it does), so ANDing across them
    narrows, while ANDing *within* one asks for a bot that drops both Gear and Jewels
    and usually returns nothing. Cross-group pairs like "Dungeon + Spellements" behave
    exactly as they did when every tag ANDed.

    The text query is an OR across the fields it searches, tags included, and
    intersects whatever the tags left.

    Pure, and at module level, so the filtering can be tested without a QApplication
    — same reasoning as :mod:`bartlebot.content.bot_roots`.
    """
    q = query.strip().casefold()
    wanted: dict[str, set[str]] = {}
    for t in tags:
        wanted.setdefault(group_of(t), set()).add(t)

    results: list[_BotMeta] = []
    matched_drops: dict[Path, list[str]] = {}
    for m in metas:
        if any(group_tags.isdisjoint(m.tags) for group_tags in wanted.values()):
            continue
        if sources and m.source_text not in sources:
            continue
        drop_matches = [d for d in m.drops if q in d.casefold()]
        if q and not (
            q in m.name.casefold()
            or q in m.description.casefold()
            or q in m.generated.casefold()
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
    #: Revert beside an "Official updated" pill was clicked; emits the subgraph id.
    revert_requested = pyqtSignal(str)

    def __init__(self, meta: "_BotMeta", matched_drops: "list[str] | None" = None, parent=None) -> None:
        super().__init__(parent)
        self._meta = meta
        self.setObjectName("BotCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # Every *plain* label the card builds is held, because each carries a THEME
        # color that apply_theme_styles has to re-derive. Several are conditional, so
        # the attributes are declared up front rather than only where they are built.
        # The pills are absent from this list deliberately: TagPill and NeutralPill own
        # their own color, so there is nothing here to re-derive for them.
        self._hovered = False
        # Selected by the library on the user's behalf -- an item just imported (#171) --
        # and drawn as hover is, so the eye lands on it.
        self._selected = False
        self._desc_lbl: QLabel | None = None
        self._meta_lbl: QLabel | None = None
        self._setup_lbl: QLabel | None = None
        self._update_lbl: QLabel | None = None
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

        # Neutral gray rather than an accent, and NeutralPill rather than a label this
        # card restyles: see its docstring for both halves of the reasoning.
        if self._meta.version:
            header.addWidget(NeutralPill(f"v{self._meta.version}"), 0, Qt.AlignmentFlag.AlignVCenter)

        # One palette for a tag everywhere it appears: the dot in the picker, the chip
        # in the filter bar, this pill, and the ones in Bot Info. TagPill owns it.
        # A subgraph's tags are its categories (#172), in their own vocabulary's color.
        vocabulary = SUBGRAPH_VOCABULARY if self._meta.subgraph_id else BOT_VOCABULARY
        for tag in self._meta.tags:
            header.addWidget(TagPill(tag, vocabulary=vocabulary), 0, Qt.AlignmentFlag.AlignVCenter)

        header.addStretch()
        # Where it comes from, at the end of the row as on a strategy card (#149): the
        # same pill, so Official reads the same on a bot as on a strategy.
        self._source_pill = source_pill(self._meta.source, official_updated=self._meta.official_updated)
        header.addWidget(self._source_pill, 0, Qt.AlignmentFlag.AlignVCenter)
        # A modified Official subgraph whose Official one has since changed: Revert is the
        # way to the new one, so it sits beside the pill that says so rather than only in
        # the right-click menu (#169). The controller asks before reverting.
        self._revert_btn: ActionButton | None = None
        if self._meta.official_updated and self._meta.subgraph_id:
            self._revert_btn = ActionButton("revert", "Revert…", fill=False)
            self._revert_btn.setToolTip("Revert to the Official version; your changes are discarded")
            sid = self._meta.subgraph_id
            self._revert_btn.clicked.connect(lambda: self.revert_requested.emit(sid))
            header.addWidget(self._revert_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)

        # Wrapping and elided: a description is user text, and one word too long for the
        # card would otherwise become the card's minimum width.
        if self._meta.description:
            self._desc_lbl = WrappingElidedLabel(self._meta.description)
            layout.addWidget(self._desc_lbl)
        elif self._meta.generated:
            # In italics, so a sentence the app wrote never reads as one the author did.
            self._desc_lbl = WrappingElidedLabel(self._meta.generated)
            self._desc_lbl.setToolTip("Generated from the graph: this bot has no description.")
            layout.addWidget(self._desc_lbl)

        parts: list[str] = []
        if self._meta.forked_from:
            # A user's copy of an Official bot says which one, so the two are told apart
            # when both are listed under the same name.
            parts.append(forked_from_text(self._meta.forked_from))
        if self._meta.authors:
            parts.append(f"by {self._meta.authors}")
        n = len(self._meta.drops)
        if n:
            parts.append(f"{n} drop{'s' if n != 1 else ''}")
        if parts:
            self._meta_lbl = QLabel("  ·  ".join(parts))
            layout.addWidget(self._meta_lbl)

        # A bot made from a template and left half set up: it cannot run yet, and the
        # card is where that is found out without opening it.
        if self._meta.setup_left:
            self._setup_lbl = QLabel(setup_left_text(self._meta.setup_left))
            layout.addWidget(self._setup_lbl)

        # The template it was made from has released since: opening it offers the review.
        if self._meta.update_available:
            self._update_lbl = QLabel(UPDATE_AVAILABLE_TEXT)
            layout.addWidget(self._update_lbl)

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
    def setup_text(self) -> str | None:
        """The "2 setup steps left" line as shown, or None for a bot with nothing left."""
        return self._setup_lbl.text() if self._setup_lbl is not None else None

    @property
    def update_text(self) -> str | None:
        """The "Template update available" line as shown, or None for a bot that is not behind."""
        return self._update_lbl.text() if self._update_lbl is not None else None

    @property
    def source_text(self) -> str:
        """What the card's Source pill says."""
        return self._source_pill.text()

    @property
    def selected(self) -> bool:
        return self._selected

    def set_selected(self, selected: bool) -> None:
        """Draw the card as the library's selection, or not."""
        self._selected = selected
        self.refresh_theme_styles()

    @property
    def revert_button(self) -> ActionButton | None:
        """The Revert beside an "Official updated" pill, or None on a card without one."""
        return self._revert_btn

    @property
    def meta_text(self) -> str | None:
        """The "copy of … · by … · N drops" line as shown, or None when the card has none."""
        return self._meta_lbl.text() if self._meta_lbl is not None else None

    @property
    def matched_drops_text(self) -> str | None:
        """The "Drops: …" line as shown, or None while it is hidden."""
        return self._match_lbl.text() if self._matched_drops else None

    def apply_theme_styles(self) -> None:
        """Restyle the frame and every label on it.

        Hover is state rather than a QSS pseudo-class because the frame and the
        labels are restyled by the same pass; ``_hovered`` picks which frame colors
        that pass uses. Before this was one method, hover was the *only* thing that
        re-read THEME, so a theme switch left a card's text in the old palette until
        the mouse crossed it — and even then only the frame followed.
        """
        # Use #BotCard selector so border/background don't bleed into child labels
        lit = self._hovered or self._selected
        bg = THEME.node_fill if lit else THEME.surface
        border = THEME.accent if lit else THEME.border
        set_style_sheet(self, f"#BotCard {{ background: {bg}; border: 1px solid {border}; border-radius: 4px; }}")

        set_style_sheet(self._name_lbl, f"color: {THEME.text}; background: transparent;")
        for lbl, color in (
            (self._desc_lbl, THEME.text_muted),
            (self._meta_lbl, THEME.text_subtle),
            (self._setup_lbl, THEME.warning),
            (self._update_lbl, THEME.accent),
            (self._match_lbl, THEME.accent),
        ):
            if lbl is not None:
                italic = " font-style: italic;" if lbl is self._desc_lbl and not self._meta.description else ""
                set_style_sheet(
                    lbl, f"color: {color}; font-size: {THEME.font_subtext_px}px; background: transparent;{italic}"
                )

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.refresh_theme_styles()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.refresh_theme_styles()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        # The left button only: a right-click is the card's menu, where it has one.
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_requested.emit(self._meta.subgraph_id or str(self._meta.path.resolve()))
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# BotLibraryPanel — shown when no bot file is open
# ---------------------------------------------------------------------------


class BotLibraryPanel(ThemedMixin, QWidget):
    """Bot library panel shown in BotCanvas when no bot file is open.

    Two-panel layout: left category list (Recent, All, then the folders under bots/),
    right scrollable bot cards. The search and the tag filters narrow the selected row,
    which the search field names in a scope pill (#269).

    Each category row carries its *key* in ``Qt.UserRole`` -- the folder name, or one of
    the two sentinels below -- and the caption is only what it shows. The caption used to
    be the key as well, which tied the lookup to wording that now comes from the game.
    """

    new_bot_requested = pyqtSignal()
    open_bot_requested = pyqtSignal()
    open_recent_requested = pyqtSignal(str)  # path
    open_subgraph_requested = pyqtSignal(str)  # subgraph id
    #: (subgraph id): a Subgraphs card's Delete, or Revert for a modified Official one.
    #: The controller asks, deletes and refreshes; the panel only offers it.
    delete_subgraph_requested = pyqtSignal(str)
    #: (subgraph id): a Subgraphs card's Rename. The controller asks for the name and writes it.
    rename_subgraph_requested = pyqtSignal(str)
    #: (bot file path): a bot card's Export… (#170). The controller gathers and writes the file.
    export_bot_requested = pyqtSignal(str)
    #: (bot file path): a bot card's Delete Bot… (#268). The controller asks, deletes and re-lists.
    delete_bot_requested = pyqtSignal(str)
    #: (subgraph id): a Subgraphs card's Export….
    export_subgraph_requested = pyqtSignal(str)

    _RECENT = "Recent"
    _ALL = "All"
    #: Definitions from the subgraph library, listed after the folders. Not in All,
    #: which is the bots: a definition opens as a document to edit, never to run.
    _SUBGRAPHS = "Subgraphs"
    _SUBGRAPHS_KEY = "\0subgraphs"
    #: The search box's prompt, for the bots and for the Subgraphs folder.
    _BOT_SEARCH = "Search bots by name, author, or drop…"
    _SUBGRAPH_SEARCH = "Search subgraphs by name, description, or category…"
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
        # that survives the filter, which is the whole library on a fresh install.
        self._listed = False
        self._build_ui()
        follow_art(self, self._on_art_changed)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._listed:
            self.refresh()

    def _on_art_changed(self) -> None:
        """Relist with the new badges and tag medallions, or on the next show when out of sight.

        Every card's pills and every category row's badge were drawn from the art there
        was, so the listing is rebuilt rather than patched. Hidden behind an open bot, the
        panel only forgets it was listed: ``showEvent`` relists when it is next looked at.
        """
        if not self._listed:
            return
        if self.isVisible():
            self.refresh(keep_category=True)
        else:
            self._listed = False

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
        # Source is a facet beside the tags, not a tag: worked out from where each file is,
        # never stored (#149). Its own picker and vocabulary, so it never reaches Bot Info.
        self._source_picker = TagPickerButton(
            title=SOURCE, placeholder=SOURCE, group=SOURCE, vocabulary=source_vocabulary(("item", "items"))
        )
        self._source_picker.setToolTip("Filter by where it comes from: Official, or your own")
        self._source_picker.selection_changed.connect(self._on_tag_selection_changed)
        # A subgraph's categories (#172) take the bot tags' place in the Subgraphs folder,
        # where a bot tag has nothing to match: shown there and only there, before Source,
        # which both lists share. See _show_pickers_for_folder.
        self._category_picker = TagPickerButton(
            title=CATEGORY, placeholder=CATEGORY, group=CATEGORY, vocabulary=SUBGRAPH_VOCABULARY
        )
        self._category_picker.setToolTip("Filter subgraphs by what they do")
        self._category_picker.selection_changed.connect(self._on_tag_selection_changed)
        self._category_picker.setVisible(False)
        top_layout.addWidget(self._category_picker, 0)
        top_layout.addWidget(self._source_picker, 0)

        # Stretch 1, not a fixed width flanked by two stretches: the search field then
        # fills whatever the two clusters leave, so it *sits* centered without being
        # anchored there, and narrowing the window shortens it instead of colliding.
        self._search = SearchLineEdit(self._BOT_SEARCH)
        self._search.setClearButtonEnabled(True)
        self._search.scope_removed.connect(self.search_every_folder)
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
        # follow a theme switch whenever they exist. The empty hint is a box holding its
        # line and, when other folders have matches, the button that widens to them.
        self._clear_btn: ActionButton | None = None
        self._empty_hint: QWidget | None = None
        self._empty_label: QLabel | None = None
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        """Restyle the panel chrome, all of which outlives a theme switch.

        Qt does not recompute a stylesheet set on a widget earlier, and nothing here
        is rebuilt on its own, which is why the search bar and the category list used
        to keep the old palette until the app was restarted.
        """
        panel = f"background: {THEME.surface}; border-bottom: 1px solid {THEME.border};"
        set_style_sheet(self._top_bar, panel)
        set_style_sheet(self._chip_bar, panel)
        set_style_sheet(self._sep, f"color: {THEME.border};")

        set_style_sheet(
            self._cat_list,
            # outline: none drops the focus rectangle Qt draws around the clicked
            # row's text; the selection fill already marks it.
            f"QListWidget {{ background: {THEME.surface}; border: none; outline: none;"
            f"  border-right: 1px solid {THEME.border}; font-size: {THEME.font_small_px}px; }}"
            f"QListWidget::item {{ padding: {CATEGORY_PAD_Y}px {CATEGORY_PAD_X}px; color: {THEME.text_muted};"
            f"  border-bottom: 1px solid {THEME.border}; }}"
            f"QListWidget::item:selected {{ background: {THEME.node_fill_selected}; color: {THEME.text}; }}",
        )
        # Compared first: an unchanged rule assigned here still broadcasts a StyleChange
        # to every card, and _rebuild_chips refreshes this on each filter change.
        set_style_sheet(self._cards_area, f"background: {THEME.bg};")
        set_style_sheet(self._cards_container, f"background: {THEME.bg};")

        if self._empty_label is not None:
            set_style_sheet(self._empty_label, f"color: {THEME.text_muted}; font-size: {THEME.font_small_px}px;")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_subgraph_library(self, library: SubgraphLibrary | None) -> None:
        """The library the Subgraphs folder lists. Read at the next refresh."""
        self._subgraph_library = library
        if self._listed:
            self.refresh()

    def _read_meta(self, path: Path, *, shipped: bool | None = None) -> "_BotMeta | None":
        return _read_bot_meta(path, library=self._subgraph_library, shipped=shipped)

    def refresh(self, *, keep_category: bool = False) -> None:
        """Rescan recent files and bots/ directory, rebuild UI.

        Lands on the first category, unless ``keep_category`` and the one selected is
        still listed -- after a subgraph is deleted, say, so the user stays in the folder.
        """
        current = self._cat_list.currentItem()
        kept = current.data(Qt.ItemDataRole.UserRole) if keep_category and current is not None else None
        self._listed = True
        sm = SettingsManager.instance()
        recent_paths = sm.recent_bot_files

        # Scan bots/ subdirectories
        self._all_metas = []
        self._category_map = {}

        # The Official bots and the user's own, as one library (bot_roots). By folder
        # name, so the numbers set the order -- the same order Explorer shows, which is
        # the game's badge page order (see bot_folders).
        for entry in bot_roots.library_files(bot_roots.shipped_bots_dir(), bot_roots.user_bots_dir()):
            meta = self._read_meta(entry.path, shipped=entry.shipped)
            if meta is None:
                continue
            self._category_map.setdefault(entry.folder or self._OTHER_KEY, []).append(meta)
            self._all_metas.append(meta)
        if self._OTHER_KEY in self._category_map:
            # Files at the top of the library are listed after every world folder.
            self._category_map[self._OTHER_KEY] = self._category_map.pop(self._OTHER_KEY)

        # Recent files
        self._recent_metas = [m for p in recent_paths if (m := self._read_meta(Path(p)))]
        # A user's copy of an Official bot says which one: "Copy of the Official …".
        name_forks((*self._all_metas, *self._recent_metas), self._all_metas)

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
                    tags=list(d.tags),
                    version=str(d.version),
                    path=Path(f"{d.id}.json"),
                    subgraph_id=d.id,
                    source=self._subgraph_library.origin(d.id),
                    official_updated=self._subgraph_library.official_updated(d.id),
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
        keys = self._row_keys()
        if keys:
            self._cat_list.setCurrentRow(keys.index(kept) if kept in keys else 0)
        else:
            self._show_cards([])

    def reveal(self, *, path: str | Path | None = None, subgraph_id: str | None = None) -> bool:
        """Show one bot or subgraph, selected: its folder, scrolled to its card. False when it isn't listed.

        What the library does after an import (#171), so the user sees where it went. The
        search and the filters are cleared first, since either could hide it.
        """
        self.refresh()
        self._search.clear()
        self._source_picker.set_selection([])
        self._set_tag_selection([])
        if subgraph_id is not None:
            key = self._SUBGRAPHS_KEY
            meta = next((m for m in self._subgraph_metas if m.subgraph_id == subgraph_id), None)
        else:
            target = Path(path).resolve() if path is not None else None
            key, meta = self._ALL_KEY, None
            for folder, metas in self._category_map.items():
                meta = next((m for m in metas if m.path.resolve() == target), None)
                if meta is not None:
                    key = folder
                    break
        if meta is None:
            return False
        self._select_row(key)
        card = self._cards.get(meta.path)
        if card is None:
            return False
        for other in self._cards.values():
            other.set_selected(other is card)
        self._cards_area.ensureWidgetVisible(card)
        return True

    def selected_card(self) -> "_BotCard | None":
        """The card :meth:`reveal` selected, while it is listed. For the tests."""
        return next((c for c in self._cards.values() if c.selected and c.isVisibleTo(self)), None)

    def search_every_folder(self) -> None:
        """Widen the search to the whole library: select All, keeping the query and the filters.

        What removing the search field's scope pill does, and the empty list's Search all
        folders button (#269).
        """
        self._select_row(self._ALL_KEY)

    def _row_keys(self) -> list:
        return [self._cat_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self._cat_list.count())]

    def _select_row(self, key: str) -> None:
        """Select the category row keyed *key*, when it is listed."""
        keys = self._row_keys()
        if key in keys:
            self._cat_list.setCurrentRow(keys.index(key))

    def current_folder(self) -> str | None:
        """The library folder the selected category row stands for, by its name on disk.

        ``None`` for the rows that are no folder (Recent, All, Other, Subgraphs), and
        before the panel has listed anything.
        """
        item = self._cat_list.currentItem()
        key = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return key if isinstance(key, str) and not key.startswith("\0") else None

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
        # Bots and subgraphs both: the Source filter narrows the Subgraphs folder as well.
        sources = Counter(m.source_text for m in (*self._all_metas, *self._subgraph_metas))
        self._source_picker.set_counts({word: sources.get(word, 0) for word in self._source_picker.vocabulary.tags})
        categories = Counter(t for m in self._subgraph_metas for t in m.tags)
        self._category_picker.set_counts({word: categories.get(word, 0) for word in SUBGRAPH_CATEGORIES})

    def _rebuild_chips(self) -> None:
        """Show one removable chip per *selected* tag; hide the bar when none are."""
        while self._chip_layout.count() > 1:
            item = self._chip_layout.takeAt(0)
            if w := item.widget():
                w.hide()
                w.deleteLater()

        # Only the facets of what is listed: bot tags for bots, categories in the Subgraphs
        # folder (#172). The other kind stays selected, out of sight, and comes back with
        # its list -- it matches nothing here, so a chip for it would only be noise.
        if self._in_subgraphs_folder():
            selected, vocabulary = self._selected_categories(), SUBGRAPH_VOCABULARY
        else:
            selected, vocabulary = self._selected_tag_list(), BOT_VOCABULARY
        for tag in selected:
            # Colored by group, matching the dot in the popup and the button it came
            # from — with two pickers feeding one chip bar, a chip has to say which.
            chip = TagChip(tag, tooltip=f"Remove the {tag} filter", vocabulary=vocabulary)
            chip.removed.connect(self._remove_tag)
            self._chip_layout.insertWidget(self._chip_layout.count() - 1, chip)
        sources = self._source_picker.tags()
        for word in sources:
            chip = TagChip(word, tooltip=f"Remove the {word} filter", vocabulary=self._source_picker.vocabulary)
            chip.removed.connect(self._remove_source)
            self._chip_layout.insertWidget(self._chip_layout.count() - 1, chip)
        selected = [*selected, *sources]

        self._clear_btn = None
        if selected:
            # Sized to its own text at the end of the chip row, so fill=False.
            self._clear_btn = clear = ActionButton("dismiss", "Clear", fill=False)
            clear.clicked.connect(self._clear_filters)
            self._chip_layout.insertWidget(self._chip_layout.count() - 1, clear)
        self.refresh_theme_styles()

        self._chip_bar.setVisible(bool(selected))

    def _remove_tag(self, tag: str) -> None:
        chosen = [*self._selected_tag_list(), *self._selected_categories()]
        self._set_tag_selection([t for t in chosen if t != tag])

    def _remove_source(self, word: str) -> None:
        self._source_picker.set_selection([w for w in self._source_picker.tags() if w != word])
        self._rebuild_chips()
        self._apply_filter()

    def _clear_filters(self) -> None:
        """The chip bar's Clear: every tag, every category and every Source word, shown or not."""
        self._source_picker.set_selection([])
        self._set_tag_selection([])

    def set_source_filter(self, words: Iterable[str]) -> None:
        """Select Source words as the picker would. For the tests, and for nothing else to need the picker."""
        self._source_picker.set_selection(list(words))
        self._rebuild_chips()
        self._apply_filter()

    def _set_tag_selection(self, tags: list[str]) -> None:
        """Change the selection and re-filter — set_selection does not emit.

        Takes the whole selection across both pickers and routes each tag back to the
        group it belongs to, so a chip's ✕ and the Clear button do not have to know
        which button produced which chip.
        """
        for group, picker in self._pickers.items():
            picker.set_selection([t for t in tags if tag_group(t) == group])
        self._category_picker.set_selection([t for t in tags if subgraph_tag_group(t) == CATEGORY])
        self._rebuild_chips()
        self._apply_filter()

    def set_category_filter(self, words: Iterable[str]) -> None:
        """Select categories as the picker would. For the tests, and for nothing else to need the picker."""
        self._category_picker.set_selection(list(words))
        self._rebuild_chips()
        self._apply_filter()

    @property
    def category_filter_visible(self) -> bool:
        """Whether the Category filter is offered: in the Subgraphs folder only."""
        return not self._category_picker.isHidden()

    @property
    def bot_tag_filters_visible(self) -> bool:
        """Whether the bot tags' pickers are offered: everywhere but the Subgraphs folder."""
        return not any(p.isHidden() for p in self._pickers.values())

    def chip_texts(self) -> list[str]:
        """What the chip bar says, in order. Public so a test can read it."""
        items = (self._chip_layout.itemAt(i).widget() for i in range(self._chip_layout.count()))
        return [w.tag for w in items if isinstance(w, TagChip)]

    def _on_tag_selection_changed(self, _keys: list) -> None:
        self._rebuild_chips()
        self._apply_filter()

    def _selected_tag_list(self) -> list[str]:
        """Every selected tag across the pickers, in ``BOT_TAGS`` order."""
        chosen = self._selected_tags()
        return [t for t in BOT_TAGS if t in chosen]

    def _selected_tags(self) -> set[str]:
        return {t for picker in self._pickers.values() for t in picker.tags()}

    def _selected_categories(self) -> list[str]:
        """Every selected subgraph category, in vocabulary order."""
        return self._category_picker.tags()

    def _in_subgraphs_folder(self) -> bool:
        item = self._cat_list.currentItem()
        return item is not None and item.data(Qt.ItemDataRole.UserRole) == self._SUBGRAPHS_KEY

    def _show_pickers_for_folder(self) -> None:
        """Offer the facets of what the selected row lists: bot tags, or a subgraph's categories (#172)."""
        subgraphs = self._in_subgraphs_folder()
        for picker in self._pickers.values():
            picker.setVisible(not subgraphs)
        self._category_picker.setVisible(subgraphs)
        self._search.setPlaceholderText(self._SUBGRAPH_SEARCH if subgraphs else self._BOT_SEARCH)
        self._show_scope()

    def _scope_caption(self) -> str | None:
        """The selected row's caption when it narrows the library, else ``None`` (All, or nothing listed)."""
        item = self._cat_list.currentItem()
        if item is None or item.data(Qt.ItemDataRole.UserRole) == self._ALL_KEY:
            return None
        return item.text()

    def _show_scope(self) -> None:
        """Name the selected row in the search field, so a search never hides where it looks (#269).

        Shown whenever a row narrows the library, typed into or not: it is there before
        anyone's eyes reach the field, and the text never jumps aside on the first
        keystroke. All has no pill, since it hides nothing. The Subgraphs row's cannot be
        removed: All is the bots, so there is no wider list of subgraphs to widen to.
        """
        caption = self._scope_caption()
        if caption is None:
            self._search.set_scope(None)
        elif self._in_subgraphs_folder():
            self._search.set_scope(
                caption, removable=False, tooltip="Searching subgraphs. Select any other row to search bots."
            )
        else:
            self._search.set_scope(
                caption,
                tooltip=f"Searching {caption} only. Click, or press Backspace at the start of the search,"
                " to search every folder.",
            )

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
        self._show_pickers_for_folder()
        self._rebuild_chips()
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
        if self._in_subgraphs_folder():
            tags, group_of = set(self._selected_categories()), subgraph_tag_group
        else:
            tags, group_of = self._selected_tags(), tag_group
        query, sources = self._search.text(), set(self._source_picker.tags())
        results, matched_drops = filter_bot_metas(self._category_metas(), query, tags, sources, group_of)
        # Nothing here, but the row narrows the library: say whether the rest of it has
        # matches, which is the case a scoped search makes look like a missing bot.
        elsewhere = 0
        if not results and self._search.scope_removable:
            elsewhere = len(filter_bot_metas(self._all_metas, query, tags, sources, group_of)[0])
        self._show_cards(results, matched_drops, elsewhere=elsewhere)

    def _show_cards(self, metas: "list[_BotMeta]", matched_drops: "dict | None" = None, *, elsewhere: int = 0) -> None:
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
        self._empty_hint = self._empty_label = None

        if not metas:
            # "All" is always in the list now, so its presence no longer distinguishes
            # "nothing matched" from "the library is empty". The metas do.
            if self._in_subgraphs_folder():
                text = "No subgraphs match these filters."
            elif elsewhere:
                # Named, and counted: an empty folder alone read as "this bot doesn't
                # exist" (#269). The way out sits under the reason for taking it.
                others = "1 other bot does" if elsewhere == 1 else f"{elsewhere} other bots do"
                text = f"No bots in {self._scope_caption()} match these filters, but {others}."
            else:
                text = "No bots match these filters." if self._all_metas else "No bots found in bots/ directory."
            self._empty_hint = hint = QWidget()
            hint_layout = QVBoxLayout(hint)
            hint_layout.setContentsMargins(0, 0, 0, 0)
            hint_layout.setSpacing(8)
            self._empty_label = label = QLabel(text)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            hint_layout.addWidget(label)
            if elsewhere:
                widen = ActionButton("search", "Search all folders", fill=False)
                widen.clicked.connect(self.search_every_folder)
                hint_layout.addWidget(widen, 0, Qt.AlignmentFlag.AlignHCenter)
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
                card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                if meta.subgraph_id:
                    card.revert_requested.connect(self.delete_subgraph_requested)
                    card.customContextMenuRequested.connect(
                        lambda pos, c=card, sid=meta.subgraph_id: self._show_subgraph_menu(c, sid, pos)
                    )
                else:
                    card.customContextMenuRequested.connect(
                        lambda pos, c=card, path=meta.path: self._show_bot_menu(c, path, pos)
                    )
            card.set_matched_drops((matched_drops or {}).get(meta.path))
            layout.insertWidget(i, card)
            card.show()

    def subgraph_menu(self, subgraph_id: str) -> QMenu:
        """The menu a Subgraphs card shows on a right-click. Public so a test can read it.

        Delete removes the user's file, so what it says depends on where the definition
        is (:class:`~bartlebot.content.store_roots.Origin`): a custom one is deleted, a modified
        Official one is reverted, and an unedited Official one has nothing to remove. Rename is
        offered on the user's own definitions only, never on an Official one, modified or not.
        """
        menu = popup_menu(self)
        menu.setToolTipsVisible(True)
        origin = self._subgraph_library.origin(subgraph_id) if self._subgraph_library is not None else None
        rename = menu.addAction("Rename…")
        if origin is not Origin.USER:
            rename.setEnabled(False)
            rename.setToolTip(BUILT_IN_RENAME_TIP)
        rename.triggered.connect(lambda: self.rename_subgraph_requested.emit(subgraph_id))
        if origin is Origin.SHIPPED_OVERRIDE:
            act = menu.addAction("Revert to Official…")
        else:
            act = menu.addAction("Delete Subgraph…")
            if origin is not Origin.USER:
                act.setEnabled(False)
                act.setToolTip(_BUILT_IN_SUBGRAPH_TIP)
        act.triggered.connect(lambda: self.delete_subgraph_requested.emit(subgraph_id))
        menu.addSeparator()
        menu.addAction(EXPORT_TEXT).triggered.connect(lambda: self.export_subgraph_requested.emit(subgraph_id))
        return menu

    def _show_subgraph_menu(self, card: QWidget, subgraph_id: str, pos) -> None:
        self.subgraph_menu(subgraph_id).exec(card.mapToGlobal(pos))

    def bot_menu(self, path: Path) -> QMenu:
        """The menu a bot card shows on a right-click. Public so a test can read it."""
        menu = popup_menu(self)
        menu.setToolTipsVisible(True)
        menu.addAction(EXPORT_TEXT).triggered.connect(lambda: self.export_bot_requested.emit(str(path)))
        menu.addSeparator()
        delete = menu.addAction("Delete Bot…")
        if bot_roots.is_shipped(path):
            delete.setEnabled(False)
            delete.setToolTip(_BUILT_IN_BOT_TIP)
        delete.triggered.connect(lambda: self.delete_bot_requested.emit(str(path)))
        return menu

    def _show_bot_menu(self, card: QWidget, path: Path, pos) -> None:
        self.bot_menu(path).exec(card.mapToGlobal(pos))
