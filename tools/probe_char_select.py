"""Diagnostic: is the game's UI tree readable *before* a character is chosen?

WHY THIS EXISTS

Auto-select has to drive a freshly launched client through two loading screens and the
character-select screen. Today the only way BartleBot does that (crash recovery) is blind:
it spams Enter on a timer until the client hooks. Blind input cannot tell which screen it
is on, so it wastes time on every gate and will happily confirm an unexpected modal — a
login queue, a ToS update, an expired subscription.

The blindness is assumed, not measured. `Client.activate_hooks()` calls
`activate_all_hooks(wait_for_ready=True)`, which waits for the **player struct** address,
and that is only written once a character is in a game zone. But the *root window* hook
(`activate_root_window_hook`) is independently activatable, and the character-select screen
plainly has a UI root — the PLAY button is a real window in it. If `current_root_window`
gets written at character select, we can read the actual screen instead of guessing, and
`CHAR_SELECT_PATH` / `CHAR_SELECT_PLAY_PATH` in bartleby/runtime/paths.py (defined, never
used) become usable.

This probe answers exactly that, on a client that has never had a character logged in —
which is the whole point, since an already-hooked client proves nothing.

WHAT IT RECORDS

A timeline, sampled continuously, of every change in:
  - whether `read_current_root_window_base()` returns a usable address
  - the names of the top-level windows under the root
  - whether CHAR_SELECT_PATH and CHAR_SELECT_PLAY_PATH exist / are visible / are grayed
  - what `is_loading()` reports

plus a one-off depth-limited dump of the tree the first time character select is seen.
Timestamps are relative to attach, so they line up with the observed timings (~3 s to the
first skippable loading screen, ~3-5 s of character-select load, 3-15 s to load in).

HOW TO RUN

    1. Close nothing. Have the probe running BEFORE you launch:
           uv run python tools/probe_char_select.py
       It waits for a new Wizard101 window to appear, attaches, and starts sampling.
    2. Launch a client and log an account in (BartleBot's launcher or by hand).
    3. Do NOT touch the window. Let the probe drive nothing — it only reads.
    4. Press keys yourself to advance the screens when the probe tells you to, or pass
       --manual to just watch a login you drive entirely by hand.
    5. Paste the timeline back.

To attach to a window that is already open instead:
       uv run python tools/probe_char_select.py --attach

READ-ONLY, with one caveat: installing the root-window hook writes to the game process.
That is the thing being tested. No clicks, no keystrokes, no other hooks.

NO REAL PLAYER DATA: the character-select screen shows real wizard names, so window text
is reported as `<len N>` rather than its contents. Pass --with-text only if you have
checked what you are about to paste. See the "No real player data" section of CLAUDE.md.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from bartleby.runtime.paths import CHAR_SELECT_PATH, CHAR_SELECT_PLAY_PATH
from wizwalker.extensions.wizsprinter import WizSprinter
from wizwalker.utils import get_all_wizard_handles, get_window_title

# Depth limit for the one-off tree dump. Character select is shallow; past this the output
# stops being readable without adding information.
MAX_DEPTH = 4

# Sampling cadence. Fine enough to place a transition within a keypress of when it
# happened, coarse enough not to contend with the game's own startup.
SAMPLE_INTERVAL = 0.25

# How long to keep sampling after attach, absent --duration. Comfortably past the worst
# case: 3 s + a loading screen + 5 s of character select + 15 s of load-in.
DEFAULT_DURATION = 120.0


def _fmt(value) -> str:
    return "?" if value is None else str(value)


async def _read_root_base(client) -> int | None:
    """The root window base address, or None if the hook has not been written yet."""
    try:
        base = await client.hook_handler.read_current_root_window_base()
    except Exception:
        return None
    return base or None


async def _window_props(window, with_text: bool) -> dict:
    out: dict = {}
    for key, getter in (("visible", window.is_visible), ("grayed", window.is_control_grayed)):
        try:
            out[key] = await getter()
        except Exception as exc:
            out[key] = f"<{type(exc).__name__}>"
    try:
        text = await window.maybe_text()
    except Exception as exc:
        out["text"] = f"<{type(exc).__name__}>"
    else:
        # Character names live here. Report shape, not content, unless asked.
        out["text"] = text if with_text else (f"<len {len(text)}>" if text else "")
    return out


async def _find_by_path(root, path: list[str]):
    """Walk a name path from the root window. Returns the window or None."""
    current = root
    for name in path:
        try:
            children = await current.children()
        except Exception:
            return None
        match = None
        for child in children:
            try:
                if await child.name() == name:
                    match = child
                    break
            except Exception:
                continue
        if match is None:
            return None
        current = match
    return current


async def _probe_path(root, path: list[str], with_text: bool) -> dict:
    window = await _find_by_path(root, path)
    if window is None:
        return {"exists": False}
    return {"exists": True, **await _window_props(window, with_text)}


async def _dump_subtree(window, with_text: bool, prefix: str = "", depth: int = 0) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if depth > MAX_DEPTH:
        return out
    try:
        children = await window.children()
    except Exception:
        return out
    for child in children:
        try:
            name = await child.name()
        except Exception:
            continue
        key = f"{prefix}/{name}" if prefix else name
        unique = key
        n = 2
        while unique in out:
            unique = f"{key}#{n}"
            n += 1
        out[unique] = await _window_props(child, with_text)
        out.update(await _dump_subtree(child, with_text, unique, depth + 1))
    return out


async def _top_level_names(root) -> list[str]:
    try:
        children = await root.children()
    except Exception:
        return []
    names = []
    for child in children:
        try:
            names.append(await child.name())
        except Exception:
            names.append("<unnamed>")
    return names


async def _wait_for_new_window(known: set[int]) -> int:
    print("Waiting for a new Wizard101 window... (launch one now; Ctrl+C to abort)")
    while True:
        new = set(get_all_wizard_handles()) - known
        if new:
            hwnd = new.pop()
            print(f"  new window: hwnd={hwnd} title={get_window_title(hwnd)!r}")
            return hwnd
        await asyncio.sleep(0.2)


def _client_for(sprinter: WizSprinter, hwnd: int):
    for client in sprinter.get_new_clients():
        if client.window_handle == hwnd:
            return client
    for client in sprinter.clients:
        if client.window_handle == hwnd:
            return client
    return None


async def probe(hwnd: int, duration: float, with_text: bool) -> None:
    sprinter = WizSprinter()
    client = _client_for(sprinter, hwnd)
    if client is None:
        print(f"Could not build a client for hwnd {hwnd}", file=sys.stderr)
        return

    t0 = time.monotonic()

    def stamp() -> str:
        return f"[{time.monotonic() - t0:6.2f}s]"

    print(f"\n{stamp()} attaching to hwnd={hwnd} title={get_window_title(hwnd)!r}")
    print(f"{stamp()} installing root-window hook only (wait_for_ready=False)")
    try:
        await client.hook_handler.activate_root_window_hook(wait_for_ready=False)
    except Exception as exc:
        print(f"{stamp()} FAILED to install root-window hook: {type(exc).__name__}: {exc}")
        print("  -> the deterministic path is out; auto-select must be blind (plan step 3B).")
        return
    print(f"{stamp()} hook installed; sampling every {SAMPLE_INTERVAL}s for {duration}s")
    print(f"{stamp()} advance the client by hand now — the probe only reads.\n")

    last: tuple | None = None
    root_first_seen: float | None = None
    char_select_first_seen: float | None = None
    dumped = False

    while time.monotonic() - t0 < duration:
        base = await _read_root_base(client)
        if base is None:
            state = ("root: not written",)
        else:
            if root_first_seen is None:
                root_first_seen = time.monotonic() - t0
                print(f"{stamp()} *** root window readable at {hex(base)} ***")
            root = client.root_window
            try:
                loading = await client.is_loading()
            except Exception as exc:
                loading = f"<{type(exc).__name__}>"
            top = await _top_level_names(root)
            sel = await _probe_path(root, CHAR_SELECT_PATH, with_text)
            play = await _probe_path(root, CHAR_SELECT_PLAY_PATH, with_text)
            state = (
                f"loading={_fmt(loading)}",
                f"top={top}",
                f"{'/'.join(CHAR_SELECT_PATH)}={sel}",
                f"{'/'.join(CHAR_SELECT_PLAY_PATH)}={play}",
            )

            if play.get("exists") and not dumped:
                char_select_first_seen = time.monotonic() - t0
                dumped = True
                print(f"\n{stamp()} *** character select reached — dumping tree ***")
                tree = await _dump_subtree(root, with_text)
                for path, props in tree.items():
                    print(f"    {path}: {props}")
                print()

        if state != last:
            print(f"{stamp()} " + "  ".join(state))
            last = state

        await asyncio.sleep(SAMPLE_INTERVAL)

    print("\n" + "=" * 72)
    print("SUMMARY")
    print(f"  root window first readable:  {'never' if root_first_seen is None else f'{root_first_seen:.2f}s'}")
    print(
        f"  character select detected:   {'never' if char_select_first_seen is None else f'{char_select_first_seen:.2f}s'}"
    )
    if char_select_first_seen is not None:
        print("  -> DETERMINISTIC PATH IS VIABLE (plan step 3A): the driver can identify the")
        print("     character-select screen and press Enter exactly once, on the right screen.")
    elif root_first_seen is not None:
        print("  -> root window reads, but CHAR_SELECT_PLAY_PATH never resolved. Check the")
        print("     top-level names above for what character select is actually called.")
    else:
        print("  -> BLIND PATH ONLY (plan step 3B): the root window is never written before")
        print("     a character loads in.")
    print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--attach",
        action="store_true",
        help="Attach to an already-open window instead of waiting for a new one.",
    )
    parser.add_argument("--hwnd", type=int, help="Attach to this specific window handle.")
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION, help="Seconds to sample.")
    parser.add_argument(
        "--with-text",
        action="store_true",
        help="Include window text verbatim. Shows real character names — check before pasting.",
    )
    args = parser.parse_args()

    async def _run() -> None:
        if args.hwnd:
            hwnd = args.hwnd
        elif args.attach:
            handles = get_all_wizard_handles()
            if not handles:
                print("No Wizard101 windows found.", file=sys.stderr)
                return
            for h in handles:
                print(f"  hwnd={h} title={get_window_title(h)!r}")
            hwnd = handles[0]
            print(f"Attaching to the first one ({hwnd}); pass --hwnd to pick another.")
        else:
            hwnd = await _wait_for_new_window(set(get_all_wizard_handles()))
        await probe(hwnd, args.duration, args.with_text)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        print("\naborted")


if __name__ == "__main__":
    main()
