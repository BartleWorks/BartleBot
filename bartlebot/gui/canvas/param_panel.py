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
from typing import Any

from PyQt6.QtCore import QSize, QStringListModel, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPen, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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

from bartlebot.bot.schema import FlagCondition, FlagGroup, RegionModel, StateModel, SubgraphDefinition, TransitionModel
from bartlebot.gui.canvas import recording
from bartlebot.gui.icon_utils import svg_icon
from bartlebot.gui.menu_utils import popup_menu
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin
from bartlebot.gui.widgets.action_button import ActionButton, ActionStyle
from bartlebot.gui.widgets.icon_button import ThemedIconButton
from bartlebot.gui.widgets.list_text import format_list_text, parse_list_text
from bartlebot.gui.widgets.player_list_widget import PlayerListWidget, PlayerSlotWidget
from bartlebot.gui.widgets.rule_heading import RuleHeading
from bartlebot.gui.widgets.themed_label import ThemedTextLabel
from bartlebot.gui.widgets.zone_picker import NavZoneButton
from bartlebot.nodes import zone_navigation
from bartlebot.nodes.param_schema import ParamField
from bartlebot.nodes.registry import NODE_CATEGORIES, NODE_PARAM_SCHEMAS, NODE_REGISTRY, NODE_SPECS
from bartlebot.settings_manager import SettingsManager
from bartlebot.store_roots import Origin
from bartlebot.subgraphs import RegionStatus, member_id
from bartlebot.wiki_pages import WIKI_PAGE_PARAM, page_title


def input_param_field(defn: SubgraphDefinition, inp) -> ParamField:
    """The ``ParamField`` the region page builds an input's widget from.

    The input declares key, type, label, help and default; what it does not
    carry -- the dropdown choices of a ``str_choice``, a placeholder, a numeric
    range -- comes from the first bound member param of the same type, so a
    World input gets the same dropdown the node has instead of an empty combo.
    """
    by_id = {s.id: s for s in defn.states}
    bound = [
        f
        for b in inp.bindings
        if (st := by_id.get(b.state)) is not None
        for f in NODE_PARAM_SCHEMAS.get(st.node_type, [])
        if f.key == b.param and f.type == inp.type
    ]
    first = bound[0] if bound else None
    return ParamField(
        key=inp.name,
        type=inp.type,
        label=inp.label or inp.name,
        help=inp.help or "",
        default=inp.default,
        choices=list(first.choices) if first is not None else [],
        placeholder=first.placeholder if first is not None else "",
        minimum=first.minimum if first is not None else None,
        maximum=first.maximum if first is not None else None,
    )


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentinel QLineEdit subclasses  (identified by get_params())
# ---------------------------------------------------------------------------


class _WikiPageLineEdit(QLineEdit):
    """Line edit for a 'wiki_page' param.

    Stores a bare page title. A pasted URL is normalised to its title on edit, so
    whichever form the user has to hand produces the same thing on disk — and a bot
    file never carries a host of its own. See bartlebot/wiki_pages.py.
    """

    def normalise(self) -> None:
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
    is identified by get_params() to deserialise back to list[str].
    """


# ---------------------------------------------------------------------------
# Clipboard XYZ parsing
# ---------------------------------------------------------------------------

_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_XYZ_CALL_RE = re.compile(
    rf"XYZ\s*\(\s*({_NUM})\s*,\s*({_NUM})\s*,\s*({_NUM})\s*\)",
    re.IGNORECASE,
)


def parse_xyz_text(text: str) -> dict[str, float]:
    """Parse clipboard text into whichever of the x/y/z axes it defines.

    Two formats are accepted:

    * ``XYZ(x, y, z)`` — the form used by Deimos scripts, so a line lifted
      straight out of one (``mass tp XYZ(1, 2, 3)``) pastes as-is.  Only the
      first such call on the line is read.
    * a JSON object with any of ``x``/``y``/``z`` — what the Copy button
      writes.  Missing or non-numeric axes are simply left out, so a partial
      object updates only the axes it names.

    Returns an empty dict when nothing parses; the caller leaves the spin
    boxes untouched in that case.
    """
    text = text.strip()
    match = _XYZ_CALL_RE.search(text)
    if match:
        return {axis: float(match.group(i)) for i, axis in enumerate(("x", "y", "z"), start=1)}
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, float] = {}
    for axis in ("x", "y", "z"):
        try:
            out[axis] = float(data[axis])
        except (KeyError, TypeError, ValueError):
            continue
    return out


class _FillButton(QPushButton):
    """QPushButton that updates its label on hover to reflect fill options.

    - 0 players connected  → "Fill current"  (no-op click)
    - 1 player connected   → "Fill current"  (direct fill, no menu)
    - 2+ players connected → "Fill current ▾"  (dropdown)
    """

    def __init__(self, get_clients: Callable[[], list], parent=None):
        super().__init__(parent)
        self._get_clients = get_clients
        self._refresh_label()

    def enterEvent(self, event) -> None:  # type: ignore[override]
        self._refresh_label()
        super().enterEvent(event)

    def _refresh_label(self) -> None:
        n = len(self._get_clients())
        self.setText("Fill current" if n == 1 else "Fill current ▾")


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
    """Zone string field with autocomplete + a 'Fill from Player' dropdown button.

    Wraps a ``_ZoneLineEdit`` and adds a compact button row so users can
    populate the field from a connected player's current zone without
    switching to the sidebar.
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

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        self._edit = _ZoneLineEdit(get_suggestions)
        outer.addWidget(self._edit)

        fill_btn = _FillButton(get_clients)
        fill_btn.setFixedHeight(22)
        fill_btn.setToolTip("Fill from a connected player's current zone")
        fill_btn.setStyleSheet("QPushButton { font-size: 11px; padding: 1px 2px; }")
        fill_btn.clicked.connect(lambda _: self._show_fill_menu(fill_btn))
        outer.addWidget(fill_btn)

        hint = QLabel("Glob patterns supported: * matches any sequence, ? matches one character")
        hint.setStyleSheet("font-size: 10px; color: gray;")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        self._edit.textChanged.connect(self.textChanged)

    # ------------------------------------------------------------------
    # Public interface (matches QLineEdit enough for form integration)
    # ------------------------------------------------------------------

    def text(self) -> str:
        return self._edit.text()

    def setText(self, text: str) -> None:
        self._edit.setText(text)

    # ------------------------------------------------------------------
    # Fill from player
    # ------------------------------------------------------------------

    def _show_fill_menu(self, btn: QPushButton) -> None:
        clients = self._get_clients()
        if not clients:
            return
        if len(clients) == 1:
            asyncio.ensure_future(self._fill_from_player(0))
            return
        menu = popup_menu(btn)
        for i in range(len(clients)):
            action = menu.addAction(f"Fill from Player {i + 1}")
            action.triggered.connect(lambda _checked, idx=i: asyncio.ensure_future(self._fill_from_player(idx)))
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    async def _fill_from_player(self, idx: int) -> None:
        clients = self._get_clients()
        if idx >= len(clients):
            return
        try:
            zone = await clients[idx].zone_name()
            if zone:
                self.setText(zone)
        except Exception:
            pass


class _AuthoringMachine:
    """The slice of BotMachine zone navigation reads, for a trip started from the properties panel.

    Records go under the Go To Zone node's logger, as they would in a run. Safety Delay is the
    user's app setting: there is no bot run here to hold an override or a declaration.
    """

    def __init__(self) -> None:
        # None until navigate() surveys who can port to whom; see BotMachine.port_map.
        self.port_map = None

    def log(self, msg: str, level: int = logging.INFO) -> None:
        logging.getLogger("bartlebot.node.go_to_zone").log(level, msg)

    def get_option(self, name: str, default=None):
        if name == "Safety Delay":
            return SettingsManager.instance().default_safety_delay
        return default


# Held so a trip started from a panel that is rebuilt mid-trip is not garbage-collected.
_NAV_TASKS: set[asyncio.Task] = set()


class _NavZoneField(QWidget):
    """A 'nav_zone' param: the zone picker, then Fill current and Go side by side beneath it.

    Fill sets the zone from a connected player's current zone, as the zone and xyz fields do. Go
    runs the same navigation the Go To Zone node runs, so a bot can be authored from wherever the
    players happen to be without starting a run.
    """

    changed = pyqtSignal(str)

    def __init__(self, get_suggested: Callable[[], list[str]], get_clients: Callable[[], list], parent=None):
        super().__init__(parent)
        self._get_clients = get_clients
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        self.picker = NavZoneButton(get_suggested)
        self.picker.changed.connect(self.changed)
        outer.addWidget(self.picker)

        btns_row = QHBoxLayout()
        btns_row.setContentsMargins(0, 0, 0, 0)
        btns_row.setSpacing(4)
        self.fill_btn = ActionButton("download", "Fill current")
        self.fill_btn.setToolTip("Fill from a connected player's current zone")
        self.fill_btn.clicked.connect(self._show_fill_menu)
        btns_row.addWidget(self.fill_btn, 1)
        self.go_btn = ActionButton("play", "Go to zone")
        self.go_btn.setToolTip("Take all connected players to this zone now")
        self.go_btn.clicked.connect(self._on_go)
        btns_row.addWidget(self.go_btn, 1)
        outer.addLayout(btns_row)

    def value(self) -> str:
        return self.picker.value()

    def set_value(self, zone: str) -> None:
        self.picker.set_value(zone)

    def _show_fill_menu(self) -> None:
        clients = self._get_clients()
        if not clients:
            return
        if len(clients) == 1:
            asyncio.ensure_future(self._fill_from_player(0))
            return
        menu = popup_menu(self.fill_btn)
        for i in range(len(clients)):
            action = menu.addAction(f"Fill from Player {i + 1}")
            action.triggered.connect(lambda _checked, idx=i: asyncio.ensure_future(self._fill_from_player(idx)))
        menu.exec(self.fill_btn.mapToGlobal(self.fill_btn.rect().bottomLeft()))

    async def _fill_from_player(self, idx: int) -> None:
        clients = self._get_clients()
        if idx >= len(clients):
            return
        try:
            zone = await clients[idx].zone_name()
        except Exception:
            return
        try:
            if zone and zone != self.value():
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
            logger.exception("Go To Zone from the properties panel failed")
        finally:
            try:
                self.go_btn.setEnabled(True)
            except RuntimeError:
                pass  # the panel was rebuilt while the players travelled


class _IntChoiceComboBox(QComboBox):
    """QComboBox variant used for 'int_choice' param fields.

    Displays a fixed dropdown of integer options defined by ``ParamField.choices``.
    ``get_params()`` returns the selected value as an int.
    """


class _StrChoiceComboBox(QComboBox):
    """QComboBox variant used for 'str_choice' param fields.

    Displays a fixed dropdown of string options defined by ``ParamField.choices``. Each
    choice may be a bare string, or a ``(value, label)`` pair when the value stored in the
    bot file is not what the user should read -- go_to_world stores the world's zone
    prefix ("DragonSpire") and shows its display name ("Dragonspyre").

    ``get_params()`` returns the stored value, never the label.
    """

    def add_choices(self, choices: list) -> None:
        for choice in choices or []:
            value, label = choice if isinstance(choice, (tuple, list)) else (choice, choice)
            self.addItem(str(label), str(value))

    def set_value(self, value) -> None:
        index = self.findData(str(value) if value is not None else "")
        self.setCurrentIndex(max(index, 0))

    def get_value(self) -> str:
        return self.currentData() or ""


# ---------------------------------------------------------------------------
# _DynamicParamForm
# ---------------------------------------------------------------------------


class _DynamicParamForm(ThemedMixin, QWidget):
    changed = pyqtSignal()
    # (param key, active) when the user flips a row's Override toggle on a locked form.
    # The form only reports; the canvas records the override and reloads the form.
    override_toggled = pyqtSignal(str, bool)
    # (param key) when the user asks to expose a row as a subgraph input, on a form
    # shown for a member of the region being edited.
    expose_requested = pyqtSignal(str)
    # (param key, free) when the author flips a row's lock while editing a subgraph:
    # free means consumers set it on the node; locked means the subgraph owns it.
    free_toggled = pyqtSignal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._form = QVBoxLayout(self)
        self._form.setContentsMargins(6, 6, 6, 6)
        self._form.setSpacing(4)
        self._form.addStretch()
        self._widgets: dict[str, Any] = {}
        self._required_widgets: dict[str, Any] = {}
        # The per-row toggles; their icons are THEME-tinted. Override toggles sit on
        # a locked form (checked = overridden), free toggles on a form shown while
        # editing a subgraph (checked = free), Expose buttons beside those.
        self._override_btns: list[QToolButton] = []
        self._free_btns: list[QToolButton] = []
        self._expose_btns: list[QToolButton] = []
        # Every label load_schema builds that carries a THEME colour, cleared and
        # refilled alongside _widgets. Field labels keep their ParamField because the
        # required marker is a colour baked into *rich text* rather than a stylesheet,
        # so restyling one means rebuilding its text.
        self._field_labels: list[tuple[QLabel, Any, bool]] = []
        self._muted_labels: list[QLabel] = []
        self._suppress = False
        self._get_clients: Callable[[], list] = lambda: []
        self._get_zone_suggestions: Callable[[], list[str]] = lambda: []

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
        locked: bool = False,
        overrides: frozenset = frozenset(),
        exposable: bool = False,
        free: frozenset = frozenset(),
        bound: dict[str, str] | None = None,
    ) -> None:
        """Rebuild the form for *schema* showing *params*.

        With *locked*, a field is disabled unless its key is in *free* (the
        author left it for the consumer: enabled, no toggle) or *overrides*; the
        rest carry a lock toggle: open unlocks that one field for this instance
        as an override, closed resets it to the subgraph's value. With
        *exposable* (a member of the subgraph being edited) each row carries the
        author's lock toggle (checked = free) and an Expose button; a row *bound*
        to an input (key -> input name) has neither, since the input owns it.
        The buttons wrap the row rather than the widget ``_make_widget`` returns,
        so every widget type gets them without knowing.
        """
        self._suppress = True
        # Remove everything except the trailing stretch.
        while self._form.count() > 1:
            item = self._form.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._widgets.clear()
        self._required_widgets.clear()
        self._field_labels.clear()
        self._muted_labels.clear()
        self._override_btns.clear()
        self._free_btns.clear()
        self._expose_btns.clear()
        bound = bound or {}
        editable = {field.key: (not locked) or field.key in overrides or field.key in free for field in schema}
        insert_idx = 0
        for field in schema:
            label_w = QLabel()
            label_w.setTextFormat(Qt.TextFormat.RichText)
            self._field_labels.append((label_w, field, insert_idx > 0))
            self._style_field_label(label_w, field, insert_idx > 0)
            if field.help:
                label_w.setToolTip(field.help)
            val = params.get(field.key, field.default)
            widget = self._make_widget(
                field.type,
                val,
                field.help,
                field.key,
                getattr(field, "choices", []),
                getattr(field, "placeholder", ""),
                minimum=getattr(field, "minimum", None),
                maximum=getattr(field, "maximum", None),
            )
            self._widgets[field.key] = widget
            if field.required:
                self._required_widgets[field.key] = widget
            display_w = widget if not isinstance(widget, tuple) else widget[3]
            row_btns: list[QToolButton] = []
            if locked:
                if field.key not in free:
                    row_btns.append(self._make_override_button(field.key, editable[field.key]))
                display_w.setEnabled(editable[field.key])
            elif exposable:
                input_name = bound.get(field.key)
                if input_name is None:
                    row_btns.append(self._make_free_button(field.key, field.key in free))
                    row_btns.append(self._make_expose_button(field.key))
                else:
                    row_btns.append(self._make_expose_button(field.key, bound_to=input_name))
            if field.type == "bool":
                container = QWidget()
                row = QHBoxLayout(container)
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(8)
                row.addWidget(label_w)
                row.addStretch()
                row.addWidget(display_w)
                for row_btn in row_btns:
                    row.addWidget(row_btn)
                self._form.insertWidget(insert_idx, container)
                insert_idx += 1
            else:
                if row_btns:
                    head = QWidget()
                    head_row = QHBoxLayout(head)
                    head_row.setContentsMargins(0, 0, 0, 0)
                    head_row.setSpacing(8)
                    head_row.addWidget(label_w)
                    head_row.addStretch()
                    for row_btn in row_btns:
                        head_row.addWidget(row_btn)
                    self._form.insertWidget(insert_idx, head)
                else:
                    self._form.insertWidget(insert_idx, label_w)
                self._form.insertWidget(insert_idx + 1, display_w)
                insert_idx += 2
            if field.default_hint and not field.required:
                hint_lbl = QLabel(f"Default: {field.default_hint}")
                self._muted_labels.append(hint_lbl)
                hint_lbl.setStyleSheet(f"color: {THEME.text_muted};")
                hint_lbl.setWordWrap(True)
                self._form.insertWidget(insert_idx, hint_lbl)
                insert_idx += 1
        if not schema:
            placeholder = QLabel("This node type has no parameters.")
            self._muted_labels.append(placeholder)
            placeholder.setStyleSheet(f"color: {THEME.text_muted};")
            self._form.insertWidget(0, placeholder)
        # Wire disabled_by: disable the target widget when the controller is "active".
        # For QCheckBox controllers: active = checked.
        # For QLineEdit controllers: active = non-empty text.
        for field in schema:
            if field.disabled_by and field.disabled_by in self._widgets:
                controller = self._widgets[field.disabled_by]
                controlled = self._widgets[field.key]
                display_w = controlled if not isinstance(controlled, tuple) else controlled[3]
                ok = editable[field.key]
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
                controlled = self._widgets[field.key]
                display_w = controlled if not isinstance(controlled, tuple) else controlled[3]
                ok = editable[field.key]
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

    @staticmethod
    def _style_override_button(btn: QToolButton, overridden: bool) -> None:
        btn.setIcon(svg_icon("unlock" if overridden else "lock", 14))
        btn.setToolTip(
            "Overridden for this instance: this value no longer follows the subgraph. Click to reset it."
            if overridden
            else "The subgraph owns this value. Click to unlock it and override it for this instance only."
        )

    @staticmethod
    def _style_free_button(btn: QToolButton, free: bool) -> None:
        btn.setIcon(svg_icon("unlock" if free else "lock", 14))
        btn.setToolTip(
            "Free: whoever uses this subgraph sets this value on the node. Click to lock it."
            if free
            else "Locked: the subgraph owns this value; users can only override it. Click to free it."
        )

    def _make_override_button(self, key: str, active: bool) -> QToolButton:
        btn = QToolButton()
        btn.setCheckable(True)
        btn.setChecked(active)
        btn.setAutoRaise(True)
        btn.setIconSize(QSize(14, 14))
        btn.setProperty("param_key", key)
        btn.toggled.connect(lambda checked, k=key: self._on_override_toggled(k, checked))
        self._override_btns.append(btn)
        return btn

    def _on_override_toggled(self, key: str, checked: bool) -> None:
        if self._suppress:
            return
        w = self._widgets.get(key)
        display_w = w if not isinstance(w, tuple) else w[3]
        if display_w is not None:
            display_w.setEnabled(checked)
        btn = self.override_button(key)
        if btn is not None:
            self._style_override_button(btn, checked)
        self.override_toggled.emit(key, checked)

    def _make_free_button(self, key: str, free: bool) -> QToolButton:
        btn = QToolButton()
        btn.setCheckable(True)
        btn.setChecked(free)
        btn.setAutoRaise(True)
        btn.setIconSize(QSize(14, 14))
        btn.setProperty("param_key", key)
        btn.toggled.connect(lambda checked, k=key: self._on_free_toggled(k, checked))
        self._free_btns.append(btn)
        return btn

    def _on_free_toggled(self, key: str, checked: bool) -> None:
        if self._suppress:
            return
        btn = self.free_button(key)
        if btn is not None:
            self._style_free_button(btn, checked)
        self.free_toggled.emit(key, checked)

    def _make_expose_button(self, key: str, *, bound_to: str | None = None) -> QToolButton:
        btn = QToolButton()
        btn.setAutoRaise(True)
        btn.setIconSize(QSize(14, 14))
        btn.setProperty("param_key", key)
        if bound_to is None:
            btn.setToolTip("Expose as a subgraph input\u2026")
            btn.clicked.connect(lambda _=None, k=key: self.expose_requested.emit(k))
        else:
            btn.setEnabled(False)
            btn.setToolTip(f"Set by the input '{bound_to}'. Unbind it on the region page to change that.")
        self._expose_btns.append(btn)
        return btn

    def expose_button(self, key: str) -> QToolButton | None:
        """The Expose button for one row of a form shown while editing a subgraph, or None."""
        return next((b for b in self._expose_btns if b.property("param_key") == key), None)

    def override_button(self, key: str) -> QToolButton | None:
        """The lock toggle for one row of a locked form (checked = overridden), or None."""
        return next((b for b in self._override_btns if b.property("param_key") == key), None)

    def free_button(self, key: str) -> QToolButton | None:
        """The author's lock toggle for one row of a form shown while editing (checked = free), or None."""
        return next((b for b in self._free_btns if b.property("param_key") == key), None)

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
        """Apply or clear a red border on required input widgets based on whether they are empty."""
        for widget in self._required_widgets.values():
            if isinstance(widget, tuple):
                # xyz — cannot be meaningfully empty
                display_w = widget[3]
                empty = False
            elif isinstance(widget, QPlainTextEdit):
                display_w = widget
                empty = not widget.toPlainText().strip()
            elif isinstance(widget, _ZoneWidget):
                display_w = widget
                empty = not widget.text().strip()
            elif isinstance(widget, QLineEdit):
                display_w = widget
                empty = not widget.text().strip()
            elif isinstance(widget, PlayerListWidget):
                display_w = widget
                empty = len(widget.get_value()) == 0
            elif isinstance(widget, PlayerSlotWidget):
                display_w = widget
                empty = widget.get_value() == 0
            else:
                # QSpinBox, QDoubleSpinBox, QCheckBox — always have a value
                display_w = widget
                empty = False
            display_w.setStyleSheet(f"border: 1px solid {THEME.error};" if empty else "")

    @staticmethod
    def _style_field_label(label: QLabel, field: Any, spaced: bool) -> None:
        """Set a field label's text and colour together.

        They cannot be separated: the required marker is a ``<span>`` carrying
        ``THEME.error`` inside the label's own text, so a theme switch has to rewrite
        the text, not just the stylesheet. Nothing in the guard test can see that —
        it looks for setStyleSheet, and this is setText.
        """
        text = field.label
        if field.required:
            text += f" <span style='color:{THEME.error}'>*</span>"
        label.setText(text)
        muted = f"color: {THEME.text_muted}; font-size: 11px; font-weight: 600;"
        label.setStyleSheet(muted + (" margin-top: 8px;" if spaced else ""))

    def apply_theme_styles(self) -> None:
        for label, field, spaced in self._field_labels:
            self._style_field_label(label, field, spaced)
        for label in self._muted_labels:
            label.setStyleSheet(f"color: {THEME.text_muted};")
        for btn in self._override_btns:
            self._style_override_button(btn, btn.isChecked())
        for btn in self._free_btns:
            self._style_free_button(btn, btn.isChecked())
        for btn in self._expose_btns:
            btn.setIcon(svg_icon("attach", 14))
        # The red border on an empty required field is THEME.error, and is applied
        # outside load_schema — re-running the check is what re-derives it.
        self._validate_required_fields()

    def widget(self, key: str):
        """The input widget for one param key, or None.

        For callers that need to inspect or drive a single field — the xyz fields hand
        back their (x, y, z, container) tuple, everything else a plain widget.
        """
        return self._widgets.get(key)

    def get_params(self) -> dict:
        result: dict[str, Any] = {}
        for key, w in self._widgets.items():
            if isinstance(w, tuple):
                x_s, y_s, z_s, _ = w
                result[key] = {"x": x_s.value(), "y": y_s.value(), "z": z_s.value()}
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
            elif isinstance(w, QSpinBox):
                result[key] = w.value()
            elif isinstance(w, QDoubleSpinBox):
                result[key] = w.value()
            elif isinstance(w, _IntChoiceComboBox):
                try:
                    result[key] = int(w.currentText())
                except (TypeError, ValueError):
                    result[key] = 0
            elif isinstance(w, _StrChoiceComboBox):
                result[key] = w.get_value()
            elif isinstance(w, _NavZoneField):
                result[key] = w.value()
            elif isinstance(w, QCheckBox):
                result[key] = w.isChecked()
        return result

    def _make_widget(
        self,
        field_type: str,
        val: Any,
        help_text: str,
        key: str = "",
        choices: list | None = None,
        placeholder: str = "",
        minimum: float | None = None,
        maximum: float | None = None,
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
        if field_type == "str":
            if "zone" in key.lower():
                w: QWidget = _ZoneWidget(self._get_zone_suggestions, self._get_clients)
                w.setText(str(val) if val is not None else "")
                if help_text:
                    w.setToolTip(help_text)
                w.textChanged.connect(self._emit_changed)
                return w
            plain = QLineEdit()
            plain.setText(str(val) if val is not None else "")
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
            w.setPlaceholderText("Creature:Luska Charmbeak")
            if help_text:
                w.setToolTip(help_text)
            w.editingFinished.connect(w.normalise)
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
            xyz = val if isinstance(val, dict) else {}
            outer = QWidget()
            if help_text:
                outer.setToolTip(help_text)
            outer_layout = QVBoxLayout(outer)
            outer_layout.setContentsMargins(0, 0, 0, 0)
            outer_layout.setSpacing(2)
            # Row 1: X/Y/Z spinboxes
            spins_row = QHBoxLayout()
            spins_row.setContentsMargins(0, 0, 0, 0)
            spins_row.setSpacing(4)
            spins = []
            for axis, axis_key in [("X", "x"), ("Y", "y"), ("Z", "z")]:
                lbl = QLabel(axis)
                lbl.setFixedWidth(12)
                spin = QDoubleSpinBox()
                spin.setRange(-999_999.0, 999_999.0)
                spin.setDecimals(2)
                spin.setSingleStep(1.0)
                try:
                    spin.setValue(float(xyz.get(axis_key, 0.0)))
                except (TypeError, ValueError):
                    spin.setValue(0.0)
                spin.valueChanged.connect(self._emit_changed)
                spins_row.addWidget(lbl)
                spins_row.addWidget(spin, 1)
                spins.append(spin)
            outer_layout.addLayout(spins_row)
            # Row 2: full-width segmented action bar — all buttons equal width, uniform height
            btns_row = QHBoxLayout()
            btns_row.setContentsMargins(0, 0, 0, 0)
            btns_row.setSpacing(2)
            _BTN_H = 22
            _btn_style = "QPushButton { font-size: 11px; padding: 1px 2px; }"
            copy_btn = QPushButton("Copy")
            copy_btn.setFixedHeight(_BTN_H)
            copy_btn.setToolTip("Copy XYZ to clipboard as JSON")
            copy_btn.setStyleSheet(_btn_style)
            copy_btn.clicked.connect(lambda _checked, k=key: self._copy_xyz(k))
            btns_row.addWidget(copy_btn, 1)
            paste_btn = QPushButton("Paste")
            paste_btn.setFixedHeight(_BTN_H)
            paste_btn.setToolTip("Paste XYZ from clipboard (JSON or XYZ(x, y, z) format)")
            paste_btn.setStyleSheet(_btn_style)
            paste_btn.clicked.connect(lambda _checked, k=key: self._paste_xyz(k))
            btns_row.addWidget(paste_btn, 1)
            fill_btn = _FillButton(self._get_clients)
            fill_btn.setFixedHeight(_BTN_H)
            fill_btn.setToolTip("Fill from a connected player's live position")
            fill_btn.setStyleSheet(_btn_style)
            fill_btn.clicked.connect(lambda _checked, k=key, b=fill_btn: self._show_fill_menu(k, b))
            btns_row.addWidget(fill_btn, 1)
            tp_btn = QPushButton("TP")
            tp_btn.setFixedHeight(_BTN_H)
            tp_btn.setToolTip("Teleport all connected players to these coordinates")
            tp_btn.setStyleSheet(_btn_style)
            tp_btn.clicked.connect(lambda _checked, k=key: asyncio.ensure_future(self._teleport_to_xyz(k)))
            btns_row.addWidget(tp_btn, 1)
            outer_layout.addLayout(btns_row)
            return (spins[0], spins[1], spins[2], outer)
        if field_type == "nav_zone":
            w = _NavZoneField(self._get_zone_suggestions, self._get_clients)
            w.set_value(str(val) if val else "")
            if help_text:
                w.setToolTip(help_text)
            w.changed.connect(self._emit_changed)
            return w
        if field_type == "str_choice":
            w = _StrChoiceComboBox()
            w.add_choices(choices or [])
            w.set_value(val)
            if help_text:
                w.setToolTip(help_text)
            w.currentTextChanged.connect(self._emit_changed)
            return w
        if field_type == "int_choice":
            w = _IntChoiceComboBox()
            for c in choices or []:
                w.addItem(str(c))
            try:
                idx = w.findText(str(int(val)) if val is not None else "")
                if idx >= 0:
                    w.setCurrentIndex(idx)
            except (TypeError, ValueError):
                pass
            if help_text:
                w.setToolTip(help_text)
            w.currentTextChanged.connect(self._emit_changed)
            return w
        w = QLabel(f"(unsupported type: {field_type})")
        self._muted_labels.append(w)
        w.setStyleSheet(f"color: {THEME.text_muted};")
        return w

    def _emit_changed(self, *_) -> None:
        if not self._suppress:
            self._validate_required_fields()
            self.changed.emit()

    def _paste_xyz(self, key: str) -> None:
        clipboard = QApplication.clipboard()
        values = parse_xyz_text(clipboard.text()) if clipboard else {}
        if not values:
            return
        widgets = self._widgets.get(key)
        if not isinstance(widgets, tuple):
            return
        x_s, y_s, z_s, _ = widgets
        self._suppress = True
        for axis, spin in (("x", x_s), ("y", y_s), ("z", z_s)):
            if axis in values:
                spin.setValue(values[axis])
        self._suppress = False
        self._emit_changed()

    def _copy_xyz(self, key: str) -> None:
        widgets = self._widgets.get(key)
        if not isinstance(widgets, tuple):
            return
        x_s, y_s, z_s, _ = widgets
        QApplication.clipboard().setText(json.dumps({"x": x_s.value(), "y": y_s.value(), "z": z_s.value()}))

    def _show_fill_menu(self, key: str, btn: QPushButton) -> None:
        clients = self._get_clients()
        if not clients:
            return
        if len(clients) == 1:
            asyncio.ensure_future(self._fill_xyz_from_player(key, 0))
            return
        menu = popup_menu(btn)
        for i in range(len(clients)):
            action = menu.addAction(f"Fill from Player {i + 1}")
            action.triggered.connect(
                lambda _checked, k=key, idx=i: asyncio.ensure_future(self._fill_xyz_from_player(k, idx))
            )
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    async def _fill_xyz_from_player(self, key: str, idx: int) -> None:
        clients = self._get_clients()
        if idx >= len(clients):
            return
        try:
            pos = await clients[idx].body.position()
            self.set_xyz(key, pos.x, pos.y, pos.z)
        except Exception:
            pass

    async def _teleport_to_xyz(self, key: str) -> None:
        from wizwalker import XYZ

        clients = self._get_clients()
        if not clients:
            return
        widgets = self._widgets.get(key)
        if not isinstance(widgets, tuple):
            return
        x_s, y_s, z_s, _ = widgets
        target = XYZ(x_s.value(), y_s.value(), z_s.value())
        try:
            await asyncio.gather(
                *[c.teleport(target, wait_on_inuse=True, purge_on_after_unuser_fixer=True) for c in clients]
            )
        except Exception:
            pass

    def set_str(self, key: str, value: str) -> None:
        """Programmatically set a 'str' field by key."""
        widget = self._widgets.get(key)
        if not isinstance(widget, (_ZoneWidget, QLineEdit)):
            return
        self._suppress = True
        widget.setText(value)
        self._suppress = False
        self._emit_changed()

    def set_xyz(self, key: str, x: float, y: float, z: float) -> None:
        """Programmatically set an 'xyz' field by key."""
        widgets = self._widgets.get(key)
        if not isinstance(widgets, tuple):
            return
        x_s, y_s, z_s, _ = widgets
        self._suppress = True
        x_s.setValue(x)
        y_s.setValue(y)
        z_s.setValue(z)
        self._suppress = False
        self._emit_changed()


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
        self._add_group_btn = QPushButton("+ Add condition group")
        self._add_group_btn.setFixedHeight(22)
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
            f"#FlagMutedBold {{ color: {THEME.text_muted}; font-size: 11px;"
            f"  font-weight: 600; border: none; }}"
            f"#FlagSep {{ color: {THEME.border}; }}"
            f"#FlagGroupFrame {{ border: 1px solid {THEME.border}; border-radius: 4px; }}"
            f"#FlagRemove {{ color: {THEME.error}; font-weight: bold; border: none; padding: 0; }}"
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

        rem_group_btn = QPushButton("✕")
        rem_group_btn.setFixedWidth(self._REM_W)
        rem_group_btn.setObjectName("FlagRemove")

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
        add_cond_btn = QPushButton("+ Add condition")
        add_cond_btn.setStyleSheet("QPushButton { border: none; }")
        add_cond_btn.setFixedHeight(20)
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

        rem_btn = QPushButton("✕")
        rem_btn.setFixedWidth(self._REM_W)
        rem_btn.setObjectName("FlagRemove")

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

_ORIGIN_TEXT = {
    Origin.SHIPPED: "shipped with BartleBot",
    Origin.SHIPPED_OVERRIDE: "your copy of a shipped subgraph",
    Origin.USER: "in your library",
    Origin.MISSING: "not in the library",
}


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
            btn.setToolTip(f"Region colour {key}")
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
    # A locked member's Override toggle, or a Reset on the region page:
    # (state id, param key, active). The canvas owns the instance's overrides.
    override_toggled = pyqtSignal(str, str, bool)
    # The region page wrote into the shown RegionModel (label, colour, inputs).
    # The bool says whether the edit is typing (True: the canvas opens a debounced
    # undo step) or discrete (False: structural_property_changed already pushed
    # one, as a node-type switch does).
    region_property_changed = pyqtSignal(bool)
    detach_requested = pyqtSignal(str)  # region ID
    edit_subgraph_requested = pyqtSignal(str)  # region ID
    # (state id, param key): a row's Expose button, on a member of the region being edited.
    expose_input_requested = pyqtSignal(str, str)
    # (state id, param key, free): the author's lock toggle on a member of the region being edited.
    free_toggled = pyqtSignal(str, str, bool)
    # (state id): Reset beside a label override on the region page.
    label_override_reset = pyqtSignal(str)
    # The inputs editor changed the working definition the canvas handed to show_region.
    definition_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state: StateModel | None = None
        self._transition: TransitionModel | None = None
        self._region: RegionModel | None = None
        self._region_defn: SubgraphDefinition | None = None
        self._region_editing: bool = False
        self._locked: bool = False
        self._region_inputs_initial: dict = {}
        self._suppress: bool = False
        self._get_clients: Callable[[], list] = lambda: []
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

        # Page 1 — node
        node_w = QWidget()
        nf = QFormLayout(node_w)
        nf.setContentsMargins(6, 6, 6, 6)
        self._node_id_lbl = QLabel()
        self._node_label_edit = QLineEdit()
        self._node_type_combo = QComboBox()
        # The category headers carry a THEME colour on the model item rather than a
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
            _font.setPointSize(max(1, _font.pointSize() - 1))
            _header.setFont(_font)
            _model.appendRow(_header)
            self._node_type_headers.append(_header)
            for _node_type in _node_types:
                _item = QStandardItem("    " + _node_type)
                _item.setData(_node_type, Qt.ItemDataRole.UserRole)
                _model.appendRow(_item)
        self._node_type_combo.setModel(_model)
        self._node_type_combo.setItemDelegate(_NodeTypeDelegate(self._node_type_combo))
        self._node_help_btn = QPushButton("?")
        self._node_help_btn.setFixedSize(22, 22)
        self._node_help_btn.setToolTip("View node documentation")
        self._node_help_btn.clicked.connect(self._on_node_help)

        _node_type_row = QWidget()
        _nt_row_layout = QHBoxLayout(_node_type_row)
        _nt_row_layout.setContentsMargins(0, 0, 0, 0)
        _nt_row_layout.setSpacing(4)
        _nt_row_layout.addWidget(self._node_type_combo, 1)
        _nt_row_layout.addWidget(self._node_help_btn)

        # FSM role checkboxes. Both roles are exactly-one and bot-wide, so these read
        # as checkboxes but behave as radios across nodes: checking one moves the role
        # here, and the box for the state that already holds it is disabled, because
        # unchecking would leave the bot with no answer and BotConfig requires one.
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
        _roles_row = QWidget()
        _roles_layout = QHBoxLayout(_roles_row)
        _roles_layout.setContentsMargins(0, 0, 0, 0)
        _roles_layout.setSpacing(12)
        _roles_layout.addWidget(self._initial_check)
        _roles_layout.addWidget(self._recovery_check)
        _roles_layout.addStretch()

        nf.addRow("ID", self._node_id_lbl)
        nf.addRow("Label", self._node_label_edit)
        nf.addRow("Node type", _node_type_row)
        nf.addRow("Roles", _roles_row)

        self._param_form = _DynamicParamForm()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        scroll.setWidget(self._param_form)

        self._returns_section = QWidget()
        returns_layout = QVBoxLayout(self._returns_section)
        returns_layout.setContentsMargins(6, 4, 6, 4)
        returns_layout.setSpacing(2)
        self._returns_title = QLabel("Returns:")
        returns_layout.addWidget(self._returns_title)
        self._returns_body_layout = QVBoxLayout()
        self._returns_body_layout.setSpacing(1)
        returns_layout.addLayout(self._returns_body_layout)
        self._returns_section.setVisible(False)

        # Record row — shown only for recordable node types
        self._record_row = QWidget()
        _rr_layout = QHBoxLayout(self._record_row)
        _rr_layout.setContentsMargins(6, 0, 6, 4)
        _rr_layout.setSpacing(6)
        self._record_btn = QPushButton("Record")
        self._record_btn.setFixedWidth(70)
        self._record_status_lbl = QLabel("")
        _rr_layout.addWidget(self._record_btn)
        _rr_layout.addWidget(self._record_status_lbl, 1)
        self._record_row.setVisible(False)
        self._record_btn.clicked.connect(self._on_record_clicked)

        # Wiki import row — shown only for handle_combat nodes
        self._wiki_import_row = QWidget()
        _wi_layout = QHBoxLayout(self._wiki_import_row)
        _wi_layout.setContentsMargins(6, 0, 6, 4)
        _wi_layout.setSpacing(6)
        self._wiki_import_btn = QPushButton("Import drops from wiki…")
        self._wiki_import_btn.setToolTip(
            "Fetch drop data from a Wizard101 wiki creature page and add selected items to the bot's drop list."
        )
        _wi_layout.addWidget(self._wiki_import_btn)
        self._wiki_import_row.setVisible(False)
        self._wiki_import_btn.clicked.connect(self._on_wiki_import)

        # Notes section — freeform per-node annotation
        self._notes_section = QWidget()
        _notes_vbox = QVBoxLayout(self._notes_section)
        _notes_vbox.setContentsMargins(6, 4, 6, 6)
        _notes_vbox.setSpacing(3)
        self._notes_title = QLabel("Notes:")
        _notes_vbox.addWidget(self._notes_title)
        self._notes_edit = QPlainTextEdit()
        self._notes_edit.setPlaceholderText("Add notes about this node…")
        self._notes_edit.setFixedHeight(72)
        _notes_vbox.addWidget(self._notes_edit)

        # Connections section — lists outgoing/incoming transitions for this node
        self._connections_section = QWidget()
        _conn_outer = QVBoxLayout(self._connections_section)
        _conn_outer.setContentsMargins(6, 4, 6, 4)
        _conn_outer.setSpacing(4)

        _conn_header = QWidget()
        _conn_header_layout = QHBoxLayout(_conn_header)
        _conn_header_layout.setContentsMargins(0, 0, 0, 0)
        _conn_header_layout.setSpacing(4)
        self._conn_toggle_btn = QPushButton("▾ Connections")
        self._conn_toggle_btn.setFlat(True)
        _conn_header_layout.addWidget(self._conn_toggle_btn)
        _conn_header_layout.addStretch()
        _conn_outer.addWidget(_conn_header)

        self._conn_body = QWidget()
        self._conn_body_layout = QVBoxLayout(self._conn_body)
        self._conn_body_layout.setContentsMargins(0, 2, 0, 2)
        self._conn_body_layout.setSpacing(2)
        _conn_outer.addWidget(self._conn_body)

        self._conn_expanded = True
        self._conn_toggle_btn.clicked.connect(self._toggle_connections)
        self._connections_section.setVisible(False)

        # Lock note -- shown above the form for a member of a linked subgraph
        self._lock_note = ThemedTextLabel("", color="text_muted")
        self._lock_note.setWordWrap(True)
        self._lock_note.setContentsMargins(6, 6, 6, 0)
        self._lock_note.setVisible(False)

        node_outer = QWidget()
        node_vbox = QVBoxLayout(node_outer)
        node_vbox.setContentsMargins(0, 0, 0, 0)
        node_vbox.setSpacing(0)
        node_vbox.addWidget(self._lock_note)
        node_vbox.addWidget(node_w)
        node_vbox.addWidget(self._record_row)
        node_vbox.addWidget(scroll, 1)
        node_vbox.addWidget(self._wiki_import_row)
        node_vbox.addWidget(self._returns_section)
        node_vbox.addWidget(self._connections_section)
        node_vbox.addWidget(self._notes_section)
        self._stack.addWidget(node_outer)

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

        # Page 3 — region. Label and colour for every region; the subgraph section
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

        self._region_link_box = QWidget()
        link_vbox = QVBoxLayout(self._region_link_box)
        link_vbox.setContentsMargins(6, 0, 6, 6)
        link_vbox.setSpacing(4)
        link_vbox.addWidget(RuleHeading("Subgraph"))
        self._region_defn_name = QLabel()
        _name_font = self._region_defn_name.font()
        _name_font.setBold(True)
        self._region_defn_name.setFont(_name_font)
        self._region_defn_desc = ThemedTextLabel("", color="text_muted")
        self._region_defn_desc.setWordWrap(True)
        self._region_version = ThemedTextLabel("", color="text_muted")
        self._region_version.setWordWrap(True)
        self._region_status = ThemedTextLabel("", color="warning")
        self._region_status.setWordWrap(True)
        self._region_editing_note = ThemedTextLabel(
            "Editing in place: the members show the definition's own values. Inputs and overrides "
            "come back when the edit is saved or discarded.",
            color="text_muted",
        )
        self._region_editing_note.setWordWrap(True)
        link_vbox.addWidget(self._region_defn_name)
        link_vbox.addWidget(self._region_defn_desc)
        link_vbox.addWidget(self._region_version)
        link_vbox.addWidget(self._region_status)
        link_vbox.addWidget(self._region_editing_note)
        link_vbox.addWidget(RuleHeading("Inputs"))
        self._region_inputs_form = _DynamicParamForm()
        self._region_inputs_note = ThemedTextLabel("This subgraph declares no inputs.", color="text_muted")
        self._region_inputs_note.setContentsMargins(6, 0, 6, 0)
        # While editing, the form gives way to an editor over the working definition:
        # one row per input, its bindings beneath, a Remove on each.
        self._region_inputs_editor = QWidget()
        self._region_inputs_editor_layout = QVBoxLayout(self._region_inputs_editor)
        self._region_inputs_editor_layout.setContentsMargins(6, 0, 6, 0)
        self._region_inputs_editor_layout.setSpacing(2)
        link_vbox.addWidget(self._region_inputs_form)
        link_vbox.addWidget(self._region_inputs_note)
        link_vbox.addWidget(self._region_inputs_editor)
        self._region_overrides_heading = RuleHeading("Overrides")
        self._region_overrides_body = QWidget()
        self._region_overrides_layout = QVBoxLayout(self._region_overrides_body)
        self._region_overrides_layout.setContentsMargins(6, 0, 6, 0)
        self._region_overrides_layout.setSpacing(2)
        link_vbox.addWidget(self._region_overrides_heading)
        link_vbox.addWidget(self._region_overrides_body)
        self._region_detach_btn = ActionButton("detach", "Detach from library", fill=False)
        self._region_detach_btn.setToolTip(
            "Drop the link to the library. The states stay exactly as they are, and stop following updates."
        )
        self._region_edit_btn = ActionButton("pencil", "Edit subgraph", style=ActionStyle.SECONDARY, fill=False)
        self._region_edit_btn.setToolTip(
            "Unlock this instance and edit the subgraph in place; Save to library pushes the result to every bot."
        )
        _detach_row = QWidget()
        _detach_layout = QHBoxLayout(_detach_row)
        _detach_layout.setContentsMargins(0, 8, 0, 0)
        _detach_layout.addStretch()
        _detach_layout.addWidget(self._region_edit_btn)
        _detach_layout.addWidget(self._region_detach_btn)
        link_vbox.addWidget(_detach_row)
        region_vbox.addWidget(self._region_link_box)
        region_vbox.addStretch()
        region_scroll = QScrollArea()
        region_scroll.setWidgetResizable(True)
        region_scroll.setFrameShape(region_scroll.Shape.NoFrame)
        region_scroll.setWidget(region_outer)
        self._stack.addWidget(region_scroll)

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
        self._param_form.override_toggled.connect(self._on_form_override_toggled)
        self._region_label_edit.textChanged.connect(self._on_region_label_changed)
        self._region_color_row.changed.connect(self._on_region_color_changed)
        self._region_inputs_form.changed.connect(self._on_region_inputs_changed)
        self._region_detach_btn.clicked.connect(self._on_detach_clicked)
        self._region_edit_btn.clicked.connect(self._on_edit_subgraph_clicked)
        self._param_form.expose_requested.connect(self._on_form_expose_requested)
        self._param_form.free_toggled.connect(self._on_form_free_toggled)

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
        self._node_help_btn.setStyleSheet(
            f"QPushButton {{ background: {THEME.surface}; border: 1px solid {THEME.border}; "
            f"border-radius: 4px; color: {THEME.accent}; font-weight: bold; font-size: 11px; }}"
            f"QPushButton:hover {{ background: {THEME.accent}; color: #ffffff; }}"
        )
        section_title = f"font-weight: bold; color: {THEME.text_subtle};"
        self._returns_title.setStyleSheet(section_title)
        self._notes_title.setStyleSheet(section_title)
        self._record_status_lbl.setStyleSheet(f"color: {THEME.text_muted}; font-size: 11px;")
        self._notes_edit.setStyleSheet(
            f"QPlainTextEdit {{ background: {THEME.surface}; border: 1px solid {THEME.border}; "
            f"border-radius: 4px; color: {THEME.text}; font-size: 11px; padding: 3px; }}"
        )
        self._conn_toggle_btn.setStyleSheet(
            f"QPushButton {{ text-align: left; font-weight: bold; color: {THEME.text_subtle};"
            " border: none; padding: 0; font-size: 11px; background: transparent; }"
        )
        self._flags_lbl.setStyleSheet(f"color: {THEME.text_muted}; font-size: 11px; font-weight: 600; margin-top: 4px;")
        # Connection rows are built per selection and hold four THEME colours each
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
        self._region_inputs_form.set_clients_getter(fn)
        self.refresh_player_availability()

    def set_zone_suggestions_getter(self, fn: Callable[[], list[str]]) -> None:
        """Pass a callable returning zone suggestions, for the node form and the region inputs form alike."""
        self._param_form.set_zone_suggestions_getter(fn)
        self._region_inputs_form.set_zone_suggestions_getter(fn)

    def refresh_player_availability(self) -> None:
        """Re-grey the player checkboxes — call when clients connect or disconnect.

        findChildren rather than a widget registry: player checkboxes appear at several
        depths inside the param form, and this needs no bookkeeping for any of them.
        """
        connected = len(self._get_clients())
        for w in self.findChildren(PlayerListWidget) + self.findChildren(PlayerSlotWidget):
            w.set_available_players(connected)

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
    def shown_region(self) -> RegionModel | None:
        """The region on the region page, or None when another page is up."""
        return self._region

    @property
    def is_locked(self) -> bool:
        """Whether the node page is showing a member of a linked subgraph."""
        return self._locked

    def show_state(
        self,
        state: StateModel,
        all_states: list | None = None,
        all_transitions: list[TransitionModel] | None = None,
        initial_id: str | None = None,
        recovery_id: str | None = None,
        *,
        locked: bool = False,
        override_keys: frozenset = frozenset(),
        subgraph_name: str | None = None,
        exposable: bool = False,
        free_keys: frozenset = frozenset(),
        bound_inputs: dict[str, str] | None = None,
    ) -> None:
        """Show *state*. With *locked*, only the roles, the label, free params and overridden params are editable.

        Roles stay enabled on a locked member because they are the bot's, not the
        subgraph's (docs/SUBGRAPHS.md), and so does the label, which is always the
        consumer's. Type and notes are the definition's, so they lock; a param in
        *free_keys* is the consumer's by the author's choice, and any other
        unlocks one at a time through its lock toggle. With *exposable* the same
        two sets drive the author's controls: *free_keys* is what is free so far,
        *bound_inputs* which params an input already owns.
        """
        self._cancel_record()
        self._state = state
        self._transition = None
        self._region = None
        self._locked = locked
        self._all_states_for_node = list(all_states or [])
        self._all_transitions_for_node = list(all_transitions or [])
        self._suppress = True
        self._node_id_lbl.setText(state.id)
        self._node_label_edit.setText(state.label or "")
        idx = self._node_type_combo.findData(state.node_type, Qt.ItemDataRole.UserRole)
        if idx >= 0:
            self._node_type_combo.setCurrentIndex(idx)
        self._node_type_combo.setEnabled(not locked)
        schema = NODE_PARAM_SCHEMAS.get(state.node_type, [])
        self._param_form.load_schema(
            schema,
            state.params or {},
            locked=locked,
            overrides=override_keys,
            exposable=exposable,
            free=free_keys,
            bound=bound_inputs,
        )
        self._rebuild_returns(state.node_type)
        self._rebuild_connections(state.id)
        self._notes_edit.blockSignals(True)
        self._notes_edit.setPlainText(state.notes or "")
        self._notes_edit.blockSignals(False)
        self._notes_edit.setEnabled(not locked)
        if locked:
            name = subgraph_name or "instance"
            self._lock_note.setText(
                f"Part of subgraph {name}. Its locked values follow the library; unlock one to override it for "
                "this instance only, and that value stops following the subgraph."
            )
        self._lock_note.setVisible(locked)
        self._suppress = False
        self._set_role_checks(state.id, initial_id, recovery_id)
        self._record_row.setVisible(state.node_type in recording.RECORDABLE_NODES and not locked)
        self._wiki_import_row.setVisible(state.node_type == "handle_combat")
        self._stack.setCurrentIndex(_PAGE_NODE)

    def show_region(
        self,
        region: RegionModel,
        defn: SubgraphDefinition | None = None,
        status: RegionStatus | None = None,
        member_labels: dict[str, str] | None = None,
        *,
        editing: bool = False,
    ) -> None:
        """Show the region page: label and colour, plus the subgraph section for a linked region.

        *defn* is the library's current definition when it has one; *status* is the
        last compose report's entry for this region; *member_labels* names members
        in the override list. The page edits ``region`` in place and reports through
        ``region_property_changed``. With *editing*, *defn* is the canvas's working
        copy and the inputs form gives way to an editor over it.
        """
        self._cancel_record()
        self._state = None
        self._transition = None
        self._region = region
        self._region_defn = defn
        self._region_editing = editing
        self._suppress = True
        try:
            self._region_label_edit.setText(region.label)
            self._region_color_row.set_key(region.color)
            source = region.source
            self._region_link_box.setVisible(source is not None)
            if source is not None:
                self._region_defn_name.setText(defn.name if defn is not None else source.subgraph_id)
                desc = (defn.description or "") if defn is not None else ""
                self._region_defn_desc.setText(desc)
                self._region_defn_desc.setVisible(bool(desc))
                parts = [f"Version {source.version}"]
                if status is not None and status.library_version not in (None, source.version):
                    parts.append(f"library version {status.library_version}")
                if status is not None:
                    parts.append(_ORIGIN_TEXT.get(status.origin, status.origin.value))
                self._region_version.setText(" \u00b7 ".join(parts))
                reasons = list(status.reasons) if status is not None else []
                self._region_status.setText("\n".join(reasons))
                self._region_status.setVisible(bool(reasons))
                schema = [input_param_field(defn, i) for i in (defn.inputs if defn is not None else [])]
                self._region_editing_note.setVisible(editing)
                self._region_edit_btn.setVisible(not editing and defn is not None)
                self._region_detach_btn.setVisible(not editing)
                if editing:
                    self._region_inputs_form.setVisible(False)
                    self._region_inputs_note.setVisible(not schema)
                    self._rebuild_inputs_editor(defn, member_labels or {})
                    self._region_inputs_editor.setVisible(bool(schema))
                    self._region_overrides_heading.setVisible(False)
                    self._region_overrides_body.setVisible(False)
                else:
                    self._region_inputs_editor.setVisible(False)
                    self._region_inputs_form.load_schema(schema, dict(source.inputs))
                    self._region_inputs_form.setVisible(bool(schema))
                    self._region_inputs_note.setVisible(not schema)
                    self._region_inputs_initial = self._region_inputs_form.get_params()
                    self._rebuild_override_rows(region, member_labels or {})
        finally:
            self._suppress = False
        self._stack.setCurrentIndex(_PAGE_REGION)

    def _rebuild_override_rows(self, region: RegionModel, member_labels: dict[str, str]) -> None:
        layout = self._region_overrides_layout
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        source = region.source
        rows = 0

        def add_row(text: str, on_reset) -> None:
            nonlocal rows
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            reset = ThemedIconButton("revert", size=20, icon_size=14)
            reset.setToolTip("Reset to the subgraph's value")
            reset.clicked.connect(on_reset)
            h.addWidget(lbl, 1)
            h.addWidget(reset)
            layout.addWidget(row)
            rows += 1

        if source is not None:
            for local, params in source.param_overrides.items():
                state_id = member_id(region.id, local)
                for key, value in params.items():
                    add_row(
                        f"{member_labels.get(state_id, local)} \u00b7 {key} = {value!r}",
                        lambda _=None, sid=state_id, k=key: self.override_toggled.emit(sid, k, False),
                    )
            for local, label in source.label_overrides.items():
                state_id = member_id(region.id, local)
                add_row(
                    f"{member_labels.get(state_id, local)} \u00b7 label = {label!r}",
                    lambda _=None, sid=state_id: self.label_override_reset.emit(sid),
                )
        self._region_overrides_heading.setVisible(rows > 0)
        self._region_overrides_body.setVisible(rows > 0)

    def _rebuild_inputs_editor(self, defn: SubgraphDefinition | None, member_labels: dict[str, str]) -> None:
        layout = self._region_inputs_editor_layout
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if defn is None or self._region is None:
            return
        for inp in defn.inputs:
            head = QWidget()
            h = QHBoxLayout(head)
            h.setContentsMargins(0, 4, 0, 0)
            h.setSpacing(6)
            title = QLabel(f"<b>{inp.label or inp.name}</b> ({inp.name}) \u00b7 {inp.type}")
            title.setToolTip(inp.help or "")
            remove = ThemedIconButton("trash", size=20, icon_size=14)
            remove.setToolTip("Remove this input; its bound params keep their current values")
            remove.clicked.connect(lambda _=None, name=inp.name: self._remove_input(name))
            h.addWidget(title, 1)
            h.addWidget(remove)
            layout.addWidget(head)
            for b in inp.bindings:
                row = QWidget()
                rh = QHBoxLayout(row)
                rh.setContentsMargins(16, 0, 0, 0)
                rh.setSpacing(6)
                state_id = member_id(self._region.id, b.state)
                text = ThemedTextLabel(f"{member_labels.get(state_id, b.state)} \u00b7 {b.param}", color="text_muted")
                unbind = ThemedIconButton("dismiss", size=18, icon_size=12)
                unbind.setToolTip("Unbind this param from the input")
                unbind.clicked.connect(
                    lambda _=None, name=inp.name, st=b.state, k=b.param: self._remove_binding(name, st, k)
                )
                rh.addWidget(text, 1)
                rh.addWidget(unbind)
                layout.addWidget(row)

    def _remove_input(self, name: str) -> None:
        defn = self._region_defn
        if defn is None:
            return
        defn.inputs = [i for i in defn.inputs if i.name != name]
        self.definition_changed.emit()

    def _remove_binding(self, name: str, state: str, key: str) -> None:
        defn = self._region_defn
        if defn is None:
            return
        for inp in defn.inputs:
            if inp.name == name:
                inp.bindings = [b for b in inp.bindings if not (b.state == state and b.param == key)]
        self.definition_changed.emit()

    def _on_edit_subgraph_clicked(self) -> None:
        if self._region is not None:
            self.edit_subgraph_requested.emit(self._region.id)

    def _on_form_expose_requested(self, key: str) -> None:
        if self._state is not None:
            self.expose_input_requested.emit(self._state.id, key)

    def _on_form_free_toggled(self, key: str, free: bool) -> None:
        if self._suppress or self._state is None:
            return
        self.free_toggled.emit(self._state.id, key, free)

    @property
    def region_editing(self) -> bool:
        """Whether the region page is showing the region being edited in place."""
        return self._region_editing

    def _on_form_override_toggled(self, key: str, active: bool) -> None:
        if self._suppress or self._state is None:
            return
        self.override_toggled.emit(self._state.id, key, active)

    def _on_region_label_changed(self, text: str) -> None:
        if self._suppress or self._region is None:
            return
        label = text.strip()
        if not label:
            return
        self._region.label = label
        self.region_property_changed.emit(True)

    def _on_region_color_changed(self, key: int) -> None:
        if self._suppress or self._region is None:
            return
        self.structural_property_changed.emit("Change Region Color")
        self._region.color = key
        self.region_property_changed.emit(False)

    def _on_region_inputs_changed(self) -> None:
        """Write the inputs the user has touched into ``source.inputs``.

        Only a key that was already set, or whose widget moved from what it loaded
        with, is written: an unset input is ``None`` and leaves the definition's
        value in place, and a widget always has *some* value, so writing every key
        would turn every untouched input into an empty override.
        """
        if self._suppress or self._region is None or self._region.source is None:
            return
        source = self._region.source
        current = self._region_inputs_form.get_params()
        for key, value in current.items():
            if key in source.inputs or value != self._region_inputs_initial.get(key):
                source.inputs[key] = value
        self.region_property_changed.emit(True)

    def _on_detach_clicked(self) -> None:
        if self._region is not None:
            self.detach_requested.emit(self._region.id)

    @property
    def region_label_edit(self) -> QLineEdit:
        return self._region_label_edit

    @property
    def region_color_row(self) -> _RegionColorRow:
        return self._region_color_row

    @property
    def region_inputs_form(self) -> _DynamicParamForm:
        return self._region_inputs_form

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
    ) -> None:
        self._transition = transition
        self._state = None
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

        self._suppress = False
        self._stack.setCurrentIndex(_PAGE_EDGE)

    def _on_node_label_changed(self, text: str) -> None:
        if self._suppress or self._state is None:
            return
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
        self._param_form.load_schema(schema, {})
        self._state.params = self._param_form.get_params()
        self._rebuild_returns(node_type)
        self._record_row.setVisible(node_type in recording.RECORDABLE_NODES)
        self._wiki_import_row.setVisible(node_type == "handle_combat")
        self.structural_property_applied.emit()

    def _on_param_form_changed(self) -> None:
        if self._state is None:
            return
        self._state.params = self._param_form.get_params()
        self.properties_changed.emit()

    def _on_notes_changed(self) -> None:
        if self._suppress or self._state is None:
            return
        text = self._notes_edit.toPlainText().strip()
        self._state.notes = text if text else None
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
        # On a locked subgraph member the field starts disabled and un-overridden;
        # set_param_value() only ever reaches the widget's text, and a value that
        # was never registered as an override is discarded the next time the
        # instance is recomposed from its definition. Flip the override on first,
        # exactly as the user's own lock toggle would, so the edit sticks.
        override_btn = self._param_form.override_button(WIKI_PAGE_PARAM)
        if override_btn is not None and not override_btn.isChecked():
            override_btn.setChecked(True)
        self._param_form.set_param_value(WIKI_PAGE_PARAM, page)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def toggle_record(self) -> None:
        """Start or cancel recording for the currently selected node (hotkey handler)."""
        self._on_record_clicked()

    def _cancel_record(self) -> None:
        recording.cancel_active_recording()
        self._record_btn.setText("Record")
        self._record_status_lbl.setText("")

    def _on_record_clicked(self) -> None:
        if recording.is_recording():
            self._cancel_record()
            return
        if self._state is None:
            return
        clients = self._get_clients()
        if not clients:
            self._record_status_lbl.setText("No player connected.")
            return
        if self._state.node_type not in recording.RECORDABLE_NODES:
            return
        asyncio.ensure_future(
            recording.run_recording(
                self._state.node_type,
                clients[0],
                self._param_form,
                self._record_status_lbl.setText,
                self._record_btn.setText,
                self._try_set_label,
            )
        )

    def _try_set_label(self, label: str) -> None:
        """Auto-populate the node label if it is currently empty."""
        if self._state is None or (self._state.label or "").strip():
            return
        self._state.label = label
        self._suppress = True
        self._node_label_edit.setText(label)
        self._suppress = False
        self.properties_changed.emit()

    def _rebuild_returns(self, node_type: str) -> None:
        while self._returns_body_layout.count():
            item = self._returns_body_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        spec = NODE_SPECS.get(node_type)
        if spec and spec.returns:
            for ret in spec.returns:
                if ret.description:
                    lbl = QLabel(f"<b>{ret.name}</b> — {ret.description}")
                else:
                    lbl = QLabel(f"<b>{ret.name}</b>")
                lbl.setWordWrap(True)
                lbl.setStyleSheet("font-size: 11px;")
                self._returns_body_layout.addWidget(lbl)
            self._returns_section.setVisible(True)
        else:
            self._returns_section.setVisible(False)

    # ------------------------------------------------------------------
    # Connections section
    # ------------------------------------------------------------------

    def _toggle_connections(self) -> None:
        self._conn_expanded = not self._conn_expanded
        self._conn_body.setVisible(self._conn_expanded)
        self._conn_toggle_btn.setText("▾ Connections" if self._conn_expanded else "▸ Connections")

    def _rebuild_connections(self, state_id: str) -> None:
        """Rebuild the outgoing/incoming connections list for the selected node."""
        # Clear existing rows
        while self._conn_body_layout.count():
            item = self._conn_body_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        all_t = self._all_transitions_for_node
        state_map = {s.id: (s.label or s.id) for s in self._all_states_for_node}

        outgoing = [t for t in all_t if t.source == state_id]
        incoming = [t for t in all_t if t.dest == state_id and t.source != state_id]

        if not outgoing and not incoming:
            placeholder = QLabel("No connections")
            placeholder.setStyleSheet(f"color: {THEME.text_muted}; font-size: 11px;")
            self._conn_body_layout.addWidget(placeholder)
            self._connections_section.setVisible(True)
            return

        # Sort outgoing: conditional first (matches evaluation order from to_machine_config)
        outgoing.sort(key=lambda t: 0 if t.when else 1)

        if outgoing:
            out_lbl = QLabel("Outgoing:")
            out_lbl.setStyleSheet(f"color: {THEME.text_muted}; font-size: 10px; font-weight: 600;")
            self._conn_body_layout.addWidget(out_lbl)
            for priority, t in enumerate(outgoing, 1):
                row = self._make_conn_row(
                    t,
                    badge_text=str(priority),
                    chip_text=t.when or "always",
                    node_label=state_map.get(t.dest, t.dest),
                    node_id=t.dest,
                    is_outgoing=True,
                )
                self._conn_body_layout.addWidget(row)

        if incoming:
            in_lbl = QLabel("Incoming:")
            in_lbl.setStyleSheet(
                f"color: {THEME.text_muted}; font-size: 10px; font-weight: 600;{'margin-top: 4px;' if outgoing else ''}"
            )
            self._conn_body_layout.addWidget(in_lbl)
            for t in incoming:
                row = self._make_conn_row(
                    t,
                    badge_text=None,
                    chip_text=t.when or "always",
                    node_label=state_map.get(t.source, t.source),
                    node_id=t.source,
                    is_outgoing=False,
                )
                self._conn_body_layout.addWidget(row)

        self._connections_section.setVisible(True)

    def _make_conn_row(
        self,
        transition: TransitionModel,
        badge_text: str | None,
        chip_text: str,
        node_label: str,
        node_id: str,
        is_outgoing: bool,
    ) -> QWidget:
        """Build a single connection row widget."""
        row = QWidget()
        row.setStyleSheet(f"QWidget {{ border-radius: 4px; }}QWidget:hover {{ background: {THEME.surface}; }}")
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        # Priority badge (outgoing only)
        if badge_text is not None:
            badge = QLabel(badge_text)
            badge.setFixedWidth(18)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet(f"color: {THEME.text_muted}; font-size: 9px; font-weight: 600;")
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
            " font-size: 10px; font-weight: 600; padding: 1px 5px;"
        )

        # Node name link
        name_lbl = QLabel(node_label)
        name_lbl.setStyleSheet(f"color: {THEME.accent}; font-size: 11px;")
        name_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        name_lbl.setToolTip(f"Navigate to: {node_label}")

        if is_outgoing:
            layout.addWidget(chip)
            layout.addWidget(name_lbl, 1)
        else:
            layout.addWidget(name_lbl, 1)
            layout.addWidget(chip)

        # Clicking the node name navigates to that state
        def _on_name_clicked(ev, nid=node_id):
            ev.accept()
            self.select_state_requested.emit(nid)

        name_lbl.mousePressEvent = _on_name_clicked  # type: ignore[method-assign]

        # Clicking anywhere else on the row selects the edge
        def _on_row_pressed(ev, t=transition):
            self.select_edge_requested.emit(t)

        row.mousePressEvent = _on_row_pressed  # type: ignore[method-assign]

        return row
