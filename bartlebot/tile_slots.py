"""Which game window sits in which grid position, and what its caption says.

WHY THERE ARE TWO NUMBERS (#112 follow-up)

A window can be embedded long before a character is in game -- embedding is Win32
``SetParent`` and ``ClientTile`` needs an hwnd and nothing else -- and that is the whole
point of embedding early: with ``auto_select_character`` off, every launched window lands
in the game view at character select, so the user picks characters in one place instead of
hunting floating windows around the desktop.

Doing that forces apart two things BartleBot used to treat as one number:

    slot    a position in ``ClientManager._client_order``. Pinned by ``reserve_slots``
            before any window exists, and held for an account for as long as it is
            running. It decides *where* a tile sits in the grid, and it does not move
            when a sibling finishes loading.

    player  a position in ``ClientManager.clients`` -- the compacted list of *hooked*
            clients that the FSM routes to and the sidebar numbers. It decides what the
            tile is *called*.

They agree exactly when every account in the ordering is in game, which is the steady
state and therefore the only state anyone had looked at. They diverge whenever a window is
still at character select, has crashed, or has been unhooked by hand.

WHY NOT ONE NUMBER

Making tiles use the player index is what BartleBot did, and it is why tiles shuffled
during a launch: the player index is a position in a list that is compacted as clients
hook, so with a reserved order of A, B, C and B hooking first, B is player 0 and takes the
first tile -- then A arrives, B becomes player 1, and B's game window is physically
re-parented into the next tile along. Harmless-looking with auto-select on, because it
hides inside the 3-15 s arrival scramble. Unmissable with it off, where four tiles sit
still at character select and reorder under the user's cursor as each one loads in.

Making *clients* use the slot index is the other direction and is worse: bot nodes address
players by position in a contiguous list, and a bot cannot route a keypress to a window
sitting at character select. A hole in that list is not expressible.

So: slots decide position, players decide captions, and a window that is not in game has
no player index at all. That last part is the honest answer rather than a fallback -- the
bot cannot address it, so it is not Player anything yet, and its tile says which *account*
it is instead.

WHEN SOMETHING LOOKS WRONG

Tile position wrong, or a tile in the wrong grid cell  ->  slot, so ``_client_order``
and ``build_tile_slots``. Caption wrong, or a name on the wrong tile  ->  player, so
``ClientManager.clients`` and whatever compacted it. A window that flashes or pops out of
the grid  ->  ``plan_tile_changes``, which exists to make a slot change a re-key rather
than a re-embed.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass

# How a tile is identified across syncs: an hwnd for a window, an account for a pending
# launch. See ``TileSlot.key``.
TileKey = tuple[str, int | str]


@dataclass(frozen=True)
class TileSlot:
    """One tile that should be on screen, and everything the view needs to show it.

    Usually a window. Not always: an account whose launch has started but whose window has
    not appeared yet is a tile with no ``hwnd``, drawn as a pending tile so the launch has
    a visible shape from the first click (#186).
    """

    slot: int
    # The game window, or None for an account that is still launching.
    hwnd: int | None
    label: str
    # Index into the live client list, or None for a window that is not in game. See the
    # module docstring: None is a real answer, not a missing one.
    player: int | None
    # The account this tile belongs to, where one is known. A client BartleBot did not
    # launch has none; a pending tile is nothing *but* this.
    username: str | None = None
    # What a pending tile says it is doing, and the monotonic time it started doing it.
    # Only a pending tile has either: a window on screen speaks for itself.
    status: str | None = None
    status_since: float | None = None

    @property
    def key(self) -> TileKey:
        """What identifies this tile across syncs, for ``plan_tile_changes`` to match on.

        The hwnd was this identity until pending tiles existed, and it cannot be any more:
        four accounts launching together have four tiles and no hwnd between them, so
        matching on it collapses the batch into one tile. A pending tile is identified by
        its account instead, which is the only thing it has and is stable for exactly as
        long as the tile is pending.

        The two spaces are kept apart by the tag rather than merged, so the arrival of a
        window is a remove-and-add and not a move. That is the right shape here: the two
        are different widget kinds, and the pending one has no game window to flash.
        """
        return ("hwnd", self.hwnd) if self.hwnd is not None else ("pending", self.username or "")

    @property
    def is_pending(self) -> bool:
        """Whether this tile is an account still launching rather than a live window."""
        return self.hwnd is None


@dataclass(frozen=True)
class TileChanges:
    """What the view has to do to match a wanted set of tiles.

    Three lists rather than "rebuild": a window whose slot changed must be *re-keyed*,
    never destroyed and rebuilt, because rebuilding a tile un-parents the game window and
    parents it again, which flashes. Applying a reorder used to be a separate code path
    (``renumber_tiles``) for exactly that reason; folding it in here is what lets one call
    handle a launch, a crash, a recovery and a player swap alike.
    """

    removed: tuple[int, ...]  # slots whose tile must go
    moved: tuple[tuple[int, int], ...]  # (old slot, new slot) — re-key, do not re-embed
    added: tuple[TileSlot, ...]  # windows with no tile yet

    @property
    def is_empty(self) -> bool:
        """Whether nothing at all changed, so the caller can skip the work and the signals."""
        return not (self.removed or self.moved or self.added)


def build_tile_slots(
    client_order: Sequence[str | None],
    hwnd_accounts: Mapping[int, str],
    embeddable_hwnds: Collection[int],
    client_hwnds: Sequence[int],
    account_label: Callable[[str], str],
    launching: Collection[str] | Mapping[str, object] = (),
) -> list[TileSlot]:
    """Every tile that should be on screen, in slot order.

    *client_order* is the reserved slot ordering, one username per position and None for a
    slot being held. *hwnd_accounts* maps a window to the account it logged in as, which
    comes from the window title and is therefore known well before hooks. *embeddable_hwnds*
    is every window that may be shown -- hooked or not, but excluding ones deliberately
    unhooked or whose hooks failed. *client_hwnds* is the live client list, in order, and
    is the only thing that decides a player index. *launching* maps each account with a
    launch running to its ``LaunchAttempt`` (see ``bartlebot.launch_tracker``); any
    collection of usernames works too, and then the pending tiles carry no stage caption.

    A window with no account is skipped unless it is already a live client. Nothing else
    can place it: an untitled window is one that has not logged in yet, and guessing a slot
    for it means moving its tile once the title arrives -- the shuffle this whole module
    exists to avoid. A client BartleBot did not launch has no account either, but it *is*
    hooked, so it goes in a trailing slot past the reserved ordering.

    A reserved slot whose account is *launching* and has no window yet gets a pending tile
    rather than being skipped (#186). That is the same branch either way -- the reservation
    already named the account and the slot before the first process was spawned, which is
    exactly what the launch screen was failing to show. The launch has to be running for it
    to be drawn: a reservation outlives the launch that made it and would otherwise leave a
    tile on screen for every account that ever failed to come up.
    """
    live = set(embeddable_hwnds)
    player_of = {hwnd: index for index, hwnd in enumerate(client_hwnds)}
    # A mapping gives stage captions as well as membership; a bare collection gives only
    # membership, which is all the tile strictly needs in order to exist.
    attempts = launching if isinstance(launching, Mapping) else {}
    in_flight = set(launching)

    tiles: list[TileSlot] = []
    placed: set[int] = set()
    for slot, username in enumerate(client_order):
        if username is None:
            continue
        hwnd = next((h for h, u in hwnd_accounts.items() if u == username and h in live), None)
        if hwnd is None:
            if username in in_flight:
                attempt = attempts.get(username)
                tiles.append(
                    TileSlot(
                        slot=slot,
                        hwnd=None,
                        label=account_label(username),
                        player=None,
                        username=username,
                        status=None if attempt is None else attempt.stage.value,
                        status_since=None if attempt is None else attempt.stage_since,
                    )
                )
            continue
        if hwnd in placed:
            continue
        placed.add(hwnd)
        player = player_of.get(hwnd)
        label = f"Player {player + 1}" if player is not None else account_label(username)
        tiles.append(TileSlot(slot=slot, hwnd=hwnd, label=label, player=player, username=username))

    trailing = len(client_order)
    for hwnd in client_hwnds:
        if hwnd in placed or hwnd not in live:
            continue
        placed.add(hwnd)
        player = player_of[hwnd]
        tiles.append(TileSlot(slot=trailing, hwnd=hwnd, label=f"Player {player + 1}", player=player, username=None))
        trailing += 1

    return tiles


def plan_tile_changes(current: Mapping[int, TileKey], wanted: Sequence[TileSlot]) -> TileChanges:
    """What to remove, move and add to turn *current* (slot -> tile key) into *wanted*.

    Matching is by ``TileSlot.key``, never by slot: that is what makes a window changing
    slot a move rather than a remove-and-add. The key was the bare hwnd until pending tiles
    existed and had none -- see ``TileSlot.key`` for why they cannot share that space.
    Removals are reported before moves so a caller applying them in order never has two
    tiles claiming one slot.
    """
    slot_of = {key: slot for slot, key in current.items()}
    wanted_keys = {tile.key for tile in wanted}

    removed = tuple(sorted(slot for slot, key in current.items() if key not in wanted_keys))
    moved: list[tuple[int, int]] = []
    added: list[TileSlot] = []
    for tile in sorted(wanted, key=lambda t: t.slot):
        old = slot_of.get(tile.key)
        if old is None:
            added.append(tile)
        elif old != tile.slot:
            moved.append((old, tile.slot))

    return TileChanges(removed=removed, moved=tuple(moved), added=tuple(added))
