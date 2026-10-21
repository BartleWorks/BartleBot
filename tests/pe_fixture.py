"""A made-up client exe for the game update tools' tests: a real PE layout, code written here.

``tools/game_update`` reads ``WizardGraphicalClient.exe`` off disk. Its tests use this
instead of the game's own file, which is never committed (the root ``CLAUDE.md``, *No game
assets, either*): an MZ/PE header, a section table, a ``.text`` holding whatever code a test
assembles with :class:`Code`, and a ``.data`` for the globals that code points at.
"""

from __future__ import annotations

import struct

TEXT_RVA = 0x1000
DATA_RVA = 0x200000
_TEXT_OFFSET = 0x400
_PE_AT = 0x40


class Code:
    """Machine code assembled at known RVAs, so a call or RIP-relative operand can be written."""

    def __init__(self) -> None:
        self.buf = bytearray()

    @property
    def rva(self) -> int:
        return TEXT_RVA + len(self.buf)

    def raw(self, data: bytes) -> int:
        """Append `data`; returns the RVA it starts at."""
        at = self.rva
        self.buf += data
        return at

    def call(self, target_rva: int) -> int:
        at = self.rva
        self.buf += b"\xe8" + struct.pack("<i", target_rva - (at + 5))
        return at

    def rip(self, opcode: bytes, target_rva: int) -> int:
        """A seven-byte RIP-relative instruction, ``opcode`` (three bytes) then disp32."""
        at = self.rva
        self.buf += opcode + struct.pack("<i", target_rva - (at + 7))
        return at

    def pad(self, n: int, byte: bytes = b"\xcc") -> None:
        self.buf += byte * n


def build_pe(code: bytes, data_size: int = 0x1000) -> bytes:
    """An exe whose .text is `code` at TEXT_RVA and whose .data is zeros at DATA_RVA."""
    text_size = (len(code) + 0x1FF) & ~0x1FF
    data_offset = _TEXT_OFFSET + text_size
    coff = struct.pack("<HHIIIHH", 0x8664, 2, 0, 0, 0, 0, 0x22)
    sections = b"".join(
        name.ljust(8, b"\0") + struct.pack("<IIIIIIHHI", vsize, rva, rsize, roff, 0, 0, 0, 0, 0)
        for name, vsize, rva, rsize, roff in (
            (b".text", len(code), TEXT_RVA, text_size, _TEXT_OFFSET),
            (b".data", data_size, DATA_RVA, data_size, data_offset),
        )
    )
    header = bytearray(_TEXT_OFFSET)
    header[:2] = b"MZ"
    header[0x3C:0x40] = struct.pack("<I", _PE_AT)
    pe = b"PE\0\0" + coff + sections
    header[_PE_AT : _PE_AT + len(pe)] = pe
    return bytes(header) + code.ljust(text_size, b"\xcc") + bytes(data_size)
