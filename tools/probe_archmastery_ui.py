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

WHAT IT DOES
    A. Reports the school the behavior currently holds, so there is a known
       starting value to compare against.

    B. Dumps the whole DeckConfiguration subtree — name, type, visibility,
       screen rect, and text — and flags every window whose name or text looks
       school-related.  Names alone are often enough; when they are not, C is.

    C. The part that actually settles it.  Change the archmastery school **in
       the game** and press Enter: the tree is re-read and only what *changed*
       is reported.  A control that changes when the school changes, and does
       not change otherwise, is the control — the same "let the difference name
       it" trick ``probe_item_cards.py`` uses for the active flag.

HOW TO RUN
    1. Log a wizard in.  Any wizard with an archmastery school set will do;
       archmastery is not tied to a world or a level bracket.
    2. Open the deck page (press P) and leave it open.
    3. poetry run python tools/probe_archmastery_ui.py
    4. When section C asks, switch the school in-game, then press Enter.

    Read-only from this side: the probe never clicks and never writes.  The one
    change to your deck configuration is the one you make yourself in step 4.

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
from wizwalker.memory.memory_objects.conditionals import school_to_str

REPORT_PATH = "probe_archmastery_report.txt"

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
        print("   Nothing in the subtree changed.  The selector is probably outside")
        print("   DeckConfiguration — re-run against the whole root window, or check")
        print("   whether the school is set somewhere other than the deck page.")
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


def write_report(nodes: list[Node], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write("Full DeckConfiguration subtree.\n")
        f.write("May contain your wizard's name in window text — check before sharing.\n\n")
        for node in nodes:
            f.write(_format(node) + "\n")


async def probe(client) -> None:
    try:
        deck_window = await _maybe_get_named_window(client.root_window, "DeckConfiguration", retries=0)
    except ValueError:
        print("   DeckConfiguration is not in the UI tree — open the deck page (P) first.")
        return

    school_before = await section_a(client)
    nodes = await section_b(deck_window)
    write_report(nodes, REPORT_PATH)
    await section_c(deck_window, client, nodes, school_before)


async def main() -> int:
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
        await probe(client)
    finally:
        for other in clients:
            try:
                await other.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
