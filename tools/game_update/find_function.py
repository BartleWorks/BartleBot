"""Find the function a broken pattern matched on the old build, and the same function on the new one.

The first step of replacing a pattern (``docs/GAME_UPDATES.md``, *Replacing a broken
pattern*). A patch that recompiles a function leaves little of its bytes, but most of what
it refers to survives: the strings it names, chiefly. So:

    1. ``--pattern``: where the old pattern matches on the old exe, the function holding it
       (its bounds from ``.pdata``), its callers, and every string it names.
    2. ``--string``: every function on both exes that names that string. On the new exe
       that is the function to write the new pattern for.

Either prints each function's opening instructions, so the two builds' can be compared.
That needs capstone, which is not a project dependency; run with ``uv run --with
capstone``. Without it the bytes are printed in hex.

USAGE
    uv run --with capstone python tools/game_update/find_function.py --pattern 'rb"\\x48\\x89\\x5C\\x24."'
    uv run --with capstone python tools/game_update/find_function.py --string "AutobotManager::Initialize"
    ... --old <exe> --new <exe> --show 24
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.game_update.pe_image import LIVE_EXE, TEST_EXE, Image, hexs  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

STRING_MIN = 4


def _disassembler():
    try:
        from capstone import CS_ARCH_X86, CS_MODE_64, Cs
    except ImportError:
        return None
    cs = Cs(CS_ARCH_X86, CS_MODE_64)
    cs.detail = True
    return cs


def read_string(image: Image, rva: int) -> str | None:
    """The ASCII or UTF-16LE string at `rva` in .rdata, if one of at least STRING_MIN characters starts there."""
    rdata = image.section(".rdata")
    if rdata is None or not rdata.rva <= rva < rdata.rva + rdata.raw_size:
        return None
    raw = image.data[image.offset(rva) : image.offset(rva) + 256]
    ascii_ = raw.split(b"\0", 1)[0]
    if len(ascii_) >= STRING_MIN and all(32 <= c < 127 for c in ascii_):
        return ascii_.decode("ascii")
    wide = raw.decode("utf-16-le", errors="replace").split("\0", 1)[0]
    if len(wide) >= STRING_MIN and wide.isascii() and wide.isprintable():
        return wide
    return None


def named_strings(image: Image, begin: int, end: int, cs) -> list[tuple[int, str]]:
    """(instruction RVA, text) for each string the function names through a RIP-relative operand."""
    if cs is None:
        return []
    from capstone.x86 import X86_OP_MEM, X86_REG_RIP

    found = []
    code = image.data[image.offset(begin) : image.offset(begin) + (end - begin)]
    for ins in cs.disasm(code, begin):
        for op in ins.operands:
            if op.type == X86_OP_MEM and op.mem.base == X86_REG_RIP:
                text = read_string(image, ins.address + ins.size + op.mem.disp)
                if text:
                    found.append((ins.address, text))
    return found


def describe(image: Image, label: str, begin: int, end: int, cs, show: int, mark: int | None = None) -> None:
    callers = image.callers(begin)
    print(f"\n[{label}] function {begin:#x}..{end:#x}, {end - begin} bytes")
    print(f"  direct callers: {', '.join(hex(c) for c in callers) or 'none'}")
    code = image.data[image.offset(begin) : image.offset(begin) + (end - begin)]
    if cs is None:
        print(f"  first bytes: {hexs(code[:64])}")
        return
    for i, ins in enumerate(cs.disasm(code, begin)):
        if i >= show:
            break
        flag = "  <- match" if ins.address == mark else ""
        print(f"  {ins.address:#x}  {ins.bytes.hex(' '):<34} {ins.mnemonic} {ins.op_str}{flag}")
    strings = named_strings(image, begin, end, cs)
    print(f"  strings it names ({len(strings)}):")
    for at, text in strings[:40]:
        print(f"    {at:#x}  {text!r}")


def by_pattern(image: Image, label: str, pattern: bytes, cs, show: int) -> None:
    hits = [image.rva(o) for o in image.find(pattern)]
    print(f"[{label}] {len(hits)} match(es)")
    for hit in hits[:8]:
        fn = image.function_at(hit)
        if fn is None:
            print(f"  {hit:#x}: in no function .pdata lists")
            continue
        print(f"  {hit:#x}: +{hit - fn[0]:#x} into the function")
        describe(image, label, *fn, cs, show, mark=hit)


def by_string(image: Image, label: str, text: str, cs, show: int) -> None:
    at = image.strings(text)
    print(f"\n[{label}] {text!r} at {', '.join(hex(a) for a in at) or 'nowhere'}")
    functions = sorted({image.function_at(r) for a in at for r in image.referrers(a)} - {None})
    for fn in functions:
        describe(image, label, *fn, cs, show)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    what = ap.add_mutually_exclusive_group(required=True)
    what.add_argument("--pattern", help="a Python bytes literal, as wizwalker writes it: matched on the old exe only")
    what.add_argument("--string", help="a string the function names: looked up on both exes")
    ap.add_argument("--old", type=Path, default=LIVE_EXE)
    ap.add_argument("--new", type=Path, default=TEST_EXE)
    ap.add_argument("--show", type=int, default=20, help="instructions to print per function")
    args = ap.parse_args()

    cs = _disassembler()
    if cs is None:
        print("(capstone is not installed: printing bytes, and no strings; run with `uv run --with capstone`)")
    old = Image.open(args.old)
    if args.pattern:
        pattern = ast.literal_eval(args.pattern)
        if not isinstance(pattern, bytes):
            print("--pattern takes a bytes literal, e.g. 'rb\"\\x48\\x8B\\x05....\"'")
            return 2
        by_pattern(old, "old", pattern, cs, args.show)
    else:
        by_string(old, "old", args.string, cs, args.show)
        by_string(Image.open(args.new), "new", args.string, cs, args.show)
    return 0


if __name__ == "__main__":
    sys.exit(main())
