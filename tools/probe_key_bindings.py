"""Probe: which key code the game means by each key name it writes for a binding.

Key rebinding (r811339, the Ahmarra Test realm) names each binding's key in ``m_input``, such
as ``keyboard.down.ctrl.shift.q``. A key the game has a name for is written by that name
(``period``, ``lsqbracket``; the table is in ``Bin/WizardGraphicalClient.exe``), and any other
key as its raw virtual-key code, one byte, letters lowercased (``;`` is byte 0xBA). Before
BartleBot can press an action the player rebound, it must turn the name back into the code it
sends, and the doubtful names (``period``: the main key or the numpad's?) need asking.

Bindings belong to the account, and each client holds its own in memory, so they are set the
way a player sets them: by hand, in the game's key bindings, pressing the physical key. That
also records the name the game gives each physical key. This probe then reads the client's
bindings from memory (``bartleby.runtime.clients.game_keys``), sends each candidate code
for each name exactly the way the nodes send keys (wizwalker's ``timed_send_key``), and
records which code opened the action's window. It also sends Ctrl+0, bound as the Character
page's second key, three ways, to see whether a binding with a modifier can be pressed at all.

HOW IT WORKS
    Each physical key under test is bound as the SECOND key of an action that opens a window,
    keeping the action's default letter as its first. The letter is pressed first, to learn
    which windows the action opens; then each candidate code for the name the game recorded,
    and the action's own windows appearing is the answer.

HOW TO RUN
    Use a Test realm account whose bindings you don't mind changing, and the wizwalker the
    Test realm needs (bartleby-core/docs/GAME_UPDATES.md, *Running against the update*). Close BartleBot:
    the probe presses keys, and a running bot would too.

    1.  uv run --no-sync python tools/probe_key_bindings.py plan
        lists the keys for this round. In game, bind each as that action's second key (Num
        Lock on, so the numpad sends numbers), and save. If the game says a key is taken,
        let it move. No numpad, Pause or Insert on the keyboard? Windows' On-Screen Keyboard
        (Win+Ctrl+O) has them all: Options > Turn on numeric key pad, and its NumLock on.
    2.  With ONE client logged in, out of combat, every page closed and the chat box showing:
            uv run --no-sync python tools/probe_key_bindings.py run
        It presses keys and opens and closes pages for a minute. Don't touch the client.
        ``run --redo`` runs the last round again on the same bindings, replacing its answers.
    3.  Repeat until ``plan`` says every key is answered (two rounds), then reset the
        account's bindings to defaults in game.
        uv run --no-sync python tools/probe_key_bindings.py report   prints the results so far.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from bartleby.runtime.clients.game_keys import find_binding_list, read_binding_list
from wizwalker import ClientHandler
from wizwalker.errors import PatternFailed

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOOLS = Path(__file__).resolve().parent
RESULTS = TOOLS / "probe_key_bindings.local.json"

PREFIX = "keyboard.down."
PRESS_SECONDS = 0.1  # what the nodes hold a key for
PRESS_WAIT = 2.0  # how long a page gets to appear
SETTLE = 0.6
POLL = 0.25
SNAPSHOT_DEPTH = 4  # deep enough to reach the page inside the spellbook frame
MATCH = 0.6  # Jaccard overlap at which a window change counts as an action's

VK_ESCAPE = 0x1B
VK_CONTROL = 0x11
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101


@dataclass(frozen=True)
class KeyName:
    """A name the game writes for a key, and the codes it might mean, likeliest first."""

    name: str
    candidates: tuple[int, ...]


# Every name in the game's table, with the codes worth trying for it.
NAMES: dict[str, KeyName] = {
    k.name: k
    for k in (
        KeyName("escape", (0x1B,)),
        KeyName("enter", (0x0D,)),
        KeyName("tab", (0x09,)),
        KeyName("backspace", (0x08,)),
        KeyName("delete", (0x2E,)),
        KeyName("space", (0x20,)),
        KeyName("home", (0x24,)),
        KeyName("left", (0x25,)),
        KeyName("up", (0x26,)),
        KeyName("right", (0x27,)),
        KeyName("down", (0x28,)),
        KeyName("pageup", (0x21,)),
        KeyName("pagedown", (0x22,)),
        KeyName("end", (0x23,)),
        KeyName("insert", (0x2D,)),
        KeyName("printscreen", (0x2C,)),
        KeyName("break", (0x13, 0x03)),
        *(KeyName(f"f{n}", (0x6F + n,)) for n in range(1, 13)),
        *(KeyName(f"num{n}", (0x60 + n,)) for n in range(10)),
        KeyName("tilde", (0xC0,)),
        KeyName("add", (0x6B, 0xBB)),
        KeyName("numlock", (0x90,)),
        KeyName("multiply", (0x6A,)),
        KeyName("divide", (0x6F,)),  # 0x6F confirmed; 0xBF is "/" (question), which stuck round 2
        KeyName("separator", (0x6C,)),
        KeyName("period", (0xBE, 0x6E)),
        KeyName("subtract", (0x6D, 0xBD)),
        KeyName("question", (0xBF,)),
        KeyName("comma", (0xBC,)),
        KeyName("equal", (0xBB,)),
        KeyName("lsqbracket", (0xDB,)),
        KeyName("rsqbracket", (0xDD,)),
        KeyName("pipe", (0xDC, 0xE2)),
    )
}

# The physical keys to bind, as a US keyboard labels them. Two rounds of twelve and thirteen.
PHYSICAL: tuple[str, ...] = (
    ". (period, main keys)",
    ", (comma)",
    "= (equals)",
    "[ (left bracket)",
    "] (right bracket)",
    "- (minus, main keys)",
    "Numpad +",
    "Numpad -",
    "Numpad *",
    "Numpad /",
    "Numpad . (Num Lock on)",
    "Numpad 0 (Num Lock on)",
    "Num Lock",
    "Pause/Break",
    "Print Screen",
    "Insert",
    "Delete",
    "Backspace",
    "F5",
    "` (backquote, left of 1)",
    "/ (slash)",
    "\\ (backslash)",
    "; (semicolon)",
    "' (apostrophe)",
)

# Actions that open or close something on screen, by their default key.
# Not the friends list: F cycles through two lists rather than toggling one.
WINDOW_ACTIONS: dict[str, str] = {
    "ToggleInventory": "b",
    "ToggleDeckConfig": "p",
    "ToggleMapWindow": "m",
    "ToggleQuestList": "q",
    "ToggleCraftingPage": "j",
    "TogglePetStats": "i",
    "ToggleJewelInventory": "y",
    "OpenMonsterTome": "t",
    "DisplayFishBasket": "v",
    "ToggleFurniture": "u",  # the housing inventory, a backpack page; H's furniture placement works in a house only
    "ToggleCompass": "n",
    "ToggleChatBox": "o",
    "ToggleCharStats": "c",
}

# The modifier check rides on the Character page in the first round, in place of a physical key.
MODIFIER_ACTION = "ToggleCharStats"
MODIFIER_KEY = "Ctrl+0"
MODIFIER_VK = 0x30
MODIFIER_METHODS = ("send_hotkey", "SendMessage+lParam", "PostMessage+lParam")


def display(name: str) -> str:
    """A key name as text a terminal can show: a raw byte as its hex code."""
    return "".join(c if c.isascii() and c.isprintable() else f"<0x{ord(c):02X}>" for c in name)


def candidates_for(name: str) -> tuple[int, ...]:
    """The codes a key name might mean: the table's guesses, or the raw byte itself."""
    if name in NAMES:
        return NAMES[name].candidates
    if len(name) == 1:
        return (ord(name.upper()) if name.isalpha() and name.isascii() else ord(name),)
    return ()


# --- state -------------------------------------------------------------------------------


def load() -> dict:
    return json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else {}


def save(data: dict) -> None:
    RESULTS.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def next_round(results: dict) -> dict[str, str]:
    """This round's action -> physical key, the modifier check first while it is unanswered."""
    actions = list(WINDOW_ACTIONS)
    plan: dict[str, str] = {}
    if "modifier" not in results:
        plan[MODIFIER_ACTION] = MODIFIER_KEY
        actions.remove(MODIFIER_ACTION)
    keys = [k for k in PHYSICAL if k not in results.get("keys", {})]
    plan.update(zip(actions, keys))
    return plan


# --- plan / report -----------------------------------------------------------------------


def plan() -> int:
    results = load()
    round_ = next_round(results)
    if not round_:
        print("Every key is answered. Reset the account's bindings to defaults in game, and see `report`.")
        return 0
    print("In game, bind each of these as the action's SECOND key, keep its first, and save:\n")
    for action, key in round_.items():
        print(f"  {action:<22} (first key {WINDOW_ACTIONS[action].upper()})   second key: {key}")
    left = len([k for k in PHYSICAL if k not in results.get("keys", {})]) - sum(
        1 for key in round_.values() if key != MODIFIER_KEY
    )
    if left:
        print(f"\n{left} more key(s) wait for the next round.")
    print("\nThen, with one client logged in and every page closed: run `run`.")
    return 0


def report() -> int:
    results = load()
    if not results:
        print("No results yet.")
        return 0
    if "modifier" in results:
        print(f"{MODIFIER_KEY} on the Character page, recorded as {results['modifier'].get('recorded')!r}:")
        for method in MODIFIER_METHODS:
            print(f"  {method:<20} {results['modifier'].get(method)}")
    print("\nphysical key                    name             codes tried")
    for key in PHYSICAL:
        entry = results.get("keys", {}).get(key)
        if entry is None:
            print(f"  {key:<30} untested")
            continue
        tried = ", ".join(f"{code} {verdict}" for code, verdict in entry.get("candidates", {}).items())
        print(f"  {key:<30} {display(entry.get('name') or '(not bound)'):<16} {tried or entry.get('note', '')}")
    return 0


# --- pressing ----------------------------------------------------------------------------


def _load_user32():
    """A user32 of our own, so the argument types set here never change wizwalker's calls."""
    if sys.platform != "win32":
        return None
    from ctypes import wintypes as w  # noqa: PLC0415

    user32 = ctypes.WinDLL("user32")
    for fn in (user32.SendMessageW, user32.PostMessageW):
        fn.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user32.MapVirtualKeyW.argtypes = [w.UINT, w.UINT]
    user32.MapVirtualKeyW.restype = w.UINT
    return user32


_user32 = _load_user32()


async def press(client, vk: int) -> None:
    """Send one key the way every node does: wizwalker's timed_send_key, which takes a Keycode.

    Any code is wanted here, including ones the Keycode enum has no member for, and
    timed_send_key only reads the member's ``.value``.
    """
    from wizwalker import utils  # noqa: PLC0415

    await utils.timed_send_key(client.window_handle, SimpleNamespace(value=vk), PRESS_SECONDS)


def _lparam(vk: int, up: bool) -> int:
    """WM_KEYDOWN/UP's lParam as a real keyboard sends it: repeat 1, the scan code, and the
    previous-state and transition bits on a release."""
    return 1 | (_user32.MapVirtualKeyW(vk, 0) << 16) | (0xC0000000 if up else 0)


async def press_with_ctrl(client, vk: int, method: str) -> None:
    """Ctrl held around *vk*, three ways, to see whether any reaches the game as Ctrl+key."""
    hwnd = client.window_handle
    if method == "send_hotkey":
        from wizwalker import utils  # noqa: PLC0415

        await utils.send_hotkey(hwnd, [SimpleNamespace(value=VK_CONTROL)], SimpleNamespace(value=vk))
        return
    send = _user32.SendMessageW if method == "SendMessage+lParam" else _user32.PostMessageW
    send(hwnd, WM_KEYDOWN, VK_CONTROL, _lparam(VK_CONTROL, False))
    send(hwnd, WM_KEYDOWN, vk, _lparam(vk, False))
    await asyncio.sleep(PRESS_SECONDS)
    send(hwnd, WM_KEYUP, vk, _lparam(vk, True))
    send(hwnd, WM_KEYUP, VK_CONTROL, _lparam(VK_CONTROL, True))


# --- seeing what a key did ---------------------------------------------------------------


async def snapshot(client) -> frozenset[str]:
    """Every visible window within SNAPSHOT_DEPTH of the root, by path of names."""
    seen: set[str] = set()

    async def walk(window, path: str, depth: int) -> None:
        try:
            children = await window.children()
        except Exception:
            return
        for child in children:
            try:
                if not await child.is_visible():
                    continue
                name = await child.name()
            except Exception:
                continue
            here = f"{path}/{name}"
            seen.add(here)
            if depth < SNAPSHOT_DEPTH:
                await walk(child, here, depth + 1)

    await walk(client.root_window, "", 1)
    return frozenset(seen)


def overlap(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


class Screen:
    """The windows showing when nothing is open, and how to get back there."""

    def __init__(self, client) -> None:
        self.client = client
        self.base: frozenset[str] = frozenset()
        self.noise: set[str] = set()

    async def settle(self) -> None:
        shots = []
        for _ in range(3):
            shots.append(await snapshot(self.client))
            await asyncio.sleep(0.5)
        self.base = shots[-1]
        self.noise |= set(frozenset.union(*shots) - frozenset.intersection(*shots))

    async def change(self, wait: float = PRESS_WAIT) -> frozenset[str]:
        """What differs from the base once it stops changing, or empty after *wait*."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait
        while loop.time() < deadline:
            if (await snapshot(self.client) ^ self.base) - self.noise:
                await asyncio.sleep(SETTLE)
                return frozenset((await snapshot(self.client) ^ self.base) - self.noise)
            await asyncio.sleep(POLL)
        return frozenset()

    async def back(self, toggle_vk: int | None) -> None:
        """Undo a change: the same key again (they are toggles), then Esc. Raises Stuck if neither works.

        Never carries on from wherever it ended up: a page or the chat line left open swallows
        every key after it, and each would read as "none" (round 2's first run).
        """
        if toggle_vk is not None:
            await press(self.client, toggle_vk)
            await asyncio.sleep(SETTLE)
        for _ in range(2):
            if not (await snapshot(self.client) ^ self.base) - self.noise:
                return
            await press(self.client, VK_ESCAPE)
            await asyncio.sleep(SETTLE)
        if (await snapshot(self.client) ^ self.base) - self.noise:
            raise Stuck


class Stuck(Exception):
    """The client could not be brought back to the starting screen."""


def distinctive(windows: dict[str, frozenset[str]]) -> dict[str, frozenset[str]]:
    """Each action's windows that no other calibrated action changes.

    The spellbook pages all open the same frame, so most of what one changes, every other
    changes too (17 windows each, in round 1); only these tell the pages apart.
    """
    return {
        action: own - frozenset().union(*(other for name, other in windows.items() if name != action))
        for action, own in windows.items()
    }


def attribute(diff: frozenset[str], windows: dict[str, frozenset[str]]) -> str:
    """Whose windows *diff* is: an action, 'ambiguous:<a>|<b>', or 'unmatched'.

    An action is a match when most of its distinctive windows changed. One with none, being
    indistinguishable from another at this depth, falls back to the overlap of all it changes.
    """
    marks = distinctive(windows)
    scores = {a: len(m & diff) / len(m) for a, m in marks.items() if m}
    best = max(scores.values(), default=0.0)
    hits = [a for a, score in scores.items() if score == best and score >= 0.5]
    if len(hits) == 1:
        return hits[0]
    if hits:
        return "ambiguous:" + "|".join(hits)
    plain = [a for a, m in marks.items() if not m and overlap(windows[a], diff) >= MATCH]
    return plain[0] if len(plain) == 1 else ("ambiguous:" + "|".join(plain) if plain else "unmatched")


async def try_key(screen: Screen, vk: int, windows: dict[str, frozenset[str]], own: str) -> str:
    """Press *vk* and say whose windows appeared: 'own', 'other:<action>', 'ambiguous:…', 'unmatched' or 'none'."""
    await press(screen.client, vk)
    diff = await screen.change()
    if not diff:
        return "none"
    whose = attribute(diff, windows)
    await screen.back(vk)
    if whose == own:
        return "own"
    return whose if whose == "unmatched" or whose.startswith("ambiguous:") else f"other:{whose}"


# --- run ---------------------------------------------------------------------------------


async def run(redo: bool) -> int:
    results = load()
    round_ = results.get("last_round", {}) if redo else next_round(results)
    if not round_:
        print("No round to redo." if redo else "Every key is answered; see `report`.")
        return 0

    async with ClientHandler() as handler:
        clients = handler.get_new_clients()
        if len(clients) != 1:
            print(f"Found {len(clients)} clients; open exactly one.")
            return 2
        client = clients[0]

        print("Reading the client's bindings from memory:")
        address = await find_binding_list(client.hook_handler)
        if address is None:
            print("No InputBindingList found. Is a wizard logged in, in the world?")
            return 1
        bound: dict[str, dict[int, str]] = {}
        for b in await read_binding_list(client.hook_handler, address):
            bound.setdefault(b.action, {})[b.slot] = b.input

        try:
            await client.hook_handler.activate_root_window_hook(wait_for_ready=True, timeout=20.0)
        except PatternFailed:
            print(
                "\nThe installed wizwalker cannot hook this client: the pinned one is in, not the update's\n"
                "branch. Something ran `uv sync` or a plain `uv run`. Reinstall the branch\n"
                "(bartleby-core/docs/GAME_UPDATES.md, *Running against the update*) and run again."
            )
            return 2
        screen = Screen(client)
        await screen.settle()
        print(f"Starting screen: {len(screen.base)} visible windows, {len(screen.noise)} that change by themselves.")

        # What each action opens, from its default letter, wherever the player left it.
        windows: dict[str, frozenset[str]] = {}
        print("\nCalibrating each action from its default key:")
        for action in round_:
            letter = PREFIX + WINDOW_ACTIONS[action]
            if letter not in bound.get(action, {}).values():
                print(f"  {action:<22} {WINDOW_ACTIONS[action].upper()} is no longer bound to it: skipped")
                continue
            vk = ord(WINDOW_ACTIONS[action].upper())
            await press(client, vk)
            diff = await screen.change()
            print(f"  {action:<22} {f'{len(diff)} window(s) changed' if diff else 'nothing changed'}")
            if diff:
                windows[action] = diff
                try:
                    await screen.back(vk)
                except Stuck:
                    print(f"\nStuck after {action}: close what is open in the client and run again. Nothing saved.")
                    return 3
        marks = distinctive(windows)
        same = [a for a, m in marks.items() if not m]
        if same:
            print(f"  No window of their own, so told apart by overlap only: {', '.join(same)}")
        results["calibration"] = {a: sorted(w) for a, w in windows.items()}

        keys = results.setdefault("keys", {})
        recorded_as: dict[str, tuple[list[str], str | None]] = {}
        for action in round_:
            others = [i for i in bound.get(action, {}).values() if i != PREFIX + WINDOW_ACTIONS[action]]
            recorded_as[action] = (
                others,
                others[0][len(PREFIX) :] if others and others[0].startswith(PREFIX) else None,
            )
        # "/" may open the chat line whatever it is bound to, and a chat line takes every key after
        # it as text, so whatever was recorded as "question" goes last.
        order = sorted(round_, key=lambda a: recorded_as[a][1] == "question")
        print("\nThe keys bound by hand:")
        for done, action in enumerate(order):
            key = round_[action]
            others, recorded = recorded_as[action]
            try:
                if key == MODIFIER_KEY:
                    results["modifier"] = await check_modifier(screen, windows, action, recorded)
                    continue
                if recorded is None:
                    keys[key] = {"name": None, "note": f"not bound to {action} ({others or 'no second key'})"}
                    print(f"  {key:<30} not bound to {action}")
                    continue
                if action not in windows:
                    print(f"  {key:<30} recorded as {display(recorded)}; untested, {action} opened nothing")
                    continue
                codes = candidates_for(recorded)
                outcome = {f"0x{vk:02X}": await try_key(screen, vk, windows, action) for vk in codes}
            except Stuck:
                print(f"  {key:<30} recorded as {display(recorded or '?')}: STUCK, the client did not come back")
                # This key and every one after it are left unanswered, earlier answers for them included.
                for later in order[done:]:
                    keys.pop(round_[later], None)
                print("\nStopped. Close what is open in the client, then `run --redo` for the rest of this round.")
                break
            keys[key] = {"name": recorded, "candidates": outcome}
            tried = ", ".join(f"{code} {verdict}" for code, verdict in outcome.items()) or "no codes known for it"
            print(f"  {key:<30} recorded as {display(recorded):<14} {tried}")

    results["last_round"] = round_
    save(results)
    left = next_round(results)
    print(f"\nSaved to {RESULTS.name}.", "Run `plan` for the next round." if left else "Every key is answered.")
    return 0


async def check_modifier(screen: Screen, windows: dict, action: str, recorded: str | None) -> dict:
    """Ctrl+0 on the Character page, sent three ways; bare 0 first, which must do nothing."""
    print(f"  {MODIFIER_KEY:<30} recorded as {display(recorded or '(not bound)')}")
    outcome: dict[str, object] = {"recorded": recorded}
    if recorded is None or action not in windows:
        outcome["note"] = "not bound, or the Character page opened nothing"
        return outcome
    outcome["bare 0"] = await try_key(screen, MODIFIER_VK, windows, action)
    for method in MODIFIER_METHODS:
        await press_with_ctrl(screen.client, MODIFIER_VK, method)
        diff = await screen.change()
        hit = bool(diff) and attribute(diff, windows) == action
        outcome[method] = "opens it" if hit else ("something else changed" if diff else "nothing")
        print(f"    by {method:<20} {outcome[method]}")
        if diff:
            await screen.back(ord(WINDOW_ACTIONS[action].upper()) if hit else None)
    print(f"    bare 0 (must do nothing): {outcome['bare 0']}")
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["plan", "run", "report"])
    parser.add_argument("--redo", action="store_true", help="run the last round again, on the same bindings")
    args = parser.parse_args()
    if args.command == "plan":
        return plan()
    if args.command == "report":
        return report()
    return asyncio.run(run(args.redo))


if __name__ == "__main__":
    sys.exit(main())
