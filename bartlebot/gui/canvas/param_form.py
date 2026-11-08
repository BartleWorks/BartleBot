"""The schema-driven row builder for a node's params, and the field widgets it draws.

``DynamicParamForm`` draws every param row the properties panel shows: the node page's
Parameters card, a subgraph node's inputs (``call_page.py``) and a definition's input
editor (``signature_page.py``). ``input_param_field`` and ``instance_key_field`` turn a
subgraph input into the ``ParamField`` such a row is drawn from.
"""

import asyncio
import json
import logging
import re
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from bartleby.qt.menu_utils import popup_menu
from bartleby.qt.sidebar.option_gutter import (
    HelpToggle,
    OverrideBar,
    RevertButton,
    build_gutter_row,
    keep_slot_when_hidden,
)
from bartleby.qt.theming.icon_utils import svg_icon, svg_pixmap
from bartleby.qt.theming.theme import THEME
from bartleby.qt.theming.themed import ThemedMixin, set_style_sheet
from bartleby.qt.widgets.buttons.action_button import ActionButton
from bartleby.qt.widgets.labels.themed_label import ThemedTextLabel
from bartleby.qt.widgets.layout.discard import discard_widget
from bartleby.qt.widgets.pills.pill import NeutralChip
from PyQt6.QtCore import QEvent, QLocale, QRectF, QSize, QStringListModel, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QDoubleValidator,
    QFont,
    QFontMetrics,
    QKeySequence,
    QPainter,
    QPen,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QCompleter,
    QDoubleSpinBox,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot import variables
from bartlebot.bot.schema import (
    VariableModel,
)
from bartlebot.content.subgraphs.subgraphs import MAX_DEPTH, InstanceKey, binding_type, nested_key, promoted_param_type
from bartlebot.content.templates.setup_steps import value_empty
from bartlebot.game import zone_names
from bartlebot.game.wiki_pages import page_title
from bartlebot.gui.canvas.variable_rows import AssignmentsField, parse_rows
from bartlebot.gui.widgets.inputs.list_text import format_list_text, parse_list_text
from bartlebot.gui.widgets.inputs.ratio_spin_box import RatioSpinBox
from bartlebot.gui.widgets.pickers.player_list_widget import PlayerListWidget, PlayerSlotWidget
from bartlebot.gui.widgets.pickers.player_picker import pick_player, picker_label
from bartlebot.gui.widgets.pickers.scales_picker import ScalesButton
from bartlebot.gui.widgets.pickers.zone_picker import NavZoneButton, is_destination
from bartlebot.nodes import zone_navigation
from bartlebot.nodes.param_schema import ParamField
from bartlebot.nodes.registry import (
    NODE_PARAM_SCHEMAS,
)
from bartlebot.settings_manager import SettingsManager

logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Sentinel QLineEdit subclasses  (identified by get_params())
# ---------------------------------------------------------------------------


class _WikiPageLineEdit(QLineEdit):
    """Line edit for a 'wiki_page' param.

    Stores a bare page title. A pasted URL is normalized to its title on edit, so
    whichever form the user has to hand produces the same thing on disk — and a bot
    file never carries a host of its own. See bartlebot/game/wiki_pages.py.
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


class _HeldParam(QLabel):
    """A param the form shows but cannot edit, handed back by get_params() exactly as it came.

    For a type this build does not know, or a set_variables node's ``assignments`` that
    are not rows a hand edit could have left. Saving the form must never drop a value
    just because it could not be drawn.
    """

    def __init__(self, value: Any, text: str):
        super().__init__(text)
        self.value = value
        self.setWordWrap(True)


def _held_text(field_type: str, val: Any) -> str:
    """What a :class:`_HeldParam` says about the value it holds."""
    if field_type != "assignments":
        return f"(unsupported type: {field_type})"
    try:
        rows = variables.parse_assignments(val)
    except ValueError:
        return "(not readable: fix the rows in the bot file)"
    return "\n".join(variables.describe_assignment(r) for r in rows)


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

    Records go under the Sync in Zone node's logger, as they would in a run. Safety Delay is the
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
    runs the same navigation the Sync in Zone node runs, so a bot can be authored from wherever the
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
            logger.exception("Sync in Zone from the properties panel failed")
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
# DynamicParamForm
# ---------------------------------------------------------------------------


class DynamicParamForm(ThemedMixin, QWidget):
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
        self._get_variables: Callable[[], list[VariableModel]] = lambda: []
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_focus_changed)

    def set_default_scales_getter(self, fn: Callable[[], Any]) -> None:
        """Pass a callable returning the Scales an unset 'scales' param means in the open bot."""
        self._get_default_scales = fn

    def set_variables_getter(self, fn: Callable[[], list[VariableModel]]) -> None:
        """Pass a callable returning the open bot's variable declarations, for an 'assignments' param."""
        self._get_variables = fn

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
            if isinstance(w, _HeldParam):
                if w.value is not None:
                    result[key] = w.value
            elif isinstance(w, AssignmentsField):
                result[key] = w.value()
            elif isinstance(w, _XyzField):
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
        if field_type == "assignments" and (rows := parse_rows(val)) is not None:
            w = AssignmentsField(lambda: self._get_variables())
            w.set_rows(rows)
            if help_text:
                w.setToolTip(help_text)
            w.changed.connect(self._emit_changed)
            return w
        w = _HeldParam(val, _held_text(field_type, val))
        if help_text:
            w.setToolTip(help_text)
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

    def set_bool(self, key: str, value: bool) -> None:
        """Programmatically set a 'bool' field by key."""
        widget = self._widgets.get(key)
        if isinstance(widget, QCheckBox):
            widget.setChecked(value)

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
        """What the zone, xyz, player list and bool fields named in *keys* hold now, for ``restore``.

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
            elif isinstance(widget, QCheckBox):
                saved[key] = widget.isChecked()
        return saved

    def restore(self, saved: dict[str, Any]) -> None:
        """Put back what ``snapshot`` read."""
        for key, value in saved.items():
            widget = self._widgets.get(key)
            if isinstance(widget, _XyzField):
                widget.set_texts(value)
            elif isinstance(widget, PlayerListWidget):
                widget.set_value(value)
            elif isinstance(widget, QCheckBox):
                widget.setChecked(value)
            else:
                self.set_str(key, value)
