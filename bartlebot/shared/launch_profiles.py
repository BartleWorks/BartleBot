"""Building a launch profile, for every surface and product that makes one (epic #215, Stage 4).

A launch profile is ``{name, accounts: [username, ...], resolution?}`` in ``accounts.json``
(:mod:`bartlebot.shared.account_store`). Settings › Game makes one in its editor, and
setup's Launch profile page makes one from a checklist; both build and add it here, so the
record's shape and the refusal of a name already taken are written once.

No Qt and nothing from the rest of ``bartlebot``: BartleBuddy's setup asks the same page.

The page is answered once any launch profile exists, made by either product, or once it
was answered with fewer than two accounts checked: :data:`ANSWERED_KEY` keeps that user
from being asked again. Never "fewer than two accounts", which is whether the page
*applies*: a first run that adds the accounts on the page before would never reach it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bartlebot.shared.account_store import AccountStore
    from bartlebot.shared.preferences import PreferenceStore

#: The name setup gives the profile it makes, or the first free one after it.
EVERYONE = "Everyone"

#: Set once the Launch profile page has been answered, whether or not it made a profile.
ANSWERED_KEY = "setup.launch_profile_answered"


def launch_profile_answered(prefs: PreferenceStore, store: AccountStore) -> bool:
    """Whether the Launch profile page needs no asking: a profile exists, or the page was answered."""
    return bool(store.launch_profiles) or bool(prefs.get(ANSWERED_KEY))


def record_launch_profile_answered(prefs: PreferenceStore) -> bool:
    """Record the Launch profile page answered. Whether it reached disk."""
    return prefs.set(ANSWERED_KEY, True)


def matching_profile(store: AccountStore, usernames: Sequence[str]) -> str | None:
    """The name of a profile launching exactly *usernames*, in that order, if one exists.

    So a rerun of setup, or Next tried again after a failed write, finds the profile it
    made rather than adding "Everyone 2" beside it.
    """
    wanted = list(usernames)
    return next((p.get("name", "") for p in store.launch_profiles if p.get("accounts") == wanted), None)


def _first_free(taken: Iterable[str], first: str, numbered: Callable[[int], str]) -> str:
    """*first*, or ``numbered(2)``, ``numbered(3)``... whichever *taken* lacks first."""
    taken = set(taken)
    candidate, n = first, 2
    while candidate in taken:
        candidate = numbered(n)
        n += 1
    return candidate


def free_name(name: str, taken: Iterable[str]) -> str:
    """*name*, or the first of "Name 2", "Name 3"... that *taken* lacks: a new profile's name."""
    return _first_free(taken, name, lambda n: f"{name} {n}")


def copy_name(base: str, taken: Iterable[str]) -> str:
    """The first of "Foo (copy)", "Foo (copy 2)", "Foo (copy 3)"... that *taken* lacks.

    Counting from 2 on the second copy rather than 1 on the first, so duplicating once
    reads "Farm Team (copy)" and not "Farm Team (copy 1)" -- the numberless form is the
    common case and should be the one that looks unremarkable.
    """
    return _first_free(taken, f"{base} (copy)", lambda n: f"{base} (copy {n})")


def profile_names(store: AccountStore) -> list[str]:
    return [profile.get("name", "") for profile in store.launch_profiles]


def build_profile(name: str, usernames: Sequence[str], resolution: Sequence[int] | None = None) -> dict:
    """The stored record: the accounts in player order, and a resolution only when one overrides the global."""
    profile: dict = {"name": name, "accounts": list(usernames)}
    if resolution is not None:
        profile["resolution"] = list(resolution)
    return profile


def add_profile(store: AccountStore, profile: dict) -> bool:
    """Append *profile* to the stored launch profiles. Whether it reached disk.

    Refused, writing nothing, when a profile already has its name: names are how
    ``accounts.json`` tells two products' profiles apart.
    """
    if profile["name"] in profile_names(store):
        return False
    return store.set_launch_profiles([*store.launch_profiles, profile])
