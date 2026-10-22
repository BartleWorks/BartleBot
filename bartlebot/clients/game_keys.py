"""Each client's key bindings, read from its own memory.

The game keeps key bindings with the account, not the install: one install serves every
account, each with its own keys, and the ``LocalInputBindings.xml`` they share holds one
account's at most. So a client's bindings are read from the client, where the game keeps them
as one ``InputBindingList`` whose ``m_inputs`` (0x48) is a list of ``InputBinding``
(r811339's type dump; ``tools/probe_binding_memory.py`` found exactly one list per client, and
a rebind saved in game changed it in place).

**Found once, read on every use.** Finding the list scans the process, about four seconds; so
its address is kept, per window, and each read goes through it (about 10 ms for 109 bindings),
which sees a rebind the moment the player saves it. An address that no longer holds the list
is found again.

**Never a reason not to run.** A client whose bindings cannot be read gets the game's
defaults for the actions BartleBot presses, with a warning, until ``key_map(refresh=True)``
tries again: the pre-run check does, before every run.

**Pressed by action.** ``press(client, GameAction.INTERACT)`` sends whatever key that client's
account has Interact on, never a fixed letter; ``test_game_keys_no_fixed_keys`` keeps a fixed
one out. ``docs/KEY_BINDINGS.md``.
"""

from __future__ import annotations

import logging
import re
import struct
from dataclasses import dataclass

from wizwalker import Keycode
from wizwalker.errors import PatternFailed
from wizwalker.memory.memory_object import DynamicMemoryObject, PropertyClass

from bartlebot.game.key_bindings import ENCODING, Binding, GameAction, KeyMap, action_label

logger = logging.getLogger(__name__)

# InputBinding's fields (r811339): m_action and m_input are std::strings, m_slot an int.
ACTION, INPUT, SLOT = 0x48, 0x68, 0x94
# InputBindingList.m_inputs: a pointer to the list's sentinel node, then its count. A node is
# next 0x0, prev 0x8, then the InputBinding pointer at 0x10, as wizwalker's
# read_shared_linked_list reads it.
LIST_HEAD, LIST_SIZE = 0x48, 0x50
NODE_NEXT, NODE_VALUE = 0x0, 0x10
MAX_BINDINGS = 1000

# Short enough to sit inside its InputBinding, in a std::string's inline buffer, so a scan for
# it lands on the object. Any action would do; this one is always bound.
_SEED = b"NPCInteract\x00"


@dataclass
class _Entry:
    address: int | None = None
    unreadable: bool = False
    """Memory gave no list: defaults until a refresh, rather than a four-second scan per press."""
    fixed: KeyMap | None = None


_entries: dict[int, _Entry] = {}


class _Object(DynamicMemoryObject, PropertyClass):
    """Any game object at a known address, for its class name."""


def _handle(client) -> int:
    return getattr(client, "window_handle", None) or id(client)


async def type_name(reader, address: int) -> str:
    """The class name of the object at *address*, as the game reflects it ("InputBinding"), or "".

    Many of the addresses asked about are guesses, and a guess fails in many ways.
    """
    if address <= 0x10000:
        return ""
    try:
        return await _Object(reader, address).read_type_name()
    except Exception:  # noqa: BLE001 - see above
        return ""


async def _u64(reader, address: int) -> int:
    return struct.unpack("<Q", await reader.read_bytes(address, 8))[0]


async def _string(reader, address: int) -> str:
    """An MSVC std::string: inline up to 15 bytes, else a pointer; then its size and capacity."""
    raw = await reader.read_bytes(address, 0x20)
    size, capacity = struct.unpack_from("<QQ", raw, 0x10)
    if size > 0x1000:
        raise ValueError(f"a string of {size} bytes")
    data = raw[:size] if capacity < 0x10 else await reader.read_bytes(struct.unpack_from("<Q", raw)[0], size)
    return data.decode(ENCODING)


def _pointers_to(addresses) -> bytes:
    """One pattern for any of *addresses* stored as a pointer."""
    return b"|".join(re.escape(struct.pack("<Q", a)) for a in sorted(set(addresses)))


async def _scan(reader, pattern: bytes) -> list[int]:
    try:
        found = await reader.pattern_scan(pattern, return_multiple=True)
    except PatternFailed:
        return []
    return [a for a in found if a % 8 == 0]


async def _holds(reader, value: int, vtable: int) -> bool:
    """Whether a node's value is an object with *vtable*. The sentinel's value is garbage, often unreadable."""
    if value <= 0x10000:
        return False
    try:
        return await _u64(reader, value) == vtable
    except Exception:  # noqa: BLE001 - see above
        return False


async def _ring(reader, start_node: int, vtable: int) -> tuple[int, int] | None:
    """The sentinel and member count of the InputBinding list ring *start_node* is on, or None.

    A member is told by its vtable, one read, rather than by its class name, several: a scan
    turns up a dozen pointers to try, each a ring of a hundred nodes.
    """
    sentinel = None
    members = 0
    node = start_node
    for _ in range(MAX_BINDINGS + 1):
        if not await _holds(reader, await _u64(reader, node + NODE_VALUE), vtable):
            if sentinel is not None:
                return None  # two nodes holding no binding: some other list
            sentinel = node
        else:
            members += 1
        node = await _u64(reader, node + NODE_NEXT)
        if node == start_node:
            return (sentinel, members) if sentinel is not None else None
    return None


async def find_binding_list(reader) -> int | None:
    """The address of the client's InputBindingList, or None.

    The InputBinding holding "NPCInteract", then the list nodes pointing at it, then the rings
    those nodes are on, then the list pointing at the chosen ring's sentinel from 0x48: three
    scans. The game's key bindings page leaves lists of its own behind, one binding each, inside
    an ``InputBindingData`` per row, and a row can still hold a key the player has since
    changed; so the ring chosen is the one with the most bindings, the player's whole set.
    """
    seeds = [hit - ACTION for hit in await _scan(reader, re.escape(_SEED))]
    seeds = [s for s in seeds if await type_name(reader, s) == "InputBinding"]
    if not seeds:
        return None
    vtable = await _u64(reader, seeds[0])
    rings: dict[int, int] = {}
    for hit in await _scan(reader, _pointers_to(seeds)):
        try:
            found = await _ring(reader, hit - NODE_VALUE, vtable)
        except Exception:  # noqa: BLE001 - a pointer that is no list node leads into garbage
            continue
        if found is not None:
            rings[found[0]] = found[1]
    # Largest first, and only a ring a list still points at: a freed list can leave its ring.
    for sentinel in sorted(rings, key=rings.__getitem__, reverse=True):
        if rings[sentinel] < 2:
            break  # only the bindings page's leftovers: no whole set to read
        for pointer in await _scan(reader, _pointers_to([sentinel])):
            if await type_name(reader, pointer - LIST_HEAD) == "InputBindingList":
                return pointer - LIST_HEAD
    return None


async def read_binding_list(reader, address: int) -> list[Binding]:
    """Every binding in the InputBindingList at *address*. ValueError when it holds none any more."""
    if await type_name(reader, address) != "InputBindingList":
        raise ValueError(f"no InputBindingList at 0x{address:X}")
    sentinel = await _u64(reader, address + LIST_HEAD)
    count = struct.unpack("<i", await reader.read_bytes(address + LIST_SIZE, 4))[0]
    if not 0 < count <= MAX_BINDINGS:
        raise ValueError(f"an InputBindingList of {count} bindings")
    bindings = []
    node = await _u64(reader, sentinel + NODE_NEXT)
    for _ in range(count):
        binding = await _u64(reader, node + NODE_VALUE)
        slot = struct.unpack("<i", await reader.read_bytes(binding + SLOT, 4))[0]
        bindings.append(Binding(await _string(reader, binding + ACTION), await _string(reader, binding + INPUT), slot))
        node = await _u64(reader, node + NODE_NEXT)
    return bindings


async def key_map(client, *, refresh: bool = False) -> KeyMap:
    """The client's key bindings, from its memory; the game's defaults when they cannot be read.

    *refresh* tries memory again for a client that failed before. Never raises.
    """
    entry = _entries.setdefault(_handle(client), _Entry())
    if entry.fixed is not None:
        return entry.fixed
    if refresh:
        entry.unreadable = False
    if entry.unreadable:
        return KeyMap.defaults()
    reader = getattr(client, "hook_handler", None)
    try:
        if entry.address is not None:
            try:
                return KeyMap(await read_binding_list(reader, entry.address), source="memory")
            except Exception:  # noqa: BLE001 - moved, or the process is gone; find it again
                entry.address = None
        address = await find_binding_list(reader)
        if address is None:
            raise LookupError("no InputBindingList in the client's memory")
        bindings = await read_binding_list(reader, address)
    except Exception:
        entry.unreadable = True
        logger.warning(
            "Could not read a client's key bindings from its memory (window %s); pressing the game's default keys",
            _handle(client),
            exc_info=True,
        )
        return KeyMap.defaults()
    entry.address = address
    return KeyMap(bindings, source="memory")


def use_map(client, keys: KeyMap | None) -> None:
    """Answer *client*'s bindings with *keys* instead of reading memory; None reads memory again.

    For a client with no game behind it: the tests' FakeClient.
    """
    _entries.setdefault(_handle(client), _Entry()).fixed = keys


class KeyNotPressable(RuntimeError):
    """The client has no key a press of the action can use: bound only with a modifier, say."""


@dataclass(frozen=True)
class VirtualKey:
    """A key code ``Keycode`` has no member for: wizwalker's ``send_key`` reads only ``.value``.

    ``Keycode`` stops short of the punctuation keys (``;`` ``=`` ``[`` and the rest, 0xBA-0xDE),
    which a player can bind like any other.
    """

    value: int

    @property
    def name(self) -> str:
        return f"VK_0x{self.value:02X}"


def keycode(code: int) -> Keycode | VirtualKey:
    """What ``client.send_key`` sends for a virtual-key code."""
    try:
        return Keycode(code)
    except ValueError:
        return VirtualKey(code)


async def press(client, action: GameAction, seconds: float = 0) -> None:
    """Press the key *client*'s account has *action* on, for *seconds* as ``send_key`` does.

    Raises KeyNotPressable, naming the action and why, when it has none a press can use. The
    pre-run check refuses a run before that can happen; this is for a rebind made mid-run.
    """
    choice = (await key_map(client)).choose(action)
    if choice.code is None:
        raise KeyNotPressable(f"{action_label(action)} {choice.problem}")
    await client.send_key(keycode(choice.code), seconds)
