"""Diagnostic: is the *selected* character at character select a writable gid in memory?

WHY THIS EXISTS

BartleBot cannot choose which character an account launches with. The only documented
lever is ``<LastPlayer TYPE="GID">`` in ``Bin/config.xml``, and it is a bad one:
there is a single config.xml for the whole install, so a per-account choice forces the
patch/start step to be serialized and destroys the batch shape that makes a launch
profile take seconds rather than minutes.

Two alternatives were considered and ruled out before this probe was written:

  * **Drive the character-select UI.** There is nothing to drive. The screen is a
    classroom with six wizards at desks, selected by clicking the wizard itself; the
    arrows page to the *next classroom* rather than cycling wizards. ``probe_char_select``
    dumped the whole tree and there are no per-character windows in it — only
    ``sprBanner/txtName``, ``sprSubBanner/txtLevel``, ``btnPlay`` and friends.

  * **Verify a click by reading the banner name.** Names are not unique. Plenty of
    accounts carry six wizards sharing one first name, so a name readback cannot tell
    you which desk you landed on.

What is unique is the character id — ``ClientObject.m_characterId``, a gid. If the
client holds the *currently selected* character's gid somewhere at character select,
and that location can be written, then picking a character is one memory write per
client: no shared file, no serialization, and the parallel batch launch survives intact.

This probe answers whether that location exists and whether writing it takes.

HOW IT WORKS

The awkward part of hunting for a character id is having one to search for. That is
already solved: the id is readable from ``client_object.character_id()`` while in game,
so the probe collects the needle *before* it needs it.

    1. Attach to a client that is in game. Read its character id  ->  gid A.
    2. You quit to character select (Spellbook -> Preferences -> Quit). The probe waits
       for the screen and does not touch the client.
    3. Scan the process for every 8-byte-aligned occurrence of gid A. Expect two
       families, indistinguishable at this point because the game preselects whichever
       character was played last -- which is A:
           - roster entries, one per character on the account
           - the selection slot, currently holding A
    4. You click a *different* wizard's desk. The probe re-reads every candidate.
       Addresses whose value **changed** are selection state; addresses that held still
       are roster. That one click is what separates the two families, and it is the
       reason this is a script rather than an eyeballed diff.
       Whatever value appeared is character B's gid, so the probe learns ids for
       characters it has never played.
    5. THE KILL TEST. With B's desk selected, the probe writes gid A back to a changed
       address and asks whether the classroom follows. If the game re-derives its
       selection from scene state when Play is pressed, this whole approach dies here --
       which is why it happens before anything is built on top of it.

It also dumps the bytes around each roster hit. If name keys or a level sit beside the
gid, then enumerating an account's characters for the launch cards falls out for free;
name keys are the one encoding bartlebot.game.name_tables can already decode. And because a
selection held as an *index* rather than a gid would never show up in step 3, the same
neighborhood snapshot is re-read after the desk click, so a small integer changing
beside the roster is caught without a second full scan.

WHAT THIS DOES NOT ANSWER

A stable path to the address. An absolute address is worthless across launches, so a
usable feature needs a pointer chain from a module base or a route from an object we can
already resolve at character select. This probe reports the module and offset when a hit
lands inside one, which is a starting point and nothing more. That hunt is a separate,
open-ended session.

HOW TO RUN

    CLOSE BARTLEBOT FIRST. This probe builds its own Client and activates hooks. Two
    Client objects over one game process mean two _prepare_autobot calls, and the second
    zeroes the scratch region the first one's hooks jump into -- corrupting live hooks in
    the game process. See the note in launcher/auto_select.py.

    1. Launch a client by hand and get a character in game. If the account has only one
       character, this probe cannot tell you anything: step 4 needs a second desk.
    2. uv run python tools/probe_character_gid.py
       (--hwnd to pick a specific window when several are open.)
    3. Follow the prompts. The probe reads only, until it asks permission to write.

NO REAL PLAYER DATA: a character id identifies an account's character, so ids are masked
in console output the way bartlebot.account_identity masks a user id. --with-ids prints
them in full and --report writes an unmasked report to probe_character_gid.local.txt,
which is gitignored (*.local.*). Check what you are pasting. See the "No real player
data" section of CLAUDE.md.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from pathlib import Path

import pymem
from wizwalker.extensions.wizsprinter import WizSprinter
from wizwalker.memory.memory_object import Primitive
from wizwalker.utils import get_all_wizard_handles, get_window_title

from bartlebot.account_identity import mask
from bartlebot.nodes import node_helpers
from bartlebot.nodes.paths import CHAR_SELECT_PLAY_PATH

# Bytes either side of a hit to snapshot. A roster entry is the thing we hope to find
# structure in -- a name key triple is 12 bytes, a level is 4 -- so this is sized to
# bracket one entry and its neighbors rather than to be read in full by eye.
NEIGHBORHOOD = 256

# How long to wait for character select before giving up, and how often to look.
CHAR_SELECT_TIMEOUT = 300.0
CHAR_SELECT_POLL = 0.5

REPORT_PATH = Path(__file__).with_name("probe_character_gid.local.txt")


class _Report:
    """Console output, plus an optional unmasked copy on disk.

    Ids are masked on the console because that is what gets pasted into an issue, and
    unmasked in the file because the file is gitignored and the whole point of it is to
    be usable afterwards.
    """

    def __init__(self, *, with_ids: bool, to_file: bool) -> None:
        self._with_ids = with_ids
        self._lines: list[str] | None = [] if to_file else None

    def gid(self, value: int | None) -> str:
        if value is None:
            return "?"
        return str(value) if self._with_ids else mask(value)

    def say(self, line: str = "", *, file_only: str | None = None) -> None:
        print(line)
        if self._lines is not None:
            self._lines.append(file_only if file_only is not None else line)

    def write(self) -> None:
        if self._lines is None:
            return
        REPORT_PATH.write_text("\n".join(self._lines) + "\n", encoding="utf-8")
        print(f"\nUnmasked report written to {REPORT_PATH.name} (gitignored).")


async def _ask(prompt: str) -> str:
    """Block for console input without blocking the event loop."""
    return await asyncio.to_thread(input, prompt)


def _client_for(sprinter: WizSprinter, hwnd: int):
    for client in sprinter.get_new_clients():
        if client.window_handle == hwnd:
            return client
    for client in sprinter.clients:
        if client.window_handle == hwnd:
            return client
    return None


def _module_of(process: pymem.Pymem, address: int) -> str | None:
    """``name+0xoffset`` if *address* falls inside a loaded module, else None.

    Only a hint for the pointer-path hunt that has to follow: a hit inside a module is
    static data reachable from a base, while a hit on the heap needs a real chain.
    """
    for module in process.list_modules():
        base = module.lpBaseOfDll
        if base <= address < base + module.SizeOfImage:
            return f"{module.name}+0x{address - base:x}"
    return None


async def _scan_u64(client, value: int) -> list[int]:
    """Every address holding *value* as a little-endian uint64."""
    pattern = re.escape(value.to_bytes(8, "little"))
    try:
        return await client.hook_handler.pattern_scan(pattern, return_multiple=True)
    except Exception:
        return []


async def _read_u64(client, address: int) -> int | None:
    try:
        return await client.hook_handler.read_typed(address, Primitive.uint64)
    except Exception:
        return None


async def _snapshot(client, addresses: list[int]) -> dict[int, bytes]:
    """The bytes around each address, for a before/after comparison."""
    out: dict[int, bytes] = {}
    for address in addresses:
        try:
            out[address] = await client.hook_handler.read_bytes(address - NEIGHBORHOOD, NEIGHBORHOOD * 2 + 8)
        except Exception:
            continue
    return out


def _changed_spans(before: bytes, after: bytes) -> list[tuple[int, bytes, bytes]]:
    """Runs of differing bytes between two equal-length snapshots.

    Offsets are relative to the hit itself (negative = before it), so a selection index
    sitting just ahead of a roster entry reads as a small negative offset rather than a
    raw address.
    """
    spans: list[tuple[int, bytes, bytes]] = []
    start: int | None = None
    for i in range(min(len(before), len(after))):
        differs = before[i] != after[i]
        if differs and start is None:
            start = i
        elif not differs and start is not None:
            spans.append((start - NEIGHBORHOOD, before[start:i], after[start:i]))
            start = None
    if start is not None:
        spans.append((start - NEIGHBORHOOD, before[start:], after[start:]))
    return spans


async def _await_char_select(client, report: _Report) -> bool:
    """Poll until the character-select Play button is up, or time out.

    btnPlay rather than mainWindow: probe_char_select measured mainWindow appearing
    ~2 s before the screen is actually settled, with every button still reading
    invisible and carrying placeholder text.
    """
    deadline = time.monotonic() + CHAR_SELECT_TIMEOUT
    while time.monotonic() < deadline:
        try:
            if await node_helpers.is_visible_by_path(client, CHAR_SELECT_PLAY_PATH):
                return True
        except Exception:
            pass
        await asyncio.sleep(CHAR_SELECT_POLL)
    report.say("Timed out waiting for the character-select screen.")
    return False


async def probe(hwnd: int, report: _Report) -> None:
    sprinter = WizSprinter()
    client = _client_for(sprinter, hwnd)
    if client is None:
        print(f"Could not build a client for hwnd {hwnd}", file=sys.stderr)
        return

    report.say(f"Attached to hwnd={hwnd} title={get_window_title(hwnd)!r}")
    await client.activate_hooks()

    # ── 1. The needle ────────────────────────────────────────────────────────
    try:
        gid_a = await client.client_object.character_id()
    except Exception as exc:
        report.say(f"Could not read the character id: {exc!r}")
        report.say("The client has to be in game, not at character select, when this starts.")
        return
    if not gid_a:
        report.say("character_id() read 0 — the client is not in game yet.")
        return
    report.say(f"Character A id: {report.gid(gid_a)}", file_only=f"Character A id: {gid_a}")

    # ── 2. Character select ──────────────────────────────────────────────────
    report.say("")
    report.say("Quit to character select now (Spellbook -> Preferences -> Quit).")
    report.say("The probe only watches; it will notice the screen on its own.")
    if not await _await_char_select(client, report):
        return
    report.say("Character select reached.")

    # ── 3. Scan ──────────────────────────────────────────────────────────────
    report.say("")
    report.say("Scanning for the id... (a full address-space scan; give it a moment)")
    candidates = await _scan_u64(client, gid_a)
    if not candidates:
        report.say("")
        report.say("No occurrence of the id anywhere in the process at character select.")
        report.say("It is not held as a plain uint64 here, so this approach is dead:")
        report.say("either the roster is not resident until Play, or ids are packed/encrypted.")
        return
    report.say(f"{len(candidates)} candidate address(es):")
    for address in candidates:
        where = _module_of(client.hook_handler.process, address) or "heap"
        report.say(f"    0x{address:x}  [{where}]")

    before = await _snapshot(client, candidates)

    # ── 4. The desk click ────────────────────────────────────────────────────
    report.say("")
    report.say("Now click a DIFFERENT wizard's desk in the classroom. Do not press Play.")
    await _ask("Press Enter once a different wizard is selected... ")

    changed: list[tuple[int, int]] = []  # (address, new value)
    unchanged: list[int] = []
    for address in candidates:
        value = await _read_u64(client, address)
        if value is None:
            report.say(f"    0x{address:x}  unreadable now")
        elif value != gid_a:
            changed.append((address, value))
        else:
            unchanged.append(address)

    report.say("")
    if changed:
        report.say(f"{len(changed)} address(es) changed — these are selection state:")
        for address, value in changed:
            where = _module_of(client.hook_handler.process, address) or "heap"
            report.say(
                f"    0x{address:x}  [{where}]  now {report.gid(value)}",
                file_only=f"    0x{address:x}  [{where}]  now {value}",
            )
        report.say("")
        report.say("The new value is character B's id, learned without ever playing B.")
    else:
        report.say("Nothing changed. The selection is not stored as a gid.")
        report.say("If an index changes beside the roster, the neighborhood diff below finds it.")

    if unchanged:
        report.say("")
        report.say(f"{len(unchanged)} address(es) held still — roster entries:")
        for address in unchanged:
            report.say(f"    0x{address:x}  [{_module_of(client.hook_handler.process, address) or 'heap'}]")

    # ── 4b. Neighborhood diff — catches an index, and shows roster structure ─
    after = await _snapshot(client, candidates)
    report.say("")
    report.say("Bytes that changed near each hit (offset relative to the hit):")
    any_span = False
    for address in candidates:
        if address not in before or address not in after:
            continue
        spans = [s for s in _changed_spans(before[address], after[address]) if s[0] != 0]
        if not spans:
            continue
        any_span = True
        report.say(f"    0x{address:x}")
        for offset, old, new in spans:
            report.say(f"        {offset:+5d}  {old.hex()} -> {new.hex()}")
    if not any_span:
        report.say("    (nothing changed around any hit)")

    report.say("")
    report.say("Bytes around the first roster hit, for structure (name keys? level?):")
    if unchanged and unchanged[0] in after:
        block = after[unchanged[0]]
        for row in range(0, len(block), 32):
            report.say(f"    {row - NEIGHBORHOOD:+5d}  {block[row : row + 32].hex()}")

    # ── 5. The kill test ─────────────────────────────────────────────────────
    if not changed:
        report.say("")
        report.say("No writable selection slot found, so there is nothing to write. Stopping.")
        return

    report.say("")
    report.say("KILL TEST — this WRITES to the game process. Nothing before this point did.")
    answer = await _ask("Write character A's id back over the selection, with B selected? [y/N] ")
    if answer.strip().lower() not in ("y", "yes"):
        report.say("Skipped. The read-only findings above stand on their own.")
        return

    for address, _value in changed:
        try:
            await client.hook_handler.write_typed(address, gid_a, Primitive.uint64)
        except Exception as exc:
            report.say(f"    0x{address:x}  write failed: {exc!r}")
            continue
        report.say("")
        report.say(f"Wrote A back to 0x{address:x}.")
        report.say("Look at the classroom: did the selection move back to character A?")
        moved = await _ask("Did the selection visibly follow the write? [y/N] ")
        report.say(f"    -> selection followed: {moved.strip().lower() in ('y', 'yes')}")
        report.say("Now press Play and see which character actually loads in.")
        loaded = await _ask("Which loaded — (a)/(b)/(neither)? ")
        report.say(f"    -> loaded: {loaded.strip().lower() or '?'}")
        break  # one address at a time; a second write would confound the first result

    report.say("")
    report.say("If the selection followed AND A loaded, the approach works and the")
    report.say("remaining problem is a stable path to that address across launches.")
    report.say("If Play loaded B regardless, the game re-derives selection from the")
    report.say("scene and this route is dead — fall back to whichever character appears.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hwnd", type=int, help="Attach to this specific window handle.")
    parser.add_argument(
        "--with-ids",
        action="store_true",
        help="Print character ids in full. They identify an account's characters — check before pasting.",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help=f"Also write an unmasked report to {REPORT_PATH.name} (gitignored).",
    )
    args = parser.parse_args()

    async def _run() -> None:
        if args.hwnd:
            hwnd = args.hwnd
        else:
            handles = get_all_wizard_handles()
            if not handles:
                print("No Wizard101 windows found. Launch one and get a character in game.", file=sys.stderr)
                return
            for handle in handles:
                print(f"  hwnd={handle} title={get_window_title(handle)!r}")
            hwnd = handles[0]
            if len(handles) > 1:
                print(f"Attaching to the first one ({hwnd}); pass --hwnd to pick another.")
        report = _Report(with_ids=args.with_ids, to_file=args.report)
        try:
            await probe(hwnd, report)
        finally:
            report.write()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        print("\naborted")


if __name__ == "__main__":
    main()
