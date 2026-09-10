"""Region CRUD management for the bot canvas editor.

RegionManager — owns the RegionItem visual layer and all region mutation operations.
Accepts callbacks for undo integration (pre_edit_fn) and change notification
(graph_changed_fn) so it has no direct dependency on EditScene's signal types.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QDialog, QGraphicsScene, QInputDialog

from bartlebot.bot.schema import BotConfig, RegionModel
from bartlebot.gui.canvas.region_item import RegionItem

if TYPE_CHECKING:
    from bartlebot.gui.canvas.node_item import NodeItem


class RegionManager:
    """Owns RegionItem creation/deletion and all region mutation methods.

    EditScene constructs one instance per setup() call, passing itself as the
    scene so addItem/removeItem/changed signal wiring works without EditScene
    subclassing or attribute injection.
    """

    def __init__(
        self,
        scene: QGraphicsScene,
        config: BotConfig,
        node_items: dict[str, NodeItem],
        pre_edit_fn: Callable[[str], None],
        graph_changed_fn: Callable[[], None],
        region_locked_fn: Callable[[str], bool] | None = None,
    ) -> None:
        self._scene = scene
        self._config = config
        self._node_items = node_items
        self._pre_edit = pre_edit_fn
        self._graph_changed = graph_changed_fn
        # Whether a region's membership is the library's to decide (a linked region
        # outside an edit). The scene answers; this class keeps no lock state.
        self._region_locked = region_locked_fn or (lambda _rid: False)
        self._region_items: dict[str, RegionItem] = {}
        self._in_structural_edit = False  # suppresses double graph_changed in remove→delete

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def items(self) -> dict[str, RegionItem]:
        return self._region_items

    def setup(self, interactive: bool) -> None:
        """Build RegionItems from config and connect scene.changed."""
        for region in self._config.regions:
            ri = RegionItem(region, self._node_items)
            ri.set_interactive(interactive)
            self._scene.addItem(ri)
            self._region_items[region.id] = ri
        for ri in self._region_items.values():
            self._scene.changed.connect(ri._recompute_rect)
            ri._recompute_rect()

    def teardown(self) -> None:
        """Disconnect signals and clear the item dict (scene.clear() removes the items)."""
        for ri in self._region_items.values():
            try:
                self._scene.changed.disconnect(ri._recompute_rect)
            except (RuntimeError, TypeError):
                pass
        self._region_items.clear()

    def set_interactive(self, interactive: bool) -> None:
        for region in self._region_items.values():
            region.set_interactive(interactive)

    def flush_regions(self) -> None:
        """Remove stale member IDs from regions; auto-delete regions with < 2 members.

        A linked region (one with ``source``) is never auto-deleted: its members are
        the definition's to decide, and a definition may legitimately have one. The
        next compose puts a deleted member back.
        """
        state_ids = {s.id for s in self._config.states}
        for region in list(self._config.regions):
            cleaned = [m for m in region.members if m in state_ids]
            if len(cleaned) < 2 and region.source is None:
                self.delete_region(region.id)
            else:
                region.members[:] = cleaned
                ri = self._region_items.get(region.id)
                if ri is not None:
                    ri._member_ids[:] = cleaned
                    ri._recompute_rect()

    def create_region(self, state_ids: list[str]) -> None:
        if len(state_ids) < 2:
            return
        view = self._scene.views()[0] if self._scene.views() else None
        label, ok = QInputDialog.getText(view, "Create Region", "Region name:")
        if not ok or not label.strip():
            return
        self._pre_edit("Create Region")
        region = RegionModel(
            id=uuid.uuid4().hex[:8],
            label=label.strip(),
            members=list(state_ids),
            color=1,
        )
        self._config.regions.append(region)
        ri = RegionItem(region, self._node_items)
        ri.set_interactive(True)
        self._scene.addItem(ri)
        self._region_items[region.id] = ri
        self._scene.changed.connect(ri._recompute_rect)
        ri._recompute_rect()
        self._graph_changed()

    def add_region(self, region: RegionModel) -> None:
        """Put an already-built region into the config and the scene.

        The caller owns the undo snapshot and the ``graph_changed``; this is the
        building block for a paste that mints a region rather than a user naming one.
        """
        self._config.regions.append(region)
        ri = RegionItem(region, self._node_items)
        ri.set_interactive(True)
        self._scene.addItem(ri)
        self._region_items[region.id] = ri
        self._scene.changed.connect(ri._recompute_rect)
        ri._recompute_rect()

    def delete_region(self, region_id: str) -> None:
        self._pre_edit("Delete Region")
        ri = self._region_items.pop(region_id, None)
        if ri is not None:
            try:
                self._scene.changed.disconnect(ri._recompute_rect)
            except RuntimeError:
                pass
            self._scene.removeItem(ri)
        self._config.regions = [r for r in self._config.regions if r.id != region_id]
        self._graph_changed()

    def rename_region(self, region_id: str) -> None:
        region = next((r for r in self._config.regions if r.id == region_id), None)
        if region is None:
            return
        view = self._scene.views()[0] if self._scene.views() else None
        label, ok = QInputDialog.getText(view, "Rename Region", "Region name:", text=region.label)
        if not ok or not label.strip():
            return
        self._pre_edit("Rename Region")
        region.label = label.strip()
        ri = self._region_items.get(region_id)
        if ri is not None:
            ri.set_label(region.label)
        self._graph_changed()

    def recolor_region(self, region_id: str) -> None:
        region = next((r for r in self._config.regions if r.id == region_id), None)
        if region is None:
            return
        from bartlebot.gui.canvas.edit_scene import _ColorPickerDialog

        view = self._scene.views()[0] if self._scene.views() else None
        dlg = _ColorPickerDialog(current_key=region.color, parent=view)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._pre_edit("Change Region Color")
        region.color = dlg.selected_color()
        ri = self._region_items.get(region_id)
        if ri is not None:
            ri.set_color(region.color)
        self._graph_changed()

    def add_to_region(self, state_ids: list[str], region_id: str) -> None:
        region = next((r for r in self._config.regions if r.id == region_id), None)
        if region is None or self._region_locked(region_id):
            return
        to_add = [sid for sid in state_ids if sid not in region.members]
        if not to_add:
            return
        self._pre_edit("Edit Region")
        for sid in to_add:
            region.members.append(sid)
        ri = self._region_items.get(region_id)
        if ri is not None:
            for sid in to_add:
                ri._member_ids.append(sid)
            ri._recompute_rect()
        self._graph_changed()

    def remove_from_region(self, state_ids: list[str], region_id: str) -> None:
        region = next((r for r in self._config.regions if r.id == region_id), None)
        if region is None or self._region_locked(region_id):
            return
        to_remove = [sid for sid in state_ids if sid in region.members]
        if not to_remove:
            return
        remaining = [m for m in region.members if m not in to_remove]
        self._pre_edit("Edit Region")
        if len(remaining) < 2:
            self._in_structural_edit = True
            self.delete_region(region_id)
            self._in_structural_edit = False
            self._graph_changed()
            return
        for sid in to_remove:
            region.members.remove(sid)
        ri = self._region_items.get(region_id)
        if ri is not None:
            for sid in to_remove:
                if sid in ri._member_ids:
                    ri._member_ids.remove(sid)
            ri._recompute_rect()
        self._graph_changed()
