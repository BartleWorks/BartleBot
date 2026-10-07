"""What the Deck Presets page may ask of the game clients, handed in by whoever owns them.

The combat dialog knows nothing about clients (``dialog.py`` says so), and the Combat tab
that opens it reaches them only through ``AppController``, which owns the deck job: one at
a time, because two on one client drive the same deck window at once. So the page is given
three callables and never a controller.

Both verbs start a job and answer later, through *done*, because a deck job is a task on
the event loop and the dialog is modal. They return False when nothing started: no client
in that slot, or a deck job already running. Then *done* is never called, and the
controller has already told the user why.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from bartlebot.bot.strategy_models import DeckData


@dataclass(frozen=True)
class DeckClientLink:
    #: How many clients are hooked; slots 1..N have one.
    client_count: Callable[[], int]
    #: Read the deck of the client in a slot. *done* gets the deck, or the reason it failed.
    import_deck: Callable[[int, Callable[[DeckData | str], None]], bool]
    #: Put a deck on the client in a slot. *done* gets None, or the reason it failed.
    apply_deck: Callable[[int, DeckData, Callable[[str | None], None]], bool]
