"""Diagnostic: find the deck window control that sets the archmastery school.

WHY THIS EXISTS
    ``deck_io.read_archmastery_school`` reads the school straight out of memory
    (``ClientDeckBehavior.archmastery_school``), but a deck *apply* cannot write
    it back: deck configuration is server-authoritative, so poking
    ``m_archmasterySchool`` would not persist.  The school has to be set the way
    a player sets it, by clicking the selector on the deck page — and nothing in
    the codebase knows what that selector is called.

    Both of the offline sources CLAUDE.md points at come up empty here, which is
    why this has to run against a live client:

      * ``tools/typedump.py`` covers reflected PropertyClass properties.
        ``m_archmasterySchool`` is in there (DeckBehavior +0xB8) but UI *window
        names* are not — the dump has no window-layout information at all.
      * ``Root.wad`` and the other archives hold game data, not window layout.
        Searching every UI-ish XML across Root, GUI-WorldData and
        _Shared-WorldData for the window names bartlebot already uses
        ("DeckConfiguration", "CardsInDeck", "ItemSpells") returns zero hits:
        the layout is compiled into the client binary.  Don't re-run that search.

WHAT IS ALREADY KNOWN
    Sections A-C have been run.  What they established, so nobody repeats it:

      * The selector is ``['WorldView', 'DeckConfiguration',
        'DeckConfigurationWindow', 'ControlSprite', 'DeckPage',
        'SchoolPipPanel', 'Guage']``.  Clicking it opens a small menu with one
        icon per school; clicking an icon sets the school and closes the menu.
      * **The subtree diff finds nothing.**  Changing the school Death -> Life
        moved no text, no visibility and no rect anywhere under
        DeckConfiguration — the pip is drawn as a texture, and the pop-up menu
        does not announce itself in the window tree either.  So section C
        cannot name the menu's entries, and a click-driven implementation would
        have to click blind and poll ``archmastery_school()`` to confirm.
      * **The memory route is dead.**  Section D was run: writing
        ``m_archmasterySchool`` reads back changed and the client even repaints
        the pip once something else forces the deck page to redraw — but it does
        not survive a relog and never reaches combat.  The client believes it
        and the server never hears.  That is outcome 2 below, the one that would
        demo perfectly and do nothing on a real run.
      * So the click path is what shipped: see ``set_archmastery_school`` in
        ``bartlebot/deck_io.py``.  Section D is kept because a future build could
        change the answer, and because re-deriving why it was rejected is
        expensive.  Note that *reading* the value stays trustworthy — a school
        changed through the UI reads back correctly, which is what makes it a
        usable confirmation oracle for the click path.

WHAT IT DOES
    A. Reports the school the behavior currently holds, so there is a known
       starting value to compare against.

    B. Dumps the whole DeckConfiguration subtree — name, type, visibility,
       screen rect, and text — and flags every window whose name or text looks
       school-related.

    C. Change the archmastery school **in the game** and press Enter: the tree
       is re-read and only what *changed* is reported.  Kept because it is the
       evidence for the "nothing changes" finding above, and because a future
       game build may start announcing the menu.

    D. ``--write`` only.  Writes ``m_archmasterySchool`` directly and then asks
       the questions that separate "the write landed in a local copy" from "the
       school actually changed".  Those are very different results and only the
       second one is worth building on — see WHAT D IS ACTUALLY ASKING below.

WHAT D IS ACTUALLY ASKING
    A memory write is only useful if the *game* agrees the school changed, not
    just our read of it.  Three outcomes, in increasing order of usefulness:

      1. The value reads back changed, the pip icon does not move, and it
         reverts on relog.  The write hit a local copy; useless.
      2. Value and pip both change, but it reverts on relog.  The client
         believes it, the server never heard.  Also useless, and *worse* than
         useless if we shipped it — it would demo perfectly and do nothing.
      3. It survives a relog.  The client sent it on.  Then a write is a real
         implementation and the click path can be dropped.

    Outcome 2 is the one this section exists to catch.  Do not skip the relog.

HOW TO RUN
    1. Log a wizard in.  Any wizard with an archmastery school set will do;
       archmastery is not tied to a world or a level bracket.
    2. Open the deck page (press P) and leave it open.
    3. poetry run python tools/probe_archmastery_ui.py            # read-only
       poetry run python tools/probe_archmastery_ui.py --write    # adds D
    4. When section C asks, switch the school in-game, then press Enter.

    Without ``--write`` the probe never clicks and never writes, and the one
    change to your deck configuration is the one you make yourself in step 4.
    With ``--write`` it modifies ``m_archmasterySchool`` and writes the original
    value back at the end; if it cannot, it says so and names the value to
    restore by hand.

OUTPUT
    A summary goes to the terminal.  The full tree, which includes window text
    and so may contain your wizard's name, is written to
    ``probe_archmastery_report.txt`` — gitignored, like every other probe report.
    Read it locally; do not paste it into an issue without checking it first.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

from wizwalker.extensions.scripting.utils import _maybe_get_named_window
from wizwalker.extensions.wizsprinter import WizSprinter
from wizwalker.memory.memory_object import Primitive
from wizwalker.memory.memory_objects.conditionals import school_id_to_names, school_to_str

REPORT_PATH = "probe_archmastery_report.txt"

#: m_archmasterySchool on DeckBehavior. Documented on wizwalker's
#: ClientDeckBehavior, which reads it but has no writer -- hence the raw offset.
ARCHMASTERY_SCHOOL_OFFSET = 0xB8

#: The seven schools a wizard can point an archmastery pip at. The wider table
#: also holds Sun/Moon/Star/Gardening/Fishing/Cantrips/CastleMagic, which are
#: not schools in this sense, and writing one is not a test of anything.
PIP_SCHOOLS = ("Fire", "Ice", "Storm", "Myth", "Life", "Death", "Balance")

#: Anything whose name or text contains one of these is worth a second look.
#: School names come from wizwalker's own table so this cannot drift from what
#: ``read_archmastery_school`` will report.
_HINTS = tuple(s.lower() for s in school_to_str.values()) + ("mastery", "school", "pip")

#: Depth guard.  The deck page is nested but not unboundedly so; a runaway walk
#: here means something is wrong with the tree, not that the page is deep.
_MAX_DEPTH = 24


@dataclass(frozen=True)
class Node:
    """One window in the dumped subtree, keyed by its path from the root."""

    path: tuple[str, ...]
    type_name: str
    text: str
    visible: bool
    rect: tuple

    @property
    def key(self) -> str:
        return "/".join(self.path)

    def state(self) -> tuple:
        """The parts a school change could plausibly move."""
        return (self.text, self.visible, self.rect)

    def looks_relevant(self) -> bool:
        haystack = f"{self.key} {self.text}".lower()
        return any(hint in haystack for hint in _HINTS)


async def _safe(coro, default=None):
    try:
        return await coro
    except Exception:
        return default


async def read_school(client) -> str | None:
    """The behavior's current archmastery school, by name."""
    behavior = await _safe(client.client_object.try_get_deck_behavior())
    if behavior is None:
        return None
    school_id = await _safe(behavior.archmastery_school())
    return school_to_str.get(school_id) if school_id is not None else None


async def walk(window, path: tuple[str, ...] = (), depth: int = 0) -> list[Node]:
    """Every window under (and including) ``window``, depth-first.

    Text is read for every node, which is the expensive part — see the UI Tree
    Browser's note on ``maybe_text`` being one synchronous ReadProcessMemory
    each.  That cost is fine here: this walks one page, once, in a script.
    """
    name = await _safe(window.name(), "") or ""
    here = (*path, name or "(unnamed)")
    node = Node(
        path=here,
        type_name=await _safe(window.maybe_read_type_name(), "?") or "?",
        text=await _safe(window.maybe_text(), "") or "",
        visible=bool(await _safe(window.is_visible(), False)),
        # window_rectangle, not scale_to_client: the latter walks every parent
        # and reads the UI scale per node, and a raw rect compares just as well.
        rect=tuple(await _safe(window.window_rectangle(), ()) or ()),
    )
    out = [node]
    if depth >= _MAX_DEPTH:
        return out
    for child in await _safe(window.children(), []) or []:
        out.extend(await walk(child, here, depth + 1))
    return out


def _format(node: Node) -> str:
    vis = "vis" if node.visible else "   "
    text = node.text.replace("\n", " ")[:60]
    return f"  {vis}  {node.type_name:<28} {node.key}" + (f"   text={text!r}" if text else "")


async def section_a(client) -> str | None:
    print("A. WHAT THE BEHAVIOR SAYS NOW")
    print()
    school = await read_school(client)
    if school is None:
        print("   archmastery_school() is unreadable or unset on this client.")
        print("   Section C still works — it compares the UI against itself — but")
        print("   there will be nothing to cross-check the answer against.")
    else:
        print(f"   archmastery_school(): {school}")
    print()
    return school


async def section_b(deck_window) -> list[Node]:
    print("B. THE DECK PAGE SUBTREE")
    print()
    nodes = await walk(deck_window)
    print(f"   {len(nodes)} windows under DeckConfiguration.")
    flagged = [n for n in nodes if n.looks_relevant()]
    if flagged:
        print(f"   {len(flagged)} name or text looks school-related:")
        print()
        for node in flagged:
            print(_format(node))
    else:
        print("   Nothing matched a school name, 'mastery', 'school' or 'pip'.")
        print("   That is not a failure — the control may be named generically.")
        print("   Section C does not depend on names at all.")
    print()
    print(f"   Full tree written to {REPORT_PATH}.")
    print()
    return nodes


async def section_c(deck_window, client, before: list[Node], school_before: str | None) -> None:
    print("C. WHAT MOVES WHEN THE SCHOOL DOES")
    print()
    print("   Switch the archmastery school in the game now, then press Enter.")
    print("   Leave the deck page open while you do it.")
    print()
    await asyncio.get_running_loop().run_in_executor(None, input, "   [Enter when done] ")
    print()

    school_after = await read_school(client)
    if school_before is not None and school_after == school_before:
        print(f"   !! The behavior still reads {school_after}.  Either the school did")
        print("      not change, or it has not been written back yet.  The diff below")
        print("      is still printed, but treat it with suspicion.")
        print()
    elif school_after is not None:
        print(f"   archmastery_school(): {school_before} -> {school_after}")
        print()

    after = await walk(deck_window)
    old = {n.key: n for n in before}
    new = {n.key: n for n in after}

    appeared = [k for k in new if k not in old]
    vanished = [k for k in old if k not in new]
    changed = [k for k in new.keys() & old.keys() if new[k].state() != old[k].state()]

    if not (appeared or vanished or changed):
        print("   Nothing in the subtree changed.  This is the expected result on")
        print("   the build this was written against: the school pip is drawn as a")
        print("   texture, so nothing the window tree exposes moves with it, and the")
        print("   selector menu does not appear in the tree either.")
        print()
        print("   The selector itself is known — SchoolPipPanel/Guage under DeckPage —")
        print("   so this is not a dead end, only a dead end for *naming the menu's")
        print("   entries*. See section D, and WHAT IS ALREADY KNOWN at the top.")
        return

    for label, keys in (("CHANGED", changed), ("APPEARED", appeared), ("VANISHED", vanished)):
        if not keys:
            continue
        print(f"   {label} ({len(keys)}):")
        for key in sorted(keys):
            node = new.get(key) or old[key]
            print(_format(node))
            if key in changed:
                was, now = old[key], new[key]
                if was.text != now.text:
                    print(f"          text: {was.text!r} -> {now.text!r}")
                if was.visible != now.visible:
                    print(f"          visible: {was.visible} -> {now.visible}")
                if was.rect != now.rect:
                    print(f"          rect: {was.rect} -> {now.rect}")
        print()

    print("   The window to click is the one that changed *and* sits in a branch a")
    print("   path can reach unambiguously.  If several siblings look identical, see")
    print("   CLAUDE.md's 'Ambiguous UI paths' — resolve_path_match with match_text.")


async def _ask(prompt: str) -> str:
    """Blocking input() without stalling the loop wizwalker's reads run on."""
    return (await asyncio.get_running_loop().run_in_executor(None, input, prompt)).strip().lower()


async def _write_school(client, school_id: int) -> bool:
    """Write m_archmasterySchool. True if the write itself did not raise.

    The behavior is re-fetched rather than cached: it hangs off the equipped
    deck item, and holding an address across a UI interaction is how a probe
    ends up writing into freed memory.
    """
    behavior = await _safe(client.client_object.try_get_deck_behavior())
    if behavior is None:
        return False
    try:
        await behavior.write_value_to_offset(ARCHMASTERY_SCHOOL_OFFSET, school_id, Primitive.uint32)
    except Exception as exc:
        print(f"   write failed: {exc!r}")
        return False
    return True


async def section_d(client) -> None:
    print("D. CAN THE SCHOOL SIMPLY BE WRITTEN?")
    print()
    original = await read_school(client)
    if original is None:
        print("   archmastery_school() is unreadable, so there is nothing to write")
        print("   back afterwards. Refusing to write blind. Set a school in game")
        print("   first, then re-run.")
        return

    target = next(s for s in PIP_SCHOOLS if s != original)
    original_id, target_id = school_id_to_names[original], school_id_to_names[target]
    print(f"   Current: {original} ({original_id}).  Writing: {target} ({target_id}).")
    print()
    if await _ask("   This modifies your deck configuration. Continue? [y/N] ") != "y":
        print("   Skipped.")
        return
    print()

    if not await _write_school(client, target_id):
        print("   Could not write. Nothing to restore.")
        return

    # 1. Did it land at all?
    readback = await read_school(client)
    print(f"   1. Read back: {readback}")
    if readback != target:
        print("      The write did not take. Stop here — the click path is the only")
        print("      route, and there is nothing to restore.")
        return
    print()

    # 2. Does the client agree, or is this a copy nothing renders from?
    print("   2. Look at the school pip icon on the deck page (SchoolPipPanel).")
    ui_agrees = await _ask(f"      Does it show {target}? [y/N] ") == "y"
    print()

    # 3. The one that matters.
    print("   3. Now close the deck page, then log the character out and back in")
    print("      (character select is enough; a full relog is safer).")
    print("      Reopen the deck page when you are back.")
    await _ask("      [Enter when you are logged back in] ")
    survived = await read_school(client) == target
    print(f"      After relog: {await read_school(client)}")
    print()

    print("   VERDICT")
    if survived and ui_agrees:
        print("      The write survived a relog and the client rendered it. The server")
        print("      took it. A memory write is a real implementation — drop the click")
        print("      path, and correct the 'server-authoritative' claim in this file,")
        print("      in deck_io, and in #134.")
    elif ui_agrees:
        print("      The client believed it and the server did not. This is the trap:")
        print("      it would demo perfectly and do nothing on a real run. Use the")
        print("      click path.")
    else:
        print("      The write hit a copy the game does not render from. Use the click")
        print("      path.")
    print()

    if not survived:
        print(f"   Restoring {original}...")
        if await _write_school(client, original_id) and await read_school(client) == original:
            print("   Restored.")
        else:
            print(f"   !! Could not restore. Set the school back to {original} by hand.")
    else:
        print(f"   The write stuck, so it is yours to undo: set the school back to {original}")
        print("   in game if you want it back.")


def write_report(nodes: list[Node], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write("Full DeckConfiguration subtree.\n")
        f.write("May contain your wizard's name in window text — check before sharing.\n\n")
        for node in nodes:
            f.write(_format(node) + "\n")


async def probe(client, *, allow_write: bool) -> None:
    try:
        deck_window = await _maybe_get_named_window(client.root_window, "DeckConfiguration", retries=0)
    except ValueError:
        print("   DeckConfiguration is not in the UI tree — open the deck page (P) first.")
        return

    school_before = await section_a(client)
    nodes = await section_b(deck_window)
    write_report(nodes, REPORT_PATH)
    await section_c(deck_window, client, nodes, school_before)
    if allow_write:
        print()
        await section_d(client)
    else:
        print()
        print("   (Re-run with --write to test whether the school can just be written.)")


async def main() -> int:
    allow_write = "--write" in sys.argv[1:]
    sprinter = WizSprinter()
    clients = sprinter.get_new_clients()
    if not clients:
        print("No Wizard101 clients found. Start the game and log a wizard in.")
        return 1

    # One client only: section C waits on a human, and asking someone to change
    # the school on four wizards in sequence is a worse probe, not a better one.
    client = clients[0]
    if len(clients) > 1:
        print(f"Found {len(clients)} clients; probing the first one only.")

    print("Activating hooks...")
    try:
        await client.activate_hooks()
    except Exception as exc:
        print(f"Hook activation failed: {exc!r}")
        return 1

    try:
        print()
        await probe(client, allow_write=allow_write)
    finally:
        for other in clients:
            try:
                await other.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
