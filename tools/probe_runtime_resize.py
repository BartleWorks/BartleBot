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
gdi32 = ctypes.WinDLL("gdi32")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # true physical pixels

GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_CHILD = 0x40000000
SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x2, 0x4, 0x10
PW_RENDERFULLCONTENT = 2


# --------------------------------------------------------------------------- the hooks
# Ported from Deimos-Wizard101/libs/wizsprinter/wizwalker/extensions/wizsprinter/resolution_hook.py.

MGR_PENDING_FLAG_OFF = 0x2229B  # byte on the video manager: non-zero => apply a pending mode


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


class Forcer:
    """Both hooks, installed together and always removed together."""

    def __init__(self, client, *, setmode: bool = True) -> None:
        self.handler = client.hook_handler
        self.want_setmode = setmode
        self.setmode: SetModeResHook | None = None
        self.vm: VideoManagerHook | None = None

    async def __aenter__(self) -> Forcer:
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
        if width is not None and self.setmode is not None:
            await self.handler.write_bytes(self.setmode.control_block, struct.pack("<III", 1, width, height))
        mgr = await self.manager()
        if not mgr:
            return False
        await self.handler.write_bytes(mgr + MGR_PENDING_FLAG_OFF, b"\x01")
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


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD),
        ("biWidth", wt.LONG),
        ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


def screenshot(hwnd: int, path: Path) -> str:
    """The window's client area as drawn, by PrintWindow; works covered and as a child."""
    from PIL import Image

    w, h = client_size(hwnd)
    if w <= 0 or h <= 0:
        return "no client area"
    hdc_win = user32.GetDC(hwnd)
    hdc = gdi32.CreateCompatibleDC(hdc_win)
    bmp = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
    gdi32.SelectObject(hdc, bmp)
    ok = user32.PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT | 1)  # | PW_CLIENTONLY
    bih = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(hdc, bmp, 0, h, buf, ctypes.byref(bih), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(hdc)
    user32.ReleaseDC(hwnd, hdc_win)
    Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB").save(path)
    return f"{path} ({w}x{h}{'' if ok else ', PrintWindow failed'})"


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


# --------------------------------------------------------------------------- commands


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

    def __init__(self, hwnd: int, w: int, h: int) -> None:
        from bartleby.qt.game_embed.win32_window import EmbeddedWindow
        from PyQt6.QtWidgets import QApplication, QWidget

        self.app = QApplication.instance() or QApplication(sys.argv)
        self.widget = QWidget()
        self.widget.setWindowTitle("probe_runtime_resize host")
        self.widget.resize(max(w, 1400) // 1, max(h, 900))
        self.widget.show()
        self.pump()
        self.window = EmbeddedWindow(hwnd)
        self.window.embed(int(self.widget.winId()))
        self.window.move(0, 0, w, h)
        self.pump()

    def pump(self) -> None:
        self.app.processEvents()

    async def settle(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.pump()
            await asyncio.sleep(0.02)

    def reembed(self, w: int, h: int) -> None:
        """Embed again after the engine's apply put the window back on the desktop."""
        from bartleby.qt.game_embed.win32_window import child_style

        hwnd = self.window.hwnd
        style = user32.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF
        user32.SetParent(hwnd, int(self.widget.winId()))
        user32.SetWindowLongW(hwnd, GWL_STYLE, ctypes.c_long(child_style(style)).value)
        user32.SetWindowPos(hwnd, 0, 0, 0, w, h, SWP_NOZORDER | 0x20)  # SWP_FRAMECHANGED
        self.pump()

    def close(self) -> None:
        self.window.restore()
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
            ui_slot = ui_candidates(args.out / "ui_scan.npz")
            if len(ui_slot) != 1:
                print(f"  !! --ui needs exactly one UI size slot from ui-scan, have {len(ui_slot)}")
                return
            ui_slot = ui_slot[0]
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


async def cmd_vm_diff(client, args) -> None:
    saved = args.out / "vm_snapshot.bin"
    meta = args.out / "vm_snapshot.json"
    span = MGR_PENDING_FLAG_OFF + 0x1000
    if not await check_patterns(client):
        return
    async with Forcer(client, setmode=False) as forcer:
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


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, default=Path(tempfile.gettempdir()) / "probe_runtime_resize")
    parser.add_argument("--pid", type=int, help="which client, when more than one runs")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info").set_defaults(fn=cmd_info)
    force = sub.add_parser("force")
    force.add_argument("width", type=int)
    force.add_argument("height", type=int)
    force.add_argument("--embed", action="store_true", help="embed in a Qt window first, as BartleBot does")
    force.add_argument("--restore", action="store_true", help="go back to the original size after")
    force.add_argument("--ui", choices=list(UI_SIZES), help="also set the UI size, through ui-scan's one slot")
    force.add_argument("--aspect", action="store_true", help="write the camera frustum to the new aspect")
    force.add_argument("--hold", type=float, default=3.0, help="seconds at the forced size before restoring")
    force.set_defaults(fn=cmd_force)
    vm = sub.add_parser("vm-diff")
    vm.add_argument("action", choices=["snap", "compare"])
    vm.set_defaults(fn=cmd_vm_diff)
    ui = sub.add_parser("ui-scan")
    ui.add_argument("action", choices=["start", "next", "try", "where"])
    ui.add_argument("size", nargs="?", default="Regular", choices=list(UI_SIZES))
    ui.set_defaults(fn=cmd_ui_scan)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    async with ClientHandler() as handler:
        clients = handler.get_new_clients()
        if args.pid:
            clients = [c for c in clients if c.process_id == args.pid]
        if len(clients) != 1:
            print(f"need exactly one client, found {[c.process_id for c in clients]}; pass --pid")
            return 1
        client = clients[0]
        print(f"client pid {client.process_id}, hwnd {client.window_handle:#x}")
        await args.fn(client, args)
    return 0


if __name__ == "__main__":
    # Which app this is, for the parts of bartleby-core that say so, as bartlebot.py does first.
    from bartleby.shared.product import use

    from bartlebot.packaging.product import product

    use(product())
    sys.exit(asyncio.run(main()))
