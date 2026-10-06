"""Properties panel widgets for the bot canvas node editor.

_DynamicParamForm  — generic schema-driven form used for node parameters.
_ParamPanel        — stacked panel showing node, edge, or idle views; owns the
                     Record button and delegates recording to ``recording.py``.
"""

import asyncio
import json
import logging
import re
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from PyQt6.QtCore import QEvent, QLocale, QRectF, QSize, QStringListModel, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QDoubleValidator,
    QFont,
    QFontMetrics,
    QKeySequence,
    QPainter,
    QPen,
    QStandardItem,
    QStandardItemModel,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from bartlebot import param_cards, zone_names
from bartlebot.bot.schema import FlagCondition, FlagGroup, RegionModel, StateModel, TransitionModel
from bartlebot.gui.canvas import recording
from bartlebot.gui.icon_utils import svg_icon, svg_pixmap
from bartlebot.gui.menu_utils import popup_menu
from bartlebot.gui.sidebar.option_gutter import (
    HelpToggle,
    OverrideBar,
    RevertButton,
    build_gutter_row,
    keep_slot_when_hidden,
)
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin, set_style_sheet
from bartlebot.gui.widgets.action_button import ActionButton, ActionStyle
from bartlebot.gui.widgets.discard import discard_widget
from bartlebot.gui.widgets.elided_label import ElidedLabel
from bartlebot.gui.widgets.icon_button import IconButton, IconTint
from bartlebot.gui.widgets.list_text import format_list_text, parse_list_text
from bartlebot.gui.widgets.player_list_widget import PlayerListWidget, PlayerSlotWidget
from bartlebot.gui.widgets.player_picker import pick_player, picker_label
from bartlebot.gui.widgets.ratio_spin_box import RatioSpinBox
from bartlebot.gui.widgets.record_button import IDLE_TEXT, RecordButton
from bartlebot.gui.widgets.scales_picker import ScalesButton
from bartlebot.gui.widgets.scroll_gutter import reserve_scrollbar_gutter
from bartlebot.gui.widgets.tag_pill import NeutralChip
from bartlebot.gui.widgets.themed_label import ThemedTextLabel
from bartlebot.gui.widgets.zone_picker import NavZoneButton, is_destination
from bartlebot.nodes import zone_navigation
from bartlebot.nodes.param_schema import ParamField
from bartlebot.nodes.registry import NODE_CATEGORIES, NODE_PARAM_SCHEMAS, NODE_REGISTRY, NODE_SPECS
from bartlebot.settings_manager import SettingsManager
from bartlebot.setup_steps import value_empty
from bartlebot.subgraphs import MAX_DEPTH, InstanceKey, binding_type, nested_key, promoted_param_type
from bartlebot.wiki_pages import WIKI_PAGE_PARAM, page_title


def input_param_field(defn, inp, library=None, _depth: int = 0) -> ParamField:
    """The ``ParamField`` an input's widget is built from, on the call node and the signature panel.

    The input stores what the author owns: key, type, label, help and default. What
    the *type* owns -- a placeholder, a numeric range --
    comes from a bound member param of the input's own type when the form is drawn,
    so a change to a node's schema reaches every input bound to it. If any bound param is
    required, so is the input. ``defn`` is anything with ``states``: a definition, or
    a definition document's config.

    An input linked into a nested instance counts that instance's input as a bound
    field, built the same way from its own definition, which takes ``library`` (a
    ``SubgraphLibrary``) to find. With no default of its own, the input shows the
    nested one's: unset, that is the value the nested instance runs with.
    """
    by_id = {s.id: s for s in defn.states}
    # The strictest type of what the input is linked to, whatever an older file stored.
    inp_type = binding_type(defn, inp.bindings, NODE_PARAM_SCHEMAS, library) or inp.type
    bound = [
        f
        for b in inp.bindings
        if (st := by_id.get(b.state)) is not None
        for f in NODE_PARAM_SCHEMAS.get(st.node_type, [])
        if f.key == b.param
    ]
    nested = [
        f
        for b in inp.bindings
        if _depth < MAX_DEPTH and (found := nested_key(defn, b, library)) is not None
        if (f := instance_key_field(found, library, _depth + 1)) is not None
    ]
    bound += nested
    first = next((f for f in bound if f.type == inp_type), bound[0] if bound else None)
    default = inp.default
    if default is None:
        default = next((f.default for f in nested if f.default is not None), None)
    return ParamField(
        key=inp.name,
        type=inp_type,
        label=inp.label or inp.name,
        help=inp.help or "",
        default=default,
        required=any(f.required for f in bound),
        placeholder=first.placeholder if first is not None else "",
        minimum=first.minimum if first is not None else None,
        maximum=first.maximum if first is not None else None,
    )


def instance_key_field(ikey: InstanceKey, library=None, _depth: int = 0) -> ParamField | None:
    """The ``ParamField`` one key of a subgraph node's inputs is drawn from, or None if its node has no such param.

    A named input's is :func:`input_param_field`. A promoted node's param is the
    member's own field, typed as the subgraph node types it, and defaulting to the
    value the member holds: unset, that is what it runs with.
    """
    if ikey.input is not None:
        return input_param_field(ikey.defn, ikey.input, library, _depth)
    member = next((s for s in ikey.defn.states if s.id == ikey.panel.member), None)
    field = (
        next((f for f in NODE_PARAM_SCHEMAS.get(member.node_type, []) if f.key == ikey.key), None) if member else None
    )
    if field is None:
        return None
    narrowed = promoted_param_type(ikey.defn, ikey.panel, field.key, NODE_PARAM_SCHEMAS) or field.type
    return replace(field, type=narrowed, default=member.params.get(field.key, field.default))


def instance_key_label(ikey: InstanceKey) -> str:
    """A key of a subgraph node's inputs as that node's own page names it: "Hub", or "Enter dungeon › From Zone"."""
    if ikey.input is not None:
        return ikey.input.label or ikey.input.name
    field = instance_key_field(ikey)
    member = next((s for s in ikey.defn.states if s.id == ikey.panel.member), None)
    group = ikey.panel.label or (member.label if member is not None else None) or ikey.panel.member
    return f"{group} › {field.label if field is not None else ikey.key}"


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentinel QLineEdit subclasses  (identified by get_params())
# ---------------------------------------------------------------------------


class _WikiPageLineEdit(QLineEdit):
    """Line edit for a 'wiki_page' param.

    Stores a bare page title. A pasted URL is normalized to its title on edit, so
    whichever form the user has to hand produces the same thing on disk — and a bot
    file never carries a host of its own. See bartlebot/wiki_pages.py.
    """

    def normalize(self) -> None:
        """Rewrite a pasted URL as its page title, leaving anything else alone."""
        raw = self.text().strip()
        if "://" not in raw:
            return
        title = page_title(raw)
        if title is not None and title != raw:
            self.setText(title)

    def is_usable(self) -> bool:
        """Whether the current text is empty or resolves to a wiki page."""
        raw = self.text().strip()
        return not raw or page_title(raw) is not None


class _StrListLineEdit(QLineEdit):
    """QLineEdit variant used for 'str_list' param fields.

    Stores values as a comma-separated string (e.g. "IronBloom, GoldKey") and
    is identified by get_params() to deserialize back to list[str].
    """


# ---------------------------------------------------------------------------
# Clipboard XYZ parsing
# ---------------------------------------------------------------------------

_AXES = ("x", "y", "z")
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_XYZ_CALL_RE = re.compile(
    rf"XYZ\s*\(\s*({_NUM})\s*,\s*({_NUM})\s*,\s*({_NUM})\s*\)",
    re.IGNORECASE,
)
_XYZ_BARE_RE = re.compile(rf"\(?\s*({_NUM})\s*,\s*({_NUM})\s*,\s*({_NUM})\s*\)?")


def parse_xyz_text(text: str) -> dict[str, float]:
    """Parse clipboard text into whichever of the x/y/z axes it defines.

    Three formats are accepted:

    * ``XYZ(x, y, z)`` — the form used by Deimos scripts, so a line lifted
      straight out of one (``mass tp XYZ(1, 2, 3)``) pastes as-is.  Only the
      first such call on the line is read.
    * a bare ``x, y, z``, optionally in parentheses — the whole text, not a
      part of it.
    * a JSON object with any of ``x``/``y``/``z`` — what the Copy button
      writes.  Missing or non-numeric axes are simply left out, so a partial
      object updates only the axes it names.

    Returns an empty dict when nothing parses; the caller leaves the boxes
    untouched in that case.
    """
    text = text.strip()
    match = _XYZ_CALL_RE.search(text) or _XYZ_BARE_RE.fullmatch(text)
    if match:
        return {axis: float(match.group(i)) for i, axis in enumerate(_AXES, start=1)}
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, float] = {}
    for axis in _AXES:
        try:
            out[axis] = float(data[axis])
        except (KeyError, TypeError, ValueError):
            continue
    return out


#: The glob hint every zone field carries, in its placeholder and, whole, in its tooltip.
#: It used to be a line of its own under every zone field, boxed in red while empty (#142).
ZONE_PLACEHOLDER = "Zone path; * matches any part"
ZONE_GLOB_HELP = "Glob patterns supported: * matches any sequence, ? matches one character."


#: ``settings.json``'s switch behind Show / Hide descriptions, shared with the option
#: surfaces: folding the prose away is a property of the reader, not of the surface.
SHOW_DESCRIPTIONS_SETTING = "show_option_descriptions"


def _read_show_descriptions() -> bool:
    try:
        return bool(SettingsManager.instance().get("general", SHOW_DESCRIPTIONS_SETTING, default=False))
    except Exception:
        logger.exception("Failed to read whether descriptions are shown")
        return False


def write_show_descriptions(shown: bool) -> None:
    """Remember Show / Hide descriptions for every surface that folds its prose."""
    try:
        SettingsManager.instance().set("general", SHOW_DESCRIPTIONS_SETTING, value=shown)
    except Exception:
        logger.exception("Failed to remember whether descriptions are shown")


def _describe(value: Any) -> str:
    """A default as Reset's tooltip names it."""
    if value is None or value == "" or value == []:
        return "empty"
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _row_action(icon_name: str, tooltip: str) -> ActionButton:
    """An action at the end of a field: its glyph alone, with the verb in the tooltip.

    The compact row (#142) puts what used to be a full-width row of labeled buttons
    under a field at its end instead, as Unity's object picker and Houdini's select
    arrow do. Still an ``ActionButton`` -- the same frame and weight the labeled ones
    had -- and always drawn, never revealed on hover: on a tool surface a hidden verb
    is one nobody finds (the launch cards' ``pins_chrome`` reasoning).
    """
    button = ActionButton(icon_name, "", fill=False)
    button.setToolTip(tooltip)
    return button


class _FillButton(ActionButton):
    """Fill current, at the end of a field: the glyph, and a ``▾`` while a click asks which player.

    - 0 players connected  → glyph alone (no-op click)
    - 1 player connected   → glyph alone (direct fill, no menu)
    - 2+ players connected → glyph and ``▾`` (dropdown)

    ``download`` for the glyph, matching ``_NavZoneField``'s own fill button: both mean
    "take this from a connected player", and they sit on the same panel.
    """

    def __init__(self, get_clients: Callable[[], list], parent=None):
        super().__init__("download", "", fill=False, parent=parent)
        self._get_clients = get_clients
        self._refresh_label()

    def enterEvent(self, event) -> None:  # type: ignore[override]
        self._refresh_label()
        super().enterEvent(event)

    def _refresh_label(self) -> None:
        self.setText(picker_label("", len(self._get_clients())).strip())


def _format_axis(value: Any) -> str:
    """One axis as its box shows it: at most two decimals, no trailing zeros. Blank if not a number."""
    try:
        text = f"{round(float(value), 2) + 0.0:.2f}"  # + 0.0 turns a rounded -0.0 into 0.0
    except (TypeError, ValueError):
        return ""
    return text.rstrip("0").rstrip(".")


class _AxisEdit(QLineEdit):
    """One box of an ``_XyzField``: a number with at most two decimals, or blank.

    Ctrl+V of a whole coordinate -- anything ``parse_xyz_text`` reads all three axes
    from -- fills all three boxes, where the validator would otherwise refuse it.
    """

    coordinate_pasted = pyqtSignal(dict)
    #: A right-click, at this global point: the field puts its own menu up.
    menu_requested = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        validator = QDoubleValidator(-999_999.0, 999_999.0, 2, self)
        validator.setNotation(QDoubleValidator.Notation.StandardNotation)
        # The C locale, so the decimal point is "." whatever the Windows region says,
        # and no group separator, which float() would not read back.
        locale = QLocale.c()
        locale.setNumberOptions(QLocale.NumberOption.RejectGroupSeparator)
        validator.setLocale(locale)
        self.setValidator(validator)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.matches(QKeySequence.StandardKey.Paste):
            clipboard = QApplication.clipboard()
            values = parse_xyz_text(clipboard.text()) if clipboard else {}
            if len(values) == len(_AXES):
                self.coordinate_pasted.emit(values)
                return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.menu_requested.emit(event.globalPos())
        event.accept()

    def number(self) -> float | None:
        """The box's value, or None while it is blank or holds an unfinished number such as "-"."""
        if not self.hasAcceptableInput():
            return None
        try:
            return float(self.text())
        except ValueError:
            return None


class _XyzField(ThemedMixin, QWidget):
    """An 'xyz' param: X, Y and Z boxes, then Fill current and TP at the end of the line.

    Copy and Paste are on each box's right-click menu, as Unity's and Blender's fields
    keep them, and Ctrl+V of a whole coordinate into any box still fills all three. The
    two that reach into the game stay drawn as buttons (#142).

    Unlike the spinboxes it replaced, it can be blank, which is how a coordinate nobody
    has filled in looks and is stored: ``value()`` is None and ``get_params`` writes
    nothing for it. Partly filled, or holding an unfinished number, it is invalid: the
    offending boxes are marked at once and ``value()`` is None too, so a half-typed
    coordinate is never saved. A required field blank or all-zero -- (0, 0, 0) is what
    an unset coordinate was saved as before this, never a real position -- is marked only
    once the form says to show it (``set_required_shown``): left empty after being
    touched, or after a refused save.

    A field nobody has edited hands back exactly the dict it loaded, so opening and
    saving a bot rewrites no coordinate.
    """

    changed = pyqtSignal()

    def __init__(
        self, value: Any, *, required: bool, get_clients: Callable[[], list], help_text: str = "", parent=None
    ):
        super().__init__(parent)
        self._required = required
        self._required_shown = False
        self._get_clients = get_clients
        self._loaded = value if isinstance(value, dict) else None
        self._quiet = False
        if help_text:
            self.setToolTip(help_text)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self._edits: dict[str, _AxisEdit] = {}
        for axis in _AXES:
            lbl = QLabel(axis.upper())
            edit = _AxisEdit()
            edit.setText(_format_axis(self._loaded.get(axis)) if self._loaded else "")
            edit.textChanged.connect(self._on_text_changed)
            edit.coordinate_pasted.connect(self.set_coordinate)
            edit.menu_requested.connect(lambda pos, e=edit: self._show_box_menu(e, pos))
            row.addWidget(lbl)
            row.addWidget(edit, 1)
            self._edits[axis] = edit
        self._loaded_texts = self.texts()
        fill_btn = _FillButton(self._get_clients)
        fill_btn.setToolTip("Fill current: a connected player's live position. Right-click a box to copy or paste.")
        fill_btn.clicked.connect(lambda _checked, b=fill_btn: self._show_fill_menu(b))
        row.addWidget(fill_btn)
        self._tp_btn = _row_action("teleport", "Teleport all connected players to these coordinates")
        self._tp_btn.clicked.connect(lambda _checked: asyncio.ensure_future(self._teleport()))
        row.addWidget(self._tp_btn)
        self._fill_btn = fill_btn
        self._update_buttons()
        self.refresh_theme_styles()

    def axis_edit(self, axis: str) -> QLineEdit:
        """The box for one axis ("x", "y" or "z")."""
        return self._edits[axis]

    @property
    def fill_button(self) -> QPushButton:
        return self._fill_btn

    @property
    def teleport_button(self) -> QPushButton:
        return self._tp_btn

    def can_copy(self) -> bool:
        """Whether the boxes hold a whole coordinate, which is what Copy and TP need."""
        return self.value() is not None

    def box_menu(self, edit: QLineEdit) -> QMenu:
        """A box's right-click menu, built but not shown: Copy XYZ and Paste XYZ, then the box's own."""
        menu = popup_menu(edit)
        copy = menu.addAction(svg_icon("copy", 16), "Copy XYZ")
        copy.setToolTip("Copy this coordinate to the clipboard as JSON")
        copy.setEnabled(self.can_copy())
        copy.triggered.connect(self._copy)
        paste = menu.addAction(svg_icon("paste", 16), "Paste XYZ")
        paste.setToolTip("Paste a coordinate: JSON, x, y, z or XYZ(x, y, z)")
        paste.triggered.connect(self._paste)
        menu.addSeparator()
        standard = edit.createStandardContextMenu()
        for action in standard.actions():
            action.setParent(menu)
            menu.addAction(action)
        standard.deleteLater()
        return menu

    def _show_box_menu(self, edit: QLineEdit, pos) -> None:
        # A synchronous slot, so exec() is safe here -- see the qasync rule in gui/CLAUDE.md.
        self.box_menu(edit).exec(pos)

    def set_required_shown(self, shown: bool) -> None:
        """Mark a blank required coordinate, or stop: the form decides when it is shown."""
        if shown == self._required_shown:
            return
        self._required_shown = shown
        self.apply_theme_styles()

    def value(self) -> dict | None:
        """The coordinate to store, or None when there is none: blank, or invalid."""
        if self._loaded is not None and self.texts() == self._loaded_texts:
            return dict(self._loaded)
        numbers = [self._edits[axis].number() for axis in _AXES]
        if any(n is None for n in numbers):
            return None
        return dict(zip(_AXES, numbers))

    def marked_axes(self) -> set[str]:
        """The axes whose box carries the error border."""
        required = self._required and self._required_shown
        filled = {axis for axis, edit in self._edits.items() if edit.text().strip()}
        if not filled:
            return set(_AXES) if required else set()
        marked = {axis for axis in filled if self._edits[axis].number() is None}
        marked |= set(_AXES) - filled
        if not marked and required and not any(self._edits[axis].number() for axis in _AXES):
            return set(_AXES)
        return marked

    def is_blank(self) -> bool:
        """Whether a required field would call this empty: blank, or all zero."""
        return not any(self._edits[axis].number() for axis in _AXES)

    def set_coordinate(self, values: dict) -> None:
        """Set the axes *values* names, rounded to two decimals; the others keep their boxes."""
        self._quiet = True
        try:
            for axis in _AXES:
                if axis in values:
                    self._edits[axis].setText(_format_axis(values[axis]))
        finally:
            self._quiet = False
        self._on_text_changed()

    def apply_theme_styles(self) -> None:
        marked = self.marked_axes()
        for axis, edit in self._edits.items():
            set_style_sheet(edit, f"border: 1px solid {THEME.error};" if axis in marked else "")

    def texts(self) -> tuple[str, ...]:
        """The three boxes' text as typed, X, Y then Z."""
        return tuple(self._edits[axis].text() for axis in _AXES)

    def set_texts(self, texts: tuple[str, ...]) -> None:
        """Put back what ``texts()`` read. Unlike ``set_coordinate`` this keeps a
        half-typed box as it was, and a loaded value at its full precision: the boxes
        match what was loaded again, so ``value()`` returns the loaded value."""
        self._quiet = True
        try:
            for axis, text in zip(_AXES, texts):
                self._edits[axis].setText(text)
        finally:
            self._quiet = False
        self._on_text_changed()

    def _on_text_changed(self, *_) -> None:
        if self._quiet:
            return
        self.apply_theme_styles()
        self._update_buttons()
        self.changed.emit()

    def _update_buttons(self) -> None:
        self._tp_btn.setEnabled(self.can_copy())

    def _copy(self) -> None:
        value = self.value()
        if value is not None:
            QApplication.clipboard().setText(json.dumps(value))

    def _paste(self) -> None:
        clipboard = QApplication.clipboard()
        values = parse_xyz_text(clipboard.text()) if clipboard else {}
        if values:
            self.set_coordinate(values)

    def _show_fill_menu(self, btn: QPushButton) -> None:
        pick_player(btn, len(self._get_clients()), "Fill", lambda i: asyncio.ensure_future(self._fill_from_player(i)))

    async def _fill_from_player(self, idx: int) -> None:
        clients = self._get_clients()
        if idx >= len(clients):
            return
        try:
            pos = await clients[idx].body.position()
            self.set_coordinate({"x": pos.x, "y": pos.y, "z": pos.z})
        except Exception:
            pass

    async def _teleport(self) -> None:
        from wizwalker import XYZ

        clients = self._get_clients()
        value = self.value()
        if not clients or value is None:
            return
        target = XYZ(value["x"], value["y"], value["z"])
        try:
            await asyncio.gather(
                *[c.teleport(target, wait_on_inuse=True, purge_on_after_unuser_fixer=True) for c in clients]
            )
        except Exception:
            pass


class _ZoneLineEdit(QLineEdit):
    """QLineEdit with zone-path autocomplete for 'str' fields whose key contains 'zone'.

    Refreshes suggestions from a caller-supplied getter on focus so the dropdown
    always reflects currently connected players and recently visited zones.
    """

    def __init__(self, get_suggestions: Callable[[], list[str]], parent=None):
        super().__init__(parent)
        self._get_suggestions = get_suggestions
        self._model = QStringListModel(self)
        completer = QCompleter(self._model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.setCompleter(completer)

    def focusInEvent(self, event) -> None:
        self._model.setStringList(self._get_suggestions())
        super().focusInEvent(event)


class _ZoneWidget(QWidget):
    """Zone string field with autocomplete, and Fill current at the end of the line.

    The glob hint is the placeholder and, whole, the tooltip: it used to be a line of
    its own under every zone field, and boxed in red while the field was empty (#142).
    """

    textChanged = pyqtSignal(str)

    def __init__(
        self,
        get_suggestions: Callable[[], list[str]],
        get_clients: Callable[[], list],
        parent=None,
    ):
        super().__init__(parent)
        self._get_clients = get_clients

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self._edit = _ZoneLineEdit(get_suggestions)
        self._edit.setPlaceholderText(ZONE_PLACEHOLDER)
        self._edit.setToolTip(ZONE_GLOB_HELP)
        row.addWidget(self._edit, 1)

        self.fill_btn = _FillButton(get_clients)
        self.fill_btn.setToolTip("Fill current: a connected player's current zone")
        self.fill_btn.clicked.connect(lambda _: self._show_fill_menu(self.fill_btn))
        row.addWidget(self.fill_btn)

        self._edit.textChanged.connect(self.textChanged)

    # ------------------------------------------------------------------
    # Public interface (matches QLineEdit enough for form integration)
    # ------------------------------------------------------------------

    @property
    def line_edit(self) -> QLineEdit:
        """The text box itself, which carries the error border."""
        return self._edit

    def text(self) -> str:
        return self._edit.text()

    def setText(self, text: str) -> None:
        self._edit.setText(text)

    def setToolTip(self, text: str) -> None:  # noqa: N802 - Qt override
        """The param's help, then the glob hint: both are about what to type."""
        self._edit.setToolTip(f"{text}\n\n{ZONE_GLOB_HELP}" if text else ZONE_GLOB_HELP)

    # ------------------------------------------------------------------
    # Fill from player
    # ------------------------------------------------------------------

    def _show_fill_menu(self, btn: QPushButton) -> None:
        pick_player(btn, len(self._get_clients()), "Fill", lambda i: asyncio.ensure_future(self._fill_from_player(i)))

    async def _fill_from_player(self, idx: int) -> None:
        clients = self._get_clients()
        if idx >= len(clients):
            return
        try:
            zone = await clients[idx].zone_name()
            if zone:
                # A bot stores the canonical zone, never a Challenge Mode copy or a gauntlet's band.
                self.setText(zone_names.canonical_zone(zone))
        except Exception:
            pass


class _AuthoringMachine:
    """The slice of BotMachine zone navigation reads, for a trip started from the properties panel.

    Records go under the Sync In Zone node's logger, as they would in a run. Safety Delay is the
    user's app setting: there is no bot run here to hold an override or a declaration.
    """

    def __init__(self) -> None:
        # None until navigate() surveys who can port to whom; see BotMachine.port_map.
        self.port_map = None

    def log(self, msg: str, level: int = logging.INFO) -> None:
        logging.getLogger("bartlebot.node.sync_in_zone").log(level, msg)

    def get_option(self, name: str, default=None):
        if name == "Safety Delay":
            return SettingsManager.instance().default_safety_delay
        return default


# Held so a trip started from a panel that is rebuilt mid-trip is not garbage-collected.
_NAV_TASKS: set[asyncio.Task] = set()


class _NavZoneField(QWidget):
    """A 'nav_zone' param: the zone picker, then Fill current and Go to zone at the end of its line.

    Fill sets the zone from a connected player's current zone, as the zone and xyz fields do. Go
    runs the same navigation the Sync In Zone node runs, so a bot can be authored from wherever the
    players happen to be without starting a run.
    """

    changed = pyqtSignal(str)

    def __init__(self, get_suggested: Callable[[], list[str]], get_clients: Callable[[], list], parent=None):
        super().__init__(parent)
        self._get_clients = get_clients
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(4)
        outer.addLayout(line)
        self.picker = NavZoneButton(get_suggested)
        self.picker.changed.connect(self.changed)
        line.addWidget(self.picker, 1)
        self.fill_btn = _FillButton(get_clients)
        self.fill_btn.setToolTip("Fill current: a connected player's current zone")
        self.fill_btn.clicked.connect(self._show_fill_menu)
        line.addWidget(self.fill_btn)
        self.go_btn = _row_action("play", "Go to zone: take all connected players there now")
        self.go_btn.clicked.connect(self._on_go)
        line.addWidget(self.go_btn)

        # Why the last fill was refused; cleared by the next pick or fill.
        self.notice = ThemedTextLabel(color="error", css=f"font-size: {THEME.font_small_px}px;")
        self.notice.setWordWrap(True)
        outer.addWidget(self.notice)
        self.notice.setVisible(False)
        self.picker.changed.connect(lambda _zone: self.notice.setVisible(False))

    def value(self) -> str:
        return self.picker.value()

    def set_value(self, zone: str) -> None:
        self.picker.set_value(zone)

    def _show_fill_menu(self) -> None:
        pick_player(
            self.fill_btn, len(self._get_clients()), "Fill", lambda i: asyncio.ensure_future(self._fill_from_player(i))
        )

    async def _fill_from_player(self, idx: int) -> None:
        clients = self._get_clients()
        if idx >= len(clients):
            return
        try:
            zone = await clients[idx].zone_name()
        except Exception:
            return
        if not zone:
            return
        try:
            if not is_destination(self.picker.nav, zone):
                self.notice.setText(
                    f"Zone not filled: Player {idx + 1} is in {zone}, which is not a valid destination on the "
                    "current zone map. Try selecting the nearest major zone."
                )
                self.notice.setVisible(True)
                return
            self.notice.setVisible(False)
            if zone != self.value():
                self.set_value(zone)
                self.changed.emit(zone)
        except RuntimeError:
            pass  # the panel was rebuilt while the zone was read

    def _on_go(self) -> None:
        zone = self.value()
        clients = self._get_clients()
        if not zone or not clients:
            return
        self.go_btn.setEnabled(False)
        task = asyncio.ensure_future(self._navigate(zone, list(clients)))
        _NAV_TASKS.add(task)
        task.add_done_callback(_NAV_TASKS.discard)

    async def _navigate(self, zone: str, clients: list) -> None:
        try:
            await zone_navigation.navigate(_AuthoringMachine(), clients, zone)
        except Exception:
            logger.exception("Sync In Zone from the properties panel failed")
        finally:
            try:
                self.go_btn.setEnabled(True)
            except RuntimeError:
                pass  # the panel was rebuilt while the players traveled


# ---------------------------------------------------------------------------
# Link controls on a definition's param rows
# ---------------------------------------------------------------------------


#: The widest a link chip grows; a longer name is elided, and the tooltip has it whole.
_LINK_CHIP_MAX_W = 180


class _LinkChip(ThemedMixin, QToolButton):
    """A linked param's status, which turns into its Unlink action under the pointer.

    At rest it reads as a status -- a chain and what the param is linked to, in the
    accent color. Hovered or focused it becomes the action: a broken chain in the
    warning color, "Unlink". A *locked* chip (a param of a promoted node) never
    turns: its node's input group owns it, and the tooltip says how to let go.

    Painted here rather than styled, at one size worked out for both states. A
    stylesheet swapped on hover re-polishes the button and re-queues its layout, and
    somewhere in that the old outline's bottom edge was left on screen. Now a hover
    changes two colors and repaints the chip, and nothing else.
    """

    _PAD_X = 5
    _GAP = 4
    _ICON = 12

    def __init__(self, text: str, *, locked: bool, tooltip: str, parent=None) -> None:
        super().__init__(parent)
        self._text = text
        self._shown_name = text
        self._locked = locked
        self._armed = False
        self.setToolTip(tooltip)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus if locked else Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor if locked else Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.refresh_theme_styles()

    @property
    def locked(self) -> bool:
        return self._locked

    @property
    def armed(self) -> bool:
        """Whether it is showing its Unlink action rather than its status."""
        return self._armed

    def text(self) -> str:  # noqa: D401 - Qt override
        """What the chip shows now: the linked name (elided if long) or "Unlink"."""
        return "Unlink" if self._armed else self._shown_name

    def _arm(self, armed: bool) -> None:
        armed = armed and not self._locked
        if armed != self._armed:
            self._armed = armed
            self.update()

    def enterEvent(self, event) -> None:
        self._arm(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._arm(self.hasFocus())
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:
        self._arm(True)
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:
        self._arm(self.underMouse())
        super().focusOutEvent(event)

    def _font(self) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(THEME.font_small_px)
        font.setBold(True)
        return font

    def apply_theme_styles(self) -> None:
        """Work out the one size both states share, and repaint in the theme's colors."""
        fm = QFontMetrics(self._font())
        chrome = 2 * self._PAD_X + self._ICON + self._GAP + 2
        self._shown_name = self._text
        if chrome + fm.horizontalAdvance(self._text) > _LINK_CHIP_MAX_W:
            self._shown_name = fm.elidedText(self._text, Qt.TextElideMode.ElideRight, _LINK_CHIP_MAX_W - chrome)
        width = chrome + max(fm.horizontalAdvance(self._shown_name), fm.horizontalAdvance("Unlink"))
        self.setFixedSize(min(width, _LINK_CHIP_MAX_W), max(fm.height(), self._ICON) + 8)
        self.update()

    def paintEvent(self, event) -> None:
        color = QColor(THEME.warning if self._armed else THEME.accent)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(color, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 3, 3)
        icon = svg_pixmap("unhook" if self._armed else "hook", self._ICON, tint=color.name())
        x = self._PAD_X + 1
        painter.drawPixmap(x, (self.height() - self._ICON) // 2, icon)
        painter.setFont(self._font())
        painter.setPen(color)
        text_rect = self.rect().adjusted(x + self._ICON + self._GAP, 0, -self._PAD_X, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.text())
        painter.end()


class _HoverRow(QWidget):
    """A param row whose Link button shows only while the row is hovered or the button focused.

    Faded rather than hidden, so the row never reflows and Tab still reaches it.
    """

    def __init__(self, button: QToolButton | None = None, parent=None) -> None:
        super().__init__(parent)
        self._button = button
        self._effect = None
        if button is not None:
            self._effect = QGraphicsOpacityEffect(button)
            self._effect.setOpacity(0.0)
            button.setGraphicsEffect(self._effect)
            button.installEventFilter(self)

    def _show(self, shown: bool) -> None:
        if self._effect is not None:
            self._effect.setOpacity(1.0 if shown else 0.0)

    @property
    def link_shown(self) -> bool:
        return self._effect is not None and self._effect.opacity() > 0

    def enterEvent(self, event) -> None:
        self._show(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._show(self._button is not None and self._button.hasFocus())
        super().leaveEvent(event)

    def eventFilter(self, obj, event) -> bool:
        if obj is self._button and event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self._show(event.type() == QEvent.Type.FocusIn or self.underMouse())
        return super().eventFilter(obj, event)


# ---------------------------------------------------------------------------
# _DynamicParamForm
# ---------------------------------------------------------------------------


class _NodeHelpButton(IconButton):
    """The node type's ``?``: the node browser's page for it.

    Framed, in the accent: the same affordance as a row's ``?`` one panel over
    (``HelpToggle``), only larger. It styles itself rather than being styled by the
    panel, because an ``IconButton`` answers every ``StyleChange`` with its own flat
    sheet, and whichever is applied last wins.
    """

    def __init__(self, parent=None) -> None:
        super().__init__("help", tooltip="View node documentation", size=22, icon_size=14, parent=parent)

    def apply_theme_styles(self) -> None:
        super().apply_theme_styles()
        set_style_sheet(
            self,
            f"QPushButton {{ background: {THEME.surface}; border: 1px solid {THEME.border}; "
            f"border-radius: 4px; color: {THEME.accent}; font-weight: bold; font-size: {THEME.font_small_px}px; }}"
            f"QPushButton:hover {{ background: {THEME.accent}; color: #ffffff; }}",
        )


class _DynamicParamForm(ThemedMixin, QWidget):
    """The rows of a node's params, or of a subgraph node's inputs: one row builder for both.

    Every row is a head line and a control, behind the ``[bar][reset][?]`` gutter the
    option surfaces use (#142, after #138). The head line is the name, the required
    marker, and whatever chip the row wears -- a link, a setup step. Actions that act on
    a field sit at the end of its control line, and prose folds behind the ``?``.

    The gutter's middle slot is Reset, and what it resets to depends on where the form
    is. On a subgraph node's page (``resettable`` given) it drops this node's value so
    the definition's default applies again, and the bar is lit while there is one to
    drop. On a node's page it puts the node type's default back; a param with no default
    has nothing to go back to, and the bar is never lit, as on Options.

    An empty required field is marked -- its asterisk and border in the error color --
    only once it has been touched and left empty, or after a refused save
    (``show_required_errors``): a freshly placed node is not a wall of red before the
    user has done anything. "N to fill" on the card says what is left in the meantime.
    """

    changed = pyqtSignal()
    # (param key) from a row's Link button, on a node shown in a definition document.
    # The form only reports; the canvas links the param and reloads.
    link_requested = pyqtSignal(str)
    # (param key) from a linked row's chip, clicked while it reads "Unlink".
    unlink_requested = pyqtSignal(str)
    # (param key) from a row's Reset, on a subgraph node's form: follow the definition again.
    reset_requested = pyqtSignal(str)
    # (param key) from a row's Add to setup step button, or from its step chip, on a node
    # of a template draft.
    step_add_requested = pyqtSignal(str)
    step_remove_requested = pyqtSignal(str)
    # The set of empty required fields shown in red changed, without any value changing:
    # a field was left empty, or a refused save asked for all of them.
    required_shown_changed = pyqtSignal()

    #: Between rows: the rows carry no margin of their own, so the gutter's buttons
    #: top-align with the head line.
    ROW_SPACING_PX = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self._form = QVBoxLayout(self)
        self._form.setContentsMargins(0, 0, 0, 0)
        self._form.setSpacing(self.ROW_SPACING_PX)
        self._form.addStretch()
        self._widgets: dict[str, Any] = {}
        self._required_widgets: dict[str, Any] = {}
        # Per-row controls, all THEME-colored: in a definition, a Link button that
        # shows on hover, or for a linked param its chip.
        self._link_btns: dict[str, QToolButton] = {}
        self._link_chips: dict[str, _LinkChip] = {}
        self._step_chips: dict[str, NeutralChip] = {}
        self._step_btns: dict[str, QToolButton] = {}
        self._fill_labels: dict[str, QLabel] = {}
        self._hover_rows: dict[str, _HoverRow] = {}
        # The gutter, per row.
        self._rows: dict[str, QWidget] = {}
        self._bars: dict[str, OverrideBar] = {}
        self._reverts: dict[str, RevertButton] = {}
        self._helps: dict[str, HelpToggle] = {}
        self._descriptions: dict[str, QLabel] = {}
        self._row_actions: dict[str, QPushButton] = {}
        self._fields: dict[str, ParamField] = {}
        self._schema: list = []
        # None on a node's page; the keys this node set, on a subgraph node's page.
        self._resettable: set[str] | None = None
        self._linked: set[str] = set()
        # Empty required fields whose emptiness is drawn in the error color.
        self._shown_required: set[str] = set()
        self._show_descriptions = _read_show_descriptions()
        # Every label load_schema builds that carries a THEME color, cleared and
        # refilled alongside _widgets. Field labels keep their ParamField because the
        # required marker is a color baked into *rich text* rather than a stylesheet,
        # so restyling one means rebuilding its text.
        self._field_labels: list[tuple[QLabel, Any]] = []
        self._muted_labels: list[QLabel] = []
        self._suppress = False
        self._get_clients: Callable[[], list] = lambda: []
        self._get_zone_suggestions: Callable[[], list[str]] = lambda: []
        self._get_default_scales: Callable[[], Any] = lambda: None
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_focus_changed)

    def set_default_scales_getter(self, fn: Callable[[], Any]) -> None:
        """Pass a callable returning the Scales an unset 'scales' param means in the open bot."""
        self._get_default_scales = fn

    def set_clients_getter(self, fn: Callable[[], list]) -> None:
        """Pass a callable returning the currently hooked clients."""
        self._get_clients = fn

    def set_zone_suggestions_getter(self, fn: Callable[[], list[str]]) -> None:
        """Pass a callable returning zone suggestions (current player zones + history)."""
        self._get_zone_suggestions = fn

    def load_schema(
        self,
        schema: list,
        params: dict,
        *,
        linkable: bool = False,
        links: dict[str, str] | None = None,
        promoted_group: str | None = None,
        resettable: set[str] | frozenset[str] | None = None,
        field_types: dict[str, str] | None = None,
        fill_hints: dict[str, str] | None = None,
        step_chips: dict[str, str] | None = None,
        step_addable: bool = False,
        row_actions: dict[str, tuple[str, str, Callable[[], None]]] | None = None,
    ) -> None:
        """Rebuild the form for *schema* showing *params*.

        A key in *fill_hints* (key -> its setup step's text) is a fill-in of the bot's
        template: the row is marked required, since the bot cannot run with it blank,
        and its placeholder reads "Fill in: <step text>". In a template draft a key in
        *step_chips* (key -> "Setup step N") wears that as a chip, which takes the row
        out of the step when clicked; with *step_addable*, every other row has an
        Add to setup step button that shows while the row is hovered, as Link does in
        a definition.

        On a node in a definition document, *linkable* gives each row a Link button
        that shows while the row is hovered; a row in *links* (key -> what it is linked
        to) shows a chip instead, which turns into Unlink on hover. On a node promoted to
        an input group, every row is linked to *promoted_group* and its chip is locked.
        *resettable* makes this a subgraph node's form: a key in it has a value this node
        set, which Reset drops. Left None, Reset puts the node type's default back.
        A key in *field_types* is drawn as that type rather than its own: a linked param
        takes the strictest type of what it is linked with, so a ``zone`` linked to a
        ``nav_zone`` gets the destination picker. *row_actions* (key -> ``(icon, tooltip,
        callback)``) adds one more action at the end of that row's control line -- the
        wiki import on a fight's page. The controls wrap the row rather than the widget
        ``_make_widget`` returns, so every widget type gets them without knowing.
        """
        self._suppress = True
        # Remove everything except the trailing stretch.
        while self._form.count() > 1:
            item = self._form.takeAt(0)
            if item.widget():
                discard_widget(item.widget())
        for store in (
            self._widgets,
            self._required_widgets,
            self._link_btns,
            self._link_chips,
            self._step_chips,
            self._step_btns,
            self._fill_labels,
            self._hover_rows,
            self._rows,
            self._bars,
            self._reverts,
            self._helps,
            self._descriptions,
            self._row_actions,
            self._fields,
        ):
            store.clear()
        self._field_labels.clear()
        self._muted_labels.clear()
        self._shown_required.clear()
        self._resettable = set(resettable) if resettable is not None else None
        # As drawn: a field's type can be narrowed by a link, and a fill-in is required.
        self._schema = []
        links = links or {}
        self._linked = set(links)
        field_types = field_types or {}
        fill_hints = fill_hints or {}
        step_chips = step_chips or {}
        row_actions = row_actions or {}
        descriptions: list[tuple[QLabel, HelpToggle]] = []
        for field in schema:
            if field_types.get(field.key, field.type) != field.type:
                field = replace(field, type=field_types[field.key])
            if field.key in fill_hints:
                field = replace(field, required=True, placeholder=f"Fill in: {fill_hints[field.key]}")
            self._schema.append(field)
            self._fields[field.key] = field
            val = params.get(field.key, field.default)
            widget = self._make_widget(
                field.type,
                val,
                field.help,
                getattr(field, "placeholder", ""),
                minimum=getattr(field, "minimum", None),
                maximum=getattr(field, "maximum", None),
                required=field.required,
            )
            self._widgets[field.key] = widget
            if field.required:
                self._required_widgets[field.key] = widget
            row_btns: list[QWidget] = []
            link_btn: QToolButton | None = None
            if promoted_group is not None:
                chip = _LinkChip(
                    promoted_group,
                    locked=True,
                    tooltip=f"Part of the {promoted_group} input group. Demote the node to unlink.",
                )
                self._link_chips[field.key] = chip
                row_btns.append(chip)
            elif field.key in links:
                chip = _LinkChip(
                    links[field.key],
                    locked=False,
                    tooltip=f"Linked to {links[field.key]}: this value is its default. Click to unlink.",
                )
                chip.clicked.connect(lambda _=False, k=field.key: self.unlink_requested.emit(k))
                self._link_chips[field.key] = chip
                row_btns.append(chip)
            elif linkable:
                link_btn = self._make_link_button(field.key)
                row_btns.append(link_btn)
            if field.key in step_chips:
                step_chip = NeutralChip(
                    step_chips[field.key],
                    tooltip="A fill-in: left blank in a release, for the bot made from it to fill. Click to remove it.",
                )
                step_chip.removed.connect(lambda _text, k=field.key: self.step_remove_requested.emit(k))
                self._step_chips[field.key] = step_chip
                row_btns.append(step_chip)
            elif step_addable and link_btn is None:
                # Takes the Link button's slot and its hover: a draft is never a definition.
                link_btn = self._make_step_button(field.key)
                row_btns.append(link_btn)

            # The body: the head line, then the control, then whatever folds under it.
            # Label and widget are one hover target, so pointing at the field itself
            # shows its Link button, not only pointing at its label.
            body: QWidget = _HoverRow(link_btn) if link_btn is not None else QWidget()
            if link_btn is not None:
                self._hover_rows[field.key] = body  # type: ignore[assignment]
            col = QVBoxLayout(body)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(3)
            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            head.setSpacing(8)
            col.addLayout(head)
            if field.type == "bool":
                # A bool has no separate label: its own caption is the name, so the
                # checkbox *is* the head line and the whole row fits on it.
                widget.setText(field.label)
                head.addWidget(widget)
            else:
                label_w = QLabel()
                label_w.setTextFormat(Qt.TextFormat.RichText)
                if field.help:
                    label_w.setToolTip(field.help)
                self._field_labels.append((label_w, field))
                head.addWidget(label_w)
            head.addStretch(1)
            for row_btn in row_btns:
                head.addWidget(row_btn)
            if field.type != "bool":
                if field.key in row_actions:
                    icon, tip, callback = row_actions[field.key]
                    line = QHBoxLayout()
                    line.setContentsMargins(0, 0, 0, 0)
                    line.setSpacing(4)
                    col.addLayout(line)
                    line.addWidget(widget, 1)
                    action = _row_action(icon, tip)
                    action.clicked.connect(lambda _=False, cb=callback: cb())
                    self._row_actions[field.key] = action
                    line.addWidget(action)
                else:
                    col.addWidget(widget)
            if field.key in fill_hints and field.type not in ("str", "text"):
                # A picker, a coordinate or a list has no placeholder to carry it.
                fill_lbl = QLabel(field.placeholder)
                self._muted_labels.append(fill_lbl)
                self._fill_labels[field.key] = fill_lbl
                fill_lbl.setWordWrap(True)
                col.addWidget(fill_lbl)
            prose = self._description_text(field)
            description = None
            if prose:
                # At the base font: the one piece of prose on the row, and on screen
                # only when asked for -- the option surfaces' rule.
                description = ThemedTextLabel(prose, color="text_muted")
                description.setWordWrap(True)
                col.addWidget(description)
                self._descriptions[field.key] = description

            bar = OverrideBar()
            revert = RevertButton()
            revert.clicked.connect(lambda _=False, k=field.key: self._on_reset(k))
            help_btn = HelpToggle()
            help_btn.setToolTip("Show what this field does")
            outer = build_gutter_row(bar, revert, help_btn, body)
            self._form.insertWidget(len(self._rows), outer)
            self._rows[field.key] = outer
            self._bars[field.key] = bar
            self._reverts[field.key] = revert
            self._helps[field.key] = help_btn
            # Visibility only now that the row has a parent: see "Never show a widget
            # before it has a parent" in gui/CLAUDE.md.
            if description is None:
                keep_slot_when_hidden(help_btn)
            else:
                help_btn.setChecked(self._show_descriptions)
                help_btn.toggled.connect(description.setVisible)
                descriptions.append((description, help_btn))
        for description, help_btn in descriptions:
            description.setVisible(help_btn.isChecked())
        if not schema:
            placeholder = QLabel("This node type has no parameters.")
            self._muted_labels.append(placeholder)
            self._form.insertWidget(0, placeholder)
        # Wire disabled_by: disable the target widget when the controller is "active".
        # For QCheckBox controllers: active = checked.
        # For QLineEdit controllers: active = non-empty text.
        for field in schema:
            if field.disabled_by and field.disabled_by in self._widgets:
                controller = self._widgets[field.disabled_by]
                display_w = self._widgets[field.key]
                ok = True
                if isinstance(controller, QCheckBox):
                    display_w.setEnabled(ok and not controller.isChecked())
                    controller.checkStateChanged.connect(
                        lambda state, w=display_w, ok=ok: w.setEnabled(ok and state != Qt.CheckState.Checked)
                    )
                elif isinstance(controller, QLineEdit):
                    display_w.setEnabled(ok and not controller.text().strip())
                    controller.textChanged.connect(
                        lambda text, w=display_w, ok=ok: w.setEnabled(ok and not text.strip())
                    )
        # Wire enabled_by: the mirror image, so a pair of fields can be mutually exclusive.
        for field in schema:
            if field.enabled_by and field.enabled_by in self._widgets:
                controller = self._widgets[field.enabled_by]
                display_w = self._widgets[field.key]
                ok = True
                if isinstance(controller, QCheckBox):
                    display_w.setEnabled(ok and controller.isChecked())
                    controller.checkStateChanged.connect(
                        lambda state, w=display_w, ok=ok: w.setEnabled(ok and state == Qt.CheckState.Checked)
                    )
        self._suppress = False
        # load_schema *is* this widget's construction — everything above was just
        # built, so this is where its styling is first applied. It also covers
        # _validate_required_fields, which apply_theme_styles re-runs.
        self.refresh_theme_styles()
        self._refresh_rows()

    @staticmethod
    def _description_text(field: Any) -> str:
        """What a row's ``?`` folds out: the param's help, then what an unset one does."""
        parts = [field.help] if field.help else []
        if field.default_hint and not field.required:
            parts.append(f"Default: {field.default_hint}")
        return "\n\n".join(parts)

    # -- the gutter -------------------------------------------------------

    def _default_of(self, field: Any) -> tuple[bool, Any]:
        """Whether *field* has a default Reset can put back on a node's page, and what it is.

        A required param whose default is its blank has none: it is something the
        author has to fill in, and Reset would only empty it.
        """
        if field.default is None or (field.required and value_empty(field.type, field.default)):
            return False, None
        return True, field.default

    @staticmethod
    def _agrees(field: Any, value: Any, default: Any) -> bool:
        """Whether *value* is *default*, the way a reader would say it: blank is blank, 3 is 3.0."""
        if value_empty(field.type, value) and value_empty(field.type, default):
            return True
        if isinstance(value, bool) or isinstance(default, bool):
            return value is default or value == default and type(value) is type(default)
        if isinstance(value, int | float) and isinstance(default, int | float):
            return float(value) == float(default)
        return value == default

    def _refresh_rows(self) -> None:
        """Set every row's bar, Reset and red state from the values the form holds now.

        Called after every value edit: it touches the chrome, never rebuilds a row, so
        nothing moves under the pointer -- a spin box rebuilt on its first arrow click
        loses the second.
        """
        params = self.get_params()
        for key, field in self._fields.items():
            revert, bar = self._reverts.get(key), self._bars.get(key)
            if revert is None or bar is None:
                continue
            if self._resettable is not None:
                mine = key in self._resettable
                bar.set_active(mine)
                revert.setEnabled(mine)
                revert.setToolTip(
                    "Changed here. Reset to follow the subgraph's default" if mine else "Already the subgraph's default"
                )
                continue
            bar.set_active(False)
            if key in self._linked:
                revert.setEnabled(False)
                revert.setToolTip("Linked: the input it is linked to sets this value")
                continue
            has_default, default = self._default_of(field)
            if not has_default:
                revert.setEnabled(False)
                revert.setToolTip("No default: this param has to be set")
                continue
            at_default = self._agrees(field, params.get(key), default)
            revert.setEnabled(not at_default)
            revert.setToolTip("Already the default" if at_default else f"Reset to the default: {_describe(default)}")

    def _on_reset(self, key: str) -> None:
        if self._resettable is not None:
            self.reset_requested.emit(key)
            return
        field = self._fields.get(key)
        if field is None:
            return
        has_default, default = self._default_of(field)
        if not has_default:
            return
        self._suppress = True
        try:
            self._set_widget_value(key, default)
        finally:
            self._suppress = False
        self._emit_changed()

    def _set_widget_value(self, key: str, value: Any) -> None:
        """Put *value* into *key*'s widget, as if the user had set it."""
        w = self._widgets.get(key)
        if isinstance(w, _XyzField):
            if isinstance(value, dict):
                w.set_coordinate(value)
            else:
                w.set_texts(("", "", ""))
        elif isinstance(w, QPlainTextEdit):
            w.setPlainText(str(value or ""))
        elif isinstance(w, PlayerListWidget | PlayerSlotWidget):
            w.set_value(value)
        elif isinstance(w, _StrListLineEdit):
            w.setText(format_list_text([str(v) for v in value]) if isinstance(value, list) else str(value or ""))
        elif isinstance(w, _NavZoneField | ScalesButton):
            w.set_value(str(value or ""))
        elif isinstance(w, _ZoneWidget | QLineEdit):
            w.setText(str(value or ""))
        elif isinstance(w, RatioSpinBox):
            w.set_ratio(float(value))
        elif isinstance(w, QSpinBox):
            w.setValue(int(value))
        elif isinstance(w, QDoubleSpinBox):
            w.setValue(float(value))
        elif isinstance(w, QCheckBox):
            w.setChecked(bool(value))

    # -- descriptions -----------------------------------------------------

    def set_descriptions_visible(self, shown: bool) -> None:
        """Show or fold every row's description at once, as Show / Hide descriptions do."""
        self._show_descriptions = shown
        for help_btn in self._helps.values():
            if not help_btn.isHidden():
                help_btn.setChecked(shown)

    @property
    def descriptions_visible(self) -> bool:
        return self._show_descriptions

    def description(self, key: str) -> QLabel | None:
        """The prose *key*'s ``?`` folds out, or None for a row with none."""
        return self._descriptions.get(key)

    # -- required fields ---------------------------------------------------

    def _empty(self, key: str) -> bool:
        widget = self._required_widgets.get(key)
        if widget is None:
            return False
        if isinstance(widget, _XyzField):
            return widget.is_blank()
        field = self._fields.get(key)
        return field is not None and value_empty(field.type, self.get_params().get(key))

    def show_required_errors(self) -> None:
        """Draw every empty required field in the error color: a save was refused for them."""
        before = set(self._shown_required)
        self._shown_required = set(self._required_widgets)
        if self._shown_required != before:
            self.refresh_theme_styles()
            self.required_shown_changed.emit()

    def required_errors_shown(self) -> bool:
        """Whether some empty required field here is drawn in the error color."""
        return any(self._empty(key) for key in self._shown_required)

    def required_shown(self, key: str) -> bool:
        """Whether *key*'s emptiness is drawn in the error color, were it empty."""
        return key in self._shown_required

    def to_fill(self) -> int:
        """How many required fields here are still empty."""
        return sum(1 for key in self._required_widgets if self._empty(key))

    def _on_focus_changed(self, old, new) -> None:
        """A required field left empty is shown red once focus leaves its row -- the NN/g "on blur" rule."""
        if old is None or self._suppress:
            return
        for key, row in list(self._rows.items()):
            if key not in self._required_widgets or key in self._shown_required:
                continue
            try:
                left = row.isAncestorOf(old) and not (new is not None and row.isAncestorOf(new))
            except RuntimeError:
                return  # the form was rebuilt or destroyed under the event
            if left and self._empty(key):
                self._shown_required.add(key)
                self.refresh_theme_styles()
                self.required_shown_changed.emit()

    def _make_link_button(self, key: str) -> QToolButton:
        btn = QToolButton()
        btn.setAutoRaise(True)
        btn.setIconSize(QSize(14, 14))
        btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        btn.setToolTip("Link to subgraph input…")
        btn.clicked.connect(lambda _=None, k=key: self.link_requested.emit(k))
        self._link_btns[key] = btn
        return btn

    def _make_step_button(self, key: str) -> QToolButton:
        btn = QToolButton()
        btn.setAutoRaise(True)
        btn.setIconSize(QSize(14, 14))
        btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        btn.setToolTip("Add to setup step…")
        btn.clicked.connect(lambda _=None, k=key: self.step_add_requested.emit(k))
        self._step_btns[key] = btn
        return btn

    def step_button(self, key: str) -> QToolButton | None:
        """One row's Add to setup step button, shown on hover in a template draft, or None."""
        return self._step_btns.get(key)

    def reset_button(self, key: str) -> RevertButton | None:
        """The Reset in *key*'s gutter. Every row has one; it is enabled while it can act."""
        return self._reverts.get(key)

    def override_bar(self, key: str) -> OverrideBar | None:
        return self._bars.get(key)

    def help_button(self, key: str) -> HelpToggle | None:
        return self._helps.get(key)

    def row(self, key: str) -> QWidget | None:
        """*key*'s whole row, gutter and all."""
        return self._rows.get(key)

    def row_action(self, key: str) -> QPushButton | None:
        """The extra action ``row_actions`` put at the end of *key*'s control line, or None."""
        return self._row_actions.get(key)

    def link_button(self, key: str) -> QToolButton | None:
        """One row's Link button, shown on hover, or None."""
        return self._link_btns.get(key)

    def link_chip(self, key: str) -> _LinkChip | None:
        """One linked row's chip, or None."""
        return self._link_chips.get(key)

    def hover_row(self, key: str) -> _HoverRow | None:
        return self._hover_rows.get(key)

    def step_chip_text(self, key: str) -> str | None:
        """The "Setup step N" chip on *key*'s row, in a template draft; None for a row with none."""
        chip = self._step_chips.get(key)
        return chip.value if chip is not None else None

    def step_chip(self, key: str) -> NeutralChip | None:
        return self._step_chips.get(key)

    def fill_hint_text(self, key: str) -> str | None:
        """ "Fill in: <step text>" as *key*'s row shows it in a bot: its placeholder, or the line under it."""
        label = self._fill_labels.get(key)
        if label is not None:
            return label.text()
        widget = self._widgets.get(key)
        text = widget.placeholderText() if isinstance(widget, (QLineEdit, QPlainTextEdit)) else ""
        return text if text.startswith("Fill in: ") else None

    def required_fields(self) -> list:
        """The required ``ParamField``s the form was last built from."""
        return [f for f in self._schema if f.required]

    @property
    def schema(self) -> list:
        """The ``ParamField``s as drawn: a linked type narrowed, a fill-in required."""
        return list(self._schema)

    def set_param_value(self, key: str, value) -> None:
        """Set one param's widget from outside, as if the user had typed it.

        Emits changed() so the edit is saved and undoable, but only when the value
        actually moves — re-importing the same page should not dirty the bot.
        """
        w = self._widgets.get(key)
        if not isinstance(w, _WikiPageLineEdit) or w.text().strip() == str(value).strip():
            return
        w.setText(str(value))

    def _validate_required_fields(self) -> None:
        """Draw the error border on each empty required field whose emptiness is shown."""
        for key, widget in self._required_widgets.items():
            if isinstance(widget, _XyzField):
                # Marks its own boxes, including a partly filled optional one.
                widget.set_required_shown(key in self._shown_required)
                continue
            red = key in self._shown_required and self._empty(key)
            target = widget.line_edit if isinstance(widget, _ZoneWidget) else widget
            set_style_sheet(target, f"border: 1px solid {THEME.error};" if red else "")

    def _style_field_label(self, label: QLabel, field: Any) -> None:
        """Set a field label's text and color together.

        They cannot be separated: the required marker is a ``<span>`` carrying its
        color inside the label's own text, so a theme switch has to rewrite the text,
        not just the stylesheet. Nothing in the guard test can see that -- it looks for
        setStyleSheet, and this is setText. The marker is muted until the field's
        emptiness is shown, then ``THEME.error``.
        """
        text = field.label
        if field.required:
            red = field.key in self._shown_required and self._empty(field.key)
            text += f" <span style='color:{THEME.error if red else THEME.text_muted}'>*</span>"
        label.setText(text)
        set_style_sheet(label, f"color: {THEME.text_muted}; font-size: {THEME.font_small_px}px; font-weight: 600;")

    def apply_theme_styles(self) -> None:
        for label, field in self._field_labels:
            self._style_field_label(label, field)
        for label in self._muted_labels:
            set_style_sheet(label, f"color: {THEME.text_muted};")
        for btn in self._link_btns.values():
            btn.setIcon(svg_icon("hook", 14))
        for btn in self._step_btns.values():
            btn.setIcon(svg_icon("plus", 14))
        # The red border on an empty required field is THEME.error, and is applied
        # outside load_schema — re-running the check is what re-derives it.
        self._validate_required_fields()

    def widget(self, key: str):
        """The input widget for one param key, or None.

        For callers that need to inspect or drive a single field — an xyz field is an
        ``_XyzField``.
        """
        return self._widgets.get(key)

    def get_params(self) -> dict:
        result: dict[str, Any] = {}
        for key, w in self._widgets.items():
            if isinstance(w, _XyzField):
                # Blank or invalid is absent, not (0, 0, 0): the nodes read an optional one with get().
                if (xyz := w.value()) is not None:
                    result[key] = xyz
            elif isinstance(w, QPlainTextEdit):
                result[key] = w.toPlainText()
            elif isinstance(w, PlayerListWidget | PlayerSlotWidget):
                result[key] = w.get_value()
            elif isinstance(w, _StrListLineEdit):
                result[key] = parse_list_text(w.text())
            elif isinstance(w, _WikiPageLineEdit):
                result[key] = page_title(w.text()) or w.text().strip()
            elif isinstance(w, _ZoneWidget):
                result[key] = w.text()
            elif isinstance(w, QLineEdit):
                result[key] = w.text()
            elif isinstance(w, RatioSpinBox):
                # Before QSpinBox, which it is: the param is the ratio, not the percent shown.
                result[key] = w.ratio()
            elif isinstance(w, QSpinBox):
                result[key] = w.value()
            elif isinstance(w, QDoubleSpinBox):
                result[key] = w.value()
            elif isinstance(w, _NavZoneField | ScalesButton):
                result[key] = w.value()
            elif isinstance(w, QCheckBox):
                result[key] = w.isChecked()
        return result

    def _make_widget(
        self,
        field_type: str,
        val: Any,
        help_text: str,
        placeholder: str = "",
        minimum: float | None = None,
        maximum: float | None = None,
        required: bool = False,
    ) -> Any:
        """Build the control for one param field.

        *minimum* / *maximum* bound the numeric types only, and ``None`` keeps the
        wide range these used to have unconditionally. A delay param that accepts a
        negative fails silently rather than loudly -- see ``BotOptionSpec.minimum``.
        """
        if field_type == "text":
            w = QPlainTextEdit()
            w.setPlainText(str(val) if val is not None else "")
            w.setFixedHeight(120)
            w.setPlaceholderText(placeholder)
            if help_text:
                w.setToolTip(help_text)
            w.textChanged.connect(self._emit_changed)
            return w
        if field_type == "zone":
            w: QWidget = _ZoneWidget(self._get_zone_suggestions, self._get_clients)
            w.setText(str(val) if val is not None else "")
            if help_text:
                w.setToolTip(help_text)
            w.textChanged.connect(self._emit_changed)
            return w
        if field_type == "str":
            plain = QLineEdit()
            plain.setText(str(val) if val is not None else "")
            plain.setPlaceholderText(placeholder)
            if help_text:
                plain.setToolTip(help_text)
            plain.textChanged.connect(self._emit_changed)
            return plain
        if field_type == "player_slot":
            w = PlayerSlotWidget()
            w.set_available_players(len(self._get_clients()))
            w.set_value(val if isinstance(val, int) else 0)
            if help_text:
                w.setToolTip(help_text)
            w.changed.connect(self._emit_changed)
            return w
        if field_type == "player_list":
            w = PlayerListWidget()
            w.set_available_players(len(self._get_clients()))
            w.set_value(val if isinstance(val, list) else [])
            if help_text:
                w.setToolTip(help_text)
            w.changed.connect(self._emit_changed)
            return w
        if field_type == "wiki_page":
            w = _WikiPageLineEdit()
            w.setText(str(val) if val else "")
            w.setPlaceholderText("Creature:Lost Soul")
            if help_text:
                w.setToolTip(help_text)
            w.editingFinished.connect(w.normalize)
            w.textChanged.connect(self._emit_changed)
            return w
        if field_type == "str_list":
            w = _StrListLineEdit()
            if isinstance(val, list):
                w.setText(format_list_text([str(v) for v in val]))
            else:
                w.setText(str(val) if val else "")
            w.setPlaceholderText("e.g. ItemA, ItemB")
            if help_text:
                w.setToolTip(help_text)
            w.textChanged.connect(self._emit_changed)
            return w
        if field_type == "int":
            w = QSpinBox()
            w.setRange(-999_999 if minimum is None else int(minimum), 999_999 if maximum is None else int(maximum))
            try:
                w.setValue(int(val) if val is not None else 0)
            except (TypeError, ValueError):
                w.setValue(0)
            if help_text:
                w.setToolTip(help_text)
            w.valueChanged.connect(self._emit_changed)
            return w
        if field_type == "ratio":
            try:
                ratio = float(val) if val is not None else 0.0
            except (TypeError, ValueError):
                ratio = 0.0
            w = RatioSpinBox(ratio, minimum, maximum)
            if help_text:
                w.setToolTip(help_text)
            w.valueChanged.connect(self._emit_changed)
            return w
        if field_type == "float":
            w = QDoubleSpinBox()
            w.setRange(
                -999_999.0 if minimum is None else float(minimum),
                999_999.0 if maximum is None else float(maximum),
            )
            w.setDecimals(3)
            w.setSingleStep(1.0)
            try:
                w.setValue(float(val) if val is not None else 0.0)
            except (TypeError, ValueError):
                w.setValue(0.0)
            if help_text:
                w.setToolTip(help_text)
            w.valueChanged.connect(self._emit_changed)
            return w
        if field_type == "bool":
            w = QCheckBox()
            w.setChecked(bool(val) if val is not None else False)
            if help_text:
                w.setToolTip(help_text)
            w.checkStateChanged.connect(self._emit_changed)
            return w
        if field_type == "xyz":
            w = _XyzField(val, required=required, get_clients=lambda: self._get_clients(), help_text=help_text)
            w.changed.connect(self._emit_changed)
            return w
        if field_type == "nav_zone":
            w = _NavZoneField(self._get_zone_suggestions, self._get_clients)
            w.set_value(str(val) if val else "")
            if help_text:
                w.setToolTip(help_text)
            w.changed.connect(self._emit_changed)
            return w
        if field_type == "scales":
            w = ScalesButton(get_default=lambda: self._get_default_scales())
            w.set_value(str(val) if val else "")
            if help_text:
                w.setToolTip(help_text)
            w.changed.connect(self._emit_changed)
            return w
        w = QLabel(f"(unsupported type: {field_type})")
        self._muted_labels.append(w)
        w.setStyleSheet(f"color: {THEME.text_muted};")
        return w

    def _emit_changed(self, *_) -> None:
        if not self._suppress:
            # The labels too: a filled field's marker goes back to muted at once ("reward early").
            for label, field in self._field_labels:
                self._style_field_label(label, field)
            self._validate_required_fields()
            self._refresh_rows()
            self.changed.emit()

    def set_str(self, key: str, value: str) -> None:
        """Programmatically set a 'str' or zone field by key: both zone widgets hold a zone path."""
        widget = self._widgets.get(key)
        self._suppress = True
        if isinstance(widget, _NavZoneField):
            # Written whatever it is -- Record must never be refused -- but a zone Go To
            # Zone cannot reach is said so here, and validation marks the node.
            widget.set_value(value)
            reachable = not value or not widget.picker.nav.worlds or is_destination(widget.picker.nav, value)
            widget.notice.setText(
                "" if reachable else f"{value} is not a destination on the zone map: pick the nearest major zone."
            )
            widget.notice.setVisible(not reachable)
        elif isinstance(widget, _ZoneWidget | QLineEdit):
            widget.setText(value)
        else:
            self._suppress = False
            return
        self._suppress = False
        self._emit_changed()

    def set_xyz(self, key: str, x: float, y: float, z: float) -> None:
        """Programmatically set an 'xyz' field by key, rounded to two decimals as the field shows it."""
        widget = self._widgets.get(key)
        if isinstance(widget, _XyzField):
            widget.set_coordinate({"x": x, "y": y, "z": z})

    def players(self, key: str) -> list[int]:
        """The 1-based players a 'player_list' field names, or [] for no such field."""
        widget = self._widgets.get(key)
        return widget.get_value() if isinstance(widget, PlayerListWidget) else []

    def set_players(self, key: str, value: list[int]) -> None:
        """Programmatically set a 'player_list' field by key."""
        widget = self._widgets.get(key)
        if isinstance(widget, PlayerListWidget):
            widget.set_value(value)

    def snapshot(self, keys) -> dict[str, Any]:
        """What the zone, xyz and player list fields named in *keys* hold now, for ``restore``.

        Record writes as it goes, so a recording cancelled partway puts these back
        rather than leaving half of it behind. An xyz field is kept as its boxes'
        text, so it comes back exactly as it was — see ``_XyzField.set_texts``.
        Keys naming no such field are skipped.
        """
        saved: dict[str, Any] = {}
        for key in keys:
            widget = self._widgets.get(key)
            if isinstance(widget, _XyzField):
                saved[key] = widget.texts()
            elif isinstance(widget, _NavZoneField):
                saved[key] = widget.value()
            elif isinstance(widget, _ZoneWidget | QLineEdit):
                saved[key] = widget.text()
            elif isinstance(widget, PlayerListWidget):
                saved[key] = widget.get_value()
        return saved

    def restore(self, saved: dict[str, Any]) -> None:
        """Put back what ``snapshot`` read."""
        for key, value in saved.items():
            widget = self._widgets.get(key)
            if isinstance(widget, _XyzField):
                widget.set_texts(value)
            elif isinstance(widget, PlayerListWidget):
                widget.set_value(value)
            else:
                self.set_str(key, value)


# ---------------------------------------------------------------------------
# _FlagConditionsWidget  — dynamic AND/OR/NOT flag condition list for edges
# ---------------------------------------------------------------------------


class _FlagConditionsWidget(ThemedMixin, QWidget):
    """Widget that edits flag conditions on a TransitionModel.

    Supports two levels of boolean logic:
    - Each group combines its conditions internally with AND or OR.
    - Multiple groups are combined with the outer AND/OR selector.

    Single-group display (one group, outer selector hidden) matches the old
    flat-list layout for simple cases and backward compatibility.
    """

    changed = pyqtSignal()

    _NOT_W = 52
    _REM_W = 24

    def __init__(self, parent=None):
        super().__init__(parent)
        self._option_names: list[str] = []
        self._updating: bool = False
        # List of (group_frame, inner_op_combo, rows_layout, row_tuples_list)
        self._groups: list[tuple[QFrame, QComboBox, QVBoxLayout, list]] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        # Outer operator row (hidden when only one group)
        self._outer_op_row = QWidget()
        _oor = QHBoxLayout(self._outer_op_row)
        _oor.setContentsMargins(0, 0, 0, 0)
        _oor.setSpacing(4)
        _oor_lbl = QLabel("Groups combine with:")
        _oor_lbl.setObjectName("FlagMutedBold")
        self._groups_op_combo = QComboBox()
        self._groups_op_combo.addItems(["OR", "AND"])
        self._groups_op_combo.setFixedWidth(64)
        _oor.addWidget(_oor_lbl)
        _oor.addWidget(self._groups_op_combo)
        _oor.addStretch()
        outer.addWidget(self._outer_op_row)
        self._outer_op_row.setVisible(False)

        # Groups container
        self._groups_widget = QWidget()
        self._groups_layout = QVBoxLayout(self._groups_widget)
        self._groups_layout.setContentsMargins(0, 0, 0, 0)
        self._groups_layout.setSpacing(6)
        outer.addWidget(self._groups_widget)

        # Add group button
        self._add_group_btn = ActionButton("plus", "Add condition group")
        self._add_group_btn.clicked.connect(self._on_add_group)
        outer.addWidget(self._add_group_btn)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setObjectName("FlagSep")
        outer.addWidget(sep)

        self._groups_op_combo.currentTextChanged.connect(self._on_any_changed)
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        """One sheet on self, matching descendants by objectName.

        Groups and rows are added and removed as the user edits, so a list of
        references would need clearing and refilling in step with them. ID selectors
        need none of that: a row built *after* a theme switch is styled by the same
        sheet, with no bookkeeping to forget.
        """
        self.setStyleSheet(
            f"#FlagMutedBold {{ color: {THEME.text_muted}; font-size: {THEME.font_small_px}px;"
            f"  font-weight: 600; border: none; }}"
            f"#FlagSep {{ color: {THEME.border}; }}"
            f"#FlagGroupFrame {{ border: 1px solid {THEME.border}; border-radius: 4px; }}"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_flag_groups(
        self,
        flag_groups: list[FlagGroup],
        flag_groups_op: str,
        option_names: list[str],
    ) -> None:
        """Load from the new grouped FlagGroup format."""
        self._updating = True
        self._option_names = list(option_names)
        self._clear_all_groups()
        idx = self._groups_op_combo.findText(flag_groups_op)
        self._groups_op_combo.setCurrentIndex(idx if idx >= 0 else 0)
        for g in flag_groups:
            self._add_group(flags=g.flags, op=g.op)
        if not self._groups:
            self._add_group()
        self._refresh_outer_op_visibility()
        self._updating = False

    def set_conditions_flat(
        self,
        flags: list[FlagCondition],
        flag_op: str,
        option_names: list[str],
    ) -> None:
        """Load from the legacy flat flags+flag_op format (single group)."""
        self._updating = True
        self._option_names = list(option_names)
        self._clear_all_groups()
        self._groups_op_combo.setCurrentIndex(0)  # OR default
        self._add_group(flags=flags, op=flag_op)
        self._refresh_outer_op_visibility()
        self._updating = False

    def get_flag_groups(self) -> tuple[list[FlagGroup], str]:
        """Return the current state as (flag_groups, flag_groups_op)."""
        result: list[FlagGroup] = []
        for _frame, inner_op, _rows_layout, rows in self._groups:
            flags = []
            for not_cb, opt_cb, _rem in rows:
                option = opt_cb.currentText()
                if option:
                    flags.append(FlagCondition(option=option, negate=not_cb.isChecked()))
            if flags:
                result.append(FlagGroup(flags=flags, op=inner_op.currentText()))
        return result, self._groups_op_combo.currentText()

    # ------------------------------------------------------------------
    # Group management
    # ------------------------------------------------------------------

    def _clear_all_groups(self) -> None:
        for frame, _, _, _ in self._groups:
            self._groups_layout.removeWidget(frame)
            frame.deleteLater()
        self._groups.clear()

    def _add_group(self, flags: list[FlagCondition] | None = None, op: str = "AND") -> None:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setObjectName("FlagGroupFrame")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(6, 4, 6, 4)
        frame_layout.setSpacing(3)

        # Group header: inner-op selector + remove button
        header = QWidget()
        header.setStyleSheet("QWidget { border: none; }")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(4)

        op_lbl = QLabel("Combine with:")
        op_lbl.setObjectName("FlagMutedBold")
        inner_op = QComboBox()
        inner_op.addItems(["AND", "OR"])
        inner_op.setFixedWidth(64)
        inner_op_idx = inner_op.findText(op)
        inner_op.setCurrentIndex(inner_op_idx if inner_op_idx >= 0 else 0)

        rem_group_btn = IconButton(
            "dismiss", tooltip="Remove this group", size=self._REM_W, icon_size=12, tint=IconTint.DANGER
        )

        h_layout.addWidget(op_lbl)
        h_layout.addWidget(inner_op)
        h_layout.addStretch()
        h_layout.addWidget(rem_group_btn)
        frame_layout.addWidget(header)

        # Rows container for this group
        rows_widget = QWidget()
        rows_widget.setStyleSheet("QWidget { border: none; }")
        rows_layout = QVBoxLayout(rows_widget)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(2)
        frame_layout.addWidget(rows_widget)

        # Add condition button
        add_cond_btn = ActionButton("plus", "Add condition")
        frame_layout.addWidget(add_cond_btn)

        row_tuples: list[tuple[QCheckBox, QComboBox, QPushButton]] = []
        group_entry = (frame, inner_op, rows_layout, row_tuples)
        self._groups.append(group_entry)
        self._groups_layout.addWidget(frame)

        # Add initial condition rows
        for fc in flags or []:
            self._add_condition_row(group_entry, fc.option, fc.negate)

        # Wire signals
        inner_op.currentTextChanged.connect(self._on_any_changed)
        add_cond_btn.clicked.connect(lambda _, ge=group_entry: self._on_add_condition(ge))
        rem_group_btn.clicked.connect(lambda _, f=frame, ge=group_entry: self._on_remove_group(f, ge))

    def _add_condition_row(
        self,
        group_entry: tuple,
        option: str = "",
        negate: bool = False,
    ) -> None:
        frame, inner_op, rows_layout, row_tuples = group_entry

        row = QWidget()
        row.setStyleSheet("QWidget { border: none; }")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)

        not_cb = QCheckBox("NOT")
        not_cb.setChecked(negate)
        not_cb.setFixedWidth(self._NOT_W)

        opt_combo = QComboBox()
        opt_combo.addItems(self._option_names)
        if option and opt_combo.findText(option) < 0:
            opt_combo.addItem(option)
        if option:
            opt_combo.setCurrentText(option)

        rem_btn = IconButton(
            "dismiss", tooltip="Remove this condition", size=self._REM_W, icon_size=12, tint=IconTint.DANGER
        )

        row_layout.addWidget(not_cb)
        row_layout.addWidget(opt_combo, 1)
        row_layout.addWidget(rem_btn)
        rows_layout.addWidget(row)

        t = (not_cb, opt_combo, rem_btn)
        row_tuples.append(t)

        not_cb.checkStateChanged.connect(self._on_any_changed)
        opt_combo.currentTextChanged.connect(self._on_any_changed)
        rem_btn.clicked.connect(lambda _=None, r=row, tup=t, ge=group_entry: self._on_remove_condition(r, tup, ge))

    def _refresh_outer_op_visibility(self) -> None:
        self._outer_op_row.setVisible(len(self._groups) > 1)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_add_group(self) -> None:
        self._add_group()
        self._refresh_outer_op_visibility()
        if not self._updating:
            self.changed.emit()

    def _on_remove_group(self, frame: QFrame, group_entry: tuple) -> None:
        if len(self._groups) <= 1:
            return  # always keep at least one group
        if group_entry in self._groups:
            self._groups.remove(group_entry)
        self._groups_layout.removeWidget(frame)
        frame.deleteLater()
        self._refresh_outer_op_visibility()
        if not self._updating:
            self.changed.emit()

    def _on_add_condition(self, group_entry: tuple) -> None:
        default = self._option_names[0] if self._option_names else ""
        self._add_condition_row(group_entry, default, False)
        if not self._updating:
            self.changed.emit()

    def _on_remove_condition(self, row: QWidget, tup: tuple, group_entry: tuple) -> None:
        _frame, _inner_op, rows_layout, row_tuples = group_entry
        if tup in row_tuples:
            row_tuples.remove(tup)
        rows_layout.removeWidget(row)
        row.deleteLater()
        if not self._updating:
            self.changed.emit()

    def _on_any_changed(self, *_) -> None:
        if not self._updating:
            self.changed.emit()


# ---------------------------------------------------------------------------
# _ParamPanel
# ---------------------------------------------------------------------------

_PAGE_IDLE = 0
_PAGE_NODE = 1
_PAGE_EDGE = 2
_PAGE_REGION = 3
_PAGE_CALL = 4
_PAGE_SIGNATURE = 5


class _RegionColorRow(ThemedMixin, QWidget):
    """The six region swatches inline, the selected one ringed in the accent."""

    changed = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._key: int = THEME.REGION_PALETTE[0]
        self._btns: dict[int, QPushButton] = {}
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        for key in THEME.REGION_PALETTE:
            btn = QPushButton()
            btn.setFixedSize(22, 22)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(f"Region color {key}")
            btn.clicked.connect(lambda _=None, k=key: self._on_clicked(k))
            self._btns[key] = btn
            row.addWidget(btn)
        row.addStretch()
        self.refresh_theme_styles()

    @property
    def key(self) -> int:
        return self._key

    def set_key(self, key: int) -> None:
        self._key = key
        self.refresh_theme_styles()

    def _on_clicked(self, key: int) -> None:
        if key == self._key:
            return
        self.set_key(key)
        self.changed.emit(key)

    def apply_theme_styles(self) -> None:
        for key, btn in self._btns.items():
            color_hex = THEME.region_color(key).name()
            border = THEME.accent if key == self._key else "transparent"
            btn.setStyleSheet(
                f"QPushButton {{ background: {color_hex}; border: 2px solid {border}; border-radius: 11px; }}"
                f"QPushButton:hover {{ border-color: {THEME.text_muted}; }}"
            )


class _NodeTypeDelegate(QStyledItemDelegate):
    """Custom delegate that renders separator items as a consistent 1px line in THEME.border."""

    def paint(self, painter, option, index):
        if index.data(Qt.ItemDataRole.AccessibleDescriptionRole) == "separator":
            painter.save()
            mid = option.rect.center().y()
            painter.setPen(QPen(QColor(THEME.border), 1))
            painter.drawLine(option.rect.left() + 4, mid, option.rect.right() - 4, mid)
            painter.restore()
            return
        super().paint(painter, option, index)

    def sizeHint(self, option, index):
        if index.data(Qt.ItemDataRole.AccessibleDescriptionRole) == "separator":
            return QSize(0, 9)
        return super().sizeHint(option, index)


class _ParamPanel(ThemedMixin, QWidget):
    # Emitted BEFORE a typed edit mutates the model -- a param, label, notes, flag
    # conditions, or a region's label or inputs -- carrying the undo description.
    # The canvas opens its debounced undo step here, which captures a before-snapshot
    # only on the first edit of a burst; opened from properties_changed instead, that
    # snapshot already held the edit and undo restored it.
    edit_starting = pyqtSignal(str)
    properties_changed = pyqtSignal()
    label_committed = pyqtSignal()
    # Emitted for discrete structural property changes (node type switch, edge
    # rewire) that need an immediate undo snapshot rather than the debounced
    # param-edit path.  Emit it BEFORE mutating the model — UndoManager.push()
    # captures a before-snapshot.
    structural_property_changed = pyqtSignal(str)
    # Emitted after such a change has been applied to the model.  Unlike
    # properties_changed this does not start a debounced undo snapshot (the
    # immediate one above already covers the edit) and it asks the canvas for a
    # full structural refresh, which is what redraws node type sub-labels,
    # output ports and rewired edges.
    structural_property_applied = pyqtSignal()
    # Emitted when the user changes source or dest via the dropdowns.
    # Args: old_source, old_when (str|None), old_dest, new_source, new_dest
    edge_endpoints_changed = pyqtSignal(str, object, str, str, str)
    # Emitted when the user changes the When condition via the dropdown.
    # Args: source, old_when (str|None), dest, new_when (str|None)
    edge_when_changed = pyqtSignal(str, object, str, object)
    # Emitted when the user completes a wiki import from the handle_combat node panel.
    # Carries the list of selected DropModel entries to be added to the bot's drops.
    wiki_import_completed = pyqtSignal(list)
    # Emitted when the user clicks a connection row to navigate to an edge or state.
    select_edge_requested = pyqtSignal(object)  # TransitionModel
    select_state_requested = pyqtSignal(str)  # state ID
    # Emitted when the user gives the shown state one of the two bot-wide FSM roles.
    # The panel holds no BotConfig, so it asks rather than assigns -- the canvas owns
    # the mutation, which is also what gets it an undo entry and a badge refresh.
    # Both roles are exactly-one, so there is no "clear" counterpart: the request
    # always names the state taking the role.
    initial_state_requested = pyqtSignal(str)  # state ID
    recovery_state_requested = pyqtSignal(str)  # state ID
    # The region or call node page wrote into the shown RegionModel (label, color,
    # inputs). Its undo step is already open: a typed edit announced itself with
    # edit_starting, a discrete one pushed through structural_property_changed.
    region_property_changed = pyqtSignal()
    # (region id, collapse): the region page's Collapse / Expand button (#67).
    region_collapse_requested = pyqtSignal(str, bool)
    detach_requested = pyqtSignal(str)  # region ID
    open_definition_requested = pyqtSignal(str)  # region ID
    # (role field, state id): the call node's page moved a bot role onto the member
    # the chosen entry port leads to.
    role_requested = pyqtSignal(str, str)
    # (state id, param key): Link to subgraph input, and Unlink, on a definition node's page.
    link_input_requested = pyqtSignal(str, str)
    unlink_input_requested = pyqtSignal(str, str)
    # (region id, input name): the same two on a nested subgraph node's page, in a definition.
    link_nested_input_requested = pyqtSignal(str, str)
    unlink_nested_input_requested = pyqtSignal(str, str)
    # (state id, promoted): the node page's Promote to input group / Demote button.
    promote_node_requested = pyqtSignal(str, bool)
    # The signature page wrote into the definition document's signature.
    definition_changed = pyqtSignal()
    # (state id, param key), on a node of a template draft: make the param a setup step's
    # fill-in, or stop it being one. The panel only reports; the canvas edits the draft.
    add_setup_target_requested = pyqtSignal(str, str)
    remove_setup_target_requested = pyqtSignal(str, str)
    add_port_requested = pyqtSignal(str)  # SUBGRAPH_ENTRY / SUBGRAPH_EXIT
    delete_port_requested = pyqtSignal(str)  # state ID

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state: StateModel | None = None
        self._transition: TransitionModel | None = None
        self._region: RegionModel | None = None
        self._recovery_id: str | None = None
        self._suppress: bool = False
        self._get_clients: Callable[[], list] = lambda: []
        # The player Ctrl+R records from; AppController points it at the last-focused game window.
        self._preferred_player: Callable[[], int] = lambda: 0
        # All states for the source/dest dropdowns; populated by show_transition()
        self._all_states: list = []
        # Other transitions in the config — for duplicate-check in endpoint change
        self._all_transitions: list[TransitionModel] = []
        # All states/transitions for connections panel; populated by show_state()
        self._all_states_for_node: list = []
        self._all_transitions_for_node: list[TransitionModel] = []

        self._stack = QStackedWidget()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._stack)

        # Page 0 — idle
        self._idle_lbl = QLabel("Select a node or edge to edit its properties.")
        self._idle_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stack.addWidget(self._idle_lbl)

        # Page 1 — node. Since #142 a page of cards like the subgraph node's: the label
        # as the title, one gray line for the node type and id, then Parameters,
        # Connections, Notes and Role, the whole page scrolling as one.
        from bartlebot.gui.canvas.node_cards import PageFoldBar, TitleEdit, ToFillLabel, record_line  # noqa: PLC0415
        from bartlebot.gui.sidebar.option_card import CardFolds, OptionCard, UiStateFoldStore  # noqa: PLC0415

        self._node_id_lbl = ThemedTextLabel("", color="text_muted")
        self._node_id_lbl.setToolTip("The node's id: what edges and the bot file call it")
        self._node_label_edit = TitleEdit()
        self._node_type_combo = QComboBox()
        self._node_type_combo.setToolTip("The node type. Changing it clears the params.")
        # Not as wide as the longest type: it shares a line with the id and the ?.
        self._node_type_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._node_type_combo.setMinimumContentsLength(12)
        # The category headers carry a THEME color on the model item rather than a
        # stylesheet, so apply_theme_styles re-sets their foreground by hand.
        self._node_type_headers: list[QStandardItem] = []
        _model = QStandardItemModel()
        for _i, (_category, _node_types) in enumerate(NODE_CATEGORIES):
            if _i > 0:
                _sep = QStandardItem()
                _sep.setData("separator", Qt.ItemDataRole.AccessibleDescriptionRole)
                _sep.setEnabled(False)
                _sep.setSelectable(False)
                _model.appendRow(_sep)
            _header = QStandardItem(_category)
            _header.setEnabled(False)
            _header.setSelectable(False)
            _font = _header.font()
            _font.setBold(True)
            _font.setPixelSize(THEME.font_small_px)
            _header.setFont(_font)
            _model.appendRow(_header)
            self._node_type_headers.append(_header)
            for _node_type in _node_types:
                _item = QStandardItem("    " + _node_type)
                _item.setData(_node_type, Qt.ItemDataRole.UserRole)
                _model.appendRow(_item)
        self._node_type_combo.setModel(_model)
        self._node_type_combo.setItemDelegate(_NodeTypeDelegate(self._node_type_combo))
        self._node_help_btn = _NodeHelpButton()
        self._node_help_btn.clicked.connect(self._on_node_help)

        node_page = QWidget()
        node_col = QVBoxLayout(node_page)
        node_col.setContentsMargins(6, 6, 6, 6)
        node_col.setSpacing(4)
        node_col.addWidget(self._node_label_edit)
        meta = QHBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(6)
        node_col.addLayout(meta)
        meta.addWidget(self._node_type_combo, 1)
        meta.addWidget(self._node_id_lbl)
        meta.addWidget(self._node_help_btn)
        node_col.addSpacing(4)
        self._node_fold_bar = PageFoldBar(self._set_node_descriptions_visible)
        node_col.addWidget(self._node_fold_bar)

        # Parameters: the form, with Record (and, in a definition, Promote) on the band.
        self._params_card = OptionCard(param_cards.PARAMS, "Parameters")
        self._params_count = ToFillLabel()
        self._params_card.add_header_widget(self._params_count)
        # Definition documents only: put this node whole on the subgraph node as an
        # input group, or take it off. A toggle on the band, with "Promoted" in the
        # summary; its tooltip names what a click does next.
        self._promote_btn = IconButton("chevrons_up", tooltip="Promote to input group", size=24, icon_size=16)
        self._params_card.add_header_widget(self._promote_btn, gap_before=2)
        self._node_promoted = False
        self._promote_btn.clicked.connect(self._on_promote_clicked)
        # Recording is how most params here get filled, so the button is drawn to be
        # found (#106): SECONDARY's accent and the red dot, on the band so it works with
        # the card folded (#142).
        self._record_btn = RecordButton(
            style=ActionStyle.SECONDARY, fill=False, get_clients=lambda: self._get_clients()
        )
        self._record_btn.setToolTip(
            "Fill this node's values from the game (Ctrl+R records from the game window you were last in)"
        )
        self._record_btn.clicked.connect(self._on_record_clicked)
        self._params_card.add_header_widget(self._record_btn, gap_before=2)
        self._record_status_lbl = record_line()
        self._params_card.body_layout.addWidget(self._record_status_lbl)
        self._param_form = _DynamicParamForm()
        self._params_card.body_layout.addWidget(self._param_form)
        self._recording_player: int | None = None

        # Connections: every route off the node by return value, including the ones the
        # canvas never draws, then what comes in.
        self._conn_card = OptionCard(param_cards.CONNECTIONS, "Connections")
        self._conn_body = QWidget()
        self._conn_body_layout = QVBoxLayout(self._conn_body)
        self._conn_body_layout.setContentsMargins(0, 0, 0, 0)
        self._conn_body_layout.setSpacing(2)
        self._conn_card.body_layout.addWidget(self._conn_body)

        # Notes — freeform per-node annotation, folded to its first line.
        self._notes_card = OptionCard(param_cards.NOTES, "Notes")
        self._notes_edit = QPlainTextEdit()
        self._notes_edit.setPlaceholderText("Add a note…")
        self._notes_edit.setFixedHeight(72)
        self._notes_card.body_layout.addWidget(self._notes_edit)

        # FSM role checkboxes. Both roles are exactly-one and bot-wide, so these read
        # as checkboxes but behave as radios across nodes: checking one moves the role
        # here, and the box for the state that already holds it is disabled, because
        # unchecking would leave the bot with no answer and BotConfig requires one.
        self._role_card = OptionCard(param_cards.ROLE, "Role")
        self._initial_check = QCheckBox("Initial state")
        self._initial_check.setToolTip(
            "Start the bot in this state.\n\nExactly one state is the initial state; "
            "checking this moves the role here from whichever state holds it now."
        )
        self._recovery_check = QCheckBox("Recovery state")
        self._recovery_check.setToolTip(
            "Resume here after a crashed client reconnects.\n\nExactly one state is the "
            "recovery state; checking this moves the role here from whichever state holds it now."
        )
        self._role_card.body_layout.addWidget(self._initial_check)
        self._role_card.body_layout.addWidget(self._recovery_check)

        node_cards_box = QWidget()
        node_cards = QVBoxLayout(node_cards_box)
        node_cards.setContentsMargins(0, 0, 0, 0)
        node_cards.setSpacing(8)
        for card in (self._params_card, self._conn_card, self._notes_card, self._role_card):
            node_cards.addWidget(card)
        node_col.addWidget(node_cards_box)
        node_col.addStretch(1)
        self._node_cards = {
            param_cards.PARAMS: self._params_card,
            param_cards.CONNECTIONS: self._conn_card,
            param_cards.NOTES: self._notes_card,
            param_cards.ROLE: self._role_card,
        }
        self._node_folds = CardFolds(
            self._node_fold_bar.heading_actions,
            store=UiStateFoldStore(param_cards.NODE_PAGE_SURFACE, param_cards.node_page_fold_defaults()),
        )
        self._node_folds.set_cards(self._node_cards)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        # Every inspector page reserves its gutter, so the panel keeps one content width
        # whichever page is up, and a fold does not jolt every card sideways (#112).
        reserve_scrollbar_gutter(scroll)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(node_page)
        # Visibility only once everything is parented: see "Never show a widget before
        # it has a parent" in gui/CLAUDE.md.
        self._promote_btn.setVisible(False)
        self._record_btn.setVisible(False)
        self._record_status_lbl.setVisible(False)
        self._stack.addWidget(scroll)

        # Page 2 — edge
        edge_outer = QWidget()
        edge_vbox = QVBoxLayout(edge_outer)
        edge_vbox.setContentsMargins(0, 0, 0, 0)
        edge_vbox.setSpacing(0)

        edge_form_w = QWidget()
        ef = QFormLayout(edge_form_w)
        ef.setContentsMargins(6, 6, 6, 6)

        self._edge_src_combo = QComboBox()
        self._edge_src_combo.setToolTip("Source state — the state that runs before this transition.")
        self._edge_dst_combo = QComboBox()
        self._edge_dst_combo.setToolTip("Destination state — the state to enter when this transition fires.")
        self._edge_when_combo = QComboBox()

        ef.addRow("Source", self._edge_src_combo)
        ef.addRow("Dest", self._edge_dst_combo)
        ef.addRow("When", self._edge_when_combo)

        self._flags_lbl = QLabel("Flag conditions")
        self._flags_lbl.setToolTip(
            "Bot options that must be satisfied for this transition to fire.\n"
            "Combine multiple conditions with AND (all must be true) or OR (any must be true).\n"
            "Check NOT to invert a condition."
        )

        self._flag_conditions_widget = _FlagConditionsWidget()

        edge_vbox.addWidget(edge_form_w)
        edge_vbox.addWidget(self._flags_lbl)
        edge_vbox.addWidget(self._flag_conditions_widget)
        edge_vbox.addStretch()
        self._stack.addWidget(edge_outer)

        # Page 3 — region. Label and color for every region; the subgraph section
        # for a linked one.
        region_outer = QWidget()
        region_vbox = QVBoxLayout(region_outer)
        region_vbox.setContentsMargins(0, 0, 0, 0)
        region_vbox.setSpacing(0)
        region_form_w = QWidget()
        rf = QFormLayout(region_form_w)
        rf.setContentsMargins(6, 6, 6, 6)
        self._region_label_edit = QLineEdit()
        self._region_color_row = _RegionColorRow()
        rf.addRow("Label", self._region_label_edit)
        rf.addRow("Color", self._region_color_row)
        region_vbox.addWidget(region_form_w)
        # Collapsing draws the region as one node and closes up the space it used.
        self._region_fold_note = ThemedTextLabel("", color="text_muted")
        self._region_fold_note.setWordWrap(True)
        self._region_fold_note.setContentsMargins(6, 0, 6, 0)
        self._region_collapse_btn = ActionButton("region_collapse", "Collapse", fill=False)
        region_actions = QWidget()
        region_actions_row = QHBoxLayout(region_actions)
        region_actions_row.setContentsMargins(6, 8, 6, 0)
        region_actions_row.addStretch(1)
        region_actions_row.addWidget(self._region_collapse_btn)
        region_vbox.addWidget(self._region_fold_note)
        region_vbox.addWidget(region_actions)
        self._region_collapse_btn.clicked.connect(self._on_region_collapse_clicked)
        self._region_collapsed = False  # as drawn, for the button's next click

        region_vbox.addStretch()
        region_scroll = QScrollArea()
        region_scroll.setWidgetResizable(True)
        region_scroll.setFrameShape(region_scroll.Shape.NoFrame)
        reserve_scrollbar_gutter(region_scroll)
        region_scroll.setWidget(region_outer)
        self._stack.addWidget(region_scroll)

        # Page 4 -- a call node; page 5 -- a definition's signature, nothing selected.
        # Imported here: both build their forms from _DynamicParamForm, defined above.
        from bartlebot.gui.canvas.call_page import CallNodePage  # noqa: PLC0415
        from bartlebot.gui.canvas.signature_page import SignaturePage  # noqa: PLC0415

        self._call_page = CallNodePage()
        call_scroll = QScrollArea()
        call_scroll.setWidgetResizable(True)
        call_scroll.setFrameShape(call_scroll.Shape.NoFrame)
        reserve_scrollbar_gutter(call_scroll)
        call_scroll.setWidget(self._call_page)
        self._stack.addWidget(call_scroll)
        self._signature_page = SignaturePage()
        sig_scroll = QScrollArea()
        sig_scroll.setWidgetResizable(True)
        sig_scroll.setFrameShape(sig_scroll.Shape.NoFrame)
        reserve_scrollbar_gutter(sig_scroll)
        sig_scroll.setWidget(self._signature_page)
        self._stack.addWidget(sig_scroll)
        self._call_page.edit_starting.connect(self.edit_starting)
        self._call_page.structural_change.connect(self.structural_property_changed)
        self._call_page.changed.connect(self.region_property_changed)
        self._call_page.role_requested.connect(self.role_requested)
        self._call_page.open_definition_requested.connect(self.open_definition_requested)
        self._call_page.detach_requested.connect(self.detach_requested)
        self._call_page.link_input_requested.connect(self.link_nested_input_requested)
        self._call_page.unlink_input_requested.connect(self.unlink_nested_input_requested)
        self._signature_page.edit_starting.connect(self.edit_starting)
        self._signature_page.structural_change.connect(self.structural_property_changed)
        self._signature_page.changed.connect(self.definition_changed)
        self._signature_page.add_port_requested.connect(self.add_port_requested)
        self._signature_page.delete_port_requested.connect(self.delete_port_requested)
        self._signature_page.select_state_requested.connect(self.select_state_requested)

        self._node_label_edit.textChanged.connect(self._on_node_label_changed)
        self._node_label_edit.editingFinished.connect(self._on_label_editing_finished)
        self._node_type_combo.currentIndexChanged.connect(self._on_node_type_changed)
        self._initial_check.clicked.connect(self._on_initial_clicked)
        self._recovery_check.clicked.connect(self._on_recovery_clicked)
        self._param_form.changed.connect(self._on_param_form_changed)
        self._notes_edit.textChanged.connect(self._on_notes_changed)
        self._edge_when_combo.currentTextChanged.connect(self._on_edge_when_changed)
        self._edge_src_combo.currentTextChanged.connect(self._on_edge_src_changed)
        self._edge_dst_combo.currentTextChanged.connect(self._on_edge_dst_changed)
        self._flag_conditions_widget.changed.connect(self._on_flag_conditions_changed)
        self._param_form.link_requested.connect(self._on_form_link_requested)
        self._param_form.step_add_requested.connect(self._on_form_step_add_requested)
        self._param_form.step_remove_requested.connect(self._on_form_step_remove_requested)
        self._param_form.unlink_requested.connect(self._on_form_unlink_requested)
        self._param_form.required_shown_changed.connect(self._refresh_params_card)
        self._region_label_edit.textChanged.connect(self._on_region_label_changed)
        self._region_color_row.changed.connect(self._on_region_color_changed)

        # The connection rows are the one part of this panel a theme switch has to
        # rebuild rather than restyle, and rebuilding deletes widgets — so it is
        # deferred off the StyleChange handler. See apply_theme_styles.
        self._conn_theme_timer = QTimer(self)
        self._conn_theme_timer.setSingleShot(True)
        self._conn_theme_timer.timeout.connect(self._rebuild_connections_for_theme)

        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        self._idle_lbl.setStyleSheet(f"color: {THEME.text_muted};")
        for header in self._node_type_headers:
            header.setForeground(QColor(THEME.text_muted))
        self._node_help_btn.refresh_theme_styles()
        # The status line owns its color, but a switch delivered to this panel alone
        # reaches no child, so it is asked here as the title and id are.
        self._record_status_lbl.refresh_theme_styles()
        self._node_id_lbl.refresh_theme_styles()
        self._node_label_edit.refresh_theme_styles()
        self._notes_edit.setStyleSheet(
            f"QPlainTextEdit {{ background: {THEME.surface}; border: 1px solid {THEME.border}; "
            f"border-radius: 4px; color: {THEME.text}; font-size: {THEME.font_small_px}px; padding: 3px; }}"
        )
        self._flags_lbl.setStyleSheet(
            f"color: {THEME.text_muted}; font-size: {THEME.font_small_px}px; font-weight: 600; margin-top: 4px;"
        )
        # Connection rows are built per selection and hold four THEME colors each
        # (chip background, accent link, hover surface), so replaying the build is
        # both shorter and more complete than holding a reference to every label.
        # It deletes the old rows, which must not happen while
        # QApplication.setStyleSheet is iterating over every widget.
        if self._state is not None:
            self._conn_theme_timer.start(0)

    def _rebuild_connections_for_theme(self) -> None:
        """Replay the connections build for the shown node under the new theme."""
        if self._state is not None:
            self._rebuild_connections(self._state.id)

    @property
    def node_type_combo(self) -> QComboBox:
        """The node type dropdown on the node page."""
        return self._node_type_combo

    @property
    def node_label_edit(self) -> QLineEdit:
        """The label field on the node page."""
        return self._node_label_edit

    @property
    def edge_src_combo(self) -> QComboBox:
        """The Source dropdown on the edge page; item data is the state ID."""
        return self._edge_src_combo

    @property
    def edge_dst_combo(self) -> QComboBox:
        """The Dest dropdown on the edge page; item data is the state ID."""
        return self._edge_dst_combo

    @property
    def when_combo(self) -> QComboBox:
        """The When dropdown on the edge page."""
        return self._edge_when_combo

    @property
    def when_choices(self) -> list[str]:
        """The return values currently offered by the When dropdown, in order."""
        return [self._edge_when_combo.itemText(i) for i in range(self._edge_when_combo.count())]

    def set_clients_getter(self, fn: Callable[[], list]) -> None:
        """Pass a callable returning the currently hooked clients."""
        self._get_clients = fn
        self._param_form.set_clients_getter(fn)
        self._call_page.set_clients_getter(fn)
        self._signature_page.set_clients_getter(fn)
        self.refresh_player_availability()

    def set_default_scales_getter(self, fn: Callable[[], Any]) -> None:
        """Pass a callable returning the open bot's default Scales, for the node form."""
        self._param_form.set_default_scales_getter(fn)

    def set_zone_suggestions_getter(self, fn: Callable[[], list[str]]) -> None:
        """Pass a callable returning zone suggestions, for the node form and the region inputs form alike."""
        self._param_form.set_zone_suggestions_getter(fn)
        self._call_page.set_zone_suggestions_getter(fn)
        self._signature_page.set_zone_suggestions_getter(fn)

    def set_subgraph_library(self, library) -> None:
        """The library an input linked into a nested instance is looked up in, to draw its field."""
        self._call_page.set_subgraph_library(library)
        self._signature_page.set_subgraph_library(library)

    def refresh_player_availability(self) -> None:
        """Re-gray the player checkboxes — call when clients connect or disconnect.

        findChildren rather than a widget registry: player checkboxes appear at several
        depths inside the param form, and this needs no bookkeeping for any of them.
        """
        connected = len(self._get_clients())
        for w in self.findChildren(PlayerListWidget) + self.findChildren(PlayerSlotWidget):
            w.set_available_players(connected)
        for button in self.findChildren(RecordButton):
            button.refresh_players()

    def show_idle(self) -> None:
        self._cancel_record()
        self._state = None
        self._transition = None
        self._region = None
        self._stack.setCurrentIndex(_PAGE_IDLE)

    @property
    def shown_state(self) -> StateModel | None:
        """The state on the node page, or None when another page is up."""
        return self._state

    @property
    def shown_transition(self) -> TransitionModel | None:
        """The transition on the edge page, or None when another page is up."""
        return self._transition

    @property
    def shown_region(self) -> RegionModel | None:
        """The region on the region page, or None when another page is up."""
        return self._region

    @property
    def call_page(self):
        """The call node's page (``CallNodePage``)."""
        return self._call_page

    @property
    def signature_page(self):
        """A definition's signature page (``SignaturePage``)."""
        return self._signature_page

    @property
    def promote_button(self) -> ActionButton:
        """The node page's Promote to input group / Demote button, shown in a definition document."""
        return self._promote_btn

    def show_state(
        self,
        state: StateModel,
        all_states: list | None = None,
        all_transitions: list[TransitionModel] | None = None,
        initial_id: str | None = None,
        recovery_id: str | None = None,
        *,
        in_definition: bool = False,
        links: dict[str, str] | None = None,
        promoted_group: str | None = None,
        field_types: dict[str, str] | None = None,
        fill_hints: dict[str, str] | None = None,
        step_chips: dict[str, str] | None = None,
        step_addable: bool = False,
    ) -> None:
        """Show *state*. *in_definition* is a node of a definition document open for editing.

        There, each param row can be linked to a subgraph input -- its Link button shows
        on hover -- and a row already linked (*links*, key -> what it is linked to) shows
        a chip that unlinks it. The page offers *Promote to input group* for the whole
        node; a node promoted to *promoted_group* has every row linked to it, locked.
        """
        self._cancel_record()
        self._state = state
        self._transition = None
        self._region = None
        self._all_states_for_node = list(all_states or [])
        self._all_transitions_for_node = list(all_transitions or [])
        self._recovery_id = recovery_id
        self._suppress = True
        self._node_id_lbl.setText(state.id)
        self._node_label_edit.setText(state.label or "")
        idx = self._node_type_combo.findData(state.node_type, Qt.ItemDataRole.UserRole)
        if idx >= 0:
            self._node_type_combo.setCurrentIndex(idx)
        schema = NODE_PARAM_SCHEMAS.get(state.node_type, [])
        self._param_form.load_schema(
            schema,
            state.params or {},
            linkable=in_definition,
            links=links,
            promoted_group=promoted_group,
            field_types=field_types,
            fill_hints=fill_hints,
            step_chips=step_chips,
            step_addable=step_addable,
            row_actions=self._row_actions_for(state.node_type, schema),
        )
        self._rebuild_connections(state.id)
        self._notes_edit.blockSignals(True)
        self._notes_edit.setPlainText(state.notes or "")
        self._notes_edit.blockSignals(False)
        self._notes_card.set_summary(param_cards.notes_summary(state.notes))
        self._node_promoted = promoted_group is not None
        self._promote_btn.set_icon_name("chevrons_down" if self._node_promoted else "chevrons_up")
        self._promote_btn.set_tint(IconTint.ACCENT if self._node_promoted else IconTint.DEFAULT)
        self._promote_btn.setToolTip(
            "Demote: take this node's input group off the subgraph node"
            if self._node_promoted
            else "Promote to input group: put every param of this node on the subgraph node, with its Record "
            "button. It follows the node: a param added to the node type later appears there too."
        )
        self._suppress = False
        self._set_role_checks(state.id, initial_id, recovery_id)
        self._promote_btn.setVisible(in_definition and bool(schema))
        # A definition's roles are a formality -- every Entry is a way in -- so there
        # is nothing for the boxes to mean there.
        self._role_card.setVisible(not in_definition)
        recordable = state.node_type in recording.RECORDABLE_NODES
        self._record_btn.setVisible(recordable)
        self._record_status_lbl.setVisible(recordable)
        self._refresh_record_hint()
        self._refresh_params_card()
        self._apply_node_folds(state.id in (initial_id, recovery_id))
        self._stack.setCurrentIndex(_PAGE_NODE)

    def _row_actions_for(self, node_type: str, schema) -> dict:
        """The wiki import, at the end of a fight's ``wiki_page`` row: the field it fills (#142)."""
        if node_type != "handle_combat" or not any(f.key == WIKI_PAGE_PARAM for f in schema):
            return {}
        return {WIKI_PAGE_PARAM: ("download", "Import drops from wiki…", self._on_wiki_import)}

    @property
    def wiki_import_button(self) -> QPushButton | None:
        """The wiki import at the end of a fight's ``wiki_page`` row, or None on any other node."""
        return self._param_form.row_action(WIKI_PAGE_PARAM)

    def _apply_node_folds(self, holds_role: bool) -> None:
        """Fold the node page's cards as the reader left them; Role opens on a node that holds one."""
        from bartlebot.gui.sidebar.option_card import UiStateFoldStore  # noqa: PLC0415

        defaults = param_cards.node_page_fold_defaults(holds_role=holds_role)
        self._node_folds.set_store(UiStateFoldStore(param_cards.NODE_PAGE_SURFACE, defaults))
        self._node_folds.reload()

    def _refresh_params_card(self) -> None:
        """The Parameters band's count and summary, and the Connections and Role summaries."""
        if self._state is None:
            return
        form = self._param_form
        self._params_count.set_count(form.to_fill(), red=form.required_errors_shown())
        params = form.get_params()
        if self._state.node_type in recording.RECORDABLE_NODES:
            parts = param_cards.recording_summary(form.schema, params, recording.recorded_keys(self._state.node_type))
        else:
            parts = param_cards.params_summary(form.schema, params)
        if self._node_promoted:
            parts = [param_cards.SummaryPart("Promoted"), *parts]
        self._params_card.set_summary(parts)

    @property
    def node_cards(self) -> dict:
        """The node page's four cards by group: ``params``, ``connections``, ``notes``, ``role``."""
        return dict(self._node_cards)

    @property
    def node_folds(self):
        return self._node_folds

    @property
    def params_to_fill(self):
        """The Parameters band's "N to fill"."""
        return self._params_count

    def _set_node_descriptions_visible(self, shown: bool) -> None:
        write_show_descriptions(shown)
        self._param_form.set_descriptions_visible(shown)

    def show_required_errors(self) -> None:
        """Draw every empty required field on the shown page red: a save was refused for them."""
        if self._stack.currentIndex() == _PAGE_NODE:
            self._param_form.show_required_errors()
        elif self._stack.currentIndex() == _PAGE_CALL:
            self._call_page.show_required_errors()

    def show_region(
        self,
        region: RegionModel,
        *,
        collapsed: bool | None = None,
        collapse_blocker: str | None = None,
        fights: list[str] | None = None,
    ) -> None:
        """Show a plain region's page: its label, color, and Collapse / Expand. Edits ``region`` in place.

        ``collapsed`` is whether it is drawn collapsed, which in Run mode can differ
        from its saved flag; it defaults to the flag. ``collapse_blocker`` is why the
        region cannot collapse, shown on the disabled button. ``fights`` are the fights a collapsed region holds, named because they
        are out of sight on the canvas.
        """
        self._cancel_record()
        self._state = None
        self._transition = None
        self._region = region
        self._suppress = True
        try:
            self._region_label_edit.setText(region.label)
            self._region_color_row.set_key(region.color)
            collapsed = bool(region.collapsed) if collapsed is None else collapsed
            self._region_collapsed = collapsed
            self._region_collapse_btn.setText("Expand" if collapsed else "Collapse")
            self._region_collapse_btn.set_icon_name("region_expand" if collapsed else "region_collapse")
            self._region_collapse_btn.setEnabled(collapsed or collapse_blocker is None)
            self._region_collapse_btn.setToolTip(
                "Draw the region's nodes again"
                if collapsed
                else (collapse_blocker or "Draw the region as one node and close up the space it uses")
            )
            lines = []
            if collapsed:
                lines.append("Drawn as one node. Double-click it to step in.")
                if fights:
                    lines.append(f"Fights inside: {', '.join(fights)}. Their strategies are set on the Combat tab.")
            self._region_fold_note.setText("\n".join(lines))
            self._region_fold_note.setVisible(bool(lines))
        finally:
            self._suppress = False
        self._stack.setCurrentIndex(_PAGE_REGION)

    def _on_region_collapse_clicked(self) -> None:
        if self._suppress or self._region is None:
            return
        self.region_collapse_requested.emit(self._region.id, not self._region_collapsed)

    @property
    def region_collapse_button(self) -> ActionButton:
        return self._region_collapse_btn

    def show_call(self, region: RegionModel, defn, status, config) -> None:
        """Show a call node's page for the subgraph instance *region*."""
        self._cancel_record()
        self._state = None
        self._transition = None
        self._region = region
        self._call_page.show_call(region, defn, status, config)
        self._stack.setCurrentIndex(_PAGE_CALL)

    def show_signature(self, config) -> None:
        """Show a definition document's signature: its page when nothing is selected."""
        self._cancel_record()
        self._state = None
        self._transition = None
        self._region = None
        self._signature_page.show_signature(config)
        self._stack.setCurrentIndex(_PAGE_SIGNATURE)

    @property
    def showing_call(self) -> bool:
        return self._stack.currentIndex() == _PAGE_CALL

    @property
    def showing_signature(self) -> bool:
        return self._stack.currentIndex() == _PAGE_SIGNATURE

    def _on_form_link_requested(self, key: str) -> None:
        if self._state is not None:
            self.link_input_requested.emit(self._state.id, key)

    def _on_form_step_add_requested(self, key: str) -> None:
        if self._state is not None:
            self.add_setup_target_requested.emit(self._state.id, key)

    def _on_form_step_remove_requested(self, key: str) -> None:
        if self._state is not None:
            self.remove_setup_target_requested.emit(self._state.id, key)

    def _on_form_unlink_requested(self, key: str) -> None:
        if self._state is not None:
            self.unlink_input_requested.emit(self._state.id, key)

    def _on_promote_clicked(self) -> None:
        if self._suppress or self._state is None:
            return
        self.promote_node_requested.emit(self._state.id, not self._node_promoted)

    def _on_region_label_changed(self, text: str) -> None:
        if self._suppress or self._region is None:
            return
        label = text.strip()
        if not label:
            return
        self.edit_starting.emit("Edit region")
        self._region.label = label
        self.region_property_changed.emit()

    def _on_region_color_changed(self, key: int) -> None:
        if self._suppress or self._region is None:
            return
        self.structural_property_changed.emit("Change Region Color")
        self._region.color = key
        self.region_property_changed.emit()

    @property
    def region_label_edit(self) -> QLineEdit:
        return self._region_label_edit

    @property
    def region_color_row(self) -> _RegionColorRow:
        return self._region_color_row

    @property
    def param_form(self) -> _DynamicParamForm:
        return self._param_form

    @property
    def initial_check(self) -> QCheckBox:
        """The Initial state checkbox on the node page."""
        return self._initial_check

    @property
    def recovery_check(self) -> QCheckBox:
        """The Recovery state checkbox on the node page."""
        return self._recovery_check

    def request_initial_role(self) -> None:
        """Ask for the shown state to become the initial state, as ticking the box does."""
        if self._state is not None:
            self.initial_state_requested.emit(self._state.id)

    def request_recovery_role(self) -> None:
        """Ask for the shown state to become the recovery state, as ticking the box does."""
        if self._state is not None:
            self.recovery_state_requested.emit(self._state.id)

    def refresh_role_checks(self, initial_id: str | None, recovery_id: str | None) -> None:
        """Re-sync the role checkboxes for the state already on screen.

        Needed because moving a role changes *two* nodes: the one gaining it and the
        one losing it.  The canvas calls this after applying the move so the panel
        does not keep showing a stale tick if the shown node was the loser.
        """
        if self._state is None:
            return
        self._set_role_checks(self._state.id, initial_id, recovery_id)

    def _set_role_checks(self, state_id: str, initial_id: str | None, recovery_id: str | None) -> None:
        """Tick and enable the role boxes for *state_id*.

        The box is disabled exactly when this state already holds the role: there is
        nothing a click could mean there, since the role cannot be vacated.
        """
        was_suppressed = self._suppress
        self._suppress = True
        try:
            is_initial = state_id == initial_id
            is_recovery = state_id == recovery_id
            self._initial_check.setChecked(is_initial)
            self._initial_check.setEnabled(not is_initial)
            self._recovery_check.setChecked(is_recovery)
            self._recovery_check.setEnabled(not is_recovery)
            self._role_card.set_summary(param_cards.role_summary(initial=is_initial, recovery=is_recovery))
        finally:
            self._suppress = was_suppressed

    def _on_initial_clicked(self, checked: bool) -> None:
        """Ask the canvas to move the initial-state role to the shown state.

        ``clicked`` rather than ``toggled`` so programmatic setChecked() in
        ``show_state`` never reaches here.  An unchecking click cannot happen --
        the box is disabled once this state holds the role -- but it is refused
        rather than ignored, so a future change that enables it cannot silently
        leave the bot with no initial state.
        """
        if self._suppress or self._state is None:
            return
        if not checked:
            self._initial_check.setChecked(True)
            return
        self.request_initial_role()

    def _on_recovery_clicked(self, checked: bool) -> None:
        """Ask the canvas to move the recovery-state role to the shown state."""
        if self._suppress or self._state is None:
            return
        if not checked:
            self._recovery_check.setChecked(True)
            return
        self.request_recovery_role()

    def _reload_when_combo(self, src_returns: list[str] | None = None) -> None:
        """Repopulate the When dropdown from the source node's return values.

        Called on show and again whenever the source endpoint changes — the
        offered returns belong to the source node, so a rewire that is not
        followed by this leaves the list describing the node the edge came from.
        A stored ``when`` the new source cannot produce is kept as an entry
        rather than silently rewritten; the node's uncovered-returns warning is
        what surfaces the mismatch.

        When *src_returns* is None the returns are derived from the transition's
        current source state.
        """
        if self._transition is None:
            return
        if src_returns is None:
            src_state = next((st for st in self._all_states if st.id == self._transition.source), None)
            spec = NODE_SPECS.get(src_state.node_type) if src_state else None
            src_returns = [r.name for r in spec.returns] if spec else []
        when = self._transition.when
        was_suppressed = self._suppress
        self._suppress = True
        try:
            self._edge_when_combo.clear()
            self._edge_when_combo.addItem("(none)")
            for name in src_returns:
                self._edge_when_combo.addItem(name)
            if when and self._edge_when_combo.findText(when) < 0:
                self._edge_when_combo.addItem(when)
            idx = self._edge_when_combo.findText(when) if when else 0
            self._edge_when_combo.setCurrentIndex(idx if idx >= 0 else 0)
        finally:
            self._suppress = was_suppressed

    def show_transition(
        self,
        transition: TransitionModel,
        src_returns: list[str] | None = None,
        option_names: list[str] | None = None,
        states: list | None = None,
        all_transitions: list[TransitionModel] | None = None,
        *,
        rewire: bool = True,
    ) -> None:
        """Show *transition*. Without *rewire* its ends and ``when`` are read-only: a call node's edge is rewired on the canvas."""
        self._cancel_record()
        self._transition = transition
        self._state = None
        self._region = None
        self._all_states = list(states or [])
        self._all_transitions = list(all_transitions or [])
        self._suppress = True

        # Source / dest dropdowns — display label (or ID as fallback), store ID as item data
        self._edge_src_combo.clear()
        for st in self._all_states:
            self._edge_src_combo.addItem(st.label or st.id, st.id)
        src_idx = self._edge_src_combo.findData(transition.source)
        self._edge_src_combo.setCurrentIndex(src_idx if src_idx >= 0 else 0)

        self._edge_dst_combo.clear()
        for st in self._all_states:
            self._edge_dst_combo.addItem(st.label or st.id, st.id)
        dst_idx = self._edge_dst_combo.findData(transition.dest)
        self._edge_dst_combo.setCurrentIndex(dst_idx if dst_idx >= 0 else 0)

        # When combo from source node's possible return values
        self._reload_when_combo(src_returns)

        # Flag conditions widget — prefer grouped format, fall back to flat legacy format
        if transition.flag_groups:
            self._flag_conditions_widget.set_flag_groups(
                transition.flag_groups,
                transition.flag_groups_op,
                option_names or [],
            )
        else:
            self._flag_conditions_widget.set_conditions_flat(
                transition.flags,
                transition.flag_op,
                option_names or [],
            )

        for combo in (self._edge_src_combo, self._edge_dst_combo, self._edge_when_combo):
            combo.setEnabled(rewire)
            combo.setToolTip("" if rewire else "Rewire an edge on a subgraph node by dragging it on the canvas")
        self._suppress = False
        self._stack.setCurrentIndex(_PAGE_EDGE)

    def _on_node_label_changed(self, text: str) -> None:
        if self._suppress or self._state is None:
            return
        self.edit_starting.emit("Edit params")
        self._state.label = text or None
        self.properties_changed.emit()

    def _on_label_editing_finished(self) -> None:
        if not self._suppress and self._state is not None:
            self.label_committed.emit()

    def _on_node_help(self) -> None:
        """Open the node browser in view mode for the currently displayed node type."""
        from bartlebot.gui.canvas.node_browser_dialog import NodeBrowserDialog

        node_type: str | None = None
        if self._state is not None:
            node_type = self._state.node_type
        else:
            node_type = self._node_type_combo.currentData(Qt.ItemDataRole.UserRole)
        NodeBrowserDialog(self, mode="view", initial_node_type=node_type).exec()

    def _on_node_type_changed(self, _index: int) -> None:
        node_type: str = self._node_type_combo.currentData(Qt.ItemDataRole.UserRole)
        if self._suppress or self._state is None or node_type not in NODE_REGISTRY:
            return
        self.structural_property_changed.emit("Change Node Type")
        self._cancel_record()
        self._state.node_type = node_type
        self._state.params = {}
        schema = NODE_PARAM_SCHEMAS.get(node_type, [])
        self._param_form.load_schema(schema, {}, row_actions=self._row_actions_for(node_type, schema))
        self._state.params = self._param_form.get_params()
        self._rebuild_connections(self._state.id)
        recordable = node_type in recording.RECORDABLE_NODES
        self._record_btn.setVisible(recordable)
        self._record_status_lbl.setVisible(recordable)
        self._refresh_record_hint()
        self._refresh_params_card()
        self.structural_property_applied.emit()

    def _on_param_form_changed(self) -> None:
        if self._state is None:
            return
        self.edit_starting.emit("Edit params")
        self._state.params = self._param_form.get_params()
        self._refresh_params_card()
        if not recording.is_recording():
            self._refresh_record_hint()
        self.properties_changed.emit()

    def _on_notes_changed(self) -> None:
        if self._suppress or self._state is None:
            return
        text = self._notes_edit.toPlainText().strip()
        self.edit_starting.emit("Edit params")
        self._state.notes = text if text else None
        self._notes_card.set_summary(param_cards.notes_summary(self._state.notes))
        self.properties_changed.emit()

    def _on_edge_when_changed(self, text: str) -> None:
        if self._suppress or self._transition is None:
            return
        new_when = None if text == "(none)" else text
        old_when = self._transition.when
        if new_when == old_when:
            return
        src = self._transition.source
        dst = self._transition.dest
        # Duplicate check: another transition with the same (source, dest, new_when)
        # must not exist — the same guard the source/dest dropdowns apply.
        if any(
            t is not self._transition and t.source == src and t.dest == dst and t.when == new_when
            for t in self._all_transitions
        ):
            QMessageBox.warning(
                self,
                "Duplicate Transition",
                f"A transition from '{src}' to '{dst}' with that When condition already exists.",
            )
            self._suppress = True
            idx = self._edge_when_combo.findText(old_when) if old_when else 0
            self._edge_when_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self._suppress = False
            return
        self.structural_property_changed.emit("Change Edge Condition")
        self._transition.when = new_when
        self.edge_when_changed.emit(src, old_when, dst, new_when)
        self.structural_property_applied.emit()

    def _on_edge_src_changed(self, _text: str) -> None:
        if self._suppress or self._transition is None:
            return
        new_src_id = self._edge_src_combo.currentData()
        if not new_src_id:
            return
        old_src = self._transition.source
        if new_src_id == old_src:
            return
        old_when = self._transition.when
        old_dst = self._transition.dest
        # Duplicate check: another transition with the same (new_src, dest, when) must not exist
        if any(
            t is not self._transition and t.source == new_src_id and t.dest == old_dst and t.when == old_when
            for t in self._all_transitions
        ):
            QMessageBox.warning(
                self,
                "Duplicate Transition",
                f"A transition from '{new_src_id}' to '{old_dst}' with the same When condition already exists.",
            )
            self._suppress = True
            self._edge_src_combo.setCurrentIndex(self._edge_src_combo.findData(old_src))
            self._suppress = False
            return
        self.structural_property_changed.emit("Rewire Edge")
        self._transition.source = new_src_id
        self.edge_endpoints_changed.emit(old_src, old_when, old_dst, new_src_id, old_dst)
        self._reload_when_combo()
        self.structural_property_applied.emit()

    def _on_edge_dst_changed(self, _text: str) -> None:
        if self._suppress or self._transition is None:
            return
        new_dst_id = self._edge_dst_combo.currentData()
        if not new_dst_id:
            return
        old_dst = self._transition.dest
        if new_dst_id == old_dst:
            return
        old_src = self._transition.source
        old_when = self._transition.when
        # Duplicate check
        if any(
            t is not self._transition and t.source == old_src and t.dest == new_dst_id and t.when == old_when
            for t in self._all_transitions
        ):
            QMessageBox.warning(
                self,
                "Duplicate Transition",
                f"A transition from '{old_src}' to '{new_dst_id}' with the same When condition already exists.",
            )
            self._suppress = True
            self._edge_dst_combo.setCurrentIndex(self._edge_dst_combo.findData(old_dst))
            self._suppress = False
            return
        self.structural_property_changed.emit("Rewire Edge")
        self._transition.dest = new_dst_id
        self.edge_endpoints_changed.emit(old_src, old_when, old_dst, old_src, new_dst_id)
        self.structural_property_applied.emit()

    def _on_flag_conditions_changed(self) -> None:
        if self._suppress or self._transition is None:
            return
        flag_groups, flag_groups_op = self._flag_conditions_widget.get_flag_groups()
        self.edit_starting.emit("Edit transition")
        self._transition.flag_groups = flag_groups
        self._transition.flag_groups_op = flag_groups_op
        # Clear legacy flat fields — the grouped format takes precedence in to_machine_config()
        self._transition.flags = []
        self.properties_changed.emit()

    # ------------------------------------------------------------------
    # Wiki import
    # ------------------------------------------------------------------

    def _on_wiki_import(self) -> None:
        from bartlebot.gui.wiki_import_dialog import WikiImportDialog

        dialog = WikiImportDialog(parent=self.window())
        if dialog.exec():
            self.apply_wiki_import(dialog.selected_entries(), dialog.selected_page())

    def apply_wiki_import(self, entries: list, page: str | None) -> None:
        """Add imported drops and record the source page on the shown handle_combat node."""
        if entries:
            self.wiki_import_completed.emit(entries)
        # Record where they came from on the node that imported them. The panel
        # is only shown for handle_combat, so this is the fight whose boss page
        # was just fetched — the association the import flow already implied and
        # previously discarded.
        if not page:
            return
        self._param_form.set_param_value(WIKI_PAGE_PARAM, page)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def set_preferred_player_getter(self, fn: Callable[[], int]) -> None:
        """Pass a callable naming the player index Ctrl+R records from."""
        self._preferred_player = fn

    def toggle_record(self) -> None:
        """Ctrl+R: start or cancel recording the shown node.

        A key has no menu to open, so it records from the preferred player -- the game
        window last in focus, the rule Bar A's buttons follow -- rather than asking.
        """
        if recording.is_recording():
            self._cancel_record()
            return
        if not self._shows_recordable_node():
            return
        clients = self._get_clients()
        if not clients:
            self._set_record_status("No player connected.")
            return
        index = self._preferred_player()
        self.start_record(clients[index if 0 <= index < len(clients) else 0])

    def start_record(self, client) -> bool:
        """Record the shown node from *client*.

        False, and nothing started, when the shown node has no recorder or another
        recording is already live -- speed-authoring (#85) says so rather than cancelling it.
        """
        if recording.is_recording() or not self._shows_recordable_node():
            return False
        clients = self._get_clients()
        self._recording_player = clients.index(client) + 1 if client in clients else None
        if self._params_card.is_collapsed:
            # Opened to watch the fields fill; not a fold the reader chose, so nothing is remembered.
            self._params_card.set_collapsed(False)
        asyncio.ensure_future(
            recording.run_recording(
                self._state.node_type,
                client,
                self._param_form,
                self._set_record_status,
                self._record_btn.set_label,
                self._try_set_label,
                player=clients.index(client) + 1 if client in clients else None,
            )
        )
        return True

    def _shows_recordable_node(self) -> bool:
        return self._state is not None and self._state.node_type in recording.RECORDABLE_NODES

    def _cancel_record(self) -> None:
        recording.cancel_active_recording()
        self._record_btn.set_label(IDLE_TEXT)
        self._set_record_status("")

    def _set_record_status(self, text: str) -> None:
        """What the recorder says, at the top of the Parameters card; what Record fills while it says nothing.

        A line always on screen for a recordable node, so starting a recording moves
        nothing under the pointer.
        """
        if not text:
            self._recording_player = None
            self._refresh_record_hint()
            return
        player = self._recording_player
        self._record_status_lbl.setText(f"Player {player} · {text}" if player else text)

    def _refresh_record_hint(self) -> None:
        if self._state is None or self._state.node_type not in recording.RECORDABLE_NODES:
            self._record_status_lbl.setText("")
            return
        self._record_status_lbl.setText(
            recording.recording_hint(self._state.node_type, self._param_form.schema, self._param_form.get_params())
        )

    def _on_record_clicked(self) -> None:
        if recording.is_recording():
            self._cancel_record()
            return
        if not self._shows_recordable_node():
            return
        clients = self._get_clients()
        if not clients:
            self._set_record_status("No player connected.")
            return
        pick_player(self._record_btn, len(clients), "Record", self._record_from_player)

    def _record_from_player(self, index: int) -> None:
        clients = self._get_clients()
        if index < len(clients):
            self.start_record(clients[index])

    def _try_set_label(self, label: str) -> None:
        """Auto-populate the node label if it is empty or one an earlier recording wrote.

        A label the author typed is never replaced.
        """
        if self._state is None:
            return
        current = (self._state.label or "").strip()
        if current == label or (current and not recording.is_recorded_label(current)):
            return
        self.edit_starting.emit("Edit params")
        self._state.label = label
        self._suppress = True
        self._node_label_edit.setText(label)
        self._suppress = False
        self.properties_changed.emit()
        # The field never had focus, so no focus-out will draw the new label on the canvas.
        self.label_committed.emit()

    # ------------------------------------------------------------------
    # Connections card: routes by return value, then what comes in
    # ------------------------------------------------------------------

    def _rebuild_connections(self, state_id: str) -> None:
        """Rebuild the Connections card for the selected node.

        Returns were a section of their own and Connections another, and neither said
        where an unrouted FAILURE goes -- to the recovery state, with no edge drawn
        (``docs/RETURN_ROUTING.md``). One card now lists each return with its route,
        the undrawn ones included, and each return's description behind its row's
        ``?`` (#142). ``param_cards.outgoing_routes`` decides the routes, by the
        engine's rules.
        """
        while self._conn_body_layout.count():
            item = self._conn_body_layout.takeAt(0)
            if item.widget():
                discard_widget(item.widget())

        state = self._state if self._state is not None and self._state.id == state_id else None
        all_t = self._all_transitions_for_node
        state_map = {s.id: (s.label or s.id) for s in self._all_states_for_node}
        spec = NODE_SPECS.get(state.node_type) if state is not None else None
        returns = [r.name for r in spec.returns] if spec and spec.returns else []
        descriptions = {r.name: r.description for r in spec.returns} if spec and spec.returns else {}
        routes = (
            param_cards.outgoing_routes(
                state,
                returns,
                all_t,
                recovery_state=self._recovery_id,
                state_ids=state_map,
            )
            if state is not None
            else []
        )
        incoming = [t for t in all_t if t.dest == state_id and t.source != state_id]

        if not routes and not incoming:
            placeholder = QLabel("No connections")
            placeholder.setStyleSheet(f"color: {THEME.text_muted}; font-size: {THEME.font_small_px}px;")
            self._conn_body_layout.addWidget(placeholder)
            self._conn_card.set_summary(param_cards.routes_summary([], state_map))
            return

        if routes:
            self._conn_body_layout.addWidget(self._conn_heading("Outgoing, by return"))
            for route in routes:
                self._conn_body_layout.addWidget(self._make_route_row(route, state_map, descriptions))
        if incoming:
            self._conn_body_layout.addWidget(self._conn_heading("Incoming", spaced=bool(routes)))
            for t in incoming:
                row = self._make_conn_row(
                    t,
                    badge_text=None,
                    chip_text=t.when or "always",
                    node_label=state_map.get(t.source, t.source),
                    node_id=t.source,
                    is_outgoing=False,
                )
                # Behind an empty gutter, so its chip lines up with the routes' above.
                revert, help_btn = RevertButton(), HelpToggle()
                self._conn_body_layout.addWidget(build_gutter_row(OverrideBar(), revert, help_btn, row))
                keep_slot_when_hidden(revert)
                keep_slot_when_hidden(help_btn)
        self._conn_card.set_summary(param_cards.routes_summary(routes, state_map))

    @staticmethod
    def _conn_heading(text: str, *, spaced: bool = False) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {THEME.text_muted}; font-size: {THEME.font_subtext_px}px; font-weight: 600;"
            f"{' margin-top: 4px;' if spaced else ''}"
        )
        return lbl

    def _make_route_row(self, route, state_map: dict[str, str], descriptions: dict[str, str]) -> QWidget:
        """One outgoing route: its priority, its return, and where it goes -- the undrawn ones say so."""
        if route.kind == param_cards.EDGE:
            body = self._make_conn_row(
                route.transition,
                badge_text=str(route.priority),
                chip_text=route.when or "always",
                node_label=state_map.get(route.dest, route.dest),
                node_id=route.dest,
                is_outgoing=True,
            )
        else:
            body = self._make_conn_row(
                None,
                badge_text="",
                chip_text=route.when or "always",
                node_label=state_map.get(route.dest, route.dest) if route.dest else "",
                node_id=route.dest,
                is_outgoing=True,
                note=self._route_note(route),
            )
        prose = descriptions.get(route.when or "", "")
        col_w = QWidget()
        col = QVBoxLayout(col_w)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)
        col.addWidget(body)
        description = None
        if prose:
            description = ThemedTextLabel(prose, color="text_muted")
            description.setWordWrap(True)
            col.addWidget(description)
        help_btn = HelpToggle()
        help_btn.setToolTip("Show what this return means")
        revert = RevertButton()
        outer = build_gutter_row(OverrideBar(), revert, help_btn, col_w)
        # The Reset slot is kept, so these rows' ? lines up with the params' above.
        keep_slot_when_hidden(revert)
        if description is None:
            keep_slot_when_hidden(help_btn)
        else:
            shown = self._param_form.descriptions_visible
            help_btn.setChecked(shown)
            description.setVisible(shown)
            help_btn.toggled.connect(description.setVisible)
        return outer

    @staticmethod
    def _route_note(route) -> tuple[str, str]:
        """The chip a route that is not an edge wears: its text and its color token."""
        if route.kind == param_cards.RECOVERY:
            return "recovery · not drawn", "warning"
        if route.kind == param_cards.STOP:
            return (f"stops the bot: {route.reason}" if route.reason else "stops the bot"), "error"
        if route.kind == param_cards.END:
            return "ends the run", "text_muted"
        return "not routed", "warning"

    def _make_conn_row(
        self,
        transition: TransitionModel | None,
        badge_text: str | None,
        chip_text: str,
        node_label: str,
        node_id: str | None,
        is_outgoing: bool,
        note: tuple[str, str] | None = None,
    ) -> QWidget:
        """Build a single connection row widget: a route, or an edge coming in."""
        row = QWidget()
        row.setStyleSheet(f"QWidget {{ border-radius: 4px; }}QWidget:hover {{ background: {THEME.surface}; }}")
        if transition is not None:
            row.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        # Priority badge (outgoing only): the order the engine tries edges in.
        if badge_text is not None:
            badge = QLabel(badge_text)
            badge.setFixedWidth(14)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setToolTip("Tried in this order: conditional edges first")
            badge.setStyleSheet(f"color: {THEME.text_muted}; font-size: {THEME.font_micro_px}px; font-weight: 600;")
            layout.addWidget(badge)

        # Condition chip
        chip_text_upper = chip_text.upper()
        if chip_text_upper in ("SUCCESS", "TRUE"):
            chip_bg = THEME.chip_success_bg
            chip_fg = "#ffffff"
        elif chip_text_upper in ("FAILURE", "FALSE"):
            chip_bg = THEME.chip_failure_bg
            chip_fg = "#ffffff"
        elif chip_text == "always":
            chip_bg = THEME.surface
            chip_fg = THEME.text_muted
        else:
            chip_bg = THEME.chip_neutral_bg
            chip_fg = "#ffffff"
        chip = QLabel(chip_text)
        chip.setStyleSheet(
            f"background: {chip_bg}; color: {chip_fg}; border-radius: 3px;"
            f" font-size: {THEME.font_subtext_px}px; font-weight: 600; padding: 1px 5px;"
        )
        arrow = QLabel("→" if is_outgoing else "←")
        arrow.setStyleSheet(f"color: {THEME.text_muted}; font-size: {THEME.font_small_px}px;")

        # Node name link. Elided: a long label beside a route's note would otherwise
        # widen the whole page past the sidebar.
        name_lbl = ElidedLabel(node_label)
        name_lbl.setStyleSheet(f"color: {THEME.accent}; font-size: {THEME.font_small_px}px;")
        name_lbl.setFont(THEME.ui_font(THEME.font_small_px))
        name_lbl.set_text_color(THEME.accent)
        name_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        name_lbl.setToolTip(f"Navigate to: {node_label}")

        layout.addWidget(chip)
        if node_label:
            layout.addWidget(arrow)
            layout.addWidget(name_lbl, 1 if note is None else 0)
        if note is not None:
            text, token = note
            color = getattr(THEME, token)
            note_lbl = QLabel(text)
            note_lbl.setStyleSheet(
                f"color: {color}; border: 1px solid {color}; border-radius: 3px;"
                f" font-size: {THEME.font_subtext_px}px; padding: 0px 5px;"
            )
            if text.startswith("recovery"):
                note_lbl.setToolTip("No edge is drawn: an unrouted FAILURE goes to the recovery state")
            layout.addWidget(note_lbl)
            layout.addStretch(1)

        # Clicking the node name navigates to that state
        def _on_name_clicked(ev, nid=node_id):
            ev.accept()
            if nid:
                self.select_state_requested.emit(nid)

        name_lbl.mousePressEvent = _on_name_clicked  # type: ignore[method-assign]

        # Clicking anywhere else on the row selects the edge
        if transition is not None:

            def _on_row_pressed(ev, t=transition):
                self.select_edge_requested.emit(t)

            row.mousePressEvent = _on_row_pressed  # type: ignore[method-assign]

        return row
