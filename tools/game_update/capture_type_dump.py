"""Capture a type dump (``r<rev>_WizardDev.json``) from a running client, even after a patch breaks wiztype's scan.

The dumps are wiztype's (``tools/CLAUDE.md``, *Type dumps*). wiztype finds the game's type
registry from one call site, ``HASH_CALL_PATTERN``: a call to the registry's getter, whose
body loads the registry's global at +41. A patch that recompiles the caller loses the
match -- r811339 did, with the getter itself still there -- and wiztype stops with an
``IndexError`` before reading anything.

So this finds the global another way, statically, from the code that first creates it: the
getter's init allocates a 0x10 holder, builds a ``std::map`` head node of 0x30 into it
(left, parent and right pointing at itself, ``isnil`` and color 1) and stores the holder to
a RIP-relative global. A handful of registries are built that way, in the same order on
every build seen. On a reference exe where wiztype's own scan still works, that scan says
which of them is the type registry; the client's exe gives the same one by position. When
the client's own exe still matches wiztype's scan, that answer is used directly.

The global is then handed to wiztype in place of its own lookup, and wiztype's V1 dumper
writes the file, the layout every checked-in dump has: wiztype is unmodified, so its
reading of the tree is the same one every earlier dump came from.

USAGE
    Run under wiztype's environment, not the project's (wiztype pins an old pefile), with
    exactly one client open, logged in or not:

    uvx --from wiztype==0.1.13 python tools/game_update/capture_type_dump.py
    uvx --from wiztype==0.1.13 python tools/game_update/capture_type_dump.py --reference <a live exe> --out-dir <folder>

    It writes ``tools/r<rev>_WizardDev.json`` by default; ``bartleby-core/docs/GAME_UPDATES.md`` says when it
    moves to ``bartlebot/game/data/``.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.game_update.pe_image import LIVE_EXE, Image  # noqa: E402

EXE = "WizardGraphicalClient.exe"

#: wiztype 0.1.13's own scan (``wiztype/type_tree.py``), kept here to name the registry on a reference build.
HASH_CALL_PATTERN = rb"\xE8....\x48\xBD........\x48\x3B\x38\x74\x12"
#: Where, in the getter wiztype's call site calls, the ``mov rax, [rip+x]`` of the global sits.
GETTER_LOAD_AT = 41

#: ``mov ecx, 10h; call <alloc>``: the holder.
HOLDER_ALLOC = rb"\xB9\x10\x00\x00\x00\xE8"
#: ``mov ecx, 30h; call <alloc>``, then the head node pointing at itself, isnil = color = 1.
HEAD_NODE_INIT = rb"\xB9\x30\x00\x00\x00\xE8....\x48\x89\x00\x48\x89\x40\x08\x48\x89\x40\x10\x66\xC7\x40\x18\x01\x01"
#: ``mov [rip+x], reg``, the store of the holder to its global.
RIP_STORE = rb"[\x48\x4C]\x89[\x05\x0D\x15\x1D\x25\x2D\x35\x3D]"
HOLDER_WINDOW = 64
STORE_WINDOW = 160

#: The registry has about 7,000 types; a wrong global reads a handful or none.
MIN_TYPES = 1000


def wiztype_global(image: Image) -> int | None:
    """The registry's global as wiztype's own scan finds it, or None if the scan no longer matches."""
    sites = image.find(HASH_CALL_PATTERN)
    if not sites:
        return None
    getter = image.call_target(sites[0])
    return image.rip_target(getter + GETTER_LOAD_AT, 3, 7)


def registry_globals(image: Image) -> list[int]:
    """RVAs of every global holding a map built the way the type registry's is, in code order."""
    found = []
    for init in image.find(HEAD_NODE_INIT):
        if not re.search(HOLDER_ALLOC, image.data[init - HOLDER_WINDOW : init], re.DOTALL):
            continue
        before = image.data[init - STORE_WINDOW : init]
        stores = [m.start() for m in re.finditer(RIP_STORE, before, re.DOTALL)]
        if stores:
            found.append(image.rip_target(init - STORE_WINDOW + stores[-1], 3, 7))
    return found


def resolve_global(target: Image, reference: Image | None) -> int:
    """The type registry's global in `target`, by wiztype's scan if it still matches, else by position."""
    if (direct := wiztype_global(target)) is not None:
        return direct
    if reference is None:
        raise LookupError("wiztype's scan does not match this client and no reference exe was given")
    named = wiztype_global(reference)
    if named is None:
        raise LookupError(f"wiztype's scan matches neither this client nor the reference {reference.label}")
    ours, theirs = registry_globals(reference), registry_globals(target)
    if named not in ours:
        raise LookupError("the reference's registry global is not among its map inits; the init shape changed")
    if len(ours) != len(theirs):
        raise LookupError(f"the reference has {len(ours)} map inits of this shape and the client {len(theirs)}")
    return theirs[ours.index(named)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--reference", type=Path, default=LIVE_EXE, help="an exe wiztype's own scan still matches")
    ap.add_argument("--out-dir", type=Path, default=Path(__file__).resolve().parents[1], help="where to write it")
    args = ap.parse_args()

    import wiztype
    import wiztype.type_tree as type_tree
    from memobj import WindowsProcess
    from wiztype.memory import HashNode

    process = WindowsProcess.from_name(EXE)
    exe = Path(process.executable_path)
    revision = (exe.parent / "revision.dat").read_text(encoding="utf-8").strip()
    reference = Image.open(args.reference) if args.reference.exists() else None
    rva = resolve_global(Image.open(exe), reference)
    print(f"client {exe}\nrevision {revision}; type registry global at base+{rva:#x}")

    def root_node(proc):
        holder = proc.read_formatted(proc.get_module_named(EXE).lpBaseOfDll + rva, "Q")
        return HashNode(address=proc.read_formatted(holder, "Q"), process=proc)

    type_tree._get_root_node = root_node
    tree = wiztype.get_type_tree()
    if len(tree) < MIN_TYPES:
        print(f"read only {len(tree)} types: that global is not the type registry")
        return 1

    # A dump is named for its revision number alone: the Test realm's revision.dat reads
    # "r811339.WizardDev" but live's "r806919.Wizard_1_610", and everything that finds a
    # dump (art_extraction.TYPE_DUMP_PATTERN, .gitattributes, --self-check) expects
    # r<number>_WizardDev.json.
    out = args.out_dir / f"{revision.split('.')[0]}_WizardDev.json"
    # wiztype collects the nodes in a set, so its order differs run to run; sorted, two
    # captures of one build are the same file.
    wiztype.JsonTypeDumperV1(dict(sorted(tree.items()))).dump(out, indent=4)
    print(f"wrote {out}: {len(tree)} types")
    return 0


if __name__ == "__main__":
    sys.exit(main())
