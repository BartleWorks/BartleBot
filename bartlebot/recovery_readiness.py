"""Whether each client could actually be recovered, asked before a run rather than after.

Recovery needs two things from a client that dies: an account to relaunch, and a
password to log it back in. A client missing either is not recoverable, and nothing
about a healthy-looking run says so — the run that exposed this lost four clients
overnight and sat paused for five hours (#79).

The check is worth its dialog only because it is quiet when there is nothing wrong.
A warning that fires every run is one that gets clicked past, so this appears solely
when a gap actually exists, at the one moment the user is present to act on it.

Formatting lives here, away from Qt, so the wording is testable. Deciding *which*
clients have gaps is ClientManager's job — it owns the account map.
"""

from __future__ import annotations

NO_ACCOUNT = "not linked to a configured account"
# The client was launched outside BartleBot and its account has never been launched
# from here, so nothing on file identifies it. See bartlebot/account_identity.py.

NO_PASSWORD = "no password saved for '{account}'"
# Identified, but Credential Manager has nothing to log back in with.


def client_label(position: int, wizard_name: str | None) -> str:
    """How to name a client in the warning: ``Player 2 — Luke SkullHunter``.

    The player number leads because it is what the sidebar and game tiles show, so it
    says which window to go look at. The wizard name follows for anyone who knows their
    characters better than their slot order, and is simply left off when the client loop
    has not read it yet — a bare number still identifies the window.
    """
    return f"Player {position} — {wizard_name}" if wizard_name else f"Player {position}"


def warning_text(gaps: list[str], total: int) -> str:
    """The dialog body for *gaps* (already-formatted lines) out of *total* clients.

    Says what will happen rather than what is misconfigured: the consequence is that a
    crash ends the run, which is the part worth weighing before leaving a bot overnight.
    """
    listed = "\n".join(f"• {line}" for line in gaps)
    subject = "This client cannot" if len(gaps) == 1 else "These clients cannot"
    return (
        f"{len(gaps)} of {total} clients cannot be recovered automatically if they crash:\n\n"
        f"{listed}\n\n"
        f"{subject} be relaunched, so a crash will leave the bot paused until you return.\n\n"
        "Launching an account from BartleBot once is enough for it to be recognised from "
        "then on, however it is started.\n\n"
        "Start the run anyway?"
    )
