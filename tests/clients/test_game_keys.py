"""Reading a client's key bindings from its memory, over a made-up process laid out as r811339's."""

from __future__ import annotations

import re
import struct

import pytest
from wizwalker.errors import MemoryReadError

from bartlebot.clients import game_keys
from bartlebot.game.key_bindings import GameAction, KeyMap

BASE = 0x2000_0000
VTABLE = 0x7FF7_1C47_8B30


class FakeMemory:
    """One region of a process: objects placed in it, read and scanned as the game's would be.

    Class names, which the real reader finds through an object's vtable, are a table here.
    """

    def __init__(self) -> None:
        self.data = bytearray(0x20000)
        self.next = 0x100
        self.names: dict[int, str] = {}
        self.scans = 0

    def alloc(self, size: int) -> int:
        address = BASE + self.next
        self.next += (size + 0xF) & ~0xF
        return address

    def put(self, address: int, raw: bytes) -> None:
        self.data[address - BASE : address - BASE + len(raw)] = raw

    def put_u64(self, address: int, value: int) -> None:
        self.put(address, struct.pack("<Q", value))

    def put_string(self, address: int, text: str) -> None:
        """An MSVC std::string: inline up to 15 bytes, else on the heap."""
        raw = text.encode("latin-1")
        if len(raw) < 16:
            self.put(address, raw.ljust(16, b"\0") + struct.pack("<QQ", len(raw), 15))
        else:
            heap = self.alloc(len(raw) + 1)
            self.put(heap, raw + b"\0")
            self.put(address, struct.pack("<Q", heap).ljust(16, b"\0") + struct.pack("<QQ", len(raw), len(raw)))

    def binding(self, action: str, key: str, slot: int = 0) -> int:
        address = self.alloc(0x98)
        self.put_u64(address, VTABLE)
        self.put_string(address + 0x48, action)
        self.put_string(address + 0x68, "keyboard.down." + key)
        self.put(address + 0x94, struct.pack("<i", slot))
        self.names[address] = "InputBinding"
        return address

    def binding_list(self, bindings: list[int]) -> int:
        """An InputBindingList: its ring of nodes, a sentinel whose value is garbage, and the list."""
        sentinel = self.alloc(0x18)
        nodes = [self.alloc(0x18) for _ in bindings]
        ring = [sentinel, *nodes]
        for i, node in enumerate(ring):
            self.put_u64(node, ring[(i + 1) % len(ring)])
            self.put_u64(node + 8, ring[i - 1])
        self.put_u64(sentinel + 0x10, 0x6F6F_6F6F_6F6F_6F6F)  # unreadable, as the game's is
        for node, binding in zip(nodes, bindings):
            self.put_u64(node + 0x10, binding)
        address = self.alloc(0x58)
        self.put_u64(address + 0x48, sentinel)
        self.put(address + 0x50, struct.pack("<i", len(bindings)))
        self.names[address] = "InputBindingList"
        return address

    # What game_keys asks of a client's hook_handler:

    async def read_bytes(self, address: int, size: int) -> bytes:
        if not BASE <= address <= BASE + len(self.data) - size:
            raise MemoryReadError(address)
        return bytes(self.data[address - BASE : address - BASE + size])

    async def pattern_scan(self, pattern: bytes, *, module=None, return_multiple=False):
        self.scans += 1
        return [BASE + m.start() for m in re.finditer(pattern, bytes(self.data), re.DOTALL)]


class MemoryClient:
    _next = 5000

    def __init__(self, memory: FakeMemory) -> None:
        MemoryClient._next += 1
        self.window_handle = MemoryClient._next
        self.hook_handler = memory


@pytest.fixture(autouse=True)
def _names(monkeypatch):
    """Class names from the fake's table."""

    async def type_name(reader, address):
        return reader.names.get(address, "") if isinstance(reader, FakeMemory) else ""

    monkeypatch.setattr(game_keys, "type_name", type_name)


def _player_set(memory: FakeMemory, interact: str = "x") -> tuple[int, list[int]]:
    bindings = [
        memory.binding("NPCInteract", interact),
        memory.binding("ToggleInventory", "b"),
        memory.binding("MoveLeft", "a"),
        memory.binding("MoveLeft", "left", slot=1),
        memory.binding("FindNearestActiveQuest", "ctrl.shift.q"),  # 26 characters: on the heap
    ]
    return memory.binding_list(bindings), bindings


async def test_the_list_is_found_and_read_in_order_with_its_slots():
    memory = FakeMemory()
    address, _ = _player_set(memory)
    assert await game_keys.find_binding_list(memory) == address
    bindings = await game_keys.read_binding_list(memory, address)
    assert [(b.action, b.input, b.slot) for b in bindings] == [
        ("NPCInteract", "keyboard.down.x", 0),
        ("ToggleInventory", "keyboard.down.b", 0),
        ("MoveLeft", "keyboard.down.a", 0),
        ("MoveLeft", "keyboard.down.left", 1),
        ("FindNearestActiveQuest", "keyboard.down.ctrl.shift.q", 0),
    ]


async def test_the_whole_set_is_chosen_over_a_list_the_bindings_page_left_behind():
    # The game's key bindings page keeps a one-binding list per row, which can still hold a
    # key the player has since changed: here Interact on K, where the player's set says X.
    memory = FakeMemory()
    memory.binding_list([memory.binding("NPCInteract", "k")])
    address, _ = _player_set(memory)
    assert await game_keys.find_binding_list(memory) == address


async def test_only_the_bindings_page_s_lists_is_no_list_at_all():
    memory = FakeMemory()
    memory.binding_list([memory.binding("NPCInteract", "k")])
    assert await game_keys.find_binding_list(memory) is None


async def test_a_list_is_found_once_and_then_read_through_its_address():
    memory = FakeMemory()
    _player_set(memory)
    client = MemoryClient(memory)
    keys = await game_keys.key_map(client)
    assert keys.source == "memory"
    assert keys.choose(GameAction.INTERACT).code == ord("X")
    scans = memory.scans
    await game_keys.key_map(client)
    assert memory.scans == scans


async def test_a_rebind_saved_in_game_is_read_without_another_scan():
    memory = FakeMemory()
    _, bindings = _player_set(memory)
    client = MemoryClient(memory)
    await game_keys.key_map(client)
    scans = memory.scans
    memory.put_string(bindings[0] + 0x68, "keyboard.down.k")  # the game edits the list in place
    keys = await game_keys.key_map(client)
    assert keys.choose(GameAction.INTERACT).code == ord("K")
    assert memory.scans == scans


async def test_an_address_that_no_longer_holds_the_list_is_found_again():
    memory = FakeMemory()
    old, _ = _player_set(memory)
    client = MemoryClient(memory)
    await game_keys.key_map(client)
    del memory.names[old]
    memory.put_u64(old + 0x48, 0)
    new, _ = _player_set(memory, interact="j")
    keys = await game_keys.key_map(client)
    assert keys.source == "memory"
    assert keys.choose(GameAction.INTERACT).code == ord("J")
    assert new != old


async def test_unreadable_memory_gives_the_defaults_and_one_warning_until_a_refresh(caplog):
    memory = FakeMemory()  # nothing in it
    client = MemoryClient(memory)
    keys = await game_keys.key_map(client)
    assert keys.source == "defaults"
    assert keys.choose(GameAction.INTERACT).code == ord("X")
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    scans = memory.scans
    await game_keys.key_map(client)
    assert memory.scans == scans  # no four-second scan per press
    _player_set(memory, interact="k")
    keys = await game_keys.key_map(client, refresh=True)
    assert keys.source == "memory"
    assert keys.choose(GameAction.INTERACT).code == ord("K")


async def test_a_client_with_no_memory_reader_gets_the_defaults():
    class NoMemory:
        window_handle = 4242

    keys = await game_keys.key_map(NoMemory())
    assert keys.source == "defaults"


async def test_a_fixed_map_answers_without_reading_memory():
    memory = FakeMemory()
    client = MemoryClient(memory)
    game_keys.use_map(client, KeyMap.defaults())
    assert (await game_keys.key_map(client)).source == "defaults"
    assert memory.scans == 0
