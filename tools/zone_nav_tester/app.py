"""Zone navigation tester: map zone transitions by playing, and test routes, on one client.

A plain window, not embedded, hooked to one Wizard101 client. It always shows where the
client is, and a tree of the current world rooted at its hub:

    green   a route exists on verified links
    amber   a route exists only over tentative links: guessed from game data, never run
    grey    no route yet

TEST tab
    Pick a world and a zone. Preview lists the steps the navigator would take, or -- when
    there is no route -- the hops down the tree that still need recording. Execute runs
    bartlebot.nodes.zone_navigation.navigate(), the code the navigation node will use. When a
    run over tentative links succeeds, one click marks them verified.

    Walk only and No hub button leave those links out of routes and out of the tree colours.
    A teleporter usually wins, so this is how a street route gets previewed, run and seen as
    mapped. Getting to the world and regrouping still use the spiral door and the hub button.

    Closing the window unhooks the client: any running route is stopped first, and the
    client is released before the process exits.

MAP tab
    Tick Record, then play. Each zone change becomes a card with its kind detected (see
    recorder.py) and its coordinates filled in: a walk's trigger is matched to the nearest
    volume in the zone's volumes.xml, and where you landed to the nearest named location in
    the destination's gamedata.bin. That landing point is saved as the destination's arrival
    point for the zone you came from, which is the default reset for the walk back. Check the
    card, change the kind if it is wrong, and Save -- it goes straight into
    bartlebot/data/zone_nav.json as a verified link, since you just used it in the game. A
    recording never silently replaces a verified link that already exists. Review with git diff.

USAGE
    uv run python -m tools.zone_nav_tester              # the first client found
    uv run python -m tools.zone_nav_tester --client 2   # 1-based

Hub-button transitions, cross-world teleports, housing, and restricted zones are never
recorded, and navigation never routes through a restricted zone.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import signal
import sys
import time
from dataclasses import dataclass, field

import qasync
from PyQt6.QtGui import QBrush, QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from bartlebot import worlds, zone_names, zone_nav
from bartlebot.nodes import node_helpers, wizard_city, zone_navigation
from bartlebot.nodes.paths import HUB_BUTTON_PATH, UNIVERSE_TELEPORT_BUTTON
from bartlebot.restricted_zones import check_zone
from tools.zone_nav_tester import edits, recorder, tree
from tools.zone_nav_tester.game_data import GameData

logger = logging.getLogger("zone_nav_tester")

POLL_SECONDS = 0.2
VOLUME_MATCH_DISTANCE = 400.0
ARRIVAL_MATCH_DISTANCE = 600.0
SAFETY_DELAY = 3.0
UNHOOK_TIMEOUT = 10.0

STATUS_COLORS = {tree.VERIFIED: "#2e7d32", tree.TENTATIVE: "#b26a00", tree.NO_DATA: "#8a8a8a"}
HERE_BACKGROUND = "#d6e9ff"
KINDS = (recorder.WALK, recorder.OBJECT, recorder.LIST, recorder.RIDE)

DEFAULT_SPEED_MULTIPLIER = 5.0
SPEED_MULTIPLIER_RANGE = (0.1, 20.0)


class ConsoleMachine:
    """The slice of BotMachine the zone-entry nodes use: log() and get_option()."""

    def log(self, msg: str, level: int = logging.INFO) -> None:
        logging.getLogger("zone_nav_tester.run").log(level, msg)

    def get_option(self, name: str, default=None):
        return SAFETY_DELAY if name == "Safety Delay" else default


class _WidgetLogHandler(logging.Handler):
    def __init__(self, widget: QPlainTextEdit) -> None:
        super().__init__()
        self._widget = widget
        self.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        self._widget.appendPlainText(self.format(record))


@dataclass
class Draft:
    """What saving the pending transition would write, and what the user should know first."""

    link: dict | None
    arrival: dict | None
    notes: list[str] = field(default_factory=list)


def _label(zone: str) -> str:
    return zone_names.label(zone)


def _world_label(nav: zone_nav.NavMap, key: str) -> str:
    return worlds.label(key) if worlds.display_names(key) else _label(nav.worlds[key].hub)


class TesterWindow(QMainWindow):
    def __init__(self, client, game: GameData) -> None:
        super().__init__()
        self.client = client
        self.game = game
        self.recorder = recorder.Recorder(self._may_be_ride_zone)
        self.sample: recorder.Sample | None = None
        self.shown_zone: str | None = None
        self.pending: recorder.Transition | None = None
        self.executing: asyncio.Task | None = None
        self.last_outcome: zone_navigation.Outcome | None = None
        self.released = asyncio.Event()
        """Set once the client is unhooked and the window has really closed."""
        self._poller: asyncio.Task | None = None
        self._release_task: asyncio.Task | None = None
        self._unhooked = False
        self._speed_original: int | None = None
        """The client's un-modified speed_multiplier value, read once so it can be restored."""
        self.setWindowTitle("Zone Navigation Tester")
        self.resize(1200, 820)
        self._build()
        self._fill_worlds()

    def start(self) -> None:
        self._poller = asyncio.ensure_future(self.poll())

    # ── Shutdown ──────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # noqa: N802
        """Unhook before closing, the way MainWindow does.

        Unhooking is async, and once the window is gone Qt may stop the loop before an await
        can finish -- which leaves the hooks in the game, and the client cannot be hooked
        again until it is relaunched. So the first close is refused, the release runs as a
        task, and the task closes the window again when it is done.
        """
        if self._unhooked:
            super().closeEvent(event)
            self.released.set()
            return
        event.ignore()
        if self._release_task is None:
            self.setEnabled(False)
            self.setWindowTitle("Zone Navigation Tester — unhooking…")
            self._release_task = asyncio.ensure_future(self._release_and_close())

    async def _release_and_close(self) -> None:
        for task in (self.executing, self._poller):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        if self._speed_original is not None:
            with contextlib.suppress(Exception):
                await self.client.client_object.write_speed_multiplier(self._speed_original)
        try:
            await asyncio.wait_for(self.client.close(), UNHOOK_TIMEOUT)
            logger.info("Client unhooked")
        except Exception:
            logger.exception("Unhooking the client failed; relaunch the game before hooking it again")
        self._unhooked = True
        self.close()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)

        self.zone_label = QLabel("—")
        self.zone_label.setFont(QFont(self.font().family(), 12, QFont.Weight.Bold))
        self.name_label = QLabel("—")
        self.state_label = QLabel("—")
        for label in (self.zone_label, self.name_label, self.state_label):
            label.setTextInteractionFlags(
                label.textInteractionFlags() | label.textInteractionFlags().TextSelectableByMouse
            )
            outer.addWidget(label)

        speed_row = QHBoxLayout()
        self.speed_check = QCheckBox("Speedhack")
        self.speed_check.toggled.connect(self._on_speed_toggled)
        speed_row.addWidget(self.speed_check)
        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setDecimals(1)
        self.speed_spin.setSingleStep(0.1)
        self.speed_spin.setRange(*SPEED_MULTIPLIER_RANGE)
        self.speed_spin.setValue(DEFAULT_SPEED_MULTIPLIER)
        self.speed_spin.setSuffix("x")
        self.speed_spin.valueChanged.connect(self._on_speed_value_changed)
        speed_row.addWidget(self.speed_spin)
        speed_row.addStretch(1)
        outer.addLayout(speed_row)

        split = QSplitter()
        self.tree_widget = QTreeWidget()
        self.tree_widget.setHeaderLabel("World")
        split.addWidget(self.tree_widget)
        tabs = QTabWidget()
        tabs.addTab(self._build_test_tab(), "Test")
        tabs.addTab(self._build_map_tab(), "Map")
        split.addWidget(tabs)
        split.setSizes([450, 750])

        vertical = QSplitter()
        vertical.setOrientation(vertical.orientation().Vertical)
        vertical.addWidget(split)
        self.log_view = QPlainTextEdit(readOnly=True)
        self.log_view.setMaximumBlockCount(3000)
        vertical.addWidget(self.log_view)
        vertical.setSizes([600, 220])
        outer.addWidget(vertical)
        self.setCentralWidget(central)

        handler = _WidgetLogHandler(self.log_view)
        for name in ("zone_nav_tester", "bartlebot"):
            logging.getLogger(name).addHandler(handler)

        legend = "  ".join(
            f'<span style="color:{STATUS_COLORS[s]}">■ {text}</span>'
            for s, text in ((tree.VERIFIED, "verified"), (tree.TENTATIVE, "tentative"), (tree.NO_DATA, "no data"))
        )
        self.statusBar().addPermanentWidget(QLabel(legend))

    def _build_test_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self.world_combo = QComboBox()
        self.world_combo.currentIndexChanged.connect(self._fill_zones)
        self.zone_combo = QComboBox()
        self.zone_combo.setMinimumWidth(320)
        row.addWidget(QLabel("World"))
        row.addWidget(self.world_combo)
        row.addWidget(QLabel("Zone"))
        row.addWidget(self.zone_combo, 1)
        layout.addLayout(row)

        self.tentative_check = QCheckBox("Include tentative links")
        layout.addWidget(self.tentative_check)
        avoid_row = QHBoxLayout()
        self.walk_only_check = QCheckBox("Walk only (no teleporters or lists)")
        self.no_hub_check = QCheckBox("No hub button")
        for box in (self.walk_only_check, self.no_hub_check):
            box.setToolTip(
                "Leaves these links out of routes and of the tree colours, so you can see what is mapped on foot."
            )
            box.toggled.connect(self._avoid_changed)
            avoid_row.addWidget(box)
        avoid_row.addStretch(1)
        layout.addLayout(avoid_row)

        buttons = QHBoxLayout()
        self.preview_button = QPushButton("Preview")
        self.preview_button.clicked.connect(self._preview)
        self.execute_button = QPushButton("Execute")
        self.execute_button.clicked.connect(self._execute)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop)
        self.verify_button = QPushButton("Mark verified")
        self.verify_button.setEnabled(False)
        self.verify_button.clicked.connect(self._mark_verified)
        for button in (self.preview_button, self.execute_button, self.stop_button, self.verify_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.test_output = QPlainTextEdit(readOnly=True)
        layout.addWidget(self.test_output, 1)
        return page

    def _build_map_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.record_check = QCheckBox("Record zone transitions")
        self.record_check.toggled.connect(lambda _on: self.recorder.reset())
        layout.addWidget(self.record_check)

        self.card = QGroupBox("Recorded transition")
        card = QVBoxLayout(self.card)
        self.card_summary = QLabel("Nothing recorded yet. Tick Record, then change zones in the game.")
        self.card_summary.setWordWrap(True)
        card.addWidget(self.card_summary)
        kind_row = QHBoxLayout()
        kind_row.addWidget(QLabel("Kind"))
        self.kind_combo = QComboBox()
        self.kind_combo.addItems(KINDS)
        self.kind_combo.currentIndexChanged.connect(self._show_card)
        kind_row.addWidget(self.kind_combo)
        self.sequential_check = QCheckBox("Sequential (teleporter serves one player at a time)")
        self.sequential_check.toggled.connect(self._show_card)
        kind_row.addWidget(self.sequential_check)
        kind_row.addStretch(1)
        card.addLayout(kind_row)
        self.declare_from = QCheckBox()
        self.declare_to = QCheckBox()
        self.replace_check = QCheckBox("Replace the verified link with this recording")
        for box in (self.declare_from, self.declare_to, self.replace_check):
            box.toggled.connect(self._show_card)
            card.addWidget(box)
        self.card_details = QPlainTextEdit(readOnly=True)
        self.card_details.setFont(QFont("Consolas", 9))
        card.addWidget(self.card_details, 1)
        actions = QHBoxLayout()
        self.save_button = QPushButton("Save link")
        self.save_button.clicked.connect(self._save)
        self.discard_button = QPushButton("Discard")
        self.discard_button.clicked.connect(self._discard)
        actions.addWidget(self.save_button)
        actions.addWidget(self.discard_button)
        actions.addStretch(1)
        card.addLayout(actions)
        layout.addWidget(self.card, 1)
        self._show_card()
        return page

    def _fill_worlds(self) -> None:
        nav = zone_nav.load()
        chosen = self.world_combo.currentData()
        self.world_combo.blockSignals(True)
        self.world_combo.clear()
        for key in nav.worlds:
            self.world_combo.addItem(_world_label(nav, key), key)
        index = self.world_combo.findData(chosen)
        self.world_combo.setCurrentIndex(max(index, 0))
        self.world_combo.blockSignals(False)
        self._fill_zones()

    def _fill_zones(self) -> None:
        nav = zone_nav.load()
        key = self.world_combo.currentData()
        chosen = self.zone_combo.currentData()
        self.zone_combo.clear()
        if key is None:
            return
        world = nav.worlds[key]
        colours = tree.statuses(nav, key, self._avoid())
        for zone in sorted(world.destinations(), key=_label):
            self.zone_combo.addItem(self._zone_text(world, zone), zone)
            self.zone_combo.setItemData(
                self.zone_combo.count() - 1, QBrush(QColor(STATUS_COLORS[colours[zone]])), 9
            )  # 9 = Qt.ItemDataRole.ForegroundRole
        self.zone_combo.setCurrentIndex(max(self.zone_combo.findData(chosen), 0))

    @staticmethod
    def _zone_text(world: zone_nav.World, zone: str) -> str:
        # The zone id is always shown, not just when a label override is set: two zones can
        # share the game's own display name too (Empyrea's two "Chaos Jungle" rows), and the
        # label alone can't be told apart from its sibling in a picker without it.
        marks = [text for flag, text in ((zone == world.hub, "hub"), (zone == world.door, "spiral door")) if flag]
        return f"{_label(zone)}  ({zone})" + (f"   [{', '.join(marks)}]" if marks else "")

    # ── Speedhack ─────────────────────────────────────────────────────────────

    def _modified_speed(self) -> int:
        return round((self.speed_spin.value() - 1) * 100)

    @qasync.asyncSlot(bool)
    async def _on_speed_toggled(self, checked: bool) -> None:
        try:
            if checked:
                self._speed_original = await self.client.client_object.speed_multiplier()
                await self.client.client_object.write_speed_multiplier(self._modified_speed())
                logger.info("Speedhack enabled at %.1fx", self.speed_spin.value())
            elif self._speed_original is not None:
                await self.client.client_object.write_speed_multiplier(self._speed_original)
                logger.info("Speedhack disabled")
                self._speed_original = None
        except Exception:
            logger.exception("Failed to change the speed multiplier")

    @qasync.asyncSlot(float)
    async def _on_speed_value_changed(self, _value: float) -> None:
        if not self.speed_check.isChecked():
            return
        try:
            await self.client.client_object.write_speed_multiplier(self._modified_speed())
        except Exception:
            logger.exception("Failed to change the speed multiplier")

    # ── Polling ───────────────────────────────────────────────────────────────

    async def poll(self) -> None:
        while True:
            try:
                sample = await self._read_sample()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("Sample failed", exc_info=True)
                await asyncio.sleep(1.0)
                continue
            self._show_sample(sample)
            if self.record_check.isChecked() and self.executing is None:
                for transition in self.recorder.feed(sample):
                    self._on_transition(transition)
            if self.speed_check.isChecked():
                # A zone or realm change resets the game's speed multiplier; keep it pinned.
                await self._maintain_speed()
            await asyncio.sleep(POLL_SECONDS)

    async def _maintain_speed(self) -> None:
        try:
            wanted = self._modified_speed()
            if await self.client.client_object.speed_multiplier() != wanted:
                await self.client.client_object.write_speed_multiplier(wanted)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("Failed to maintain the speed multiplier", exc_info=True)

    async def _read_sample(self) -> recorder.Sample:
        c = self.client
        loading = await c.is_loading()
        pos = await c.body.position()
        if loading:
            return recorder.Sample(time.monotonic(), None, (pos.x, pos.y, pos.z), loading=True)
        return recorder.Sample(
            time.monotonic(),
            await c.zone_name(),
            (pos.x, pos.y, pos.z),
            prompt=await c.is_in_npc_range(),
            list_open=await node_helpers.is_visible_by_path(c, UNIVERSE_TELEPORT_BUTTON),
            hub_enabled=await node_helpers.is_enabled_by_path(c, HUB_BUTTON_PATH),
            speed_boosted=self.speed_check.isChecked(),
        )

    def _show_sample(self, sample: recorder.Sample) -> None:
        self.sample = sample
        x, y, z = sample.xyz
        flags = [
            name
            for on, name in (
                (sample.loading, "LOADING"),
                (sample.prompt, "prompt"),
                (sample.list_open, "zone list open"),
                (sample.hub_enabled, "hub button ready"),
            )
            if on
        ]
        self.state_label.setText(f"XYZ({x:.2f}, {y:.2f}, {z:.2f})    {'  ·  '.join(flags)}")
        if sample.zone is None or sample.zone == self.shown_zone:
            return
        self.shown_zone = sample.zone
        self.zone_label.setText(sample.zone)
        names = zone_names.display_names(sample.zone)
        restricted = check_zone(sample.zone)
        self.name_label.setText(
            (_label(sample.zone) + (f"   ({' / '.join(names[1:])})" if len(names) > 1 else ""))
            + (f"   RESTRICTED: {restricted.describe()}" if restricted else "")
        )
        self._refresh_tree()

    def _refresh_tree(self) -> None:
        self.tree_widget.clear()
        zone = self.shown_zone
        nav = zone_nav.load()
        world = nav.world_of(zone) if zone else None
        if world is None:
            self.tree_widget.addTopLevelItem(QTreeWidgetItem([f"{zone or '—'} is not in a mapped world"]))
            return

        built = tree.build(nav, world.key, self.game.neighbours)
        colours = tree.statuses(nav, world.key, self._avoid())

        def item_for(z: str) -> QTreeWidgetItem:
            item = QTreeWidgetItem([self._zone_text(world, z)])
            item.setForeground(0, QBrush(QColor(STATUS_COLORS[colours[z]])))
            item.setToolTip(0, f"{z}\n{colours[z]}")
            if z == zone:
                self._highlight(item)
            for child in sorted(built.children.get(z, []), key=_label):
                item.addChild(item_for(child))
            return item

        self.tree_widget.setHeaderLabel(_world_label(nav, world.key))
        if zone not in world.zones:
            here = QTreeWidgetItem([f"You are here, not on the map: {_label(zone)}"])
            here.setToolTip(0, zone)
            self._highlight(here)
            self.tree_widget.addTopLevelItem(here)
        self.tree_widget.addTopLevelItem(item_for(built.root))
        if built.unattached:
            loose = QTreeWidgetItem(["Not linked to the hub in the game's zone graph"])
            for z in sorted(built.unattached, key=_label):
                loose.addChild(item_for(z))
            self.tree_widget.addTopLevelItem(loose)
        self.tree_widget.expandAll()

    @staticmethod
    def _highlight(item: QTreeWidgetItem) -> None:
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        item.setBackground(0, QBrush(QColor(HERE_BACKGROUND)))
        item.setText(0, "▶ " + item.text(0))

    # ── Test ──────────────────────────────────────────────────────────────────

    def _preview(self) -> None:
        target = self.zone_combo.currentData()
        if target is None:
            return
        self.test_output.setPlainText("\n".join(self._describe_route(target)))

    def _avoid(self) -> set[str]:
        """The kinds of link the Test tab's checkboxes leave out of routes and colours."""
        avoid = {zone_nav.OBJECT, zone_nav.LIST} if self.walk_only_check.isChecked() else set()
        return avoid | ({zone_nav.HUB} if self.no_hub_check.isChecked() else set())

    def _avoid_changed(self) -> None:
        self._refresh_tree()
        self._fill_zones()

    def _describe_route(self, target: str) -> list[str]:
        nav = zone_nav.load()
        world = nav.world_of(target)
        start = self.shown_zone
        include = self.tentative_check.isChecked()
        avoid = self._avoid()
        lines = [f"From {_label(start) if start else '?'} to {_label(target)}"]
        if avoid:
            lines.append(f"avoiding: {', '.join(sorted(avoid))}")
        lines.append("")

        via_door = start is None or not zone_navigation.in_reach(nav, start, world)
        cooldown = 0.0 if self.sample and self.sample.hub_enabled else zone_nav.HUB_BUTTON_COOLDOWN
        route = (
            None
            if via_door
            else zone_nav.plan(nav, start, target, hub_cooldown=cooldown, include_unverified=include, avoid=avoid)
        )
        if route is None:
            # What navigate() does next: the spiral door, then plan from where it leaves you.
            via_door = True
            entry = nav.entry_world(world.key)
            door = wizard_city.RAVENWOOD_ZONE if entry == "WizardCity" else nav.worlds[entry].door
            route = zone_nav.plan(nav, door, target, include_unverified=include, avoid=avoid)
            lines.append(f"0. spiral door to {_world_label(nav, entry)} (lands in {_label(door)})")

        if route is None:
            return lines + self._explain_missing(nav, world, target, include)

        total = 0.0
        for i, step in enumerate(route, start=1):
            total += zone_nav.step_cost(step)
            flags = [] if step.verified else ["tentative"]
            if isinstance(runnable := zone_navigation.step_call(step), str):
                flags.append(f"CANNOT RUN: {runnable}")
            lines.append(
                f"{i}. {step.kind:<6} {_label(step.from_zone)} -> {_label(step.to_zone)}"
                + (f"   ({'; '.join(flags)})" if flags else "")
            )
        lines += ["", f"about {total:.0f} s after reaching the world" if via_door else f"about {total:.0f} s"]
        return lines

    def _explain_missing(self, nav: zone_nav.NavMap, world: zone_nav.World, target: str, include: bool) -> list[str]:
        lines = ["No route. Nothing will be executed.", ""]
        built = tree.build(nav, world.key, self.game.neighbours)
        hops = tree.hops_to(nav, built, target, self._avoid())
        if not hops and target != built.root:
            lines.append(f"{_label(target)} is not linked to {_label(world.hub)} in the game's zone graph.")
            lines.append("Record the transition into it from whichever zone leads there.")
        else:
            lines.append("The way down the tree from the hub, and what each hop needs:")
            for src, dst, status in hops:
                need = {
                    tree.VERIFIED: "ok",
                    tree.TENTATIVE: "tentative" + ("" if include else " -- tick Include tentative links to use it"),
                    tree.NO_DATA: "RECORD THIS",
                }[status]
                lines.append(f"  {_label(src)} -> {_label(dst)}: {need}")
        if world.entered_from and not any(s.to_zone == world.hub for s in nav.links):
            parent = nav.worlds[world.entered_from]
            lines.append(f"Also record the way into {_label(world.hub)} from {_world_label(nav, parent.key)}.")
        return lines

    @qasync.asyncSlot()
    async def _execute(self) -> None:
        target = self.zone_combo.currentData()
        if target is None or self.executing is not None:
            return
        self.verify_button.setEnabled(False)
        self.test_output.setPlainText("\n".join(self._describe_route(target)) + "\n\nExecuting…")
        self._set_running(True)
        self.executing = asyncio.ensure_future(
            zone_navigation.navigate(
                ConsoleMachine(),
                [self.client],
                target,
                include_unverified=self.tentative_check.isChecked(),
                avoid=self._avoid(),
            )
        )
        try:
            outcome = await self.executing
        except asyncio.CancelledError:
            self.test_output.appendPlainText("Stopped.")
            return
        finally:
            self.executing = None
            self.recorder.reset()
            self._set_running(False)

        self.last_outcome = outcome
        if not outcome.success:
            self.test_output.appendPlainText(f"FAILED: {outcome.reason}")
            return
        self.test_output.appendPlainText(f"Arrived after {len(outcome.taken)} step(s).")
        if outcome.unverified:
            self.verify_button.setText(f"Mark {len(outcome.unverified)} tentative link(s) verified")
            self.verify_button.setEnabled(True)
            for step in outcome.unverified:
                self.test_output.appendPlainText(
                    f"  worked: {step.kind} {_label(step.from_zone)} -> {_label(step.to_zone)}"
                )

    def _stop(self) -> None:
        if self.executing is not None:
            self.executing.cancel()

    def _set_running(self, running: bool) -> None:
        self.execute_button.setEnabled(not running)
        self.stop_button.setEnabled(running)

    def _mark_verified(self) -> None:
        if self.last_outcome is None:
            return
        raw = self._read_raw()
        changed = edits.mark_verified(raw, self.last_outcome.unverified)
        if self._write_raw(raw):
            logger.info("Marked %d link(s) verified", changed)
            self.verify_button.setEnabled(False)
            self.last_outcome = None

    # ── Map ───────────────────────────────────────────────────────────────────

    def _may_be_ride_zone(self, zone: str) -> bool:
        return zone_nav.load().world_of(zone) is not None and zone not in self._declared()

    @staticmethod
    def _declared() -> set[str]:
        return {z for world in zone_nav.load().worlds.values() for z in world.zones}

    def _on_transition(self, t: recorder.Transition) -> None:
        nav = zone_nav.load()
        where = f"{_label(t.from_zone)} -> {_label(t.to_zone)}"
        if check_zone(t.from_zone) or check_zone(t.to_zone):
            logger.warning("Not recorded: %s touches a restricted zone", where)
            return
        if t.kind == recorder.HUB:
            logger.info("Hub button %s: implied for every zone, nothing to record", where)
            return
        if "Housing" in t.from_zone or "Housing" in t.to_zone:
            logger.info("Not recorded: %s is housing", where)
            return
        src, dst = nav.world_of(t.from_zone), nav.world_of(t.to_zone)
        if src is None or dst is None or nav.entry_world(src.key) != nav.entry_world(dst.key):
            logger.info("Not recorded: %s crosses worlds (spiral door, friend teleport or recall)", where)
            return
        if self.pending is not None:
            logger.warning("Unsaved transition replaced: %s -> %s", self.pending.from_zone, self.pending.to_zone)
        self.pending = t
        logger.info("Recorded %s %s", t.kind, where)
        self.kind_combo.blockSignals(True)
        self.kind_combo.setCurrentText(t.kind)
        self.kind_combo.blockSignals(False)
        self.declare_from.setChecked(False)
        self.declare_to.setChecked(False)
        self.sequential_check.setChecked(False)
        self.replace_check.setChecked(False)
        self._show_card()

    def _keeps_verified_link(self, kind: str) -> bool:
        """True when a verified link already covers the pending transition and is not being replaced.

        Recording a gate that already works must not swap tested coordinates for untested
        ones: saving then records only the arrival point, unless the user asks to replace it.
        """
        t = self.pending
        link_kind = zone_nav.LIST if kind == recorder.LIST else kind
        verified = any(
            s.verified and (s.kind, s.from_zone, s.to_zone) == (link_kind, t.from_zone, t.to_zone)
            for s in zone_nav.load().links
        )
        self.replace_check.setVisible(verified)
        return verified and not self.replace_check.isChecked()

    def _show_card(self) -> None:
        t = self.pending
        self.save_button.setEnabled(False)
        self.discard_button.setEnabled(t is not None)
        if t is None:
            for box in (self.declare_from, self.declare_to, self.sequential_check, self.replace_check):
                box.setVisible(False)
            self.card_details.setPlainText("")
            return

        kind = self.kind_combo.currentText()
        self.card_summary.setText(
            f"<b>{kind}</b> &nbsp; {_label(t.from_zone)} → {_label(t.to_zone)}"
            f"<br><small>{t.from_zone} → {t.to_zone}"
            + (f"<br>detected as {t.kind}" if t.kind != kind else "")
            + (f"<br>ride through {t.ride_zone}" if t.ride_zone else "")
            + "</small>"
        )
        self.sequential_check.setVisible(kind == recorder.OBJECT)
        nav = zone_nav.load()
        declared = self._declared()
        ready = True
        for box, zone in ((self.declare_from, t.from_zone), (self.declare_to, t.to_zone)):
            box.setVisible(zone not in declared)
            if zone not in declared:
                world = nav.world_of(zone)
                box.setText(f"Add {_label(zone)} ({zone}) to {world.key if world else '?'}'s zones")
                ready = ready and box.isChecked()

        draft = self._draft(kind)
        keep = self._keeps_verified_link(kind)
        lines = [zone_nav.dumps(draft.link).rstrip() if draft.link else "(nothing can be saved for this kind)"]
        if keep:
            lines.insert(0, "A VERIFIED link already covers this. Save keeps it and records only the arrival point.\n")
        if draft.arrival:
            lines.append(f"arrival in {_label(t.to_zone)} from {_label(t.from_zone)}: {draft.arrival}")
        lines += ["", *draft.notes]
        if not ready:
            lines.append("Tick the Add boxes above: a link may only name zones on the map.")
        self.card_details.setPlainText("\n".join(lines))
        self.save_button.setText("Save arrival point only" if keep else "Save link")
        self.save_button.setEnabled(ready and (draft.link is not None or (keep and draft.arrival is not None)))

    def _draft(self, kind: str) -> Draft:
        t = self.pending
        nav = zone_nav.load()
        notes: list[str] = []
        link: dict | None = {"kind": kind, "from": t.from_zone, "to": t.to_zone}
        # A ride is boarded the way its first transition went: through a gate, or by interacting.
        board = (t.board or recorder.OBJECT) if kind == recorder.RIDE else kind
        if kind == recorder.RIDE:
            link["board"] = board
        prompt_xyz, pre_prompt_xyz = t.prompt_xyz, t.pre_prompt_xyz

        if board == recorder.WALK:
            link["xyz"] = edits.xyz(t.trigger_xyz)
            world = nav.world_of(t.from_zone)
            if world is not None and world.arrival(t.from_zone, t.to_zone):
                notes.append(f"reset_xyz: the arrival point in {_label(t.from_zone)} from {_label(t.to_zone)}")
            elif t.far_xyz is not None:
                link["reset_xyz"] = edits.xyz(t.far_xyz)
                notes.append(f"reset_xyz: where you stood {recorder.RESET_DISTANCE:.0f}+ units before the trigger")
            else:
                notes.append("no reset_xyz: you did not move far enough before the trigger")
            if match := edits.nearest(self.game.volumes(t.from_zone), t.trigger_xyz, VOLUME_MATCH_DISTANCE):
                link["volume"] = match[0]
                notes.append(f"volume: {match[0]} at {match[1][0]:.0f}, {match[1][1]:.0f} ({match[2]:.0f} units away)")
            else:
                notes.append(
                    f"WARNING: no volume within {VOLUME_MATCH_DISTANCE:.0f} units. If this was a teleport, a "
                    f"mark/recall or a friend port rather than a gate, discard it."
                )
        elif prompt_xyz is None:
            notes.append(
                "No interact prompt was seen before the zone change, so there is no interact position. "
                "Record it again, or pick walk if it was a gate."
            )
            link = None
        elif pre_prompt_xyz is None:
            notes.append(
                f"You were never {recorder.PROMPT_CLEAR_DISTANCE:.0f}+ units from the object before its prompt "
                f"came up, so there is no reset position where the prompt is gone. Walk away from the object, "
                f"come back, and use it again."
            )
            link = None
        else:
            link["interact_xyz"] = edits.xyz(prompt_xyz)
            link["reset_xyz"] = edits.xyz(pre_prompt_xyz)
            if kind == recorder.OBJECT and self.sequential_check.isChecked():
                link["sequential"] = True
            if kind == recorder.LIST:
                notes.append(
                    f"Saves where the list object stands in {_label(t.from_zone)}. {_label(t.to_zone)} "
                    f"joins the network once a list use from there is recorded too."
                )
        if kind == recorder.RIDE and link is not None:
            if t.ride_zone:
                link["ride_zone"] = t.ride_zone
            else:
                notes.append(
                    "No cutscene zone was seen. The Krokotopia boats ride inside the zone they start in and "
                    "cannot be recorded: add them by hand."
                )
                link = None
        if link is not None and kind != recorder.LIST:
            # You just did it in the game, so it has worked in a real client.
            link.update(verified=True, source=edits.SOURCE)

        arrival = None
        if match := edits.nearest(self.game.arrival_points(t.to_zone), t.arrival_xyz, ARRIVAL_MATCH_DISTANCE):
            arrival = edits.xyz(match[1])
            notes.append(f"arrival: the game's location {match[0]!r}, {match[2]:.0f} units from where you landed")
        else:
            arrival = edits.xyz(t.arrival_xyz)
            notes.append("arrival: where you landed; no named location in the game data is near it")
        return Draft(link, arrival, notes)

    def _save(self) -> None:
        t = self.pending
        if t is None:
            return
        kind = self.kind_combo.currentText()
        draft = self._draft(kind)
        keep = self._keeps_verified_link(kind)
        if draft.link is None and not keep:
            return
        nav = zone_nav.load()
        raw = self._read_raw()
        for zone in (t.from_zone, t.to_zone):
            if edits.world_declaring(raw, zone) is None:
                edits.declare_zone(raw, nav.world_of(zone).key, zone)
        if keep:
            verb = "kept the verified link, saved the arrival point"
        elif kind == recorder.LIST:
            edits.add_list_spot(
                raw, t.from_zone, t.to_zone, draft.link["interact_xyz"], draft.link["reset_xyz"], verified=True
            )
            verb = "saved list object"
        else:
            verb = edits.upsert_link(raw, draft.link)
        if draft.arrival is not None:
            edits.set_arrival(raw, t.to_zone, t.from_zone, draft.arrival)
        if self._write_raw(raw):
            logger.info("%s: %s %s -> %s", verb, kind, t.from_zone, t.to_zone)
            self.pending = None
            self._show_card()

    def _discard(self) -> None:
        self.pending = None
        self.card_summary.setText("Discarded.")
        self._show_card()

    # ── The file ──────────────────────────────────────────────────────────────

    @staticmethod
    def _read_raw() -> dict:
        return json.loads(zone_nav.ZONE_NAV_PATH.read_text(encoding="utf-8"))

    def _write_raw(self, raw: dict) -> bool:
        try:
            zone_nav.parse(raw)
        except ValueError as exc:
            logger.error("Not saved -- the map would be invalid:\n%s", exc)
            return False
        zone_nav.ZONE_NAV_PATH.write_text(zone_nav.dumps(raw), encoding="utf-8")
        zone_nav.load.cache_clear()
        self._refresh_tree()
        self._fill_worlds()
        return True


async def _run(app: QApplication, client_number: int) -> int:
    from wizwalker.extensions.wizsprinter import WizSprinter  # noqa: PLC0415

    clients = WizSprinter().get_new_clients()
    if not clients:
        print("No Wizard101 clients found. Start the game and log a wizard in.")
        return 1
    if not 1 <= client_number <= len(clients):
        print(f"--client must be between 1 and {len(clients)}.")
        return 1
    client = clients[client_number - 1]
    window = None
    try:
        await client.activate_hooks()
        game = GameData()
        game.load_graph()
        window = TesterWindow(client, game)
        # Ctrl+C in the terminal closes the window, which unhooks, rather than killing the
        # process with the hooks still in the game.
        signal.signal(signal.SIGINT, lambda *_: window.close())
        window.show()
        window.start()
        await window.released.wait()
    finally:
        if window is None or not window.released.is_set():
            # Hooking or building the window failed: nothing will close the window to unhook.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(client.close(), UNHOOK_TIMEOUT)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client", type=int, default=1, help="1-based client to hook (default: 1)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("wizwalker").setLevel(logging.WARNING)

    app = QApplication(sys.argv)
    # The loop must outlive the window: _run returns only after the client is unhooked.
    app.setQuitOnLastWindowClosed(False)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    with loop:
        return loop.run_until_complete(_run(app, args.client))
