"""Diagnostic: does ``<LastPlayer>`` in config.xml decide which character a launch brings up?

RESULT: NO. THE SERVER DECIDES. (2026-08-31)

Run against a six-character account, the patch took in the file and the game ignored it:
character select still preselected the character that had most recently *played*, not the
one config.xml named. LastPlayer is a client-side cache of a value the login server sends
back, so writing it changes nothing a launch can see.

That closes #112. There is no way to choose a character at launch -- not through the file,
not through the UI (nothing per-character to click), not through memory (the selection is
re-derived server-side). A launch gets the most recently played character or nothing.

What follows from it is the ``auto_select_character`` setting: on, the client is driven
straight in on that character; off, it is left at character select for the user to choose.
Making that choice pleasant is an *embedding* problem, not a launch one -- the window has
to reach the game view before a character is in game.

The probe is kept because the answer is negative and expensive to re-derive: a future game
patch or a login-flow change could make LastPlayer authoritative again, and this re-checks
it in one run. It is also the only tool that lists the stored rosters (``--show``).

WHY IT EXISTED

The launch cards can say which characters live on an account (``bartlebot.character_roster``)
but not *pick* one. ``tools/probe_character_gid.py`` documents why the obvious routes are
dead ends -- there is nothing per-character to click at character select, and names are not
unique -- and names the one remaining lever (#112):

    <LastPlayer TYPE="GID">12345678901234567</LastPlayer>   in Bin/config.xml

Character select preselects whoever that names, and ``auto_select`` presses Enter, so if
the game honours a value *we* wrote then choosing a character costs one config patch and
no change to the drive at all.

The catch is that there is one config.xml for the whole install. A per-account choice
therefore has to patch the file and start that instance before the next account's patch
lands, which serializes the spawn step ``launch_accounts`` deliberately batches. That is a
real cost for a *profile* and no cost at all for a single **account card**, which starts
one instance anyway -- so the experiment is worth running even if the answer only ever
reaches the account cards.

WHAT IT ANSWERS

    1. Is the roster gid we stored the same kind of id the game writes to LastPlayer?
       (``--show`` alone, with a client that recently played a known character.)
    2. Does a launch preselect the character we asked for? The banner name at character
       select is read before anything is pressed, so the verdict lands without playing.
    3. Does Play then load that character? ``--play`` presses Enter and compares
       ``client_object.character_id()`` against the gid that was requested.

HOW TO RUN

    CLOSE BARTLEBOT FIRST. This probe builds its own Client and activates hooks. Two
    Client objects over one game process mean two _prepare_autobot calls, and the second
    zeroes the scratch region the first one's hooks jump into -- corrupting live hooks in
    the game process. See the note in launcher/auto_select.py.

    poetry run python tools/probe_last_player.py --show
    poetry run python tools/probe_last_player.py --account <username> --character 2
    poetry run python tools/probe_last_player.py --account <username> --character 2 --play

    The account has to have been played through BartleBot at least twice, on two different
    characters -- the roster is only learned from clients that connect, and a one-character
    roster cannot tell a honoured patch from a coincidence.

    config.xml's original LastPlayer is restored on the way out unless --keep is passed.

NO REAL PLAYER DATA: gids are masked the way ``bartlebot.account_identity`` masks a user
id; --with-ids prints them in full. Wizard names are printed unmasked because choosing a
character is the point of the listing -- do not paste this output into an issue. See the
"No real player data" section of CLAUDE.md.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys

from wizwalker.extensions.wizsprinter import WizSprinter
from wizwalker.utils import (
    get_all_wizard_handles,
    get_window_title,
    instance_login,
    patch_open_browser,
    set_window_title,
)

from bartlebot import character_roster
from bartlebot.account_identity import mask
from bartlebot.credentials import CredentialStore
from bartlebot.launcher import auto_select, launch_profile
from bartlebot.nodes.node_helpers import get_text_by_path
from bartlebot.nodes.paths import CHAR_SELECT_PATH
from bartlebot.settings_manager import SettingsManager

# The name shown on the character-select banner for the preselected wizard. Not in
# nodes/paths.py: nothing in the app reads it, and it is only trustworthy on this screen.
CHAR_SELECT_NAME_PATH = [*CHAR_SELECT_PATH, "sprBanner", "txtName"]

# How long to wait for the launched window to reach character select, and how often to
# look. Generous: a cold start plus two loading screens is the slow case here.
CHAR_SELECT_TIMEOUT = 180.0
CHAR_SELECT_POLL = 0.5

# How long to wait for hooks after Play, i.e. for a character to be in a game zone.
IN_GAME_TIMEOUT = 180.0


def _gid(value: int | None, with_ids: bool) -> str:
    if value is None:
        return "?"
    return str(value) if with_ids else mask(value)


def _accounts() -> list[dict]:
    return SettingsManager.instance().accounts


def show(with_ids: bool) -> None:
    """Print the stored roster for every account, beside the current LastPlayer."""
    current = launch_profile.read_last_player()
    print(f"config.xml LastPlayer: {_gid(current, with_ids)}")
    accounts = _accounts()
    if not accounts:
        print("No accounts configured. Add one in Settings -> Accounts.")
        return
    for account in accounts:
        username = account.get("username", "")
        display = CredentialStore.display_name(username, account.get("alias") or None)
        roster = character_roster.load(username)
        print(f"\n{display}  ({username})")
        if not roster:
            print("    no characters known yet - play one through BartleBot to learn it")
            continue
        for index, entry in enumerate(roster, start=1):
            marker = "  <- LastPlayer" if entry["gid"] == current else ""
            print(f"    {index}. {entry['name']}  gid={_gid(entry['gid'], with_ids)}{marker}")


def _resolve_target(username: str, character: int | None, gid: int | None) -> dict | None:
    """The roster entry to launch, or None with a reason already printed."""
    roster = character_roster.load(username)
    if not roster:
        print(f"No characters known for '{username}'. Run --show.", file=sys.stderr)
        return None
    if gid is not None:
        entry = character_roster.find(roster, gid)
        if entry is None:
            print(f"gid not in the roster for '{username}'. Run --show.", file=sys.stderr)
        return entry
    if character is None or not 1 <= character <= len(roster):
        print(f"--character must be between 1 and {len(roster)} for '{username}'.", file=sys.stderr)
        return None
    return roster[character - 1]


def _client_for(sprinter: WizSprinter, hwnd: int):
    for client in sprinter.get_new_clients():
        if client.window_handle == hwnd:
            return client
    for client in sprinter.clients:
        if client.window_handle == hwnd:
            return client
    return None


async def _wait_for(predicate, timeout: float, poll: float) -> bool:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        with contextlib.suppress(Exception):
            if await predicate():
                return True
        await asyncio.sleep(poll)
    return False


async def _launch(username: str, password: str, display_name: str) -> int:
    """Start one instance, log it in, title it, and return its handle.

    Deliberately not ``launch_profile.launch_account``: that patches config.xml itself,
    which would overwrite the LastPlayer value this probe just wrote.
    """
    patch_open_browser()
    known = set(get_all_wizard_handles())
    handle = await launch_profile._start_and_claim_handle(known)
    await launch_profile._wait_for_login_screen(handle)
    instance_login(handle, username, password)
    set_window_title(handle, f"[{display_name}] Wizard101")
    return handle


async def probe(args) -> None:
    username = args.account
    account = next((a for a in _accounts() if a.get("username") == username), None)
    if account is None:
        print(f"No account '{username}' in settings.", file=sys.stderr)
        return
    target = _resolve_target(username, args.character, args.gid)
    if target is None:
        return
    password = CredentialStore.retrieve(username)
    if password is None:
        print(f"No stored password for '{username}'.", file=sys.stderr)
        return
    display_name = CredentialStore.display_name(username, account.get("alias") or None)

    previous = launch_profile.read_last_player()
    print(f"Current LastPlayer: {_gid(previous, args.with_ids)}")
    if previous is not None and previous == target["gid"]:
        print("WARNING: that is already the last played character - this run proves nothing.")
        print("         Pick a different character, or play another one first.")

    print(f"Requesting: {target['name']}  gid={_gid(target['gid'], args.with_ids)}")
    launch_profile.patch_last_player(target["gid"])
    written = launch_profile.read_last_player()
    if written != target["gid"]:
        print(f"The patch did not take - config.xml still reads {_gid(written, args.with_ids)}.", file=sys.stderr)
        return

    try:
        handle = await _launch(username, password, display_name)
        print(f"Launched hwnd={handle} title={get_window_title(handle)!r}")

        sprinter = WizSprinter()
        client = _client_for(sprinter, handle)
        if client is None:
            print("Could not build a client for the new window.", file=sys.stderr)
            return
        # Hooks are installed up front and only the player struct waits for a character,
        # so the UI tree becomes readable at character select - see auto_select's docstring.
        activation = asyncio.create_task(client.activate_hooks())

        print("Waiting for character select...")
        if not await _wait_for(lambda: auto_select.at_character_select(client), CHAR_SELECT_TIMEOUT, CHAR_SELECT_POLL):
            print("Never reached character select.", file=sys.stderr)
            activation.cancel()
            return

        # THE VERDICT. Read before anything is pressed: the banner names the preselected
        # wizard, which is exactly what LastPlayer is claimed to decide.
        banner = ""
        with contextlib.suppress(Exception):
            banner = (await get_text_by_path(client, CHAR_SELECT_NAME_PATH)).strip()
        print("")
        print(f"Preselected at character select: {banner or '(could not read the banner)'}")
        print(f"Requested:                       {target['name']}")
        if banner and banner == target["name"]:
            print("-> LastPlayer was honoured.")
        elif banner:
            print("-> NOT honoured, or the account has two wizards sharing this name.")
            print("   Names are not unique; --play settles it by gid.")

        if not args.play:
            print("\nLeaving the window at character select. Pass --play to press Play and")
            print("confirm by character id.")
            activation.cancel()
            return

        print("\nPressing Play...")
        await auto_select.press_character_select_play(handle)
        try:
            await asyncio.wait_for(activation, timeout=IN_GAME_TIMEOUT)
        except TimeoutError:
            print("Hooks never came up - the character did not reach a game zone.", file=sys.stderr)
            return
        loaded = await client.client_object.character_id()
        print(f"Loaded in as gid {_gid(loaded, args.with_ids)}")
        print("-> MATCH" if loaded == target["gid"] else "-> MISMATCH: the game ignored the patch.")
    finally:
        if previous is not None and not args.keep:
            launch_profile.patch_last_player(previous)
            print(f"\nRestored LastPlayer to {_gid(previous, args.with_ids)}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--show", action="store_true", help="List accounts, their rosters, and the current LastPlayer.")
    parser.add_argument("--account", help="Username of the account to launch.")
    parser.add_argument("--character", type=int, help="1-based position in that account's roster (see --show).")
    parser.add_argument("--gid", type=int, help="Character gid, instead of --character.")
    parser.add_argument("--play", action="store_true", help="Also press Play and confirm by character id.")
    parser.add_argument("--keep", action="store_true", help="Leave the patched LastPlayer in place on exit.")
    parser.add_argument("--with-ids", action="store_true", help="Print gids in full rather than masked.")
    args = parser.parse_args()

    if args.show or not args.account:
        show(args.with_ids)
        if not args.account:
            return
    try:
        asyncio.run(probe(args))
    except KeyboardInterrupt:
        print("\naborted")


if __name__ == "__main__":
    main()
