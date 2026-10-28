"""Hook one running client with wizwalker and read through every hook and offset group a bot uses.

The first run against a patched client, before a bot: a bot that fails says only that
something did, and this says which piece. Each check prints OK or FAIL with what it read.
Nothing identifying is printed (no wizard name, no character or account id): only whether
an id was read, and game facts such as the zone, the position and the stats.

WHAT IT CHECKS, AND THROUGH WHAT
    player position          PlayerHook
    stats                    PlayerStatHook, and GameStats' offsets
    quest arrow position     QuestHook (written only while the quest arrow is on)
    client object            ClientHook, and ClientObject's offsets, CoreObject's scale among them
    root window              RootWindowHook
    UI scale                 RenderContextHook
    teleport to where it is  MovementTeleportHook and its collision jes: moves nobody
    drops off, then on       DropsToggleHook
    mouseless cursor         the user32 cave MouselessCursorMoveHook uses
    in a duel (--in-duel)    CombatParticipant's offsets, for every participant

Close BartleBot first: two sets of hooks in one client corrupt each other (``tools/README.md``,
*On live clients*). Run it with exactly one client open, in the world, and with the wizwalker
to test installed (``bartleby-core/docs/GAME_UPDATES.md``, *Running against the update*).

USAGE
    uv run --no-sync python tools/game_update/smoke_test_client.py
    uv run --no-sync python tools/game_update/smoke_test_client.py --in-duel     # while in a fight
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOOK_TIMEOUT = 20.0
STEP_TIMEOUT = 10.0


class Report:
    def __init__(self) -> None:
        self.failed: list[str] = []

    async def check(self, label: str, read, accept=lambda value: True) -> object:
        try:
            value = await asyncio.wait_for(read(), STEP_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - every failure is a finding
            self.failed.append(label)
            print(f"  FAIL  {label}: {type(exc).__name__}: {exc}")
            traceback.print_exc(limit=2, file=sys.stdout)
            return None
        ok = accept(value)
        if not ok:
            self.failed.append(label)
        print(f"  {'OK  ' if ok else 'FAIL'}  {label}: {value}")
        return value


def _plausible_xyz(xyz) -> bool:
    return (
        xyz is not None
        and any(abs(c) > 0.001 for c in (xyz.x, xyz.y, xyz.z))
        and all(abs(c) < 1e7 for c in (xyz.x, xyz.y, xyz.z))
    )


async def run(in_duel: bool) -> int:
    from wizwalker import ClientHandler

    report = Report()
    async with ClientHandler() as handler:
        clients = handler.get_new_clients()
        if len(clients) != 1:
            print(f"found {len(clients)} clients; open exactly one")
            return 2
        client = clients[0]
        print(f"client pid {client.process_id}")

        print("\nhooks")
        await report.check(
            "activate_all_hooks",
            lambda: client.hook_handler.activate_all_hooks(wait_for_ready=True, timeout=HOOK_TIMEOUT),
        )

        print("\nreads")
        await report.check("zone", client.zone_name, lambda z: bool(z))
        start = await report.check("player position (PlayerHook)", client.body.position, _plausible_xyz)
        await report.check(
            "health / max (PlayerStatHook)",
            lambda: _pair(client.stats.current_hitpoints, client.stats.max_hitpoints),
            lambda v: 0 < v[0] <= v[1],
        )
        await report.check(
            "mana / max",
            lambda: _pair(client.stats.current_mana, client.stats.max_mana),
            lambda v: 0 <= v[0] <= v[1],
        )
        await report.check("level", client.stats.reference_level, lambda lvl: 1 <= lvl <= 200)
        await report.check("quest arrow (QuestHook)", client.quest_position.position, _plausible_xyz)
        await report.check(
            "global id read (ClientHook)", lambda: _nonzero(client.client_object.global_id_full), lambda v: v
        )
        await report.check(
            "character id read (ClientObject 0x1C0)", lambda: _nonzero(client.client_object.character_id), lambda v: v
        )
        await report.check(
            "object scale (CoreObject.m_fScale, moved in r811339)",
            client.client_object.scale,
            lambda s: 0.1 < s < 10,
        )
        await report.check(
            "root window children (RootWindowHook)", lambda: _count(client.root_window.children), lambda n: n > 0
        )
        await report.check("UI scale (RenderContextHook)", client.render_context.ui_scale, lambda s: 0.1 < s < 10)

        print("\nwrites")
        if start is not None:
            await report.check("teleport to where it stands (MovementTeleportHook)", lambda: client.teleport(start))
            await report.check(
                "still where it stood",
                client.body.position,
                lambda xyz: xyz is not None and abs(xyz.x - start.x) < 5 and abs(xyz.y - start.y) < 5,
            )
        await report.check("drops off (DropsToggleHook)", client.disable_drops)
        await report.check("drops on", client.enable_drops)
        await report.check("mouseless cursor hook", client.hook_handler.activate_mouseless_cursor_hook)
        await report.check("mouseless cursor unhook", client.hook_handler.deactivate_mouseless_cursor_hook)

        if in_duel:
            print("\nduel")
            await report.check("in battle", client.in_battle, lambda b: b is True)
            parts = await report.check("participants", client.duel.participant_list, lambda ps: bool(ps))
            for i, part in enumerate(parts or []):
                await report.check(
                    f"participant {i}: health / max (CombatParticipant, moved in r811339)",
                    lambda p=part: _pair(p.player_health, p.max_player_health),
                    lambda v: 0 <= v[0] <= v[1] and v[1] > 0,
                )

    print(f"\n{'all checks passed' if not report.failed else 'failed: ' + ', '.join(report.failed)}")
    return 1 if report.failed else 0


async def _pair(a, b):
    return await a(), await b()


async def _nonzero(read) -> bool:
    return bool(await read())


async def _count(read) -> int:
    return len(await read())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--in-duel", action="store_true", help="also read every duel participant; run it in a fight")
    args = ap.parse_args()
    return asyncio.run(run(args.in_duel))


if __name__ == "__main__":
    sys.exit(main())
