"""Sidebar section: the *values* half of a bot's options.

This is the surface a user gets when they are running someone else's bot.  Every
row shows the **effective** value -- what the run will actually use -- and says
where it came from when it did not come from the user.  Editing a row writes an
*override* to ``settings.json`` and never touches the bot file, which is the
whole point of the split: a shared bot stays byte-identical no matter how many
people tune it.

The rows sit in **folding cards** (#138): *This dungeon*, *Team*, *Quicksell* and
*General*, in that order in every bot (``bartlebot.options.option_groups`` says which option
goes where, and ``option_card`` draws one).  A flat list of thirteen options ran 859px
tall against a 404px sidebar at 1080p; folding the groups nobody is working in is
what gets the rest on screen.  Which cards are folded is the reader's, remembered in
``settings.json`` for every bot and shared with the Options tab.  Enable Quicksell is
not a row: it is the Quicksell card's header switch, and with it off every row in
the card is disabled.

Two markings, both borrowed from the systems that solved this before us:

* an **inherited** caption naming the level that answered -- the safety dialog's
  ``(app setting)`` suffix generalised to all four levels;
* a colored **left bar** plus an enabled **revert** button on any row the user
  has overridden, following VS Code's settings gutter and Unity's prefab
  overrides.  A card's header carries the same mark as a stripe, lit while any
  value in it is the user's, so a folded card cannot hide one.

Those two live in a fixed leading **gutter**, ``[bar][revert][?]``, which is what
gives every row's chrome one column each instead of a staircase -- see
``_build_row`` for why no other placement does.  The accepted cost is roughly
40px of permanent, mostly blank left inset.  Card headers carry no gutter: a header
holding only a title would carry fifty pixels of nothing before it.

The revert tooltip names the value a reset would fall back to, which is the free
mitigation for the one accepted cost of storing overrides as bare values: an
author who later improves their declaration is outranked silently, so the
one-way door has to be visibly openable.  The header switch has no revert: for a
two-state control, flipping back to the inherited value *is* the reset (see
``_on_value_changed``).

Descriptions are folded away behind a ``?`` and shown at the base font when
asked for.  Measured on a shipped bot, node-authored prose was 41% of an
ordinary row's height and the source caption another 17%, leaving the control
itself the minority of its own row -- at 9px, three quarters of the base size,
which is where Windows' subpixel antialiasing starts coloring the strokes.
Every settings surface that shows help inline keeps it to a single line;
the ones whose prose runs this long put it behind an affordance instead.

The declaration editor is the other half -- see ``options_section``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from bartlebot.gui.sidebar.option_card import CardFolds, OptionCard
from bartlebot.gui.sidebar.option_gutter import (
    DescriptionSwitches,
    HelpToggle,
    OverrideBar,
    RevertButton,
    build_gutter_row,
    keep_slot_when_hidden,
)
from bartlebot.gui.sidebar.sidebar_section import HeadingAction, SidebarSection, field_label
from bartlebot.gui.sidebar.strategy_panel import LOCKED
from bartlebot.gui.theming.theme import THEME
from bartlebot.gui.widgets.buttons.toggle_switch import ToggleSwitch
from bartlebot.gui.widgets.inputs.option_widgets import (
    TALL_TYPES,
    block_separate_if_merging,
    connect_widget,
    make_option_widget,
    set_widget_value,
)
from bartlebot.gui.widgets.labels.themed_label import ThemedTextLabel
from bartlebot.gui.widgets.pickers.player_list_widget import PlayerListWidget
from bartlebot.options import bot_options, option_groups
from bartlebot.options.bot_options import UNSET, compose_options, is_set, option_specs_for, values_agree
from bartlebot.settings_manager import SettingsManager

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

logger = logging.getLogger(__name__)

_PLACEHOLDER = "—"

# There is no caption map any more: ``OptionResolution.source`` *is* the caption.
# The two used to be separate strings for the same four levels, which is how the
# log came to say "the app setting" under a row reading "App settings" -- one value
# with two names.  Every level still has a caption, including the user's own: one
# that appeared and disappeared would move every row beneath it as a value was typed.


def _is_tall(opt) -> bool:
    """Whether *opt* is drawn as a table rather than a field, and so goes last in its card.

    A 220px drop table between two spin boxes pushes everything after it out of sight;
    this depends on the control, never on the option's name.
    """
    return opt.type in TALL_TYPES


class _SourceCaption(ThemedTextLabel):
    """The right-hand end of a row's head line, naming the level that answered.

    Given a **fixed** slot, wide enough for the longest of the four words, and
    right-aligned inside it.  The word changes as a value is edited, and a caption
    that resized with its own text would drag the option's name sideways on every
    keystroke -- the same reasoning as ``_size_status_slots`` in
    ``canvas/bot_header.py``, which reserves equal widths either side of the
    transport for exactly this.  The slot is a *minimum*: an unforeseen longer word
    widens it rather than being clipped.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("", color="text_disabled", parent=parent)
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        # The caption's size is set on the *font* rather than in the style sheet,
        # because a style sheet's ``font-size`` never reaches ``QWidget.font()`` and
        # the slot below is measured from the font -- the same trap the run status
        # label in ``canvas/bot_header.py`` documents.
        font = THEME.ui_font(THEME.font_micro_px)
        self.setFont(font)
        metrics = QFontMetrics(font)
        self.setMinimumWidth(max(metrics.horizontalAdvance(text) for text in bot_options.SOURCE_LEVELS))
        super().apply_theme_styles()


@dataclass
class _RowChrome:
    """The parts of one row that change when its override state does."""

    widget: QWidget
    bar: OverrideBar
    revert_btn: RevertButton
    caption: _SourceCaption
    help_btn: HelpToggle
    outer: QWidget
    body: QWidget
    description: QWidget | None = None
    #: The bool option this row is a child of, or ``None``.  A child is indented
    #: under its parent -- unless the parent is its card's header switch, since the
    #: card already says what the row belongs to.
    depends_on: str | None = None
    #: The bool option whose being off disables this row: its parent, or else the
    #: switch heading its card.  The keep list has no parent and is still nothing
    #: but a list while quicksell is off.
    gate: str | None = None


@dataclass
class _HeaderChrome:
    """An option drawn as its card's header switch rather than as a row."""

    widget: ToggleSwitch
    caption: _SourceCaption
    group: str
    #: Its description, at the top of the card body while descriptions are shown.
    description: QWidget | None = None


class OptionValuesSection(SidebarSection):
    """Collapsible 'Option Values' section: a card per option group, editing the user's values."""

    # Emitted after an override is written or reset.  Deliberately *not* wired to
    # ``config_changed``: an override is not an edit to the bot, and marking the
    # file dirty for one is the confusion this whole split exists to remove.
    values_changed = pyqtSignal()

    # The reader asked for the level beneath every row: Settings -> Bot.  Emitted
    # rather than opened here, because the dialog is modal and ``exec()`` may only
    # be entered from a synchronous Qt slot -- see the qasync rule in CLAUDE.md.
    app_settings_requested = pyqtSignal()

    def __init__(self, parent=None, settings: SettingsManager | None = None):
        super().__init__("Bot Settings", parent)
        self._config: BotConfig | None = None
        self._settings = settings
        self._rows: list[QWidget] = []
        self._composed: dict[str, object] = {}
        self._chrome: dict[str, _RowChrome] = {}
        self._headers: dict[str, _HeaderChrome] = {}
        self._option_widgets: dict[str, QWidget] = {}
        self._cards: dict[str, OptionCard] = {}
        self._groups: list[option_groups.OptionGroup] = []
        self._group_of: dict[str, str] = {}
        self._option_specs: dict | None = None
        self._get_clients: Callable[[], list] = lambda: []
        # A bot is running: values are read at run start, so an edit now would do nothing.
        self._locked = False
        self._show_descriptions = self._read_show_descriptions()

        outer = QVBoxLayout()
        outer.setSpacing(4)

        # The app-setting level is the one a row can name but not edit, so the route
        # to it lives here rather than in every hint that mentions it: eight rows
        # each carrying the same link is eight things to read and one destination.
        self._switches = DescriptionSwitches(
            self._on_show_all_toggled,
            trailing=(
                "Open app settings",
                "Open Settings → Bot, where a row falling back to the app setting reads its value",
                self.app_settings_requested.emit,
            ),
        )
        outer.addWidget(self._switches)

        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(6)

        self._empty_lbl = QLabel(_PLACEHOLDER)
        self._empty_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._rows_layout.addWidget(self._empty_lbl)

        outer.addLayout(self._rows_layout)
        self.set_content_layout(outer)

        # Which cards are folded, and the heading's Collapse all and Expand all.
        self._folds = CardFolds(self, self._sm)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_config(self, config: BotConfig | None) -> None:
        self._config = config
        self._rebuild()

    def set_clients_getter(self, fn: Callable[[], list]) -> None:
        """Pass a callable returning the currently hooked clients."""
        self._get_clients = fn
        self.refresh_player_availability()

    def refresh_player_availability(self) -> None:
        """Re-gray the player checkboxes — call when clients connect or disconnect."""
        connected = len(self._get_clients())
        for w in self.findChildren(PlayerListWidget):
            w.set_available_players(connected)

    def set_editable(self, editable: bool) -> None:
        """Accepted and ignored: this surface has no authoring controls to reveal.

        Editing a *value* is what a user does before pressing Run, so nothing here
        is gated on the canvas being in Edit mode.  The controls that are gated —
        add, edit, delete a declaration — live in ``OptionsSection``.
        """

    def set_locked(self, locked: bool) -> None:
        """Hold every value while a bot runs, with the reason as each row's tooltip.

        Not ``set_editable``, which is the canvas's Edit mode: a run reads most options
        once, at its start, so a mid-run change would give no sign that it did nothing.
        The description switches and the app-settings link stay live, since they edit
        nothing.
        """
        if locked == self._locked:
            return
        self._locked = locked
        self._apply_lock()

    @property
    def locked(self) -> bool:
        return self._locked

    def _apply_lock(self) -> None:
        for name, header in self._headers.items():
            header.widget.setEnabled(not self._locked)
            self._refresh_row(name)
        for name, chrome in self._chrome.items():
            chrome.body.setEnabled(not self._locked)
            chrome.outer.setToolTip(LOCKED if self._locked else "")
            self._refresh_row(name)
        for name in {chrome.gate for chrome in self._chrome.values() if chrome.gate}:
            self._refresh_children_of(name)

    @property
    def option_widgets(self) -> dict[str, QWidget]:
        """``{option name: value widget}`` for the options currently shown.

        Enable Quicksell's is its card's header switch, which speaks the same check
        box protocol as any other bool's control.
        """
        return dict(self._option_widgets)

    @property
    def cards(self) -> dict[str, OptionCard]:
        """``{group id: card}`` for the cards this bot has, in drawing order."""
        return dict(self._cards)

    @property
    def collapse_all_button(self) -> HeadingAction:
        return self._folds.collapse_all_button

    @property
    def expand_all_button(self) -> HeadingAction:
        return self._folds.expand_all_button

    @property
    def descriptions_visible(self) -> bool:
        """Whether descriptions are shown for every row by default."""
        return self._show_descriptions

    def set_descriptions_visible(self, shown: bool) -> None:
        """Show or fold every option's description, exactly as the switches would.

        Unconditional, like the switches themselves: the per-row ``?`` can leave the
        rows disagreeing with the remembered default, and a no-op here when the two
        already match would leave those rows where they were.
        """
        self._on_show_all_toggled(shown)

    def resolution_for(self, name: str) -> bot_options.OptionResolution | None:
        """The effective value of *name* and the level that supplied it."""
        if self._config is None:
            return None
        return bot_options.resolve_for_config(
            self._config,
            name,
            override=self._override(name),
            app_value=self._app_value(name),
            option_specs=self._option_specs,
        )

    # ------------------------------------------------------------------
    # Folding
    # ------------------------------------------------------------------

    def set_group_collapsed(self, group: str, collapsed: bool) -> None:
        """Fold or unfold one card, and remember it for every bot."""
        self._folds.set_group_collapsed(group, collapsed)

    def collapse_all(self) -> None:
        self._folds.collapse_all()

    def expand_all(self) -> None:
        self._folds.expand_all()

    def collapse_others(self, keep: str) -> None:
        """Leave *keep* open and fold every other card (Blender's Ctrl+click)."""
        self._folds.collapse_others(keep)

    def card_menu(self, group: str):
        """The menu a right-click on *group*'s header opens, built but not shown."""
        return self._folds.card_menu(group)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Pick up folds made on the Options tab while this one was not showing."""
        self._folds.reload()
        super().showEvent(event)

    # ------------------------------------------------------------------
    # Settings access
    # ------------------------------------------------------------------

    def _sm(self) -> SettingsManager:
        """The settings store, resolved late so a test can inject its own."""
        if self._settings is None:
            self._settings = SettingsManager.instance()
        return self._settings

    def _override(self, name: str) -> Any:
        if self._config is None:
            return UNSET
        try:
            return self._sm().get_option_override(self._config.id, name)
        except Exception:
            logger.exception("Failed to read the stored value for option %r", name)
            return UNSET

    def _app_value(self, name: str) -> Any:
        key = bot_options.APP_SETTING_KEYS.get(name)
        if key is None:
            return UNSET
        try:
            return self._sm().get("general", key, default=UNSET)
        except Exception:
            logger.exception("Failed to read the app setting behind option %r", name)
            return UNSET

    def _declared_default(self, name: str) -> Any:
        """The bot file's own default for *name*, or ``None`` if it declares none."""
        if self._config is None:
            return None
        for opt in self._config.options:
            if opt.name == name:
                return opt.default
        return None

    # ------------------------------------------------------------------
    # Cards and rows
    # ------------------------------------------------------------------

    def _rebuild(self) -> None:
        """Build every card. Only a *different set of options* needs this.

        A value edit refreshes one row in place instead -- rebuilding would destroy
        the control the user is still holding, which for a spin box means the arrow
        they are clicking vanishes from under the pointer on the first click.
        """
        for card in self._rows:
            self._rows_layout.removeWidget(card)
            card.deleteLater()
        self._rows.clear()
        self._composed.clear()
        self._option_widgets.clear()
        self._chrome.clear()
        self._headers.clear()
        self._cards.clear()
        self._group_of.clear()

        options = compose_options(self._config) if self._config is not None else []
        # Computed once per rebuild rather than per resolution: ``resolve_for_config``
        # walks the whole registry for it otherwise, and a card refresh resolves every
        # option on the card.
        self._option_specs = option_specs_for(self._config) if self._config is not None else None
        self._composed = {opt.name: opt for opt in options}
        self._groups = option_groups.group_options(options, self._option_specs or {}, is_tall=_is_tall)
        self._switches.setVisible(any(opt.description for opt in options))
        if not options:
            self._empty_lbl.show()
            self._folds.set_cards({})
            return
        self._empty_lbl.hide()

        connected = len(self._get_clients())
        for group in self._groups:
            card = self._build_card(group, connected)
            self._rows_layout.addWidget(card)
            self._rows.append(card)
            self._cards[group.id] = card
        for name in {chrome.gate for chrome in self._chrome.values() if chrome.gate}:
            self._refresh_children_of(name)
        self._folds.set_cards(self._cards)
        self._push_keep_list_context()
        if self._locked:
            self._apply_lock()

    def _build_card(self, group: option_groups.OptionGroup, connected: int) -> OptionCard:
        """One group's card: its header switch, if it has one, then its rows."""
        card = OptionCard(group.id, group.title)
        header = group.header.name if group.header is not None else None
        if group.header is not None:
            self._build_header(card, group)
        for opt in group.members:
            self._group_of[opt.name] = group.id
            card.body_layout.addWidget(self._build_row(opt, connected, card_header=header))
        return card

    def _build_header(self, card: OptionCard, group: option_groups.OptionGroup) -> None:
        """The card's header switch, with its source caption beside it.

        The caption still names the level that answered, so the user can tell a switch
        they set from one the app settings set. There is no revert and no ``?``: the
        revert would only flip the switch back, and the description is in the switch's
        tooltip and, while descriptions are shown, at the top of the card.
        """
        opt = group.header
        self._group_of[opt.name] = group.id
        resolution = self.resolution_for(opt.name)
        switch = ToggleSwitch()
        switch.setAccessibleName(opt.name)
        set_widget_value(switch, resolution.value if resolution is not None else opt.default)
        caption = _SourceCaption()
        card.add_header_widget(caption)
        card.add_header_widget(switch, gap_before=2)
        connect_widget(switch, lambda new_value, name=opt.name: self._on_value_changed(name, new_value))
        self._option_widgets[opt.name] = switch

        description: QWidget | None = None
        if opt.description:
            description = ThemedTextLabel(opt.description)
            description.setWordWrap(True)
            card.body_layout.addWidget(description)
            description.setVisible(self._show_descriptions)
        self._headers[opt.name] = _HeaderChrome(widget=switch, caption=caption, group=group.id, description=description)
        self._refresh_row(opt.name)

    def _build_row(self, opt, connected: int, *, card_header: str | None = None) -> QWidget:
        """One option's row: a fixed leading gutter, then a head line and a control.

        The gutter is ``[bar][revert][?]``, and it is the only placement that gives
        those two buttons one column each.  Right-aligning the ``?`` gives *two*
        columns rather than one, because the head line's right edge sits at two
        different x values; a fixed-width name column fails on any long name, and on
        a bool row, which has no separate label at all.  In the gutter both buttons
        top-align with the head line whether the row is 20px tall or 240.

        Revert sits against the ``OverrideBar`` it agrees with, ``?`` against the
        name it explains -- so the destructive button is the one furthest from the
        text being read.

        Nothing here appears or disappears with the override state: the bar keeps its
        width and changes color, revert keeps its slot and grays out, and the
        caption always names a level.  Chrome that comes and goes as you type moves
        everything under it, which on a spin box means the second click of a pair
        lands somewhere else.

        The description is the one exception, and it is not one: it folds on a
        deliberate click of its own ``?``, never on a value edit, and everything it
        moves is below the button that moved it.

        *card_header* is the option heading this row's card, if any. A row whose
        parent is that switch is not indented under it -- the card says what the row
        belongs to -- but it is still disabled while the switch is off.
        """
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(1)

        resolution = self.resolution_for(opt.name)
        widget = make_option_widget(
            opt.type,
            resolution.value if resolution is not None else opt.default,
            connected=connected,
            tooltip=opt.description or "",
            label=opt.name,
            minimum=getattr(opt, "minimum", None),
            maximum=getattr(opt, "maximum", None),
            suffix=getattr(opt, "suffix", None),
            get_drops=self._drops,
            get_clients=lambda: self._get_clients(),
        )
        block_separate_if_merging(widget, self._config)
        is_bool = opt.type == "bool"
        caption = _SourceCaption()

        # The head line is the same shape on every row -- name on the left, source on
        # the right -- now that the ``?`` has moved into the gutter.  The caption used
        # to have a line of its own beneath the control, which cost every row 12px to
        # say two words.
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(4)
        # A bool has no separate label: its own caption is the name, so the checkbox
        # *is* the head line and the whole row fits on it.
        if is_bool:
            head.addWidget(widget, 0)
        else:
            lbl = field_label(opt.name)
            if opt.description:
                # The hover path stays: the ? is the discoverable affordance, not
                # the only one, and a tooltip costs no height.
                lbl.setToolTip(opt.description)
            head.addWidget(lbl, 0)
        head.addStretch(1)
        head.addWidget(caption, 0)
        body_layout.addLayout(head)

        if not is_bool:
            body_layout.addWidget(widget)

        description: QWidget | None = None
        if opt.description:
            # At the base font, not the 9px the captions use: this is the one piece
            # of prose on the surface, and it is only on screen when asked for.
            description = ThemedTextLabel(opt.description)
            description.setWordWrap(True)
            body_layout.addWidget(description)
            # Shown only once it has a parent: ``setVisible(True)`` on a parentless
            # widget makes it a top-level window, which Qt maps and unmaps as the
            # row is assembled -- a frame flashing on screen once per row.
            description.setVisible(self._show_descriptions)

        connect_widget(widget, lambda new_value, name=opt.name: self._on_value_changed(name, new_value))
        self._option_widgets[opt.name] = widget

        # The bar is a sibling of the body, not a border on it: a stylesheet border
        # set on the row would be inherited by every control inside it.
        bar = OverrideBar()
        revert_btn = RevertButton()
        revert_btn.clicked.connect(lambda _checked, name=opt.name: self._on_reset(name))

        help_btn = HelpToggle()
        help_btn.toggled.connect(lambda shown, name=opt.name: self._set_row_description(name, shown))

        parent_name = self._parent_of(opt.name)
        indented = parent_name is not None and parent_name != card_header
        outer = build_gutter_row(bar, revert_btn, help_btn, body, child=indented)
        if description is None:
            keep_slot_when_hidden(help_btn)
        else:
            help_btn.setChecked(self._show_descriptions)

        self._chrome[opt.name] = _RowChrome(
            widget=widget,
            bar=bar,
            revert_btn=revert_btn,
            caption=caption,
            help_btn=help_btn,
            outer=outer,
            body=body,
            description=description,
            depends_on=parent_name,
            gate=parent_name or card_header,
        )
        self._refresh_row(opt.name)
        return outer

    def refresh_drops(self) -> None:
        """Re-read the drop table into the keep-list picker, after a Possible Drops edit."""
        for name, widget in self._option_widgets.items():
            if self._composed_type(name) == "drop_keep_list":
                widget.refresh()
        self._refresh_cards()

    def _drops(self) -> list:
        """This bot's drop table, for the keep-list picker to show."""
        return list(self._config.drops) if self._config is not None else []

    def _keep_list_pickers(self) -> list[QWidget]:
        return [w for name, w in self._option_widgets.items() if self._composed_type(name) == "drop_keep_list"]

    def _push_keep_list_context(self) -> None:
        """Tell the keep-list picker what the sell flags resolved to.

        The picker shows what will *happen* to each drop, and three of the answers
        are not its own: Sell Pets off spares every pet whatever the list says, and
        Sell Socket Wrenches off adds a keyword the user never typed. Those are
        separate rows on this same surface, so flipping one has to reach the table --
        and the Quicksell card's folded summary and warning, which read the picker.
        """
        pickers = self._keep_list_pickers()
        if pickers and self._config is not None:
            try:
                from bartlebot.nodes.utility import quicksell  # noqa: PLC0415 - heavy import, only when shown

                context = quicksell.resolve_options_for(self._config, settings=self._settings)
            except Exception:
                logger.exception("Could not resolve the quicksell options behind the keep-list picker")
            else:
                for picker in pickers:
                    picker.set_context(context)
        self._refresh_cards()

    def _composed_type(self, name: str) -> str | None:
        opt = self._composed.get(name)
        return getattr(opt, "type", None)

    # ------------------------------------------------------------------
    # The cards' headers
    # ------------------------------------------------------------------

    def _refresh_cards(self) -> None:
        for group in self._groups:
            self._refresh_card(group)

    def _refresh_card(self, group: option_groups.OptionGroup) -> None:
        """Re-mark one card's header from what its options resolve to.

        The stripe is lit while any value on the card is the user's, so a folded card
        cannot hide one. The summary and the warning read the keep-list picker, which
        is the one place the counts and the unmatchable drops are worked out.
        """
        card = self._cards.get(group.id)
        if card is None:
            return
        values: dict[str, Any] = {}
        marked = False
        for opt in group.options:
            resolution = self.resolution_for(opt.name)
            if resolution is None:
                continue
            values[opt.name] = resolution.value
            marked = marked or resolution.source == bot_options.SOURCE_OVERRIDE
        card.set_marked(marked)

        # Nothing is sold while the switch is off, so nothing is lost either.
        live = group.header is None or values.get(group.header.name) is not False
        pickers = [w for w in self._keep_list_pickers() if self._group_of.get(self._name_of(w)) == group.id]
        keep_counts = pickers[0].kept_counts[:2] if pickers else None
        unmatchable = pickers[0].unmatchable_counts if pickers and live else {}
        card.set_summary(
            option_groups.group_summary(group, values, keep_counts=keep_counts, pets_sold=unmatchable.get("Pets", 0))
        )
        card.set_warning(self._warning_text(unmatchable))

    def _name_of(self, widget: QWidget) -> str | None:
        return next((name for name, w in self._option_widgets.items() if w is widget), None)

    @staticmethod
    def _warning_text(unmatchable: dict[str, int]) -> str | None:
        """The keep list's own warning line, as the header's ``⚠`` tooltip."""
        if not unmatchable:
            return None
        from bartlebot.nodes.utility import quicksell  # noqa: PLC0415 - heavy import, only when shown

        # Every category the picker counts comes from this same table.
        return "\n".join(
            f"{count} {category}: {quicksell.UNMATCHABLE_CATEGORIES[category]}"
            for category, count in sorted(unmatchable.items())
        )

    # ------------------------------------------------------------------
    # Children
    # ------------------------------------------------------------------

    def _parent_of(self, name: str) -> str | None:
        """The bool option *name* is a child of, if that option is on this surface.

        Checked against the composed list rather than taken from the declaration on
        trust: a child indented under a row that is not there reads as a child of
        whatever happens to precede it, and a hand-edited file can name anything.
        """
        parent = getattr(self._composed.get(name), "depends_on", None)
        if not parent or parent == name:
            return None
        return parent if self._composed_type(parent) == "bool" else None

    def _children_of(self, name: str) -> list[str]:
        """Every row *name* being off disables: its children, and its card's rows."""
        return [child for child, chrome in self._chrome.items() if chrome.gate == name]

    def _refresh_children_of(self, name: str) -> None:
        """Gray or restore every row hanging off *name*, from its resolved value.

        A child is disabled rather than hidden.  Hiding it would move every row
        beneath it the moment the parent is ticked -- the thing this surface avoids
        everywhere else -- and it would hide the fact that the user still has a
        value stored there, which comes back untouched when the parent comes back
        on.  Only the *body* is grayed: the gutter stays live, so the override bar
        still says whose value it is and revert can still discard it.
        """
        resolution = self.resolution_for(name)
        active = bool(resolution.value) if resolution is not None else True
        for child in self._children_of(name):
            chrome = self._chrome[child]
            chrome.body.setEnabled(active and not self._locked)
            if self._locked:
                chrome.outer.setToolTip(LOCKED)
            else:
                chrome.outer.setToolTip("" if active else f"{name} is off, so this has no effect.")

    def _refresh_row(self, name: str) -> None:
        """Re-mark one option from its current resolution, touching no other row."""
        resolution = self.resolution_for(name)
        if resolution is None:
            return
        header = self._headers.get(name)
        if header is not None:
            header.caption.setText(resolution.source)
            opt = self._composed.get(name)
            state = "on" if resolution.value else "off"
            prose = getattr(opt, "description", None)
            header.widget.setToolTip(f"{name}: {state}\n\n{prose}" if prose else f"{name}: {state}")
            return
        chrome = self._chrome.get(name)
        if chrome is None:
            return
        overridden = resolution.source == bot_options.SOURCE_OVERRIDE
        # These two lines are the whole reason revert sits against the bar: they are
        # one fact -- "this value is yours" -- said twice, and adjacent they read as
        # one affordance rather than two marks to correlate across a row.
        chrome.bar.set_active(overridden)
        chrome.revert_btn.setEnabled(overridden and not self._locked)
        chrome.revert_btn.setToolTip(
            LOCKED
            if self._locked
            else self._reset_tooltip(name)
            if overridden
            else "Already the value this option resolves to"
        )
        chrome.caption.setText(resolution.source)

    def _fallback_for(self, name: str) -> bot_options.OptionResolution | None:
        """What *name* would resolve to with the user's override taken away.

        Two callers, and they are the same question asked from opposite sides: the
        Reset tooltip naming where the value would land, and a value edit asking
        whether it has landed there already.
        """
        opt = self._composed.get(name)
        if opt is None:
            return None
        return bot_options.resolve_option(
            name,
            override=UNSET,
            declaration=self._declared_default(name),
            declared_type=opt.type,
            app_value=self._app_value(name),
            spec_default=opt.default,
            minimum=getattr(opt, "minimum", None),
            maximum=getattr(opt, "maximum", None),
        )

    def _reset_tooltip(self, name: str) -> str:
        """Name the value a reset would fall back to, and the level it comes from.

        This is the mitigation for two things at once: an icon-only button is less
        discoverable than the word it replaced, and storing overrides as bare values
        means an author who later improves their declaration is outranked silently.
        Both are answered by saying, on hover, exactly what a click would leave
        behind.

        The level is named in running prose rather than parenthesised as a
        capitalised noun phrase -- "Reset to 3.0 - app settings", not
        "Reset to 3.0 (App settings)".  The captions are labels on a column and are
        capitalised for that; a tooltip is a sentence, so it lowercases the one it
        shares with the column.
        """
        fallback = self._fallback_for(name)
        if fallback is None:
            return "Reset to the value this bot recommends"
        level = fallback.source.lower()
        return f"Reset to {fallback.value!r} — {level}"

    # ------------------------------------------------------------------
    # Edits
    # ------------------------------------------------------------------

    def _on_value_changed(self, name: str, value: Any) -> None:
        """Store the user's value -- or forget it, when they have typed their way back.

        Setting a control back to what the option already resolves to is the same
        act as pressing Reset, and has to leave the same state behind. A row marked
        as overridden while holding the value it would have had anyway is a lie
        about what the user did, and it would then silently outrank an author who
        later improved the declaration it happens to match. Every settings UI that
        marks overrides this way drops the row instead; so does this one.

        It is also the whole of the header switch's reset: a switch has two states,
        so flipping it back to the inherited one lands here and drops the override.
        """
        if self._config is None:
            return
        fallback = self._fallback_for(name)
        if fallback is not None and values_agree(fallback.value, value):
            self._on_reset(name)
            return
        if values_agree(self._override(name), value):
            return
        try:
            self._sm().set_option_override(self._config.id, name, value)
        except Exception:
            logger.exception("Failed to store your value for option %r", name)
            return
        self._refresh_row(name)
        self._refresh_children_of(name)
        self._push_keep_list_context()
        self.values_changed.emit()

    def _read_show_descriptions(self) -> bool:
        try:
            return bool(self._sm().get("general", "show_option_descriptions", default=False))
        except Exception:
            logger.exception("Failed to read whether option descriptions are shown")
            return False

    def _on_show_all_toggled(self, shown: bool) -> None:
        """Set every row at once, and remember the choice.

        The per-row ``?`` still overrides one row afterwards; this is the default
        each row is built with and what a later use of these switches puts them back
        to. Nothing here touches the bot -- it is a reading preference.
        """
        self._show_descriptions = shown
        try:
            self._sm().set("general", "show_option_descriptions", value=shown)
        except Exception:
            logger.exception("Failed to remember whether option descriptions are shown")
        for chrome in self._chrome.values():
            chrome.help_btn.setChecked(shown)
        for header in self._headers.values():
            if header.description is not None:
                header.description.setVisible(shown)

    def _set_row_description(self, name: str, shown: bool) -> None:
        """Fold one row's description in or out, leaving every other row alone."""
        chrome = self._chrome.get(name)
        if chrome is None or chrome.description is None:
            return
        chrome.description.setVisible(shown)

    def _on_reset(self, name: str) -> None:
        """Forget the user's value for *name* and put the resolved one back on screen."""
        if self._config is None:
            return
        had_override = is_set(self._override(name))
        try:
            self._sm().drop_option_override(self._config.id, name)
        except Exception:
            logger.exception("Failed to forget your value for option %r", name)
            return
        widget = self._option_widgets.get(name)
        fallback = self._fallback_for(name)
        if widget is not None and fallback is not None:
            # Blocked, because writing the value back through the widget would come
            # straight back round as an edit -- and land here again.
            widget.blockSignals(True)
            set_widget_value(widget, fallback.value)
            widget.blockSignals(False)
            widget.update()
        self._refresh_row(name)
        self._refresh_children_of(name)
        self._push_keep_list_context()
        if had_override:
            self.values_changed.emit()
