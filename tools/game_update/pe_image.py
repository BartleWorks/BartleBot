"""A game client exe read from disk: its sections, the RVA of a file offset and back, and byte patterns.

Everything here is static. A pattern wizwalker scans the loaded module for matches the same
bytes in the file, because the code section is mapped as it is stored and x64 code addresses
its data RIP-relative, so nothing a pattern spans is rewritten by relocation. That is what
lets a patch on the Test realm be checked before any client of it has run.

Standard library only, so ``capture_type_dump.py`` can import it from wiztype's own
environment (``bartleby-core/docs/GAME_UPDATES.md``).
"""

from __future__ import annotations

import bisect
import re
import struct
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

LIVE_EXE = Path(r"C:\ProgramData\KingsIsle Entertainment\Wizard101\Bin\WizardGraphicalClient.exe")
TEST_EXE = Path(r"C:\ProgramData\KingsIsle Entertainment\Wizard101 Test\Bin\WizardGraphicalClient.exe")


@dataclass(frozen=True)
class Section:
    name: str
    rva: int
    virtual_size: int
    file_offset: int
    raw_size: int


class Image:
    """A PE file's bytes and section table."""

    def __init__(self, data: bytes, label: str = "") -> None:
        self.data = data
        self.label = label
        if data[:2] != b"MZ":
            raise ValueError(f"{label or 'image'}: not an MZ executable")
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe : pe + 4] != b"PE\0\0":
            raise ValueError(f"{label or 'image'}: no PE header")
        count, optional_size = struct.unpack_from("<H", data, pe + 6)[0], struct.unpack_from("<H", data, pe + 20)[0]
        table = pe + 24 + optional_size
        self.sections: list[Section] = []
        for i in range(count):
            raw = data[table + 40 * i : table + 40 * (i + 1)]
            name = raw[:8].rstrip(b"\0").decode("ascii", errors="replace")
            vsize, rva, rsize, roff = struct.unpack_from("<IIII", raw, 8)
            self.sections.append(Section(name, rva, vsize, roff, rsize))
        self.text = next((s for s in self.sections if s.name == ".text"), None)
        if self.text is None:
            raise ValueError(f"{label or 'image'}: no .text section")

    @classmethod
    def open(cls, path: Path) -> Image:
        return cls(Path(path).read_bytes(), label=str(path))

    def rva(self, offset: int) -> int:
        for s in self.sections:
            if s.file_offset <= offset < s.file_offset + s.raw_size:
                return s.rva + offset - s.file_offset
        raise ValueError(f"file offset {offset:#x} is in no section")

    def offset(self, rva: int) -> int:
        for s in self.sections:
            if s.rva <= rva < s.rva + max(s.virtual_size, s.raw_size):
                return s.file_offset + rva - s.rva
        raise ValueError(f"rva {rva:#x} is in no section")

    def find(self, pattern: bytes) -> list[int]:
        """File offsets of every match in .text, scanned as wizwalker scans (``re.DOTALL``)."""
        start = self.text.file_offset
        code = self.data[start : start + self.text.raw_size]
        return [start + m.start() for m in re.finditer(pattern, code, re.DOTALL)]

    def i32(self, offset: int) -> int:
        return struct.unpack_from("<i", self.data, offset)[0]

    def call_target(self, offset: int) -> int:
        """File offset a rel32 ``call``/``jmp`` (E8/E9) at `offset` lands on."""
        return self.offset(self.rva(offset) + 5 + self.i32(offset + 1))

    def rip_target(self, offset: int, displacement_at: int, length: int) -> int:
        """RVA a RIP-relative operand names: the instruction at `offset`, its disp32 at `displacement_at`."""
        return self.rva(offset) + length + self.i32(offset + displacement_at)

    def section(self, name: str) -> Section | None:
        return next((s for s in self.sections if s.name == name), None)

    @cached_property
    def functions(self) -> list[tuple[int, int]]:
        """(begin, end) RVAs of every function, sorted: the x64 unwind table in ``.pdata`` lists them all.

        A chained entry (a function's cold part, split off) is listed as its own range, so a
        function can be two entries; neither overlaps another.
        """
        pdata = self.section(".pdata")
        if pdata is None:
            return []
        entries = set()
        for at in range(pdata.file_offset, pdata.file_offset + min(pdata.virtual_size, pdata.raw_size) - 11, 12):
            begin, end, _unwind = struct.unpack_from("<III", self.data, at)
            if begin and end > begin:
                entries.add((begin, end))
        return sorted(entries)

    def function_at(self, rva: int) -> tuple[int, int] | None:
        """The (begin, end) of the function holding `rva`, or None outside every function."""
        i = bisect.bisect_right(self.functions, (rva, 1 << 63)) - 1
        if i >= 0 and self.functions[i][0] <= rva < self.functions[i][1]:
            return self.functions[i]
        return None

    def strings(self, text: str) -> list[int]:
        """RVAs in ``.rdata`` where the NUL-terminated `text` starts, as ASCII or UTF-16LE."""
        rdata = self.section(".rdata")
        if rdata is None:
            return []
        blob = self.data[rdata.file_offset : rdata.file_offset + rdata.raw_size]
        found = []
        for needle, width in ((text.encode("ascii") + b"\0", 1), (text.encode("utf-16-le") + b"\0\0", 2)):
            for m in re.finditer(re.escape(needle), blob):
                if m.start() < width or blob[m.start() - width : m.start()] == b"\0" * width:
                    found.append(rdata.rva + m.start())
        return sorted(found)

    def referrers(self, target_rva: int) -> list[int]:
        """RVAs of every ``lea``/``mov reg, [rip+x]`` (REX.W, seven bytes) whose operand is `target_rva`."""
        return [
            self.rva(o)
            for o in self.find(rb"[\x48\x4C][\x8B\x8D][\x05\x0D\x15\x1D\x25\x2D\x35\x3D]")
            if self.rip_target(o, 3, 7) == target_rva
        ]

    def callers(self, target_rva: int) -> list[int]:
        """RVAs of every rel32 ``call`` (E8) in .text that lands on `target_rva`."""
        return [self.rva(o) for o in self.find(rb"\xE8") if self.rva(o) + 5 + self.i32(o + 1) == target_rva]


def hexs(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)
