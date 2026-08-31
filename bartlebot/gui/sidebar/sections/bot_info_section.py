"""Sidebar section: bot metadata fields (name, version, description, …)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot_tags import BOT_TAGS, TAG_GROUPS, tag_group
from bartlebot.gui.sidebar.sidebar_section import (
    SidebarField,
    SidebarSection,
    value_edit,
    value_edit_multi,
    value_label,
)
from bartlebot.gui.widgets.flow_layout import flow_container
from bartlebot.gui.widgets.tag_picker import TagPickerButton
from bartlebot.gui.widgets.tag_pill import TagChip, TagPill

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

_PLACEHOLDER = "—"


def _swappable(label_w: QWidget, edit_w: QWidget) -> QWidget:
    """Container that shows either *label_w* or *edit_w* (not both).

    The label is shown by default.  Call ``setVisible`` on each child to
    switch modes; the layout collapses the hidden widget to zero height.
    """
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addWidget(label_w)
    lay.addWidget(edit_w)
    edit_w.setVisible(False)
    return w


class _TagsField(QWidget):
    """The Tags row: the bot's tags as pills, and two buttons that add more.

    Unlike the other rows this is not a label swapped for an editor. The pills are
    the value in both modes — what changes is whether they carry a ✕ and whether the
    Add buttons are shown — because a tag list reads the same either way and building
    a second, differently-shaped view of it just to be read-only is how the two drift.

    The buttons are captioned "Add Rewards…" / "Add Activity…" rather than the
    library's "Rewards" / "Activity": there, the button *is* the filter and its text
    has to report the selection; here the chips below already do that, so the text is
    free to say what pressing it does. Naming the group in the caption is what makes
    the vocabulary visible without opening anything — the same reason the library
    splits the picker in two.
    """

    tags_changed = pyqtSignal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tags: list[str] = []
        self._editable = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        self._pill_host, self._pill_layout = flow_container()
        root.addWidget(self._pill_host)

        self._empty_lbl = value_label()
        self._empty_lbl.setText(_PLACEHOLDER)
        root.addWidget(self._empty_lbl)

        # Wrapped too: "Add Rewards…" and "Add Activity…" side by side need more than
        # a 300px sidebar has once the field's own indent is taken off.
        self._buttons, button_layout = flow_container()
        self._pickers: dict[str, TagPickerButton] = {}
        for group in TAG_GROUPS:
            btn = TagPickerButton(title=group, group=group, caption=f"Add {group}…")
            btn.setToolTip(f"Choose which {group.lower()} tags this bot carries")
            btn.selection_changed.connect(self._on_picker_changed)
            self._pickers[group] = btn
            button_layout.addWidget(btn)
        root.addWidget(self._buttons)

        self._rebuild()

    # -- public API ----------------------------------------------------

    def tags(self) -> list[str]:
        """The selected tags, in ``BOT_TAGS`` order."""
        return list(self._tags)

    def set_tags(self, tags: list[str]) -> None:
        """Show *tags* without emitting ``tags_changed``."""
        self._tags = [t for t in BOT_TAGS if t in set(tags)]
        for group, picker in self._pickers.items():
            picker.set_selection([t for t in self._tags if tag_group(t) == group])
        self._rebuild()

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self._rebuild()

    # -- internals -----------------------------------------------------

    def _on_picker_changed(self, _keys: list) -> None:
        self._tags = [t for t in BOT_TAGS if any(t in p.tags() for p in self._pickers.values())]
        self._rebuild()
        self.tags_changed.emit(list(self._tags))

    def _remove(self, tag: str) -> None:
        self.set_tags([t for t in self._tags if t != tag])
        self.tags_changed.emit(list(self._tags))

    def _rebuild(self) -> None:
        while (item := self._pill_layout.takeAt(0)) is not None:
            if w := item.widget():
                w.hide()
                w.deleteLater()

        for tag in self._tags:
            if self._editable:
                chip = TagChip(tag)
                chip.removed.connect(self._remove)
                self._pill_layout.addWidget(chip)
            else:
                self._pill_layout.addWidget(TagPill(tag))

        # The placeholder stands in for the pills, not for the whole row: with the Add
        # buttons showing, an untagged bot already reads as untagged.
        self._empty_lbl.setVisible(not self._tags and not self._editable)
        self._pill_host.setVisible(bool(self._tags))
        self._buttons.setVisible(self._editable)
        self._pill_host.updateGeometry()


class BotInfoSection(SidebarSection):
    """Collapsible 'Bot Info' section: file, name, version, description, etc."""

    metadata_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Bot Info", parent)
        self._config: BotConfig | None = None
        self._suppress: bool = False

        # File field — always a label, never editable
        self._file_lbl = value_label(wrap=True)

        # Switchable fields: each has a display label and an edit widget.
        # Labels are shown in Run mode (or when no config is loaded).
        # Edit widgets are shown in Edit mode.
        self._name_lbl = value_label()
        self._name_edit = value_edit()
        self._name_edit.setReadOnly(False)

        self._version_lbl = value_label()
        self._version_edit = value_edit()
        self._version_edit.setReadOnly(False)

        self._desc_lbl = value_label(wrap=True)
        self._desc_edit = value_edit_multi(lines=3)
        self._desc_edit.setReadOnly(False)

        self._start_lbl = value_label()
        self._start_edit = value_edit()
        self._start_edit.setReadOnly(False)

        self._authors_lbl = value_label()
        self._authors_edit = value_edit()
        self._authors_edit.setReadOnly(False)

        # Not a label/editor pair: the pills are the value in both modes, and
        # _TagsField switches its own chrome. See _set_label_mode.
        self._tags_field = _TagsField()

        # Initial and Recovery are read-only here.  Both are set from the state's own
        # properties panel, where the badge that shows the role already lives (#71);
        # these labels stay because they are the only summary of the two roles, and
        # the only view of them at all in Run mode, where nothing is selectable.
        self._initial_lbl = value_label()
        self._recovery_lbl = value_label()

        # Ordered list of (label, edit) pairs for bulk visibility switching.
        self._switchable_pairs: list[tuple[QWidget, QWidget]] = [
            (self._name_lbl, self._name_edit),
            (self._version_lbl, self._version_edit),
            (self._desc_lbl, self._desc_edit),
            (self._start_lbl, self._start_edit),
            (self._authors_lbl, self._authors_edit),
        ]

        vbox = QVBoxLayout()
        vbox.setSpacing(0)
        vbox.addWidget(SidebarField("File", self._file_lbl))
        vbox.addWidget(SidebarField("Name", _swappable(self._name_lbl, self._name_edit)))
        vbox.addWidget(SidebarField("Version", _swappable(self._version_lbl, self._version_edit)))
        vbox.addWidget(SidebarField("Description", _swappable(self._desc_lbl, self._desc_edit)))
        vbox.addWidget(SidebarField("Start", _swappable(self._start_lbl, self._start_edit)))
        vbox.addWidget(SidebarField("Author(s)", _swappable(self._authors_lbl, self._authors_edit)))
        vbox.addWidget(SidebarField("Tags", self._tags_field))
        vbox.addWidget(SidebarField("Initial State", self._initial_lbl))
        vbox.addWidget(SidebarField("Recovery State", self._recovery_lbl))

        self.set_content_layout(vbox)

        self._name_edit.textChanged.connect(self._on_name_changed)
        self._version_edit.textChanged.connect(self._on_version_changed)
        self._desc_edit.textChanged.connect(self._on_desc_changed)
        self._start_edit.textChanged.connect(self._on_start_changed)
        self._authors_edit.textChanged.connect(self._on_authors_changed)
        self._tags_field.tags_changed.connect(self._on_tags_changed)

        self.set_config(None)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_file(self, filename: str | None) -> None:
        self._file_lbl.setText(filename or _PLACEHOLDER)

    def set_config(self, config: BotConfig | None) -> None:
        self._config = config
        self._suppress = True
        if config is None:
            for lbl in (
                self._name_lbl,
                self._version_lbl,
                self._desc_lbl,
                self._start_lbl,
                self._authors_lbl,
                self._initial_lbl,
                self._recovery_lbl,
            ):
                lbl.setText(_PLACEHOLDER)
            self._tags_field.set_tags([])
            for w in (
                self._name_edit,
                self._version_edit,
                self._start_edit,
                self._authors_edit,
            ):
                w.setText("")
            self._desc_edit.setPlainText("")
            self._set_label_mode(True)
        else:
            self._name_lbl.setText(config.name or _PLACEHOLDER)
            self._version_lbl.setText(config.version or _PLACEHOLDER)
            self._desc_lbl.setText(config.description or _PLACEHOLDER)
            self._start_lbl.setText(config.start_location or _PLACEHOLDER)
            self._authors_lbl.setText(config.authors or _PLACEHOLDER)
            self._name_edit.setText(config.name or "")
            self._version_edit.setText(config.version or "")
            self._desc_edit.setPlainText(config.description or "")
            self._start_edit.setText(config.start_location or "")
            self._authors_edit.setText(config.authors or "")
            self._tags_field.set_tags(config.tags)
            self._set_role_labels(config.states, config.initial, config.recovery_state)
        self._suppress = False

    @property
    def initial_text(self) -> str:
        """Text shown in the read-only Initial State row."""
        return self._initial_lbl.text()

    @property
    def recovery_text(self) -> str:
        """Text shown in the read-only Recovery State row."""
        return self._recovery_lbl.text()

    def refresh_states(self, states: list) -> None:
        if self._config is None:
            return
        self._set_role_labels(states, self._config.initial, self._config.recovery_state)

    def set_editable(self, editable: bool) -> None:
        if self._config is None:
            return
        self._set_label_mode(not editable)

    # ------------------------------------------------------------------
    # Change handlers
    # ------------------------------------------------------------------

    def _on_name_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.name = text
        self._name_lbl.setText(text or _PLACEHOLDER)
        self.metadata_changed.emit()

    def _on_version_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.version = text
        self._version_lbl.setText(text or _PLACEHOLDER)
        self.metadata_changed.emit()

    def _on_desc_changed(self) -> None:
        if self._suppress or self._config is None:
            return
        text = self._desc_edit.toPlainText()
        self._config.description = text or None
        self._desc_lbl.setText(text or _PLACEHOLDER)
        self.metadata_changed.emit()

    def _on_start_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.start_location = text or None
        self._start_lbl.setText(text or _PLACEHOLDER)
        self.metadata_changed.emit()

    def _on_authors_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.authors = text or None
        self._authors_lbl.setText(text or _PLACEHOLDER)
        self.metadata_changed.emit()

    def _on_tags_changed(self, tags: list) -> None:
        if self._suppress or self._config is None:
            return
        # _TagsField hands them back in BOT_TAGS order, which is what gets saved.
        self._config.tags = list(tags)
        self.metadata_changed.emit()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _set_label_mode(self, label_mode: bool) -> None:
        """Switch all switchable fields between display labels and edit widgets."""
        for lbl, edit in self._switchable_pairs:
            lbl.setVisible(label_mode)
            edit.setVisible(not label_mode)
        self._tags_field.set_editable(not label_mode)

    def _set_role_labels(self, states: list, initial_id: str, recovery_id: str) -> None:
        """Show the label (or id) of whichever state currently holds each role.

        Re-derived from *states* rather than cached, because renaming a state has to
        move its name here too.
        """
        display = {state.id: (state.label or state.id) for state in states}
        self._initial_lbl.setText(display.get(initial_id, _PLACEHOLDER))
        self._recovery_lbl.setText(display.get(recovery_id, _PLACEHOLDER))
