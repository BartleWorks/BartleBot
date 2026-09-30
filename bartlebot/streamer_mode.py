"""Hide wizard names in the UI, without hiding which wizard is which.

The Quick Launch screen and the Players sidebar are the two most screenshotted parts of
this app -- a bug report, a tutorial video, a stream. Per the "No real player data"
section of CLAUDE.md a wizard name links an account to whoever posted the image, and
that rule cannot stop at files we commit: a screenshot is a way of publishing one.

So: a setting that replaces every displayed name with a positional placeholder.
"Streamer Mode" because that is what the same setting is called everywhere else it
exists, and someone looking for it will look for that.

WHY POSITIONAL, AND NOT A MASK

``account_identity.mask()`` is the precedent for a redacted-but-stable rendering, and it
is the right answer for a *user id* -- a bounded integer nobody reads, where the last
four digits only ever serve to correlate two log lines. A wizard name is not that. The
tail of one is still a fragment of a real name, and the whole reason the name is on
screen is that a person is choosing between characters with it. So the placeholder is
positional: it distinguishes the entries from each other, which is the job, and carries
nothing of the name, which is the point.

The number is the character's position within whatever list it is shown in -- a client
slot in the sidebar, a roster index on an account card -- so it is stable for as long as
that list is, and the same wizard reads the same way each time you look at it.

WHERE IT APPLIES

Masking happens at *display* time, never at the source. ``wizard_names`` keeps the real
name, group formation keeps using it, and only the last step before a QLabel goes
through here. Masking at the source would mean a toggle could not take effect without
re-detecting every name, and would put the setting in the path of a bot's own logic --
which is the one place a wrong answer breaks a run rather than a screenshot.

Logs need none of this: they never contain a wizard name in the first place.
"""

from __future__ import annotations

from bartlebot.settings_manager import SettingsManager


def enabled() -> bool:
    """Whether names should be hidden right now."""
    return bool(SettingsManager.instance().streamer_mode)


def display_name(name: str | None, position: int, *, placeholder: str = "—") -> str:
    """The name to show for the character at *position*, honoring Streamer Mode.

    *position* is zero-based; the placeholder counts from one, because it is read by
    people. *placeholder* is what to show when the name is not known yet -- that case is
    not a privacy question, so it answers the same either way.

    Pure, and takes the name rather than a client, so it is callable from anywhere a
    label is built and testable without Qt or a game process.
    """
    if not name:
        return placeholder
    if enabled():
        return f"Character {position + 1}"
    return name
