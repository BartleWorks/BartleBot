"""Probe: where each client keeps its account's key bindings in memory.

Key rebinding (r811339, the Ahmarra Test realm) stores bindings with the account, not the
install: one install serves several accounts, each with its own keys, and the game rewrites
``Data/GameData/LocalInputBindings.xml`` from whichever account logs in or saves last. So the
file cannot say what each client of a multi-account team will do with a key, and BartleBot
has to read every client's own copy. The type dump has it: an ``InputBindingList`` whose
``m_inputs`` (0x48) is a list of ``InputBinding`` (``m_action`` 0x48, ``m_input`` 0x68,
``m_managerType`` 0x88, ``m_rebindable`` 0x8C, ``m_version`` 0x90, ``m_slot`` 0x94).

What this answers, per client:

  * how many ``InputBindingList``s a client holds (the shipped defaults and the account's?),
    and which one is the account's: the one that differs from ``InputBindings.xml`` where the
    account was rebound, and that changes when a binding is saved in game;
  * whether a save edits the list in place or builds a new one (the list's address);
  * what holds each list, and at which offset, and whether a global in the game's own image
    points at that holder: a fixed path is what lets BartleBot read the bindings without a
    scan of the whole process;
  * how long the scans take, for the case where no fixed path turns up.

HOW IT FINDS THEM
    ``NPCInteract`` is short enough to sit inside the ``InputBinding`` itself (a std::string's
    inline buffer), so a scan of the process for it finds the objects, and each is confirmed by
    its class name, read through its vtable. A scan for pointers to those objects finds the list
    nodes (next 0x0, prev 0x8, value 0x10, as wizwalker's ``read_shared_linked_list`` reads
    them), and walking the ring gives the whole list and its sentinel node, which the
    ``InputBindingList`` points at from 0x48. A scan for pointers to the list finds its holder.
    The vtable scan counts every ``InputBinding`` in memory, so copies on no list (freed, or
    held elsewhere) show up too.

HOW TO RUN
    Read-only, and takes no hooks, so BartleBot may stay open. Log in every client to test,
    each on the account you want to compare, in the world.

        uv run --no-sync python tools/probe_binding_memory.py

    Then change a binding in game on ONE client (Interact from X to K is a good one), save, and
    run it again: each run is saved to tools/probe_binding_memory.local.json, and the next one
    says what changed, per client and per list. Paste both reports back. Change it back after.

    Each client takes several scans of its whole memory: expect tens of seconds per client.
"""

from __future__ import annotations

import asyncio
import json
import re
import struct
import sys
import time
from pathlib import Path

from bartleby.shared.game_install import install_of_client, process_executable
from wizwalker import ClientHandler
from wizwalker.errors import PatternFailed
from wizwalker.memory.memory_object import DynamicMemoryObject, PropertyClass

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SAVED = Path(__file__).resolve().parent / "probe_binding_memory.local.json"
MODULE = "WizardGraphicalClient.exe"
# The game's binding files are not UTF-8: a key with no name is its raw key code, one byte.
ENCODING = "latin-1"

# InputBinding's fields (r811339).
ACTION, INPUT, MANAGER, REBINDABLE, VERSION, SLOT = 0x48, 0x68, 0x88, 0x8C, 0x90, 0x94
# InputBindingList.m_inputs: a pointer to the sentinel node, then the count.
LIST_HEAD, LIST_SIZE = 0x48, 0x50
NODE_NEXT, NODE_VALUE = 0x0, 0x10
MAX_LIST = 1000
OWNER_REACH = 0x400  # how far back from a pointer to look for the start of its holder
# A class name as read_type_name returns it: bare, "InputBinding", never "class InputBinding".
_CLASS_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_:<>,* ]{1,80}")

# The actions BartleBot presses, shown for every list.
WATCHED = (
    "NPCInteract",
    "ToggleInventory",
    "ToggleJewelInventory",
    "ToggleCharStats",
    "ToggleDeckConfig",
    "ToggleFurniture",
    "DisplayNewBuddyList",
    "MoveLeft",
    "EmoteJump",
    "CloseUI",
)


class _Object(DynamicMemoryObject, PropertyClass):
    """Any game object at a known address."""


def pointer_pattern(addresses) -> bytes:
    """One regex for any of *addresses* stored as a pointer, so one pass finds them all."""
    return b"|".join(re.escape(struct.pack("<Q", a)) for a in sorted(set(addresses)))


async def scan(reader, label: str, pattern: bytes, *, module: str | None = None) -> list[int]:
    """Every 8-aligned match of *pattern*, and how long the pass took."""
    started = time.perf_counter()
    try:
        found = await reader.pattern_scan(pattern, module=module, return_multiple=True)
    except PatternFailed:
        found = []
    aligned = [a for a in found if a % 8 == 0]
    print(f"    scan {label}: {len(aligned)} aligned of {len(found)} hit(s) in {time.perf_counter() - started:.1f}s")
    return aligned


async def type_name(reader, address: int) -> str:
    """The class name of the object at *address*, or "" when there is none there."""
    if address <= 0x10000:
        return ""
    try:
        name = await _Object(reader, address).read_type_name()
    except Exception:  # noqa: BLE001 - a guess at an address fails in many ways
        return ""
    return name if _CLASS_NAME.fullmatch(name) else ""


async def u64(reader, address: int) -> int:
    return struct.unpack("<Q", await reader.read_bytes(address, 8))[0]


async def read_binding(reader, address: int) -> dict:
    obj = _Object(reader, address)
    head = await reader.read_bytes(address + MANAGER, SLOT + 4 - MANAGER)
    manager, rebindable, version, slot = struct.unpack_from("<i?3xii", head)
    return {
        "action": await obj.read_string_from_offset(ACTION, encoding=ENCODING),
        "input": await obj.read_string_from_offset(INPUT, encoding=ENCODING),
        "manager": manager,
        "rebindable": rebindable,
        "version": version,
        "slot": slot,
    }


async def walk_ring(reader, start_node: int, is_binding) -> tuple[int, list[int]] | None:
    """The sentinel and members, in order, of the list ring *start_node* is on; None if it is no such ring."""
    members: list[int] = []
    sentinel = None
    node = start_node
    for _ in range(MAX_LIST):
        value = await u64(reader, node + NODE_VALUE)
        if await is_binding(value):
            members.append((node, value))
        elif sentinel is None:
            sentinel = node
        else:
            return None  # two nodes holding no binding: not an InputBindingList's ring
        node = await u64(reader, node + NODE_NEXT)
        if node == start_node:
            break
    else:
        return None
    if sentinel is None:
        return None
    # Members in list order: from the node after the sentinel.
    order: list[int] = []
    node = await u64(reader, sentinel + NODE_NEXT)
    by_node = dict(members)
    while node != sentinel and node in by_node:
        order.append(by_node[node])
        node = await u64(reader, node + NODE_NEXT)
    return sentinel, order


_ENTRY = re.compile(r"<m_inputs>\s*<Class Name=\"class InputBinding\">(.*?)</Class>\s*</m_inputs>", re.DOTALL)
_FIELD = re.compile(r"<(m_\w+)>([^<]*)</\1>")


def parse_bindings(text: str) -> list[dict[str, str]]:
    """Every binding in an ``InputBindingList`` file, its fields in file order."""
    return [dict(_FIELD.findall(body)) for body in _ENTRY.findall(text)]


async def holder_of(reader, pointer_at: int) -> tuple[int, str, int] | None:
    """The object a pointer stored at *pointer_at* belongs to: its address, class and the field's offset."""
    for back in range(0, OWNER_REACH, 8):
        name = await type_name(reader, pointer_at - back)
        if name:
            return pointer_at - back, name, back
    return None


def load_defaults(pid: int) -> tuple[dict, dict]:
    """The shipped defaults and the install's LocalInputBindings.xml, each as {(action, slot): input}."""

    def read(path: Path) -> dict:
        if not path.exists():
            return {}
        entries = parse_bindings(path.read_bytes().decode(ENCODING))
        return {(e.get("m_action"), int(e.get("m_slot", "0"))): e.get("m_input") for e in entries}

    exe = process_executable(pid)
    if exe is None:
        return {}, {}
    data = install_of_client(exe) / "Data" / "GameData"
    return read(data / "InputBindings.xml"), read(data / "LocalInputBindings.xml")


def show(text: str) -> str:
    return ascii(text)[1:-1]


async def probe_client(client) -> dict:
    reader = client.hook_handler
    pid = client.process_id
    print(f"\n{'=' * 78}\nCLIENT pid {pid}\n{'=' * 78}")
    defaults, local_file = load_defaults(pid)

    # 1. The InputBindings, by the action name inside them.
    hits = await scan(reader, "NPCInteract text", re.escape(b"NPCInteract\x00"), module=None)
    seeds = []
    for hit in hits + [h for h in []]:
        address = hit - ACTION
        if (await type_name(reader, address)) == "InputBinding":
            seeds.append(address)
    # The aligned filter in scan() drops strings at odd offsets, but an inline buffer at 0x48 is
    # 8-aligned whenever its object is, so nothing real is lost.
    print(f"    NPCInteract InputBindings: {len(seeds)}")
    if not seeds:
        print("    none found: is a wizard logged in?")
        return {}

    binding_cache: dict[int, bool] = {}

    async def is_binding(address: int) -> bool:
        if address not in binding_cache:
            binding_cache[address] = (await type_name(reader, address)) == "InputBinding"
        return binding_cache[address]

    # 2. Every InputBinding in memory, by its vtable.
    vtables = {await u64(reader, s) for s in seeds}
    everything = await scan(reader, f"InputBinding vtable(s) {[hex(v) for v in vtables]}", pointer_pattern(vtables))
    instances = [a for a in everything if await is_binding(a)]
    print(f"    InputBinding objects in memory: {len(instances)}")

    # 3. The lists: nodes pointing at the seeds, the ring each is on, the list holding the ring.
    node_hits = await scan(reader, "pointers to the NPCInteract bindings", pointer_pattern(seeds))
    rings: dict[int, list[int]] = {}
    for hit in node_hits:
        try:
            found = await walk_ring(reader, hit - NODE_VALUE, is_binding)
        except Exception:  # noqa: BLE001 - a pointer that is not a list node leads into garbage
            found = None
        if found and found[0] not in rings:
            rings[found[0]] = found[1]
    print(f"    list rings holding them: {len(rings)}")

    list_hits = await scan(reader, "pointers to the rings' sentinels", pointer_pattern(rings)) if rings else []
    # The sentinel is also pointed at by its neighbor nodes; a list is told by its class, or failing
    # that by a count that matches its ring.
    lists: list[dict] = []
    for hit in list_hits:
        sentinel = await u64(reader, hit)
        list_address = hit - LIST_HEAD
        if sentinel not in rings or any(e["address"] == list_address for e in lists):
            continue
        name = await type_name(reader, list_address)
        size = struct.unpack("<i", await reader.read_bytes(list_address + LIST_SIZE, 4))[0]
        if name == "InputBindingList" or size == len(rings[sentinel]):
            lists.append(
                {"address": list_address, "class": name or "(class unread)", "size": size, "sentinel": sentinel}
            )
    for sentinel, members in rings.items():
        if not any(entry["sentinel"] == sentinel for entry in lists):
            lists.append({"address": None, "class": "(no holder found)", "size": len(members), "sentinel": sentinel})

    # 4. Who holds each list, and whether a global in the game's image reaches the holder.
    real = [entry["address"] for entry in lists if entry["address"]]
    holder_hits = await scan(reader, "pointers to the lists", pointer_pattern(real)) if real else []
    for hit in holder_hits:
        target = await u64(reader, hit)
        entry = next(e for e in lists if e["address"] == target)
        held = await holder_of(reader, hit)
        entry.setdefault("held_by", []).append(
            {
                "pointer_at": hit,
                "holder": held[0] if held else None,
                "class": held[1] if held else "?",
                "offset": held[2] if held else None,
            }
        )
    holders = {h["holder"] for e in lists for h in e.get("held_by", []) if h["holder"]}
    statics = (
        await scan(reader, f"{MODULE} for pointers to the holders", pointer_pattern(holders), module=MODULE)
        if holders
        else []
    )
    statics += (
        await scan(reader, f"{MODULE} for pointers to the lists", pointer_pattern(real), module=MODULE) if real else []
    )
    base = None
    if statics:
        import pymem.process  # noqa: PLC0415 - the module base, for an RVA

        module = pymem.process.module_from_name(reader.process.process_handle, MODULE)
        base = module.lpBaseOfDll

    # 5. The report.
    on_lists = {b for members in rings.values() for b in members}
    print(f"    InputBindings on no list found here: {len(set(instances) - on_lists)}")
    saved = []
    for index, entry in enumerate(lists, 1):
        members = rings[entry["sentinel"]]
        bindings = [await read_binding(reader, b) for b in members]
        table = {(b["action"], b["slot"]): b["input"] for b in bindings}
        address = f"0x{entry['address']:X}" if entry["address"] else "?"
        print(
            f"\n  LIST {index} at {address}: {entry['class']}, {len(bindings)} bindings (its count says {entry['size']})"
        )
        for held in entry.get("held_by", []):
            where = (
                f"0x{held['holder']:X} {held['class']} +0x{held['offset']:X}" if held["holder"] else "no object found"
            )
            print(f"    held at 0x{held['pointer_at']:X}: {where}")
        for hit in statics:
            target = await u64(reader, hit)
            if target == entry["address"] or any(h["holder"] == target for h in entry.get("held_by", [])):
                print(f"    reached from {MODULE}+0x{hit - base:X} (-> 0x{target:X})")
        managers = sorted({b["manager"] for b in bindings})
        print(f"    m_managerType values: {managers}")
        for action in WATCHED:
            keys = [show(table[(action, s)]) for s in (0, 1) if (action, s) in table]
            print(f"    {action:<22} {' | '.join(keys) or '(none)'}")
        rebound = sorted(k for k in table.keys() | defaults.keys() if table.get(k) != defaults.get(k))
        print(f"    differs from InputBindings.xml in {len(rebound)} binding(s)")
        for action, slot in rebound[:15]:
            print(
                f"      {action} slot {slot}: {show(str(defaults.get((action, slot))))} -> {show(str(table.get((action, slot))))}"
            )
        same_as_file = bool(local_file) and table == local_file
        print(f"    matches LocalInputBindings.xml exactly: {same_as_file}")
        saved.append(
            {
                "address": entry["address"],
                "class": entry["class"],
                "bindings": {f"{a}|{s}": i for (a, s), i in table.items()},
            }
        )
    return {"lists": saved, "instances": len(instances)}


def compare(previous: dict, current: dict) -> None:
    """What changed for each client since the last run: lists moved, added or rebound."""
    print(f"\n{'=' * 78}\nSINCE THE LAST RUN\n{'=' * 78}")
    for pid, now in current.items():
        before = previous.get(pid)
        if before is None:
            print(f"  pid {pid}: not in the last run")
            continue
        old = {tuple(sorted(lst["bindings"].items())): lst for lst in before["lists"]}
        print(f"  pid {pid}: {len(before['lists'])} list(s) then, {len(now['lists'])} now")
        for index, lst in enumerate(now["lists"], 1):
            same = old.get(tuple(sorted(lst["bindings"].items())))
            if same is not None:
                moved = "same address" if same["address"] == lst["address"] else "NEW address"
                print(f"    list {index}: bindings unchanged, {moved}")
                continue
            closest = max(before["lists"], key=lambda o: len(set(o["bindings"].items()) & set(lst["bindings"].items())))
            changed = {
                k
                for k in lst["bindings"].keys() | closest["bindings"].keys()
                if lst["bindings"].get(k) != closest["bindings"].get(k)
            }
            moved = "same address" if closest["address"] == lst["address"] else "NEW address"
            print(f"    list {index}: {len(changed)} binding(s) changed against its nearest old list, {moved}")
            for key in sorted(changed)[:10]:
                print(
                    f"      {show(key)}: {show(str(closest['bindings'].get(key)))} -> {show(str(lst['bindings'].get(key)))}"
                )


async def main() -> int:
    previous = json.loads(SAVED.read_text(encoding="utf-8")) if SAVED.exists() else {}
    current: dict[str, dict] = {}
    async with ClientHandler() as handler:
        clients = handler.get_new_clients()
        if not clients:
            print("No Wizard101 clients found. Log a wizard in first.")
            return 1
        print(f"Found {len(clients)} client(s).")
        started = time.perf_counter()
        for client in clients:
            current[str(client.process_id)] = await probe_client(client)
        print(f"\nAll clients in {time.perf_counter() - started:.0f}s.")
    if previous:
        compare(previous, current)
    SAVED.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    print(f"\nSaved to {SAVED.name}; change a binding in game and run again to see what moves.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
