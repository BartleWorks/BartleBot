"""Diagnostic: can the wizard's character name be read without opening the character page?

OUTCOME (2026-08-12): NO usable route.  Do not spend more time on the offsets below.

The name IS in memory as UTF-16, and there is an object holding the bare name at +0x380
and the nameplate at +0x300 (an 0x80 stride, seen independently on two characters).  But
that object is NOT ClientObject.  Reading those offsets off ClientObject worked exactly
once -- session start, first zone -- and broke permanently at the first zone change:

    [ 0.0s] WC_Hub        +0x380 = '<wizard name>'  +0x300 = '<wizard name>\\n ... <title>'
    [ 9.1s] WC_Ravenwood  +0x380 = ''               +0x300 = <ValueError>
    [24.2s] WC_Hub        +0x380 = <AddressOutOfRange>     (back in the zone that worked)

It never recovered, in any zone, for the rest of the session.  Meanwhile character_id at
0x1C0 kept reading correctly throughout -- so the base really is a live ClientObject, and
0x300/0x380 simply are not its fields.  The one good read was heap aliasing: at session
start those offsets happened to overlap an unrelated allocation holding the name.

A character switch alone preserved the read; a zone change destroyed it.  That is the
shape of an aliased allocation, not a struct member.

Reaching the real container would mean identifying its type and finding its live instance
by vtable-from-constructor-pattern plus a heap scan -- the technique in
CurrentSocialSystemsManager, whose docstring explains at length why that is painful.  The
payoff is removing two keypresses per client from detect_wizard_name, which already works.
Not worth it unless something else needs that object.

WHAT WAS WORTH KEEPING
    ClientObject.character_id() (0x1C0) is a reliable character identity.  Across this
    session it held steady through a zone change, went to 0 at character select, took the
    new character's value on switch, and returned to the original value on switching back.
    That is the character-switch detector the sidebar needs to invalidate a cached wizard
    name -- the open question this whole investigation started from.  The name itself still
    has to come from wizard_names.detect_wizard_name().

    Also established: every name in this process is UTF-16; UTF-8 never matched.  And no
    window in the UI tree holds the name while the character page is closed (430 windows
    swept), so there is no keypress-free UI route either.

Two independent searches got there, because the first run showed the answer is not where it
was expected: a raw memory scan for the name as plain text, and a sweep of the entire UI
window tree for a control already holding it.

WHY THIS EXISTS
    bartlebot.game.wizard_names.detect_wizard_name() gets the character name by pressing C,
    waiting for the character page to render, reading its title window, and pressing C
    again.  It works, but it costs two keypresses and a UI settle per client, it visibly
    moves the game UI, and it runs before every group formation and after every recovery.

    A plain memory read would delete that whole path, make the name cheap enough to show
    on the sidebar player cards, and let a character *switch* be detected automatically
    rather than guessed at.

    The module docstring in wizard_names.py says no memory route was found, but that was a
    survey of the *documented* fields (ClientObject.display_name resolves through the
    object template, CharacterRegistry holds quest ids, the config files hold graphics
    settings).  It was never a search of raw process memory for the name itself.

    This probe does that search.  It is a known-plaintext attack: you tell it the name you
    are logged in as, and it finds every place that byte sequence occurs.

WHAT MAKES IT PLAUSIBLE
    GameClient carries two fields lifted straight out of MSG_CHARACTERSELECTED --
    player_gid (CharID) at 0x214C0 and user_id at 0x21258.  That message is also what
    delivers the character name, so if the name is stored durably anywhere, the same
    region is the first place to expect it.

WHAT WOULD SINK IT
    BasicChatPlayer.name_blob and BasicChatChannelInfo.owner_name_blob are both annotated
    "packed/encoded name", so KI demonstrably does not store every name field as text.  If
    the local character's name is packed the same way, this probe finds nothing -- which is
    a cheap, definitive answer, and the point of running it before designing anything.

WHY THERE IS ALSO A WINDOW SWEEP
    The first run found the name ten times, all as UTF-16 and every
    one of them wrapped in display markup -- "<center>...</center>", "[fn]", an <image;>
    chat tag.  Not one sat at a fixed offset in GameClient or ClientObject.  So the name is
    not a data field; it is text some UI control is holding.

    That is a better lead than it sounds, because the character page was CLOSED during that
    scan and the strings were there anyway.  If a window in the tree already carries the
    name, walking client.root_window reaches it with no keypresses and no page-open wait --
    which is the entire saving detect_wizard_name was after, reached through the UI tree
    rather than a memory offset.

    The sweep runs first because it is fast and is the likelier answer.

A NOTE ON NAME CHOICE
    Run this with the rarest name you have.  A common surname is worthless as a search
    term: one test surname matched 6113 times in UTF-8 alone -- spell names, NPCs, and a
    cinematic asset path that landed close enough to GameClient to be reported as a field
    candidate before the window was tightened.  A freshly created
    character with an unusual name gives a clean signal, and has the further advantage of
    never having had a UI window opened on it, so nothing found can be a stale cache.

HOW TO READ THE OUTPUT
    A hit is interesting when it is (a) outside the .exe module -- an in-module hit is
    static data, most likely the name-part dictionary the character creator picks from,
    not your character -- and (b) either passes the inline-string check or has an owning
    std::string pointing at it.

    "SSO" means the hit looks like the payload of an MSVC std::string stored inline: the
    int32 at hit+0x10 equals the byte length and capacity at hit+0x18 is 15.  That is the
    layout wizwalker's read_string() expects, so an SSO hit is directly readable with the
    existing helpers.  Names longer than 15 bytes, and all wide strings, live on the heap
    instead and are found by the owner pass rather than this check.

    "off GameClient +0x...." is the payoff line.  A hit at a fixed offset inside the
    GameClient struct is a field we can read the same way player_gid is read.

HOW TO RUN
    1. Log in ONE wizard whose name you know exactly, as spelled in game.
    2. uv run python tools/probe_wizard_name.py "Firstname Lastname"
    3. Paste the console output back. The full hit list goes to a report file
       (--out, default probe_wizard_name_report.txt) because a common surname
       matches thousands of times and would otherwise scroll away the answer.

HOW TO RUN THE DURABILITY CHECK (--watch)
    uv run python tools/probe_wizard_name.py --watch

    No name argument and no scanning: it polls the two offsets on every hooked client
    and prints a line whenever anything changes. Leave it running and, in any order:

      - walk through a zone door
      - log out to character select and back in
      - switch to a different character on the same account
      - log a second client in

    An offset that survives all four is a field. One that goes empty, stale, or starts
    returning another character's name after a switch is not, and the sidebar must not
    show it. Read errors are reported rather than swallowed, because what these offsets
    do during a loading screen decides how the caller has to be written.

    By default only the full name is searched. --parts also searches the first and
    last name separately, which is worth doing once to rule out the name being stored
    in halves -- but expect a flood of hits, since a common surname also occurs in
    every spell and NPC name sharing it.

    With several clients open, pick one with --client N (0-based, default 0).
    The full-process scan takes a while -- it walks every committed page.

Read-only: it only reads memory and never clicks, types, or writes.
"""

from __future__ import annotations

import argparse
import asyncio
import struct
import sys

import pymem.process
import regex
from wizwalker.extensions.wizsprinter import WizSprinter

from bartlebot.game.wizard_names import strip_markup

_EXE_NAME = "WizardGraphicalClient.exe"

# The two fields this probe found. Undocumented in wizwalker; confirmed by read-back on one
# character, and pending the --watch durability check before anything depends on them.
WIZARD_NAME_OFFSET = 0x380  # std::wstring, the bare wizard name
NAMEPLATE_OFFSET = 0x300  # std::wstring, name + school badge + title

# Slow enough not to matter next to a game that repaints at 30fps, fast enough to catch the
# moment a character switch lands.
WATCH_INTERVAL = 1.0

# Ceiling on the window sweep. The live tree is a few thousand windows; this only exists so
# a cyclic or corrupt parent/child link cannot spin forever.
MAX_WINDOWS = 50000

# How far past the GameClient base to still call a hit "inside the struct". The documented
# fields run to about 0x224A8 (character_registry); 0x23000 allows a little room past that
# for unmapped fields without reaching into unrelated heap.
#
# This was 0x30000 and it produced two false positives on a surname search -- both landed
# past 0x26000, in a cinematic asset filename that happens to share the surname. A window
# loose enough to swallow neighboring allocations turns a
# coincidence into a "field candidate", which is the one thing this probe must not do.
GAME_CLIENT_WINDOW = 0x23000

# ClientObject is far smaller; its documented fields stop well before this.
CLIENT_OBJECT_WINDOW = 0x2000

# MSVC std::string: 16-byte inline buffer, int32 length at +0x10, capacity at +0x18.
# Capacity is 15 for an inline (SSO) narrow string -- see wizwalker read_string().
STRING_LEN_OFFSET = 0x10
STRING_CAP_OFFSET = 0x18
SSO_NARROW_CAPACITY = 15

# Bytes of context dumped around each interesting hit.
CONTEXT_BEFORE = 0x20
CONTEXT_AFTER = 0x20

# Cap on how many hit addresses go into the single alternation used by the owner pass.
# One scan handles all of them at once, but an unbounded alternation makes a pathological
# regex, and past a few dozen hits the answer is "this string is everywhere" anyway.
MAX_OWNER_TARGETS = 64


class Hit:
    """One occurrence of the searched text in the process's memory."""

    def __init__(self, address: int, label: str, encoding: str, raw: bytes):
        self.address = address
        self.label = label  # which search term matched
        self.encoding = encoding
        self.raw = raw
        self.in_module = False
        self.gc_offset: int | None = None
        self.co_offset: int | None = None
        self.sso = False
        self.owners: list[int] = []

    @property
    def promising(self) -> bool:
        """Worth looking at: outside the module, and either a readable string or in-struct."""
        return not self.in_module and (self.sso or bool(self.owners) or self.gc_offset is not None)


class Report:
    """Collects every line, but only echoes the ones worth watching scroll by.

    The first run of this probe produced 6997 hits and the two that mattered scrolled
    off the top. Everything goes to the file; the console gets the answer.
    """

    def __init__(self) -> None:
        self._lines: list[str] = []

    def line(self, text: str = "", *, console: bool = True) -> None:
        self._lines.append(text)
        if console:
            print(text)

    def write(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(self._lines) + "\n")


class WindowMatch:
    """A UI window whose text contains the searched name."""

    def __init__(self, path: list[str], raw: str, clean: str, visible: bool, type_name: str, address, searched: str):
        self.path = path
        self.raw = raw
        self.clean = clean
        self.visible = visible
        self.type_name = type_name
        self.address = address
        self.searched = searched

    @property
    def exact(self) -> bool:
        """The window holds the name and nothing else -- a name label, not a sentence."""
        return bool(self.clean) and self.clean.casefold() == self.searched.casefold()

    @property
    def where(self) -> str:
        return f"0x{self.address:X}" if isinstance(self.address, int) else str(self.address)


class FieldCandidate:
    """A std::string object that lives at a fixed offset inside a known game object.

    This is the shape the answer actually took, and the shape the probe originally could
    not see. A long name does not fit in a std::string's inline buffer, so the *payload*
    ends up on the heap far from anything recognizable -- but the string object owning it
    sits in the parent struct at a fixed offset. Classifying only payload addresses meant
    the real field looked like unreachable heap.
    """

    def __init__(self, anchor: str, offset: int, owner_address: int, hit: Hit):
        self.anchor = anchor
        self.offset = offset
        self.owner_address = owner_address
        self.hit = hit
        self.read_back: str | None = None

    @property
    def confirmed(self) -> bool:
        return bool(self.read_back) and not self.read_back.startswith("<")


async def _safe(coro):
    try:
        return await coro
    except Exception as exc:
        return f"<{type(exc).__name__}>"


async def _window_text(window) -> str:
    try:
        return await window.maybe_text()
    except Exception:
        # Most windows have no text property at all; maybe_text raises rather than
        # answering "" for those, so this is the common path, not an error path.
        return ""


async def sweep_windows(client, name: str, report: Report) -> list[WindowMatch]:
    """Walk the whole UI tree and return every window whose text contains *name*.

    Records the full parent path for each hit, because a usable result is not "the name is
    in a window somewhere" but "the name is at this path" -- a path is what node_helpers
    can read on demand. Reads only; it opens nothing and sends no input, so whatever it
    finds is text the game was already holding with the UI closed.
    """
    needle = name.casefold()
    matches: list[WindowMatch] = []
    visited = 0

    async def walk(window, path: list[str]) -> None:
        nonlocal visited
        if visited >= MAX_WINDOWS:
            return
        visited += 1

        try:
            window_name = await window.name()
        except Exception:
            window_name = "<unreadable>"
        here = path + [window_name or "<unnamed>"]

        raw = await _window_text(window)
        if raw:
            clean = strip_markup(raw)
            if needle in raw.casefold() or needle in clean.casefold():
                matches.append(
                    WindowMatch(
                        path=here,
                        raw=raw,
                        clean=clean,
                        visible=await _safe(window.is_visible()) is True,
                        type_name=str(await _safe(window.maybe_read_type_name())),
                        address=await _safe(window.read_base_address()),
                        searched=name,
                    )
                )

        try:
            children = await window.children()
        except Exception:
            return
        for child in children:
            await walk(child, here)

    report.line("\nSweeping the UI window tree (read-only, opens nothing)...")
    try:
        await walk(client.root_window, [])
    except Exception as exc:
        report.line(f"  sweep failed: {exc!r}")
        return matches

    report.line(f"  visited {visited} window(s), {len(matches)} hold the name")
    return matches


def print_window_matches(report: Report, matches: list[WindowMatch]) -> None:
    if not matches:
        report.line(
            "\n  No window carries the name with the UI closed. If detect_wizard_name still\n"
            "  works, the character page builds its title on open rather than keeping it."
        )
        return

    exact = [m for m in matches if m.exact]
    report.line(f"\nWindow(s) holding the name ({len(matches)}, {len(exact)} of them exactly):")
    for match in sorted(matches, key=lambda m: (not m.exact, len(m.path))):
        marker = "  <-- name only, no surrounding text" if match.exact else ""
        report.line(f"\n  /{'/'.join(match.path)}{marker}")
        report.line(f"      type={match.type_name}  visible={match.visible}  @ {match.where}")
        report.line(f"      raw   = {match.raw!r}")
        if match.clean != match.raw:
            report.line(f"      clean = {match.clean!r}")


def search_terms(name: str, include_parts: bool = False) -> list[tuple[str, str]]:
    """The strings to hunt for, as (label, text).

    The full name is the primary target and the only one searched by default. KI's names
    are assembled from a fixed dictionary of first names and surname parts, so the halves
    may well be stored separately -- but a surname also appears in every spell and NPC
    name that shares it, which buries the result. --parts turns those on deliberately.
    """
    terms = [("full", name)]
    parts = name.split()
    if include_parts and len(parts) > 1:
        terms.append(("first", parts[0]))
        terms.append(("last", " ".join(parts[1:])))
    return terms


async def scan_for(client, text: str, encoding: str) -> list[int]:
    """Every address holding *text* in *encoding*. Empty when the pattern is absent."""
    try:
        encoded = text.encode(encoding)
    except UnicodeEncodeError:
        return []
    try:
        matches = await client.hook_handler.pattern_scan(regex.escape(encoded), return_multiple=True)
    except Exception:
        # PatternFailed is the normal "not present" answer, not an error worth reporting.
        return []
    if isinstance(matches, int):
        matches = [matches]
    return matches


async def read_anchors(client) -> dict:
    """The known-good identity fields, plus the base addresses hits are measured against.

    player_gid and user_id come straight out of MSG_CHARACTERSELECTED, so they double as a
    sanity check: if they read 0, the client is not fully logged in and a name search would
    be measuring the wrong moment.
    """
    anchors: dict = {}
    anchors["player_gid"] = await _safe(client.game_client.player_gid())
    anchors["user_id"] = await _safe(client.game_client.user_id())
    anchors["global_id_full"] = await _safe(client.client_object.global_id_full())
    anchors["character_id"] = await _safe(client.client_object.character_id())
    anchors["game_client_base"] = await _safe(client.game_client.read_base_address())
    anchors["client_object_base"] = await _safe(client.client_object.read_base_address())
    return anchors


def classify(hit: Hit, anchors: dict, module_base: int, module_end: int) -> None:
    """Fill in a hit's region and its offset from each anchor object."""
    hit.in_module = module_base <= hit.address < module_end

    gc_base = anchors.get("game_client_base")
    if isinstance(gc_base, int) and 0 <= hit.address - gc_base < GAME_CLIENT_WINDOW:
        hit.gc_offset = hit.address - gc_base

    co_base = anchors.get("client_object_base")
    if isinstance(co_base, int) and 0 <= hit.address - co_base < CLIENT_OBJECT_WINDOW:
        hit.co_offset = hit.address - co_base


async def check_inline_string(client, hit: Hit, text: str) -> None:
    """Test whether the hit is the inline payload of a narrow std::string.

    An MSVC std::string holding 15 bytes or fewer keeps them in its own 16-byte buffer, so
    the string object starts exactly at the payload. That makes the hit address directly
    usable with wizwalker's read_string(), which is the best possible outcome here.
    """
    if hit.encoding != "utf-8":
        return  # a wide string long enough to hold a name never fits inline
    byte_len = len(text.encode("utf-8"))
    if byte_len >= 16:
        return
    try:
        header = await client.hook_handler.read_bytes(hit.address, STRING_CAP_OFFSET + 8)
    except Exception:
        return
    length = struct.unpack_from("<i", header, STRING_LEN_OFFSET)[0]
    capacity = struct.unpack_from("<Q", header, STRING_CAP_OFFSET)[0]
    hit.sso = length == byte_len and capacity == SSO_NARROW_CAPACITY


async def find_owners(client, hits: list[Hit]) -> None:
    """Locate std::string objects whose heap pointer targets one of these hits.

    A single alternation over every hit address does this in one pass. Scanning per hit
    would mean one full-process walk each, which for a name that appears in chat and UI
    text would take minutes to say the same thing.
    """
    targets = [h for h in hits if not h.sso][:MAX_OWNER_TARGETS]
    if not targets:
        return

    by_pointer = {struct.pack("<Q", h.address): h for h in targets}
    pattern = b"(?:" + b"|".join(regex.escape(p) for p in by_pointer) + b")"
    try:
        matches = await client.hook_handler.pattern_scan(pattern, return_multiple=True)
    except Exception:
        return
    if isinstance(matches, int):
        matches = [matches]

    for addr in matches:
        try:
            pointer = await client.hook_handler.read_bytes(addr, 8)
        except Exception:
            continue
        owned = by_pointer.get(pointer)
        if owned is not None:
            owned.owners.append(addr)


def find_field_candidates(hits: list[Hit], anchors: dict) -> list[FieldCandidate]:
    """Every owning std::string that sits at a fixed offset inside GameClient or ClientObject."""
    windows = (
        ("GameClient", anchors.get("game_client_base"), GAME_CLIENT_WINDOW),
        ("ClientObject", anchors.get("client_object_base"), CLIENT_OBJECT_WINDOW),
    )
    candidates: list[FieldCandidate] = []
    seen: set[tuple[str, int]] = set()
    for hit in hits:
        for owner in hit.owners:
            for anchor, base, size in windows:
                if not isinstance(base, int) or not 0 <= owner - base < size:
                    continue
                key = (anchor, owner - base)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(FieldCandidate(anchor, owner - base, owner, hit))
    return candidates


async def verify_candidates(client, candidates: list[FieldCandidate], report: Report) -> None:
    """Read each candidate back through wizwalker's own string helpers.

    A candidate is only worth reporting if the existing read path returns the name from it,
    because that is exactly how production code would consume it. Reading it here turns the
    probe's output from "an address that happens to contain the name" into a field that has
    been shown to work.
    """
    if not candidates:
        return
    report.line("\nReading each candidate back through wizwalker's string helpers:")
    for candidate in candidates:
        target = client.client_object if candidate.anchor == "ClientObject" else client.game_client
        reader = (
            target.read_wide_string_from_offset
            if candidate.hit.encoding == "utf-16-le"
            else (target.read_string_from_offset)
        )
        candidate.read_back = str(await _safe(reader(candidate.offset)))
        report.line(f"  {candidate.anchor} +0x{candidate.offset:X}  ->  {candidate.read_back!r}")


def hex_dump(data: bytes, base: int, highlight: tuple[int, int]) -> list[str]:
    """16-byte-per-row hex+ascii, with the matched span marked by [ ]."""
    lines = []
    start, end = highlight
    for row in range(0, len(data), 16):
        chunk = data[row : row + 16]
        cells = []
        for i, byte in enumerate(chunk):
            pos = row + i
            mark = "[" if pos == start else ("]" if pos == end else " ")
            cells.append(f"{mark}{byte:02X}")
        ascii_col = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
        lines.append(f"    0x{base + row:X}  {''.join(cells):<52} {ascii_col}")
    return lines


async def dump_context(client, hit: Hit) -> list[str]:
    start = hit.address - CONTEXT_BEFORE
    size = CONTEXT_BEFORE + len(hit.raw) + CONTEXT_AFTER
    try:
        data = await client.hook_handler.read_bytes(start, size)
    except Exception:
        return ["    <context unreadable>"]
    return hex_dump(data, start, (CONTEXT_BEFORE, CONTEXT_BEFORE + len(hit.raw)))


def describe(hit: Hit) -> str:
    notes = []
    if hit.in_module:
        notes.append("in .exe module (static data)")
    if hit.gc_offset is not None:
        notes.append(f"*** off GameClient +0x{hit.gc_offset:X} ***")
    if hit.co_offset is not None:
        notes.append(f"*** off ClientObject +0x{hit.co_offset:X} ***")
    if hit.sso:
        notes.append("SSO std::string payload")
    if hit.owners:
        owners = ", ".join(f"0x{a:X}" for a in hit.owners[:4])
        more = f" (+{len(hit.owners) - 4} more)" if len(hit.owners) > 4 else ""
        notes.append(f"owned by std::string @ {owners}{more}")
    return "; ".join(notes) if notes else "loose bytes, no string header"


async def read_field_snapshot(client) -> dict:
    """Everything the durability check compares between polls, for one client.

    character_id rides along deliberately: it is the switch detector the sidebar would use
    to invalidate a cached name, so watching the two change together is what proves they
    agree. A name that updates while character_id does not -- or the reverse -- means the
    cache-invalidation story does not hold.
    """
    client_object = client.client_object
    return {
        "base": await _safe(client_object.read_base_address()),
        "character_id": await _safe(client_object.character_id()),
        "name": await _safe(client_object.read_wide_string_from_offset(WIZARD_NAME_OFFSET)),
        "nameplate": await _safe(client_object.read_wide_string_from_offset(NAMEPLATE_OFFSET)),
        "zone": await _safe(client.zone_name()),
    }


def format_snapshot(index: int, elapsed: float, snapshot: dict) -> list[str]:
    base = snapshot["base"]
    where = f"0x{base:X}" if isinstance(base, int) else base
    return [
        f"\n[{elapsed:7.1f}s] client {index}   ClientObject @ {where}",
        f"           zone         = {snapshot['zone']!r}",
        f"           character_id = {snapshot['character_id']}",
        f"           +0x{WIZARD_NAME_OFFSET:X} name  = {snapshot['name']!r}",
        f"           +0x{NAMEPLATE_OFFSET:X} plate = {snapshot['nameplate']!r}",
    ]


async def watch_fields(clients: list, report: Report, interval: float = WATCH_INTERVAL) -> None:
    """Poll the two offsets on every client, reporting only what changes.

    Printing every poll would bury the transitions that matter under hundreds of identical
    lines, and the transitions are the entire point: what the fields do across a zone door,
    a relog and a character switch.
    """
    report.line(
        f"Watching ClientObject +0x{WIZARD_NAME_OFFSET:X} and +0x{NAMEPLATE_OFFSET:X} "
        f"on {len(clients)} client(s), every {interval}s."
    )
    report.line("Only changes are printed. Zone, relog, switch character, then Ctrl-C.\n")

    started = asyncio.get_event_loop().time()
    previous: dict[int, dict] = {}
    while True:
        for index, client in enumerate(clients):
            snapshot = await read_field_snapshot(client)
            if snapshot != previous.get(index):
                elapsed = asyncio.get_event_loop().time() - started
                for line in format_snapshot(index, elapsed, snapshot):
                    report.line(line)
                previous[index] = snapshot
        await asyncio.sleep(interval)


def print_anchors(report: Report, anchors: dict, module_base: int, module_end: int) -> None:
    report.line("\nIdentity anchors (all read-only):")
    for key in ("player_gid", "user_id", "global_id_full", "character_id"):
        report.line(f"  {key:<16} = {anchors[key]}")
    for key in ("game_client_base", "client_object_base"):
        value = anchors[key]
        shown = f"0x{value:X}" if isinstance(value, int) else value
        report.line(f"  {key:<16} = {shown}")
    report.line(f"  {'module':<16} = 0x{module_base:X} .. 0x{module_end:X}")

    if anchors["player_gid"] == 0 or anchors["global_id_full"] == 0:
        report.line(
            "\n  WARNING: an identity field reads 0, which usually means the character is not\n"
            "  fully in-game yet. Finish loading and re-run, or the scan is measuring the\n"
            "  wrong moment."
        )


def print_verdict(
    report: Report,
    hits: list[Hit],
    windows: list[WindowMatch],
    candidates: list[FieldCandidate],
    name: str,
) -> None:
    report.line("\n" + "=" * 78)

    confirmed = [c for c in candidates if c.confirmed and name.casefold() in c.read_back.casefold()]
    if confirmed:
        report.line(
            f"VERDICT: {name!r} is a field at a fixed offset, and it reads back correctly.\n\n"
            "  This is the whole objective. The name costs one memory read -- no keypresses,\n"
            "  no character page, no UI settle -- so detect_wizard_name can be replaced\n"
            "  outright and the sidebar can afford to show names on every card:"
        )
        for candidate in confirmed:
            report.line(f"      {candidate.anchor} +0x{candidate.offset:X}   ->  {candidate.read_back!r}")
        report.line(
            "\n  Stage 3 is the durability check, and it is not optional: confirm the offset\n"
            "  survives a relog, a zone change, and a different character on the same account\n"
            "  before any code depends on it."
        )
        report.line("=" * 78)
        return

    if candidates:
        report.line(
            f"VERDICT: {len(candidates)} owning string(s) sit at a fixed offset, but none read back as\n"
            f"  {name!r}. Either the offset is a near-miss on a neighboring field, or the\n"
            "  string layout differs from what read_string/read_wide_string assume. The hex\n"
            "  context above shows which."
        )
        report.line("=" * 78)
        return

    exact = [m for m in windows if m.exact]
    if exact:
        report.line(
            f"VERDICT: {len(exact)} window(s) hold {name!r} as their entire text, with the UI closed.\n\n"
            "  This is the result worth having, and it needs no new memory offsets. Any of\n"
            "  these paths can be read with node_helpers.get_text_by_path, so the two C\n"
            "  keypresses and the character-page settle come out of detect_wizard_name.\n"
            "  Confirm the path holds after a relog and on a second character before relying\n"
            "  on it -- a path under a window that only exists this session is not a route."
        )
        for match in exact:
            report.line(f"      /{'/'.join(match.path)}   (visible={match.visible})")
        report.line("=" * 78)
        return

    if windows:
        report.line(
            f"VERDICT: {len(windows)} window(s) mention {name!r}, but none hold it alone.\n\n"
            "  The name is embedded in longer text (a sentence, a chat line, a template), so\n"
            "  reading it back means parsing rather than a straight path read. Check the raw\n"
            "  strings above -- if one has a fixed surrounding format it may still be usable."
        )
        report.line("=" * 78)
        return

    if not hits:
        report.line(
            f"VERDICT: {name!r} does not occur anywhere in the process, in either encoding.\n\n"
            "The name is not held as plain text, which matches the 'packed/encoded name'\n"
            "annotation on BasicChatPlayer.name_blob. The plaintext route is dead; the next\n"
            "thing worth trying is the GID->name direction, decoding that packing, since\n"
            "player_gid is already readable."
        )
        report.line("=" * 78)
        return

    promising = [h for h in hits if h.promising]
    in_struct = [h for h in hits if h.gc_offset is not None or h.co_offset is not None]

    report.line(f"VERDICT: {len(hits)} occurrence(s) of {name!r} found.")
    if in_struct:
        report.line(
            f"\n  {len(in_struct)} sit inside a known object -- the best case. The name is a field at a\n"
            "  fixed offset and can be read exactly the way player_gid is:"
        )
        for hit in in_struct:
            anchor = "GameClient" if hit.gc_offset is not None else "ClientObject"
            offset = hit.gc_offset if hit.gc_offset is not None else hit.co_offset
            extra = " (inline SSO std::string)" if hit.sso else ""
            report.line(f"      {anchor} +0x{offset:X}   {hit.encoding}{extra}   @ 0x{hit.address:X}")
        report.line("\n  Stage 3 confirms the offset holds across a relog and a second character.")
    elif promising:
        report.line(
            f"\n  {len(promising)} look like real strings on the heap, but none fall inside GameClient\n"
            "  or ClientObject. The name exists as text, but reaching it needs a pointer chain\n"
            "  rather than a fixed offset. Worth following -- an owning object may be reachable."
        )
    else:
        report.line(
            "\n  Every occurrence is either inside the .exe module (static data -- most likely\n"
            "  the name dictionary the character creator draws from, not your character) or\n"
            "  loose bytes with no string header. No usable field here."
        )
    report.line("=" * 78)


async def run(
    client, name: str, report: Report, include_parts: bool
) -> tuple[list[Hit], list[WindowMatch], list[FieldCandidate]]:
    module = pymem.process.module_from_name(client.hook_handler.process.process_handle, _EXE_NAME)
    if module is None:
        report.line(f"{_EXE_NAME} module not found.")
        return [], [], []
    module_base = module.lpBaseOfDll
    module_end = module_base + module.SizeOfImage

    anchors = await read_anchors(client)
    print_anchors(report, anchors, module_base, module_end)

    # Window sweep first: it is fast, and it is the likelier of the two to answer.
    windows = await sweep_windows(client, name, report)
    print_window_matches(report, windows)

    hits: list[Hit] = []
    report.line("\nScanning every committed page (this is the slow part)...")
    for label, text in search_terms(name, include_parts):
        for encoding in ("utf-8", "utf-16-le"):
            addresses = await scan_for(client, text, encoding)
            report.line(f"  {label:<6} {text!r:<20} {encoding:<10} {len(addresses)} hit(s)")
            for address in addresses:
                hits.append(Hit(address, label, encoding, text.encode(encoding)))

    if not hits:
        return hits, windows, []

    for hit in hits:
        classify(hit, anchors, module_base, module_end)
        await check_inline_string(client, hit, hit.raw.decode(hit.encoding))

    report.line("\nLooking for std::string objects that point at these addresses...")
    await find_owners(client, hits)

    candidates = find_field_candidates(hits, anchors)
    await verify_candidates(client, candidates, report)

    heap = [h for h in hits if not h.in_module]
    in_module = [h for h in hits if h.in_module]

    # The full listings are the part that scrolls away, so they go to the file only.
    report.line(f"\nHits outside the module ({len(heap)}):", console=False)
    for hit in sorted(heap, key=lambda h: (h.gc_offset is None, h.address)):
        report.line(f"  0x{hit.address:X}  {hit.label:<6} {hit.encoding:<10} {describe(hit)}", console=False)

    report.line(f"\nHits inside the module ({len(in_module)}) -- static data:", console=False)
    for hit in in_module:
        report.line(f"  0x{hit.address:X}  {hit.label:<6} {hit.encoding:<10}", console=False)

    report.line(f"\n{len(heap)} hit(s) outside the module, {len(in_module)} inside it (static data).")

    promising = [h for h in hits if h.promising]
    if promising:
        report.line(f"\nContext around the {min(len(promising), 6)} most promising of {len(promising)} hit(s):")
        for hit in sorted(promising, key=lambda h: h.gc_offset is None)[:6]:
            report.line(f"\n  0x{hit.address:X}  {hit.label} / {hit.encoding} -- {describe(hit)}")
            for line in await dump_context(client, hit):
                report.line(line)

    return hits, windows, candidates


async def main() -> int:
    parser = argparse.ArgumentParser(description="Search process memory for the logged-in wizard's name.")
    parser.add_argument(
        "name",
        nargs="?",
        help='The character name exactly as spelled in game, e.g. "Firstname Lastname". Not used with --watch.',
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Skip the search; poll the confirmed offsets on every client and report changes (Ctrl-C to stop)",
    )
    parser.add_argument("--client", type=int, default=0, help="Index of the client to probe (default 0)")
    parser.add_argument(
        "--parts",
        action="store_true",
        help="Also search the first and last name separately (noisy: a surname matches every spell sharing it)",
    )
    parser.add_argument(
        "--out",
        default="probe_wizard_name_report.txt",
        help="Where to write the full hit list (default probe_wizard_name_report.txt)",
    )
    args = parser.parse_args()

    if not args.watch and not args.name:
        parser.error('a name is required unless --watch is given, e.g. "Firstname Lastname"')

    sprinter = WizSprinter()
    clients = sprinter.get_new_clients()
    if not clients:
        print("No Wizard101 clients found. Start the game and log a wizard in.")
        return 1

    if not args.watch and not 0 <= args.client < len(clients):
        print(f"--client {args.client} is out of range; {len(clients)} client(s) found.")
        return 1

    print(f"Found {len(clients)} client(s); activating hooks...")
    try:
        await asyncio.gather(*[c.activate_hooks() for c in clients])
    except Exception as exc:
        print(f"Hook activation failed: {exc!r}")
        return 1

    report = Report()
    try:
        if args.watch:
            await watch_fields(clients, report)
        else:
            hits, windows, candidates = await run(clients[args.client], args.name, report, args.parts)
            print_verdict(report, hits, windows, candidates, args.name)
    except (KeyboardInterrupt, asyncio.CancelledError):
        # The expected way to end a --watch session, not a failure.
        report.line("\nStopped.")
    finally:
        try:
            report.write(args.out)
            print(f"\nReport written to {args.out}")
        except OSError as exc:
            print(f"\nCould not write the report to {args.out}: {exc}")
        for c in clients:
            try:
                await c.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
