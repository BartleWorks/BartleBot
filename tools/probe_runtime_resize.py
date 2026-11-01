"""Probe: can a running client change its resolution and UI size, with no relaunch?

BartleBuddy wants the window the user plays in to be large and the followers' small, and
the client that is played can change mid-session. BartleBot sizes a client only at launch,
by patching ``preferences.xml`` and ``config.xml`` (bartleby-core/docs/WINDOW_SIZING.md). This probe asks
what a running client allows, in three parts.

FORCE (Deimos's resolution forcer, without its drag border)
    Two pattern-scanned hooks, ported from Deimos's vendored wizsprinter
    (``resolution_hook.py``): one overrides the width and height the engine's set-mode
    function is called with, the other captures the video manager so its pending-change byte
    can be set. The engine then runs its own apply. The questions: does each pattern still
    match exactly once; does the render come out at the new size; does the window follow
    or need resizing; does the camera keep the right aspect across a 4:3 / 16:9 change;
    what does the HUD do (Deimos never touches UI size); and does all of it hold while the
    window is embedded as a child, as BartleBot embeds it (``--embed``).

VM-DIFF (what the options menu writes)
    Snapshots the video manager before and after the user changes the resolution in the
    game's own options menu, and prints every 32-bit field that changed. If the menu writes
    a size there, setting that field and the pending byte is the game's own route, with no
    set-mode hook at all.

UI-SCAN (where the UI size lives)
    UI size is not a reflected property, so the type dump cannot name it. The user steps the
    UI size through several values in the options menu; after each, the scan keeps only the
    32-bit slots holding that size's stored value (Micro 5, Tiny 0, Small 1, Regular 2,
    Large 3, Huge 4). ``ui-scan try`` then writes a value to the survivors and triggers a
    mode apply at the current size, to see whether the HUD rescales.

RESULTS (2026-10-28, live r806919, one client launched at 800x600, Regular)
    A running client resizes, UI size included, in about two seconds and inside its tile.
    The recipe, in order:

    1. UI size. Write the int32 value of the live ``UISize`` config entry (below). The write
       alone changes nothing on screen; the next mode apply redraws the HUD at the new size.
       Micro (5) works this way although the options menu never offers it, and Huge (4) at
       534x300 draws an absurd HUD, so nothing clamps it.
    2. Resolution. Arm ``SetModeResHook``'s control block {1, w, h}, read the video manager
       ``VideoManagerHook`` captured (it is filled within a frame), and write 1 to the byte at
       manager + 0x2229B. The engine's own apply runs on its next frame; ~1.5 s covers it.
       Both patterns matched exactly once. Disarm the control block after (``release``), so a
       later apply the game makes itself is not overridden.
    3. Window. The apply re-creates the device at w x h but leaves the window where it was:
       until the window is resized the frame is the new size stretched into the old window.
       A top-level window is resized by its frame (``set_client_size``). An embedded one is
       worse: the apply resets the style to 0x00CB0000 (overlapped, caption, sysmenu,
       minimize, **no WS_VISIBLE**) and the parent to none, so the window leaves the tile and
       is hidden. Embedding it again (SetParent, ``win32_window.child_style``, SetWindowPos to
       w x h with SWP_FRAMECHANGED) holds, crisp, with the HUD re-anchored to the new edges.
       BartleBot's ``EmbeddedWindow`` has no re-embed; it captures its style once and attaches
       input in ``embed``, so a re-embed must not attach twice.
    4. Camera. The apply keeps the frustum, so a 4:3 / 16:9 change stretches the 3D view.
       Keep the vertical half-extent t (``viewport_top``) and write left/right to -/+ t * w/h
       on ``selected_camera_controller().gamebryo_camera().cam_view()``. It held 4 s. Only that
       camera was written; Deimos rewrites every controller each tick because combat and the
       others keep their own frustums, and a zone change may rebuild them: unchecked here.
       The options menu's own resolution change does correct the camera itself.

    Frames during an apply can be white or half laid out (one white capture, one with the
    HUD still at the old scale), so a tile should cover the swap for about a second.

    WHERE UISIZE LIVES. Not a reflected property. The config is held as generic key/value
    nodes, one C++ class, vtable at module + 0x2C20BE8 on r806919 (130,933 such objects:
    login keys, zone names, every setting). A node:

        +0x00  vtable                   +0x40  u32 0x20 or 0x28
        +0x10  float value (type 0x08)  +0x44  u8 type: 0x00 string, 0x02 int, 0x08 float
        +0x14  int32 value (type 0x02)  +0x45  u8 0x01 on the live copy, 0x00 on the other
        +0x30  int32, 2 for UISize       +0x50  name, inline, NUL-terminated ("UISize")

    Every setting appears twice; the live copy is the one with +0x45 == 1 (its ``WindowedX``
    holds the window's real x, the other's 0), and writing it is what worked. So: scan
    private memory for 8-aligned slots equal to module + RVA, keep the one whose +0x50 reads
    ``UISize\\0`` and +0x45 is 1, then read and write +0x14. That pass takes ~3 s; nothing
    points into the 0x400 bytes before the node, so no pointer path was found. The RVA
    moves with each game patch. Neighbors in the same block: ``UIScale`` (float, 10.0) and
    ``TextureDetail``. The game may write the changed value to config.xml on exit.

    WHAT THE MENU DOES. Changing resolution in the options menu changes no 32-bit field in
    the first 0x23000 bytes of the video manager that holds a size: 32 fields moved, frame
    counters and a frustum copy (+0x22A40/44, -/+0.849 -> -/+1.133). ``Resolution`` is a
    string node (type 0x00) like ``UISize``'s: writing it and setting the pending byte might
    resize through the game's own path with no set-mode hook. Untested.

    Deimos's drag border (``WS_THICKFRAME`` plus a WndProc hook) is not needed and is left
    out: a size changes only when the app asks for it.

HOW TO RUN
    One client, in the world, out of combat. BartleBot closed: this takes hooks of its own,
    and two processes hooking one client corrupt each other's code cave. Every hook is
    removed before the probe exits; a probe killed mid-run can leave a jump into freed
    memory, so restart the client after one.

        uv run --no-sync --with numpy python tools/probe_runtime_resize.py info
        uv run --no-sync --with numpy python tools/probe_runtime_resize.py force 960 540 --restore
        uv run --no-sync --with numpy python tools/probe_runtime_resize.py force 800 600 --embed --restore
        uv run --no-sync --with numpy python tools/probe_runtime_resize.py vm-diff snap
          (change the resolution in the options menu)
        uv run --no-sync --with numpy python tools/probe_runtime_resize.py vm-diff compare
        uv run --no-sync --with numpy python tools/probe_runtime_resize.py ui-scan start Regular
          (change UI size in the options menu, then for each new size:)
        uv run --no-sync --with numpy python tools/probe_runtime_resize.py ui-scan next Small
        uv run --no-sync --with numpy python tools/probe_runtime_resize.py ui-scan try Tiny

    Screenshots and saved state go to ``--out`` (default: the system temp folder), never the
    repo: they show the wizard, and the repo holds no player data.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import ctypes.wintypes as wt
import json
import re
import struct
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from bartleby.shared.game_install import install_of_client, process_executable
from wizwalker import ClientHandler
from wizwalker.errors import PatternFailed
from wizwalker.memory.hooks import SimpleHook
from wizwalker.memory.memory_reader import Primitive

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MODULE = "WizardGraphicalClient.exe"
UI_SIZES = {"Micro": 5, "Tiny": 0, "Small": 1, "Regular": 2, "Large": 3, "Huge": 4}

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # true physical pixels

GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_CHILD = 0x40000000
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x1, 0x2, 0x4, 0x10


# --------------------------------------------------------------------------- the hooks
# Ported from Deimos-Wizard101/libs/wizsprinter/wizwalker/extensions/wizsprinter/resolution_hook.py.

# Where things are on the video manager, read from the function VideoManagerHook matches rather
# than assumed: the pending byte is +0x2229B on live r806919 and +0x222A3 on the Test realm's exe,
# and a write to the wrong one sets an unrelated byte and reports success.
PENDING_TEST_AT = 0x3D  # 80 B9 disp32 00: cmp byte ptr [rcx + disp32], 0
PENDING_CLEAR_AT = 0x1A0  # C6 83 disp32 00: mov byte ptr [rbx + disp32], 0
DEVICE_LOAD_AT = 0x4A  # 48 8B 89 disp32: mov rcx, [rcx + disp32], the render device
DEVICE_SIZE_OFF = 0x1C  # u32 width, u32 height on the device (the function reads them at +0x150)


class SetModeResHook(SimpleHook):
    """Override setMode's width (edx) and height (r8d) with a forced size."""

    pattern = (
        rb"\x48\x89\x5C\x24\x08\x44\x89\x4C\x24\x20\x44\x89\x44\x24\x18"
        rb"\x89\x54\x24\x10\x55\x56\x57\x41\x54\x41\x55\x41\x56\x41\x57"
        rb"\x48\x81\xEC\xB0\x00\x00\x00"
    )
    instruction_length = 5
    exports = [("control_block", 12)]  # u32 enabled, u32 width, u32 height

    async def bytecode_generator(self, packed_exports):
        ctrl = packed_exports[0][1]
        return (
            b"\x48\xb8"
            + ctrl  # mov rax, control_block
            + b"\x83\x38\x00"  # cmp dword [rax], 0
            + b"\x74\x07"  # je +7
            + b"\x8b\x50\x04"  # mov edx, [rax+4]
            + b"\x44\x8b\x40\x08"  # mov r8d, [rax+8]
            + b"\x48\x89\x5c\x24\x08"  # original: mov [rsp+8], rbx
        )


class VideoManagerHook(SimpleHook):
    """Capture the video manager (rcx) at the per-frame pending-mode check."""

    pattern = (
        rb"\x48\x8B\xC4\x48\x89\x58\x10\x48\x89\x70\x18\x55\x57\x41\x56"
        rb"\x48\x8D\x68\x88\x48\x81\xEC\x60\x01\x00\x00"
        rb"\x0F\x29\x70\xD8\x0F\x29\x78\xC8\x44\x0F\x29\x40\xB8"
        rb"\x48\x8B\x05....\x48\x33\xC4\x48\x89\x45\x20\x48\x8B\xD9"
    )
    instruction_length = 7
    noops = 2
    exports = [("manager_ptr", 8)]

    async def bytecode_generator(self, packed_exports):
        mgr = packed_exports[0][1]
        return (
            b"\x48\xb8"
            + mgr  # mov rax, manager_ptr
            + b"\x48\x89\x08"  # mov [rax], rcx
            + b"\x48\x8b\xc4"  # original: mov rax, rsp
            + b"\x48\x89\x58\x10"  # original: mov [rax+0x10], rbx
        )


async def manager_offsets(handler) -> tuple[int, int]:
    """(pending byte, device pointer) on the video manager, read from code; raises if they can't be trusted.

    Only before VideoManagerHook is installed: the hook overwrites the bytes its pattern matches.
    """
    match = await handler.pattern_scan(VideoManagerHook.pattern, module=MODULE)
    test = await handler.read_bytes(match + PENDING_TEST_AT, 7)
    clear = await handler.read_bytes(match + PENDING_CLEAR_AT, 7)
    device = await handler.read_bytes(match + DEVICE_LOAD_AT, 7)
    if test[:2] != b"\x80\xb9" or clear[:2] != b"\xc6\x83" or device[:3] != b"\x48\x8b\x89":
        raise RuntimeError(f"unexpected code: test {test.hex()}, clear {clear.hex()}, device {device.hex()}")
    pending = struct.unpack("<I", test[2:6])[0]
    if struct.unpack("<I", clear[2:6])[0] != pending:
        raise RuntimeError(f"the test and the clear disagree: {test.hex()}, {clear.hex()}")
    return pending, struct.unpack("<I", device[3:])[0]


class Forcer:
    """Both hooks, installed together and always removed together."""

    def __init__(self, client, *, setmode: bool = True, by_node: bool = False) -> None:
        self.client = client
        self.handler = client.hook_handler
        self.want_setmode = setmode and not by_node
        self.by_node = by_node
        self.setmode: SetModeResHook | None = None
        self.vm: VideoManagerHook | None = None
        self.pending_off = 0
        self.device_off = 0
        self.resolution_node: int | None = None

    async def __aenter__(self) -> Forcer:
        self.pending_off, self.device_off = await manager_offsets(self.handler)
        if self.by_node:
            self.resolution_node = await live_node(self.client, "Resolution")
            if self.resolution_node is None:
                raise RuntimeError("no single live Resolution node")
        await self.handler._check_for_autobot()
        try:
            if self.want_setmode:
                self.setmode = SetModeResHook(self.handler)
                await self.setmode.hook()
            self.vm = VideoManagerHook(self.handler)
            await self.vm.hook()
        except Exception:
            await self.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, *exc) -> None:
        for hook in (self.vm, self.setmode):
            if hook is not None:
                try:
                    await hook.unhook()
                except Exception as e:  # noqa: BLE001
                    print(f"  !! unhook {type(hook).__name__} failed: {e!r}")
        self.vm = self.setmode = None

    async def manager(self, timeout: float = 3.0) -> int:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                addr = await self.handler.read_typed(self.vm.manager_ptr, Primitive.int64)
            except Exception:  # noqa: BLE001
                addr = 0
            if addr:
                return addr
            await asyncio.sleep(0.1)
        return 0

    async def apply(self, width: int | None = None, height: int | None = None) -> bool:
        """Trigger the engine's apply; with a size, override set-mode's arguments first."""
        if width is not None and self.by_node:
            await write_resolution(self.client, width, height, self.resolution_node)
        if width is not None and self.setmode is not None:
            await self.handler.write_bytes(self.setmode.control_block, struct.pack("<III", 1, width, height))
        mgr = await self.manager()
        if not mgr:
            return False
        await self.handler.write_bytes(mgr + self.pending_off, b"\x01")
        return True

    async def release(self) -> None:
        if self.setmode is not None:
            await self.handler.write_bytes(self.setmode.control_block, struct.pack("<III", 0, 0, 0))


# --------------------------------------------------------------------------- windows


def client_size(hwnd: int) -> tuple[int, int]:
    r = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    return r.right - r.left, r.bottom - r.top


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def set_client_size(hwnd: int, w: int, h: int) -> None:
    """Resize so the client area is w x h: a child directly, a framed window by its frame."""
    style = user32.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF
    if style & WS_CHILD:
        user32.SetWindowPos(hwnd, 0, 0, 0, w, h, SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE)
        return
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
    r = wt.RECT(0, 0, w, h)
    user32.AdjustWindowRectEx(ctypes.byref(r), style, False, ex)
    user32.SetWindowPos(hwnd, 0, 0, 0, r.right - r.left, r.bottom - r.top, SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE)


def screenshot(hwnd: int, path: Path) -> str:
    """The window's client area as it is on screen, with its top-level window on top for the shot.

    Not PrintWindow, which comes back all white from a client once its device has been reset,
    while the game itself draws normally.
    """
    from PIL import ImageGrab

    w, h = client_size(hwnd)
    if w <= 0 or h <= 0:
        return "no client area"
    root = user32.GetAncestor(hwnd, 2)  # GA_ROOT
    flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
    user32.SetWindowPos(root, ctypes.c_void_p(-1), 0, 0, 0, 0, flags)  # HWND_TOPMOST
    time.sleep(0.1)  # the compositor shows it a frame later
    origin = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    image = ImageGrab.grab(bbox=(origin.x, origin.y, origin.x + w, origin.y + h), all_screens=True)
    user32.SetWindowPos(root, ctypes.c_void_p(-2), 0, 0, 0, 0, flags)  # HWND_NOTOPMOST
    image.convert("RGB").save(path)
    return f"{path} ({w}x{h})"


# --------------------------------------------------------------------------- game reads


def xml_value(path: Path, tag: str) -> str | None:
    try:
        m = re.search(rf"<{tag}[^>]*>([^<]*)</{tag}>", path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    return m.group(1) if m else None


async def frustum(client) -> str:
    """The selected camera's frustum aspect, (right-left)/(top-bottom)."""
    try:
        ctrl = await client.game_client.selected_camera_controller()
        view = await (await ctrl.gamebryo_camera()).cam_view()
        left, right = await view.viewport_left(), await view.viewport_right()
        top, bottom = await view.viewport_top(), await view.viewport_bottom()
        return f"aspect {(right - left) / (top - bottom):.4f} (l {left:.4f} r {right:.4f} t {top:.4f} b {bottom:.4f})"
    except Exception as e:  # noqa: BLE001
        return f"unreadable ({e!r})"


async def set_aspect(client, aspect: float) -> str:
    """Widen or narrow the selected camera's frustum to *aspect*, keeping its vertical extent."""
    try:
        ctrl = await client.game_client.selected_camera_controller()
        view = await (await ctrl.gamebryo_camera()).cam_view()
        half_h = await view.viewport_top()
        await view.write_viewport_left(-half_h * aspect)
        await view.write_viewport_right(half_h * aspect)
        return f"half width {half_h * aspect:.4f}, half height {half_h:.4f}"
    except Exception as e:  # noqa: BLE001
        return f"failed ({e!r})"


async def describe(client, label: str, out: Path, hwnd: int | None = None) -> None:
    hwnd = hwnd or client.window_handle
    w, h = client_size(hwnd)
    style = user32.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF
    print(
        f"  [{label}] client {w}x{h} (aspect {w / h if h else 0:.4f}), window {window_rect(hwnd)}, "
        f"style {style:#010x}{' CHILD' if style & WS_CHILD else ''}, parent {user32.GetParent(hwnd):#x}"
    )
    print(f"  [{label}] camera {await frustum(client)}")
    print(f"  [{label}] screenshot {screenshot(hwnd, out / f'{label}.png')}")


async def pattern_count(client, pattern: bytes) -> int:
    try:
        return len(await client.hook_handler.pattern_scan(pattern, module=MODULE, return_multiple=True))
    except PatternFailed:
        return 0


async def check_patterns(client) -> bool:
    ok = True
    for hook in (SetModeResHook, VideoManagerHook):
        n = await pattern_count(client, hook.pattern)
        print(f"  pattern {hook.__name__}: {n} match(es)")
        ok = ok and n == 1
    if not ok:
        print("  !! a pattern does not match exactly once; not hooking (a wrong match would crash the client)")
    return ok


# A config node's fields (RESULTS, "WHERE UISIZE LIVES").
NODE_NAME, NODE_TYPE, NODE_LIVE, NODE_INT, NODE_STRING = 0x50, 0x44, 0x45, 0x14, 0x18
NODE_CLASS = 0x40  # 0x28 on the class holding the live settings; a second class has 0x20 here
NODE_PARENT = 0x38  # zero on the copies of Resolution that exist while a window is embedded
NODE_TYPE_NAMES = {0x00: "string", 0x02: "int", 0x08: "float"}


async def find_nodes(client, name: str) -> list[dict]:
    """Every config node called *name*: a seed scan for the name, then a check of the node around it."""
    handler = client.hook_handler
    base = handler.process.process_base.lpBaseOfDll
    end = base + handler.process.process_base.SizeOfImage
    found = []
    for hit in await handler.pattern_scan(re.escape(name.encode()) + b"\0", return_multiple=True):
        try:
            raw = await handler.read_bytes(hit - NODE_NAME, NODE_NAME + len(name) + 1)
        except Exception:  # noqa: BLE001
            continue
        vtable = struct.unpack_from("<Q", raw, 0)[0]
        if not base <= vtable < end or raw[NODE_TYPE] not in NODE_TYPE_NAMES or raw[NODE_LIVE] > 1:
            continue
        parent = struct.unpack_from("<Q", raw, NODE_PARENT)[0]
        live = bool(parent) and raw[NODE_CLASS] == 0x28 and raw[NODE_LIVE] == 1
        found.append({"address": hit - NODE_NAME, "type": raw[NODE_TYPE], "live": live})
    return found


async def live_node(client, name: str) -> int | None:
    """The one live node called *name*; the scan is tried three times, and says what it saw."""
    for attempt in range(3):
        nodes = await find_nodes(client, name)
        live = [node["address"] for node in nodes if node["live"]]
        if len(live) == 1:
            return live[0]
        seen = [f"{node['address']:#x} type {node['type']} live {node['live']}" for node in nodes]
        print(f"  !! {name}, scan {attempt + 1}: {len(live)} live of {seen}")
        await asyncio.sleep(0.5)
    return None


async def read_c_string(handler, address: int, size: int = 64) -> bytes:
    return (await handler.read_bytes(address, size)).split(b"\0", 1)[0]


# A string node's +0x18 points at an MSVC std::string: 16 bytes inline, then the size at +0x10
# and the capacity at +0x18. The ladder's longest, 2560x1440, is 9 characters, so a size is
# always inline while the capacity is 15; the string's own length has to change with it, or the
# game reads "1024x76" out of "1024x768".
STRING_SIZE, STRING_CAPACITY, STRING_INLINE = 0x10, 0x18, 15


async def read_resolution(client, node: int | None = None) -> tuple[int, bytes]:
    """The live Resolution node's std::string, and its text."""
    handler = client.hook_handler
    node = node or await live_node(client, "Resolution")
    if node is None:
        raise RuntimeError("no single live Resolution node")
    string = struct.unpack("<Q", await handler.read_bytes(node + NODE_STRING, 8))[0]
    size = struct.unpack("<Q", await handler.read_bytes(string + STRING_SIZE, 8))[0]
    return string, await handler.read_bytes(await resolution_text_at(handler, string), size)


async def resolution_text_at(handler, string: int) -> int:
    capacity = struct.unpack("<Q", await handler.read_bytes(string + STRING_CAPACITY, 8))[0]
    if capacity <= STRING_INLINE:
        return string
    return struct.unpack("<Q", await handler.read_bytes(string, 8))[0]


async def write_resolution(client, width: int, height: int, node: int | None = None) -> None:
    """Write the live Resolution node's string in place; an apply then takes the new size from it."""
    handler = client.hook_handler
    string, _old = await read_resolution(client, node)
    value = f"{width}x{height}".encode()
    if len(value) > STRING_INLINE:
        raise RuntimeError(f"{value!r} is too long")
    await handler.write_bytes(await resolution_text_at(handler, string), value + b"\0")
    await handler.write_bytes(string + STRING_SIZE, struct.pack("<Q", len(value)))


async def device_size(handler, manager: int, device_off: int) -> tuple[int, int] | None:
    """The render device's own width and height, which the 1:1 contract says equal the client's."""
    try:
        device = struct.unpack("<Q", await handler.read_bytes(manager + device_off, 8))[0]
        return struct.unpack("<II", await handler.read_bytes(device + DEVICE_SIZE_OFF, 8))
    except Exception:  # noqa: BLE001
        return None


@dataclass
class Applied:
    """One apply, watched from the byte being set until it clears, and for a while after."""

    cleared_ms: float | None
    events: list[str]


async def run_apply(client, forcer, width, height, *, host=None, ui=None, timeout=15.0, after=0.0) -> Applied:
    """Write the UI size if given as (address, value), set the byte, and record each change until it clears.

    The override is disarmed as soon as the byte clears, and on a timeout. Nothing here embeds
    the window again: the caller does, so it can look at what the apply left first.
    """
    handler = client.hook_handler
    hwnd = client.window_handle
    manager = await forcer.manager()
    if not manager:
        return Applied(None, ["video manager never captured"])
    if ui is not None:
        await handler.write_bytes(ui[0], struct.pack("<i", ui[1]))
    events: list[str] = []
    last = None
    cleared: float | None = None
    started = time.perf_counter()
    await forcer.apply(width, height)
    try:
        while True:
            elapsed = (time.perf_counter() - started) * 1000
            byte = (await handler.read_bytes(manager + forcer.pending_off, 1))[0]
            state = (
                byte,
                user32.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF,
                user32.GetParent(hwnd),
                client_size(hwnd),
                await device_size(handler, manager, forcer.device_off),
            )
            if state != last:
                events.append(
                    f"{elapsed:7.0f} ms  byte {state[0]}  style {state[1]:#010x}  parent {state[2]:#x}  "
                    f"client {state[3]}  device {state[4]}"
                )
                last = state
            if cleared is None and byte == 0:
                cleared = elapsed
            if cleared is not None and elapsed - cleared >= after * 1000:
                return Applied(cleared, events)
            if elapsed > timeout * 1000:
                events.append(f"{elapsed:7.0f} ms  !! the byte never cleared")
                return Applied(None, events)
            if host:
                host.pump()
            await asyncio.sleep(0.002)
    finally:
        await forcer.release()


async def size_problems(client, forcer, width: int, height: int) -> list[str]:
    """What is wrong with this client at this size: its window, its device, its parentage."""
    hwnd = client.window_handle
    manager = await forcer.manager()
    problems = []
    if client_size(hwnd) != (width, height):
        problems.append(f"client {client_size(hwnd)}")
    device = await device_size(client.hook_handler, manager, forcer.device_off)
    if device != (width, height):
        problems.append(f"device {device}")
    return problems


async def click_lands(client) -> str | None:
    """Open the spellbook by its HUD button, close it by its own: None if both landed, else what didn't."""
    from bartleby.runtime.actions.node_helpers import _get_window_from_path
    from bartleby.runtime.paths import BACKPACK_CLOSE_BUTTON_PATH, DECK_CONFIGURATION_PATH, SPELLBOOK_BUTTON_PATH

    async def wait_for(path: list[str], wanted: bool) -> bool:
        end = time.monotonic() + 4.0
        while time.monotonic() < end:
            if (await _get_window_from_path(client.root_window, path) is not None) == wanted:
                return True
            await asyncio.sleep(0.05)
        return False

    button = await _get_window_from_path(client.root_window, SPELLBOOK_BUTTON_PATH)
    if button is None:
        return "no spellbook button"
    await client.mouse_handler.click_window(button)
    if not await wait_for(DECK_CONFIGURATION_PATH, True):
        return "the click on the HUD button did not open the spellbook"
    close = await _get_window_from_path(client.root_window, BACKPACK_CLOSE_BUTTON_PATH)
    if close is None:
        return "no close button on the spellbook"
    await client.mouse_handler.click_window(close)
    if not await wait_for(DECK_CONFIGURATION_PATH, False):
        return "the click on the spellbook's close button did not close it"
    return None


def parse_size(value: str) -> tuple[int, int]:
    width, height = value.lower().split("x")
    return int(width), int(height)


# --------------------------------------------------------------------------- commands


async def cmd_pending(client, args) -> None:
    """Read-only: where the pending byte is, from the code."""
    handler = client.hook_handler
    match = await handler.pattern_scan(VideoManagerHook.pattern, module=MODULE)
    for label, at in (("test", PENDING_TEST_AT), ("clear", PENDING_CLEAR_AT), ("device", DEVICE_LOAD_AT)):
        print(f"  {label} at match + {at:#x}: {(await handler.read_bytes(match + at, 7)).hex()}")
    pending, device = await manager_offsets(handler)
    print(f"  pending byte +{pending:#x}, device pointer +{device:#x}")
    if args.refs:
        async with Forcer(client, setmode=False) as forcer:
            manager = await forcer.manager()
        print(f"  video manager at {manager:#x}")
        module_base = handler.process.process_base.lpBaseOfDll
        module_end = module_base + handler.process.process_base.SizeOfImage
        hits = pointers_into(client.process_id, manager, manager)
        slots = [hit for hit in hits if module_base <= hit < module_end]
        print(f"  {len(hits)} pointer(s), {len(slots)} in the module: {[hex(slot - module_base) for slot in slots]}")
        code = await handler.read_bytes(module_base + 0x1000, 0x2000000)
        for slot in slots:
            reads = []
            for match in re.finditer(rb"\x48\x8b[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]....", code, re.DOTALL):
                at = module_base + 0x1000 + match.start()
                if at + 7 + struct.unpack_from("<i", code, match.start() + 3)[0] == slot:
                    reads.append(at)
            print(f"  slot +{slot - module_base:#x}: {len(reads)} mov reads")
            for at in reads[:6]:
                nearby = await handler.read_bytes(at, 24)
                print(f"    +{at - module_base:#x} {nearby.hex(' ', 1)}")


async def cmd_nodes(client, args) -> None:
    """Read-only: the seed scan for each config node named, and a dump of each node."""
    host = None
    if args.embed:
        w, h = client_size(client.window_handle)
        host = Host(client.window_handle, w, h)
        await host.settle(args.embed)
    try:
        await dump_nodes(client, args)
    finally:
        if host:
            host.close()


async def dump_nodes(client, args) -> None:
    for name in args.names:
        started = time.perf_counter()
        nodes = await find_nodes(client, name)
        print(f"  {name}: {len(nodes)} node(s) in {time.perf_counter() - started:.1f}s")
        for node in nodes:
            raw = await client.hook_handler.read_bytes(node["address"], args.span)
            value = struct.unpack_from("<i", raw, NODE_INT)[0]
            print(f"    {node['address']:#x} {NODE_TYPE_NAMES[node['type']]}, live {node['live']}, int {value}")
            pointer = struct.unpack_from("<Q", raw, NODE_STRING)[0]
            if node["type"] == 0x00 and pointer:
                print(f"      string at {pointer:#x}: {await read_c_string(client.hook_handler, pointer)!r}")
                around = await client.hook_handler.read_bytes(pointer - 16, 64)
                print(f"      around it, from -0x10: {around.hex(' ', 8)}")
            if args.dump:
                for off in range(0, len(raw), 16):
                    row = raw[off : off + 16]
                    text = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
                    print(f"      +{off:#05x} {row.hex(' ', 4)}  {text}")


async def cmd_info(client, args) -> None:
    install = install_of_client(process_executable(client.process_id))
    bin_dir = install / "Bin"
    print(f"  install {install}")
    print(f"  preferences.xml Resolution={xml_value(bin_dir / 'preferences.xml', 'Resolution')}")
    print(f"  config.xml UISize={xml_value(bin_dir / 'config.xml', 'UISize')}")
    await describe(client, "info", args.out)
    await check_patterns(client)


class Host:
    """A plain Qt window to embed the game in as a child, as BartleBot's ClientTile does."""

    def __init__(self, hwnd: int, w: int, h: int, *, wide: bool = False, follow_focus: bool = False) -> None:
        from PyQt6.QtGui import QFont
        from PyQt6.QtWidgets import QApplication, QLabel, QWidget

        self.app = QApplication.instance() or QApplication(sys.argv)
        self.widget = QWidget()
        self.widget.setWindowTitle("probe_runtime_resize host")
        self.widget.resize(1860 if wide else max(w, 1400), max(h, 940))
        self.label = QLabel(self.widget)
        self.label.setFont(QFont("Segoe UI", 14))
        self.label.setWordWrap(True)
        self.label.setGeometry(10, 780, self.widget.width() - 20, 150)
        self.widget.show()
        self.follow_focus = False
        self.windows: dict = {}
        self.pump()
        self.add(hwnd, w, h, 0)
        self.window = self.windows[hwnd][0]
        self.follow_focus = follow_focus

    def add(self, hwnd: int, w: int, h: int, x: int) -> None:
        from bartleby.qt.game_embed.win32_window import EmbeddedWindow

        window = EmbeddedWindow(hwnd)
        window.embed(int(self.widget.winId()))
        window.move(x, 0, w, h)
        self.windows[hwnd] = (window, x)
        self.pump()

    def say(self, text: str) -> None:
        self.label.setText(text)
        self.pump()

    def countdown(self, end: float) -> None:
        left = max(0, round(end - time.monotonic()))
        self.widget.setWindowTitle(f"Ends in {left} s. Close this window to end now; never kill the probe.")

    def pump(self) -> None:
        self.app.processEvents()
        if self.follow_focus and user32.GetForegroundWindow() == int(self.widget.winId()):
            if not self.window.is_focused():
                self.window.focus()

    async def settle(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.pump()
            await asyncio.sleep(0.02)

    def reembed(self, w: int, h: int, hwnd: int | None = None) -> None:
        """Embed again after the engine's apply put the window back on the desktop."""
        from bartleby.qt.game_embed.win32_window import child_style

        hwnd = hwnd or self.window.hwnd
        x = self.windows[hwnd][1]
        style = user32.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF
        user32.SetParent(hwnd, int(self.widget.winId()))
        user32.SetWindowLongW(hwnd, GWL_STYLE, ctypes.c_long(child_style(style)).value)
        user32.SetWindowPos(hwnd, 0, x, 0, w, h, SWP_NOZORDER | 0x20)  # SWP_FRAMECHANGED
        self.pump()

    def close(self) -> None:
        for window, _x in self.windows.values():
            window.restore()
        self.widget.close()
        self.pump()


async def cmd_force(client, args) -> None:
    hwnd = client.window_handle
    if not await check_patterns(client):
        return
    w0, h0 = client_size(hwnd)
    host = Host(hwnd, w0, h0) if args.embed else None

    async def settle(seconds: float) -> None:
        if host:
            await host.settle(seconds)
        else:
            await asyncio.sleep(seconds)

    try:
        await settle(0.5)
        await describe(client, "before", args.out)
        ui_slot = None
        if args.ui:
            node = await live_node(client, "UISize")
            if node is None:
                print("  !! no single live UISize node")
                return
            ui_slot = node + NODE_INT
            ui_was = struct.unpack("<I", await client.hook_handler.read_bytes(ui_slot, 4))[0]
            await client.hook_handler.write_bytes(ui_slot, struct.pack("<I", UI_SIZES[args.ui]))
            print(f"  UI size slot {ui_slot:#x}: {ui_was} -> {UI_SIZES[args.ui]} ({args.ui})")
        async with Forcer(client) as forcer:
            started = time.perf_counter()
            if not await forcer.apply(args.width, args.height):
                print("  !! video manager never captured; nothing applied")
                return
            await settle(1.5)
            print(f"  apply triggered {time.perf_counter() - started:.2f}s ago")
            await describe(client, "forced", args.out)
            if host:
                host.reembed(args.width, args.height)
                await settle(0.8)
                await describe(client, "reembedded", args.out)
            elif client_size(hwnd) != (args.width, args.height):
                set_client_size(hwnd, args.width, args.height)
                await settle(0.8)
                await describe(client, "fitted", args.out)
            if args.aspect:
                print(f"  wrote camera aspect: {await set_aspect(client, args.width / args.height)}")
                await settle(0.5)
                await describe(client, "aspect", args.out)
                await settle(args.hold)
                await describe(client, "later", args.out)
            if args.restore:
                await settle(args.hold)
                if ui_slot is not None:
                    await client.hook_handler.write_bytes(ui_slot, struct.pack("<I", ui_was))
                await forcer.apply(w0, h0)
                await settle(1.5)
                if host:
                    host.reembed(w0, h0)
                elif client_size(hwnd) != (w0, h0):
                    set_client_size(hwnd, w0, h0)
                if args.aspect:
                    await set_aspect(client, w0 / h0)
                await settle(0.8)
                await describe(client, "restored", args.out)
            await forcer.release()
    finally:
        if host:
            await settle(0.3)
            host.close()
            print(f"  restored to top level, client {client_size(hwnd)}")


def forcer_for(client, args) -> Forcer:
    """The node route, or with --hook the set-mode override the probe first proved."""
    return Forcer(client, by_node=not args.hook)


async def cmd_timeline(client, args) -> None:
    """One apply, every change to the byte, the style, the parent, the window and the device."""
    hwnd = client.window_handle
    w0, h0 = client_size(hwnd)
    host = Host(hwnd, w0, h0) if args.embed else None
    ui = None
    ui_was = None
    if args.ui:
        node = await live_node(client, "UISize")
        ui = (node + NODE_INT, UI_SIZES[args.ui])
        ui_was = struct.unpack("<i", await client.hook_handler.read_bytes(ui[0], 4))[0]
    try:
        async with forcer_for(client, args) as forcer:
            result = await run_apply(client, forcer, args.width, args.height, host=host, ui=ui, after=args.after)
            print(f"  byte cleared at {result.cleared_ms} ms")
            for event in result.events:
                print(f"    {event}")
            if host:
                host.reembed(args.width, args.height)
            elif client_size(hwnd) != (args.width, args.height):
                set_client_size(hwnd, args.width, args.height)
            for shot_at, delay in ((0.0, 0.0), (0.1, 0.1), (0.3, 0.2), (1.0, 0.7), (3.0, 2.0)):
                await (host.settle(delay) if host else asyncio.sleep(delay))
                print(f"  shot +{shot_at:.1f}s: {screenshot(hwnd, args.out / f'timeline_{shot_at:.1f}.png')}")
            print(f"  problems: {await size_problems(client, forcer, args.width, args.height) or 'none'}")
            print(f"  camera {await frustum(client)}")
            await (host.settle(args.hold) if host else asyncio.sleep(args.hold))
            back = run_apply(client, forcer, w0, h0, host=host, ui=(ui[0], ui_was) if ui else None)
            print(f"  back: byte cleared at {(await back).cleared_ms} ms")
            if host:
                host.reembed(w0, h0)
            elif client_size(hwnd) != (w0, h0):
                set_client_size(hwnd, w0, h0)
            await (host.settle(0.5) if host else asyncio.sleep(0.5))
    finally:
        if host:
            host.close()
    print(f"  ended at client {client_size(hwnd)}, style {user32.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF:#010x}")


async def cmd_soak(client, args) -> None:
    """Many resizes on one client with the hooks held, embedded, then give everything back."""
    from bartleby.runtime.game.game_sizing import ui_size_for

    handler = client.hook_handler
    hwnd = client.window_handle
    w0, h0 = client_size(hwnd)
    node = await live_node(client, "UISize")
    if node is None:
        print("  !! no single live UISize node")
        return
    slot = node + NODE_INT
    preferred = struct.unpack("<i", await handler.read_bytes(slot, 4))[0]
    sizes = [parse_size(size) for size in args.sizes]
    if args.click:
        await client.activate_hooks(wait_for_ready=True, timeout=30)
    host = Host(hwnd, w0, h0)
    failures = 0
    times: list[float] = []
    try:
        await host.settle(0.5)
        async with forcer_for(client, args) as forcer:
            cave = handler._autobot_pos
            if args.click:
                await handler.activate_mouseless_cursor_hook()
                print(f"  click at {w0}x{h0} before any resize: {await click_lands(client) or 'landed'}")
            print(f"  hooks held; cave at {cave} of {handler.AUTOBOT_SIZE}, {args.count} resizes")
            for i in range(args.count):
                width, height = sizes[i % len(sizes)]
                ui_value = ui_size_for(preferred, (width, height))
                result = await run_apply(client, forcer, width, height, host=host, ui=(slot, ui_value))
                if result.cleared_ms is None:
                    print("\n".join(result.events))
                    print(f"  !! {i + 1}: the apply never finished; stopping")
                    failures += 1
                    break
                host.reembed(width, height)
                await host.settle(args.settle)
                problems = await size_problems(client, forcer, width, height)
                clicked = await click_lands(client) if args.click else None
                failures += bool(problems or clicked)
                times.append(result.cleared_ms)
                print(
                    f"  {i + 1:2}: {width}x{height} UI {ui_value}, clear {result.cleared_ms:5.0f} ms, "
                    f"{problems or 'sizes right'}, click {clicked or ('landed' if args.click else 'not tried')}, "
                    f"{await frustum(client)}"
                )
                if i < len(sizes):
                    screenshot(hwnd, args.out / f"soak_{width}x{height}.png")
            print(f"  cave at {handler._autobot_pos} after {len(times)}; {failures} failure(s)")
            if times:
                print(f"  clear: min {min(times):.0f}, max {max(times):.0f}, mean {sum(times) / len(times):.0f} ms")
            result = await run_apply(client, forcer, w0, h0, host=host, ui=(slot, preferred))
            host.reembed(w0, h0)
            await host.settle(0.5)
            print(f"  back at {w0}x{h0} UI {preferred}: {await size_problems(client, forcer, w0, h0) or 'sizes right'}")
            if args.click:
                await handler.deactivate_mouseless_cursor_hook()
    finally:
        host.close()
    print(f"  unhooked; client {client_size(hwnd)}")


async def cmd_res_node(client, args) -> None:
    """Does writing the Resolution string node and the byte resize, with no set-mode hook?"""
    handler = client.hook_handler
    hwnd = client.window_handle
    w0, h0 = client_size(hwnd)
    string, old = await read_resolution(client)
    print(f"  Resolution string at {string:#x}: {old!r}, {(await handler.read_bytes(string, 32)).hex(' ', 8)}")
    async with Forcer(client, setmode=False) as forcer:
        await write_resolution(client, args.width, args.height)
        try:
            result = await run_apply(client, forcer, None, None, after=2.0)
            print(f"  byte cleared at {result.cleared_ms} ms, with no set-mode hook")
            for event in result.events:
                print(f"    {event}")
            print(f"  shot: {screenshot(hwnd, args.out / 'res_node.png')}")
            manager = await forcer.manager()
            print(f"  client {client_size(hwnd)}, device {await device_size(handler, manager, forcer.device_off)}")
        finally:
            await write_resolution(client, w0, h0)
        result = await run_apply(client, forcer, None, None, after=1.0)
        print(f"  string put back, applied again: byte cleared at {result.cleared_ms} ms")
        manager = await forcer.manager()
        device = await device_size(handler, manager, forcer.device_off)
        print(f"  client {client_size(hwnd)}, device {device}")


async def cmd_focus(client, args) -> None:
    """Keyboard focus and a held key across a resize, embedded: the user holds W for the first part."""
    from wizwalker.constants import Keycode

    handler = client.hook_handler
    hwnd = client.window_handle
    w0, h0 = client_size(hwnd)
    await client.activate_hooks(wait_for_ready=True, timeout=30)
    host = Host(hwnd, w0, h0, follow_focus=True)
    window = host.window

    async def phase(label: str, seconds: float) -> None:
        """How far the wizard walks in this phase, and whether W was down and the game had focus."""
        moved = 0.0
        held = focused = samples = 0
        last = await client.body.position()
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            await host.settle(0.1)
            now = await client.body.position()
            moved += ((now.x - last.x) ** 2 + (now.y - last.y) ** 2) ** 0.5
            last = now
            samples += 1
            held += bool(user32.GetAsyncKeyState(0x57) & 0x8000)
            focused += window.is_focused()
        print(f"  {label}: walked {moved:7.0f}; W down {held}/{samples}, focused {focused}/{samples}")

    async def resize(width: int, height: int) -> None:
        result = await run_apply(client, forcer, width, height, host=host)
        host.reembed(width, height)
        print(
            f"  resized to {width}x{height}: clear {result.cleared_ms} ms; focused right after: {window.is_focused()}"
        )

    try:
        async with forcer_for(client, args) as forcer:
            window.focus()
            print(f"  focused after embedding and focus(): {window.is_focused()}")
            host.say(f"Find open ground. Then HOLD W and keep holding it. Resize in {args.lead:.0f} s...")
            await phase("real W, before the resize", args.lead)
            host.say("RESIZING. KEEP HOLDING W.")
            host.follow_focus = False
            print(f"  W down at the resize: {bool(user32.GetAsyncKeyState(0x57) & 0x8000)}")
            await resize(args.width, args.height)
            host.say("KEEP HOLDING W. No focus call has been made yet.")
            await phase("real W, held through, no focus call", 5)
            window.focus()
            print(f"  focus() after the resize: {window.is_focused()}")
            host.say("KEEP HOLDING W. focus() has now been called.")
            await phase("real W, held through, after focus()", 5)
            host.say("Let go of W. Then tap-and-hold W again.")
            await phase("real W, pressed anew", 6)
            host.say("Let go of W, and leave the keyboard. The probe posts W itself now.")
            await asyncio.sleep(3)
            posted = asyncio.create_task(client.send_key(Keycode.W, 9))
            await phase("posted W, before the resize", 3)
            await resize(w0, h0)
            await phase("posted W, held through", 4)
            await posted
            await phase("after the posted W ended", 2)
            host.say("Done.")
            await host.settle(0.5)
    finally:
        host.close()
    print(f"  unhooked; client {client_size(hwnd)}; {handler._autobot_pos} cave bytes before close")


async def cmd_watch(client, args) -> None:
    """Hold a client at a size while the user plays, with a shot at each change and an optional swap."""
    from bartleby.runtime.game.game_sizing import ui_size_for

    handler = client.hook_handler
    hwnd = client.window_handle
    w0, h0 = client_size(hwnd)
    node = await live_node(client, "UISize")
    if node is None:
        print("  !! no single live UISize node")
        return
    slot = node + NODE_INT
    preferred = struct.unpack("<i", await handler.read_bytes(slot, 4))[0]
    await client.activate_hooks(wait_for_ready=True, timeout=30)
    host = Host(hwnd, w0, h0, follow_focus=True)
    swap_to = parse_size(args.swap) if args.swap else None
    shots = 0

    async def shot(label: str) -> None:
        nonlocal shots
        shots += 1
        print(f"    shot {screenshot(hwnd, args.out / f'watch_{shots:03}_{label}.png')}")

    async def to_size(forcer, width: int, height: int) -> None:
        result = await run_apply(
            client, forcer, width, height, host=host, ui=(slot, ui_size_for(preferred, (width, height)))
        )
        host.reembed(width, height)
        window = host.window
        window.focus()
        problems = await size_problems(client, forcer, width, height)
        print(
            f"  [{time.strftime('%X')}] resized to {width}x{height}: clear {result.cleared_ms} ms, {problems or 'sizes right'}"
        )

    async def state() -> tuple:
        try:
            zone = await client.zone_name()
            battle = await client.in_battle()
            loading = await client.is_loading()
            dialog = await client.is_in_dialog()
        except Exception as e:  # noqa: BLE001
            return (f"unreadable {type(e).__name__}",)
        return zone, battle, loading, dialog

    try:
        await host.settle(0.5)
        async with forcer_for(client, args) as forcer:
            width, height = args.width, args.height
            await to_size(forcer, width, height)
            host.say(args.say or "Play: the probe is watching. It restores this window when it ends.")
            last = None
            last_aspect = None
            end = time.monotonic() + args.seconds
            while time.monotonic() < end and host.widget.isVisible():
                host.countdown(end)
                await host.settle(args.every)
                if not user32.GetParent(hwnd):
                    style = user32.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF
                    print(
                        f"  [{time.strftime('%X')}] !! the game left its tile: client {client_size(hwnd)}, {style:#010x}"
                    )
                    host.reembed(width, height)
                    window = host.window
                    window.focus()
                now = await state()
                aspect = (await frustum(client))[:13]
                if now != last or aspect != last_aspect:
                    labels = ("zone", "battle", "loading", "dialog")
                    readable = dict(zip(labels, now, strict=False)) if len(now) == 4 else now
                    print(f"  [{time.strftime('%X')}] {readable}, camera {aspect}, client {client_size(hwnd)}")
                    await host.settle(1.0)
                    await shot("battle" if len(now) == 4 and now[1] else "world")
                    entered = len(now) == 4 and (
                        not last or len(last) != 4 or (now[1] and not last[1]) or (now[3] and not last[3])
                    )
                    if swap_to and last is not None and entered and (now[1] or now[3]):
                        await to_size(forcer, *swap_to)
                        await host.settle(3.0)
                        await shot("swapped")
                        await to_size(forcer, width, height)
                        await host.settle(1.0)
                        await shot("swapped_back")
                    last, last_aspect = now, aspect
            host.say("Restoring...")
            result = await run_apply(client, forcer, w0, h0, host=host, ui=(slot, preferred))
            host.reembed(w0, h0)
            await host.settle(0.5)
            print(
                f"  back at {w0}x{h0}: clear {result.cleared_ms} ms, {await size_problems(client, forcer, w0, h0) or 'sizes right'}"
            )
    finally:
        host.close()
    print(f"  unhooked; client {client_size(hwnd)}")


async def cmd_pair(clients, args) -> None:
    """Two clients resizing at once, swapping a large size and a small one, as Main changing hands does."""
    from bartleby.runtime.game.game_sizing import ui_size_for

    if len(clients) != 2:
        print(f"  !! needs two clients, have {[c.process_id for c in clients]}")
        return
    large, small = parse_size(args.large), parse_size(args.small)
    first, second = clients
    sizes = {client: client_size(client.window_handle) for client in clients}
    slots = {}
    preferred = {}
    for client in clients:
        node = await live_node(client, "UISize")
        if node is None:
            print(f"  !! {client.process_id}: no single live UISize node")
            return
        slots[client] = node + NODE_INT
        preferred[client] = struct.unpack("<i", await client.hook_handler.read_bytes(slots[client], 4))[0]
    if args.click:
        await asyncio.gather(*(c.activate_hooks(wait_for_ready=True, timeout=30) for c in clients))
    host = Host(first.window_handle, *sizes[first], wide=True)
    host.add(second.window_handle, *sizes[second], large[0] + 10)

    async def one(client, forcer, size) -> str:
        ui = (slots[client], ui_size_for(preferred[client], size))
        result = await run_apply(client, forcer, *size, host=host, ui=ui)
        host.reembed(*size, client.window_handle)
        await host.settle(args.settle)
        problems = await size_problems(client, forcer, *size)
        clicked = await click_lands(client) if args.click else None
        camera = (await frustum(client))[:13]
        return (
            f"{size[0]}x{size[1]} UI {ui[1]}, clear {result.cleared_ms} ms, {problems or 'sizes right'}, "
            f"click {clicked or ('landed' if args.click else 'not tried')}, {camera}"
        )

    try:
        await host.settle(0.5)
        async with forcer_for(first, args) as first_forcer, forcer_for(second, args) as second_forcer:
            if args.click:
                await asyncio.gather(*(c.hook_handler.activate_mouseless_cursor_hook() for c in clients))
            for i in range(args.count):
                first_size, second_size = (large, small) if i % 2 == 0 else (small, large)
                started = time.perf_counter()
                done = await asyncio.gather(
                    one(first, first_forcer, first_size), one(second, second_forcer, second_size)
                )
                print(f"  {i + 1:2}: both in {time.perf_counter() - started:.2f}s, settle and checks included")
                print(f"      {first.process_id}: {done[0]}")
                print(f"      {second.process_id}: {done[1]}")
                screenshot(first.window_handle, args.out / f"pair_{i % 2}_first.png")
                screenshot(second.window_handle, args.out / f"pair_{i % 2}_second.png")
            await asyncio.gather(
                run_apply(first, first_forcer, *sizes[first], host=host, ui=(slots[first], preferred[first])),
                run_apply(second, second_forcer, *sizes[second], host=host, ui=(slots[second], preferred[second])),
            )
            for client in clients:
                host.reembed(*sizes[client], client.window_handle)
            await host.settle(0.5)
            if args.click:
                await asyncio.gather(*(c.hook_handler.deactivate_mouseless_cursor_hook() for c in clients))
    finally:
        host.close()
    print(f"  unhooked; {[client_size(c.window_handle) for c in clients]}")


def files_state(out: Path) -> Path:
    return out / "files"


def cmd_files_snap(install: Path, out: Path) -> None:
    saved = files_state(out)
    saved.mkdir(exist_ok=True)
    for name in ("config.xml", "preferences.xml"):
        (saved / name).write_bytes((install / "Bin" / name).read_bytes())
    (saved / "install.json").write_text(json.dumps({"install": str(install)}), encoding="utf-8")
    print(f"  saved; config UISize={xml_value(saved / 'config.xml', 'UISize')}, ", end="")
    print(f"preferences Resolution={xml_value(saved / 'preferences.xml', 'Resolution')}")


def cmd_files_after(args) -> int:
    """Compare the game's files with the snapshot; ``restore`` puts the snapshot back."""
    saved = files_state(args.out)
    install = Path(json.loads((saved / "install.json").read_text(encoding="utf-8"))["install"])
    for name, tag in (("config.xml", "UISize"), ("preferences.xml", "Resolution")):
        before, now = saved / name, install / "Bin" / name
        same = before.read_bytes() == now.read_bytes()
        print(
            f"  {name}: {tag} {xml_value(before, tag)} -> {xml_value(now, tag)}, {'identical' if same else 'changed'}"
        )
        if not same:
            old_lines = before.read_text(encoding="utf-8", errors="replace").splitlines()
            new_lines = now.read_text(encoding="utf-8", errors="replace").splitlines()
            changed = [
                re.sub(r"\s+", " ", b).strip()[:60] for a, b in zip(old_lines, new_lines, strict=False) if a != b
            ]
            print(
                f"    {len(changed)} line(s) differ: tags {sorted({m[1] for c in changed if (m := re.match(r'<(\w+)', c))})}"
            )
        if args.action == "restore" and not same:
            now.write_bytes(before.read_bytes())
            print("    put back from the snapshot")
    return 0


async def cmd_vm_diff(client, args) -> None:
    saved = args.out / "vm_snapshot.bin"
    meta = args.out / "vm_snapshot.json"
    if not await check_patterns(client):
        return
    async with Forcer(client, setmode=False) as forcer:
        span = forcer.pending_off + 0x1000
        mgr = await forcer.manager()
        if not mgr:
            print("  !! video manager never captured")
            return
        data = await client.hook_handler.read_bytes(mgr, span)
    size = client_size(client.window_handle)
    print(f"  video manager at {mgr:#x}, client {size[0]}x{size[1]}")
    if args.action == "snap":
        saved.write_bytes(data)
        meta.write_text(json.dumps({"manager": mgr, "size": size}), encoding="utf-8")
        print(f"  saved {span:#x} bytes; change the resolution in the options menu, then run 'vm-diff compare'")
        return
    before = saved.read_bytes()
    old = json.loads(meta.read_text(encoding="utf-8"))
    print(f"  snapshot was at {old['manager']:#x}, client {old['size'][0]}x{old['size'][1]}")
    interesting = set(old["size"]) | set(size)
    changed = []
    for off in range(0, span - 3, 4):
        a, b = struct.unpack_from("<I", before, off)[0], struct.unpack_from("<I", data, off)[0]
        if a != b:
            changed.append((off, a, b))
    print(f"  {len(changed)} changed 32-bit field(s); sizes marked *")
    for off, a, b in changed[:400]:
        mark = "*" if a in interesting or b in interesting else " "
        print(
            f"   {mark} +{off:#07x}: {a:>10} -> {b:<10} ({struct.unpack('<f', struct.pack('<I', a))[0]:.4g} -> "
            f"{struct.unpack('<f', struct.pack('<I', b))[0]:.4g})"
        )


# --- UI scan: masks over every committed, writable, private region.

PROCESS_QUERY_INFORMATION, PROCESS_VM_READ = 0x0400, 0x0010
MEM_COMMIT, MEM_PRIVATE = 0x1000, 0x20000
WRITABLE = {0x04, 0x08, 0x40, 0x80}  # RW, WRITECOPY, EXECUTE_RW, EXECUTE_WRITECOPY
PAGE_GUARD = 0x100


class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_ulonglong),
        ("AllocationBase", ctypes.c_ulonglong),
        ("AllocationProtect", wt.DWORD),
        ("PartitionId", wt.WORD),
        ("RegionSize", ctypes.c_ulonglong),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
    ]


kernel32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
kernel32.ReadProcessMemory.argtypes = [
    wt.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.OpenProcess.restype = wt.HANDLE


MEM_IMAGE = 0x1000000


def regions(handle, *, image: bool = False) -> list[tuple[int, int]]:
    types = (MEM_PRIVATE, MEM_IMAGE) if image else (MEM_PRIVATE,)
    out, addr, mbi = [], 0, MBI()
    while kernel32.VirtualQueryEx(handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
        if (
            mbi.State == MEM_COMMIT
            and mbi.Type in types
            and (mbi.Protect & 0xFF) in WRITABLE
            and not mbi.Protect & PAGE_GUARD
        ):
            out.append((mbi.BaseAddress, mbi.RegionSize))
        addr = mbi.BaseAddress + mbi.RegionSize
        if addr >= 0x7FFFFFFFFFFF:
            break
    return out


def read_region(handle, base: int, size: int) -> np.ndarray | None:
    buf = (ctypes.c_char * size)()
    got = ctypes.c_size_t(0)
    if not kernel32.ReadProcessMemory(handle, ctypes.c_void_p(base), buf, size, ctypes.byref(got)) or got.value != size:
        return None
    return np.frombuffer(buf, dtype="<u4")


def pointers_into(pid: int, lo: int, hi: int) -> list[int]:
    """Every 8-aligned slot, heap or the game's own data, holding an address in [lo, hi]."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    found: list[int] = []
    started = time.perf_counter()
    for base, size in regions(handle, image=True):
        words = read_region(handle, base, size - size % 8)
        if words is None:
            continue
        values = words.view("<u8")
        found.extend(base + 8 * int(i) for i in np.flatnonzero((values >= lo) & (values <= hi)))
    kernel32.CloseHandle(handle)
    print(f"  pointer scan {time.perf_counter() - started:.1f}s")
    return found


def ui_scan(pid: int, value: int, state: Path, *, first: bool) -> list[int]:
    handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    started = time.perf_counter()
    masks: dict[tuple[int, int], np.ndarray] = {}
    if not first:
        with np.load(state) as saved:
            for key in saved.files:
                base, size = (int(x) for x in key.split("_"))
                masks[(base, size)] = np.unpackbits(saved[key])[: size // 4].astype(bool)
    total = 0
    keep: dict[str, np.ndarray] = {}
    survivors: list[int] = []
    for base, size in regions(handle):
        if not first and (base, size) not in masks:
            continue
        words = read_region(handle, base, size)
        if words is None:
            continue
        mask = words == value
        if not first:
            mask &= masks[(base, size)]
        n = int(mask.sum())
        if n:
            total += n
            keep[f"{base}_{size}"] = np.packbits(mask)
            if total <= 64:
                survivors.extend(base + 4 * int(i) for i in np.flatnonzero(mask))
    kernel32.CloseHandle(handle)
    np.savez(state, **keep)
    print(
        f"  {total} slot(s) hold {value} {'' if first else 'and every earlier value'} "
        f"({time.perf_counter() - started:.1f}s)"
    )
    return survivors if total <= 64 else []


def ui_candidates(state: Path) -> list[int]:
    with np.load(state) as saved:
        addrs = []
        for key in saved.files:
            base, size = (int(x) for x in key.split("_"))
            mask = np.unpackbits(saved[key])[: size // 4].astype(bool)
            addrs.extend(base + 4 * int(i) for i in np.flatnonzero(mask))
    return addrs


async def type_name(client, address: int) -> str:
    """The class name of a PropertyClass at *address*, or "" when there is none."""
    from wizwalker.memory.memory_object import DynamicMemoryObject, PropertyClass

    class _Object(DynamicMemoryObject, PropertyClass):
        pass

    if address <= 0x10000:
        return ""
    try:
        name = await _Object(client.hook_handler, address).read_type_name()
    except Exception:  # noqa: BLE001
        return ""
    return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_:<>,* ]{1,80}", name) else ""


async def cmd_ui_where(client, slot: int) -> None:
    """What holds the slot: a reflected object behind it, and who points at the block it is in."""
    reader = client.hook_handler
    for back in range(0, 0x400, 8):
        name = await type_name(client, (slot & ~7) - back)
        if name:
            print(f"  slot is +{slot - ((slot & ~7) - back):#x} into a {name} at {(slot & ~7) - back:#x}")
            break
    else:
        print("  no reflected object within 0x400 bytes before the slot")
    # Pointers to anything in the 0x400 bytes before the slot: candidates for its holder's start.
    hits = pointers_into(client.process_id, slot - 0x400, slot)
    module_base = reader.process.process_base.lpBaseOfDll
    module_end = module_base + reader.process.process_base.SizeOfImage
    print(f"  {len(hits)} pointer(s) into the 0x400 bytes before the slot")
    for hit in hits[:40]:
        target = struct.unpack("<Q", await reader.read_bytes(hit, 8))[0]
        where = f"{MODULE}+{hit - module_base:#x}" if module_base <= hit < module_end else f"{hit:#x}"
        owner = ""
        for back in range(0, 0x200, 8):
            name = await type_name(client, hit - back)
            if name:
                owner = f" (+{back:#x} in a {name})"
                break
        print(f"    {where} -> {target:#x} (slot at +{slot - target:#x}){owner}")


async def cmd_ui_scan(client, args) -> None:
    state = args.out / "ui_scan.npz"
    if args.action in ("start", "next"):
        found = ui_scan(client.process_id, UI_SIZES[args.size], state, first=args.action == "start")
        for addr in found:
            print(f"    {addr:#x}")
        return
    addrs = ui_candidates(state)
    if args.action == "where":
        for addr in addrs[:4]:
            print(f"  slot {addr:#x}")
            await cmd_ui_where(client, addr)
        return
    # try: write the value to every survivor, then trigger a mode apply at the current size.
    if not addrs or len(addrs) > 32:
        print(f"  {len(addrs)} candidate(s); narrow to 32 or fewer before trying")
        return
    hwnd = client.window_handle
    w, h = client_size(hwnd)
    await describe(client, "ui_before", args.out)
    for addr in addrs:
        await client.hook_handler.write_bytes(addr, struct.pack("<I", UI_SIZES[args.size]))
    print(f"  wrote {UI_SIZES[args.size]} ({args.size}) to {len(addrs)} slot(s)")
    await asyncio.sleep(1.0)
    await describe(client, "ui_written", args.out)
    if not await check_patterns(client):
        return
    async with Forcer(client) as forcer:
        await forcer.apply(w, h)
        await asyncio.sleep(1.5)
        await forcer.release()
    if client_size(hwnd) != (w, h):
        set_client_size(hwnd, w, h)
        await asyncio.sleep(0.8)
    await describe(client, "ui_applied", args.out)


class Tee:
    """Stdout and a log file, flushed at each write, so a run that is killed keeps its log."""

    def __init__(self, stream, path: Path) -> None:
        self.stream = stream
        self.log = path.open("a", encoding="utf-8")

    def write(self, text: str) -> int:
        self.log.write(text)
        self.log.flush()
        self.stream.flush()
        return self.stream.write(text)

    def flush(self) -> None:
        self.stream.flush()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, default=Path(tempfile.gettempdir()) / "probe_runtime_resize")
    parser.add_argument("--pid", type=int, help="which client, when more than one runs")
    parser.add_argument("--hook", action="store_true", help="resize by SetModeResHook, not the Resolution node")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info").set_defaults(fn=cmd_info)
    pending = sub.add_parser("pending")
    pending.add_argument("--refs", action="store_true", help="hook for a moment, and list what points at the manager")
    pending.set_defaults(fn=cmd_pending)
    nodes = sub.add_parser("nodes")
    nodes.add_argument("names", nargs="*", default=["UISize", "Resolution"])
    nodes.add_argument("--dump", action="store_true", help="print each node's bytes")
    nodes.add_argument("--embed", type=float, default=0.0, help="embed first, and wait this many seconds")
    nodes.add_argument("--span", type=lambda v: int(v, 0), default=0x80, help="how many bytes of each node to read")
    nodes.set_defaults(fn=cmd_nodes)
    force = sub.add_parser("force")
    force.add_argument("width", type=int)
    force.add_argument("height", type=int)
    force.add_argument("--embed", action="store_true", help="embed in a Qt window first, as BartleBot does")
    force.add_argument("--restore", action="store_true", help="go back to the original size after")
    force.add_argument("--ui", choices=list(UI_SIZES), help="also set the UI size, through ui-scan's one slot")
    force.add_argument("--aspect", action="store_true", help="write the camera frustum to the new aspect")
    force.add_argument("--hold", type=float, default=3.0, help="seconds at the forced size before restoring")
    force.set_defaults(fn=cmd_force)
    timeline = sub.add_parser("timeline")
    timeline.add_argument("width", type=int)
    timeline.add_argument("height", type=int)
    timeline.add_argument("--embed", action="store_true")
    timeline.add_argument("--ui", choices=list(UI_SIZES))
    timeline.add_argument("--after", type=float, default=1.0, help="seconds to keep watching after the byte clears")
    timeline.add_argument("--hold", type=float, default=2.0)
    timeline.set_defaults(fn=cmd_timeline)
    soak = sub.add_parser("soak")
    soak.add_argument("--count", type=int, default=30)
    soak.add_argument("--sizes", nargs="+", default=["1024x768", "480x360", "640x480", "800x600"])
    soak.add_argument("--click", action="store_true", help="open and close the spellbook after each resize")
    soak.add_argument("--settle", type=float, default=0.3)
    soak.set_defaults(fn=cmd_soak)
    res_node = sub.add_parser("res-node")
    res_node.add_argument("width", type=int)
    res_node.add_argument("height", type=int)
    res_node.set_defaults(fn=cmd_res_node)
    focus = sub.add_parser("focus")
    focus.add_argument("width", type=int)
    focus.add_argument("height", type=int)
    focus.add_argument("--lead", type=float, default=15.0, help="seconds to get ready and hold W")
    focus.set_defaults(fn=cmd_focus)
    watch = sub.add_parser("watch")
    watch.add_argument("width", type=int)
    watch.add_argument("height", type=int)
    watch.add_argument("--seconds", type=float, default=120.0)
    watch.add_argument("--every", type=float, default=1.0)
    watch.add_argument("--swap", help="on entering combat or a dialog, go to this size and back, e.g. 480x360")
    watch.add_argument("--say", help="what the host window tells the user")
    watch.set_defaults(fn=cmd_watch)
    pair = sub.add_parser("pair")
    pair.add_argument("--count", type=int, default=6)
    pair.add_argument("--large", default="1024x768")
    pair.add_argument("--small", default="480x360")
    pair.add_argument("--click", action="store_true")
    pair.add_argument("--settle", type=float, default=0.3)
    pair.set_defaults(fn_all=cmd_pair)
    files = sub.add_parser("files")
    files.add_argument("action", choices=["snap", "compare", "restore"])
    files.set_defaults(fn=None)
    vm = sub.add_parser("vm-diff")
    vm.add_argument("action", choices=["snap", "compare"])
    vm.set_defaults(fn=cmd_vm_diff)
    ui = sub.add_parser("ui-scan")
    ui.add_argument("action", choices=["start", "next", "try", "where"])
    ui.add_argument("size", nargs="?", default="Regular", choices=list(UI_SIZES))
    ui.set_defaults(fn=cmd_ui_scan)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    sys.stdout = Tee(sys.stdout, args.out / f"{args.cmd}.log")

    if args.cmd == "files" and args.action != "snap":
        return cmd_files_after(args)

    async with ClientHandler() as handler:
        clients = handler.get_new_clients()
        if args.cmd == "pair":
            await args.fn_all(clients, args)
            return 0
        if args.pid:
            clients = [c for c in clients if c.process_id == args.pid]
        if len(clients) != 1:
            print(f"need exactly one client, found {[c.process_id for c in clients]}; pass --pid")
            return 1
        client = clients[0]
        print(f"client pid {client.process_id}, hwnd {client.window_handle:#x}")
        if args.cmd == "files":
            cmd_files_snap(install_of_client(process_executable(client.process_id)), args.out)
            return 0
        await args.fn(client, args)
    return 0


if __name__ == "__main__":
    # Which app this is, for the parts of bartleby-core that say so, as bartlebot.py does first.
    from bartleby.shared.product import use

    from bartlebot.packaging.product import product

    use(product())
    sys.exit(asyncio.run(main()))
