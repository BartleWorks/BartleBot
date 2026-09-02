"""One-time migration: extract BotCombatProfile / DuelProfile / CombatBehavior data from
bot JSON files and write them out as PlayerCombatStrategy, DuelCombatStrategy, and
BotCombatStrategy files under strategies/ and bot_strategies/.

Also renames use_default_duel_profile → use_default_strategy in every handle_combat
state's params, and strips the old combat fields from BotConfig.

Run from the project root:
    poetry run python scripts/migrate_combat_profiles.py

The script is idempotent: re-running it after migration is a no-op for bots that
have already had their legacy fields removed.

Reference commit for old default_combat_config values: 5edbd5879a67439eddcb76848e6a2d76ac154da7

**Its idempotency check no longer detects an unmigrated bot.** Deriving option
declarations (#159) put every bot file through ``BotConfig.save`` once, which
drops any key the model does not carry -- so the legacy fields this script looks
for are gone from ``bots/`` whether or not the bot was ever migrated. Thirteen
files still had a ``default_combat_config`` and no ``bot_strategies/`` entry
behind it at that point; their DSL is unchanged at the reference commit above,
which is where ``_read_old_bot_json`` reads it from anyway. To finish that
migration, list the bots from the reference commit rather than from the tree.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# Ensure project root is on sys.path so we can import bartlebot packages.
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bartlebot.bot.strategy_models import (  # noqa: E402
    BotCombatStrategy,
    DeckData,
    DuelCombatStrategy,
    PlayerAssignment,
    PlayerCombatStrategy,
    StateAssignment,
)
from bartlebot.strategy_store import (  # noqa: E402
    save_bot_strategy,
    save_duel_strategy,
    save_player_strategy,
)

REFERENCE_COMMIT = "5edbd5879a67439eddcb76848e6a2d76ac154da7"

# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------
_stats = {"bots": 0, "pcs": 0, "dcs": 0, "bcs": 0, "skipped": 0}


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _read_old_bot_json(relative_path: str) -> dict | None:
    """Read a bot JSON file from the reference commit. Returns None if not found."""
    try:
        result = subprocess.run(
            ["git", "show", f"{REFERENCE_COMMIT}:{relative_path}"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# DSL extraction helpers
# ---------------------------------------------------------------------------


def _parse_per_player_dsl(dsl: str) -> dict[int, str]:
    """Parse a Deimos-style multi-player DSL into a dict of {player_num: dsl}.

    Handles '### p1', '### p2', ... section headers.  If no headers exist the
    full DSL is assigned to player 1.  Player numbers are 1-based.
    """
    import re

    pattern = re.compile(r"###\s*p\s*(\d+)", re.IGNORECASE)
    result: dict[int, str] = {}
    sections: list[tuple[int, list[str]]] = []
    current: tuple[int, list[str]] | None = None

    for line in dsl.splitlines():
        m = pattern.match(line.strip())
        if m:
            if current:
                sections.append(current)
            current = (int(m.group(1)), [])
        else:
            if current is not None:
                current[1].append(line)

    if current:
        sections.append(current)

    if not sections:
        result[1] = dsl.strip()
    else:
        for player_num, lines in sections:
            result[player_num] = "\n".join(lines).strip()

    return result


def _make_pcs(name: str, dsl: str) -> PlayerCombatStrategy:
    pcs = PlayerCombatStrategy(name=name, dsl=dsl.strip())
    return pcs


# ---------------------------------------------------------------------------
# Case A: bot still has default_combat_config (old DSL format)
# ---------------------------------------------------------------------------


def _migrate_case_a(
    bot_id: str,
    bot_name: str,
    current_data: dict,
    old_data: dict | None,
) -> tuple[BotCombatStrategy, list[PlayerCombatStrategy], list[DuelCombatStrategy]]:
    """Migrate a bot that uses the old default_combat_config string."""
    # Prefer the authoritative DSL from the reference commit; fall back to current.
    source = old_data if old_data is not None else current_data
    dsl = source.get("default_combat_config", "").strip()

    pcs_list: list[PlayerCombatStrategy] = []
    player_assignments: list[PlayerAssignment] = []

    if dsl:
        per_player = _parse_per_player_dsl(dsl)
        # Group players with identical DSL into one PCS.
        dsl_to_pcs: dict[str, PlayerCombatStrategy] = {}
        for player_num in sorted(per_player):
            player_dsl = per_player[player_num]
            if player_dsl not in dsl_to_pcs:
                pcs = _make_pcs(f"{bot_name} P{player_num}", player_dsl)
                dsl_to_pcs[player_dsl] = pcs
                pcs_list.append(pcs)
            pa = PlayerAssignment(
                players=[player_num],
                player_combat_strategy_id=dsl_to_pcs[player_dsl].id,
            )
            player_assignments.append(pa)

    dcs = DuelCombatStrategy(
        name=f"{bot_name} default",
        min_players=1,
        max_players=4,
        combat_order=None,
        player_assignments=player_assignments,
    )
    bcs = BotCombatStrategy(
        name=f"{bot_name}",
        bot_id=bot_id,
        default_strategy_id=dcs.id if dsl else None,
    )
    return bcs, pcs_list, [dcs] if dsl else []


# ---------------------------------------------------------------------------
# Case B/C: bot has combat_profiles (new format)
# ---------------------------------------------------------------------------


def _migrate_case_bc(
    bot_id: str,
    bot_name: str,
    current_data: dict,
    old_data: dict | None,
    states: list[dict],
) -> tuple[list[BotCombatStrategy], list[PlayerCombatStrategy], list[DuelCombatStrategy]]:
    """Migrate a bot that has combat_profiles.  Returns all BCSes for this bot."""
    combat_profiles = current_data.get("combat_profiles", [])

    all_bcs: list[BotCombatStrategy] = []
    all_pcs: list[PlayerCombatStrategy] = []
    all_dcs: list[DuelCombatStrategy] = []

    # Build a map of {state_id: duel_profile_name} from handle_combat states.
    state_duel_map: dict[str, str] = {}
    for state in states:
        if state.get("node_type") == "handle_combat":
            dp_name = state.get("params", {}).get("duel_profile_name", "").strip()
            if dp_name and dp_name != "default":
                state_duel_map[state["id"]] = dp_name

    for profile in combat_profiles:
        profile_name = profile.get("name", "Unnamed")
        duel_profiles = profile.get("duel_profiles", {})
        combat_order_raw = profile.get("combat_order", [])

        # Player decks (embedded in BCS).
        player_decks_raw = profile.get("player_decks", {})
        player_decks: dict[int, DeckData] = {}
        for slot_str, deck_data in player_decks_raw.items():
            try:
                slot = int(slot_str)
            except ValueError:
                continue
            preset = deck_data.get("preset") if isinstance(deck_data, dict) else None
            if preset:
                player_decks[slot] = DeckData(
                    normal=preset.get("normal", {}),
                    tc=preset.get("tc", {}),
                    item=preset.get("item", {}),
                )

        # Build DCS per duel profile.
        dp_name_to_dcs: dict[str, DuelCombatStrategy] = {}
        for dp_name, dp in duel_profiles.items():
            behaviors = dp.get("behaviors", [])
            use_default = dp.get("use_bartlebot_default", True)
            order_override = dp.get("combat_order_override")

            pcs_for_dp: list[PlayerCombatStrategy] = []
            assignments: list[PlayerAssignment] = []

            if not use_default and behaviors:
                dsl_to_pcs: dict[str, PlayerCombatStrategy] = {}
                for behavior in behaviors:
                    beh_dsl = behavior.get("dsl", "").strip()
                    beh_players: list[int] = behavior.get("players", [])
                    if not beh_dsl or not beh_players:
                        continue
                    if beh_dsl not in dsl_to_pcs:
                        pcs = _make_pcs(f"{profile_name}/{dp_name} ({','.join(str(p) for p in beh_players)})", beh_dsl)
                        dsl_to_pcs[beh_dsl] = pcs
                        pcs_for_dp.append(pcs)
                    assignments.append(
                        PlayerAssignment(
                            players=beh_players,
                            player_combat_strategy_id=dsl_to_pcs[beh_dsl].id,
                        )
                    )

            combat_order = order_override if order_override is not None else (combat_order_raw or None)
            dcs = DuelCombatStrategy(
                name=f"{profile_name}/{dp_name}" if dp_name != "default" else profile_name,
                min_players=1,
                max_players=4,
                combat_order=combat_order if combat_order else None,
                player_assignments=assignments,
            )
            dp_name_to_dcs[dp_name] = dcs
            all_pcs.extend(pcs_for_dp)
            all_dcs.append(dcs)

        # Build state assignments: which states reference non-default duel profiles?
        duel_name_to_states: dict[str, list[str]] = {}
        for state_id, dp_name in state_duel_map.items():
            duel_name_to_states.setdefault(dp_name, []).append(state_id)

        state_assignments: list[StateAssignment] = []
        for dp_name, state_ids in duel_name_to_states.items():
            if dp_name in dp_name_to_dcs:
                state_assignments.append(
                    StateAssignment(
                        duel_combat_strategy_id=dp_name_to_dcs[dp_name].id,
                        state_ids=state_ids,
                    )
                )

        default_dcs_id = dp_name_to_dcs["default"].id if "default" in dp_name_to_dcs else None

        # Case C: also has old default_combat_config — wrap in a separate default DCS.
        old_dsl = ""
        if old_data:
            old_dsl = old_data.get("default_combat_config", "").strip()
        if not old_dsl:
            old_dsl = current_data.get("default_combat_config", "").strip()

        if old_dsl and default_dcs_id is None:
            per_player = _parse_per_player_dsl(old_dsl)
            dsl_to_pcs2: dict[str, PlayerCombatStrategy] = {}
            assignments2: list[PlayerAssignment] = []
            for player_num in sorted(per_player):
                player_dsl = per_player[player_num]
                if player_dsl not in dsl_to_pcs2:
                    pcs2 = _make_pcs(f"{bot_name} legacy P{player_num}", player_dsl)
                    dsl_to_pcs2[player_dsl] = pcs2
                    all_pcs.append(pcs2)
                assignments2.append(
                    PlayerAssignment(
                        players=[player_num],
                        player_combat_strategy_id=dsl_to_pcs2[player_dsl].id,
                    )
                )
            fallback_dcs = DuelCombatStrategy(
                name=f"{profile_name} fallback",
                min_players=1,
                max_players=4,
                combat_order=None,
                player_assignments=assignments2,
            )
            all_dcs.append(fallback_dcs)
            default_dcs_id = fallback_dcs.id

        bcs = BotCombatStrategy(
            name=profile_name,
            bot_id=bot_id,
            player_decks=player_decks,
            default_strategy_id=default_dcs_id,
            state_assignments=state_assignments,
        )
        all_bcs.append(bcs)

    return all_bcs, all_pcs, all_dcs


# ---------------------------------------------------------------------------
# Per-bot migration
# ---------------------------------------------------------------------------


def _migrate_bot(bot_path: Path) -> None:
    with open(bot_path, encoding="utf-8") as f:
        current_data = json.load(f)

    bot_name = current_data.get("name", bot_path.stem)

    # If old fields are already stripped, this bot has already been migrated.
    legacy_fields = {"default_combat_config", "use_bartlebot_default_combat_config", "combat_profiles"}
    if not any(f in current_data for f in legacy_fields):
        print(f"  [skip] {bot_path.relative_to(PROJECT_ROOT)} (already migrated)")
        _stats["skipped"] += 1
        return

    # Ensure bot has a UUID (it will be auto-generated if not present).
    if "id" not in current_data:
        from uuid import uuid4

        current_data["id"] = str(uuid4())

    bot_id: str = current_data["id"]

    # Try to get the authoritative old DSL from git history.
    rel_path = str(bot_path.relative_to(PROJECT_ROOT)).replace("\\", "/")
    old_data = _read_old_bot_json(rel_path)

    states: list[dict] = current_data.get("states", [])

    combat_profiles = current_data.get("combat_profiles", [])
    has_new_format = bool(combat_profiles)

    all_bcs: list[BotCombatStrategy] = []
    all_pcs: list[PlayerCombatStrategy] = []
    all_dcs: list[DuelCombatStrategy] = []

    if has_new_format:
        # Case B or C
        bcs_list, pcs_list, dcs_list = _migrate_case_bc(bot_id, bot_name, current_data, old_data, states)
        all_bcs.extend(bcs_list)
        all_pcs.extend(pcs_list)
        all_dcs.extend(dcs_list)
    else:
        # Case A: old format only, or Case D (no custom DSL at all)
        bcs, pcs_list, dcs_list = _migrate_case_a(bot_id, bot_name, current_data, old_data)
        all_bcs.append(bcs)
        all_pcs.extend(pcs_list)
        all_dcs.extend(dcs_list)

    # Save new strategy files.
    for pcs in all_pcs:
        save_player_strategy(pcs, root=PROJECT_ROOT)
    for dcs in all_dcs:
        save_duel_strategy(dcs, root=PROJECT_ROOT)
    for bcs in all_bcs:
        save_bot_strategy(bcs, root=PROJECT_ROOT)

    _stats["pcs"] += len(all_pcs)
    _stats["dcs"] += len(all_dcs)
    _stats["bcs"] += len(all_bcs)

    # Rename use_default_duel_profile → use_default_strategy in handle_combat states.
    for state in states:
        if state.get("node_type") == "handle_combat":
            p = state.setdefault("params", {})
            if "use_default_duel_profile" in p:
                p["use_default_strategy"] = p.pop("use_default_duel_profile")
            # Remove the now-unused duel_profile_name key.
            p.pop("duel_profile_name", None)

    # Strip legacy fields from the bot JSON.
    for field in (
        "default_combat_config",
        "use_bartlebot_default_combat_config",
        "combat_profiles",
        "active_combat_profile_name",
    ):
        current_data.pop(field, None)

    current_data["states"] = states

    # Atomic write.
    import os
    import tempfile

    fd, tmp = tempfile.mkstemp(dir=bot_path.parent, suffix=".tmp", prefix=bot_path.stem + "_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(current_data, f, indent=2)
        os.replace(tmp, bot_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    print(f"  [ok]   {bot_path.relative_to(PROJECT_ROOT)} → {len(all_pcs)} PCS, {len(all_dcs)} DCS, {len(all_bcs)} BCS")
    _stats["bots"] += 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    bots_dir = PROJECT_ROOT / "bots"
    if not bots_dir.exists():
        print(f"ERROR: bots/ directory not found at {bots_dir}")
        sys.exit(1)

    bot_files = sorted(bots_dir.rglob("*.json"))
    print(f"Found {len(bot_files)} bot file(s) under {bots_dir}\n")

    for bot_path in bot_files:
        try:
            _migrate_bot(bot_path)
        except Exception as exc:
            print(f"  [ERR]  {bot_path.relative_to(PROJECT_ROOT)}: {exc}")

    print(
        f"\nDone. Migrated {_stats['bots']} bot(s), "
        f"skipped {_stats['skipped']}. "
        f"Created: {_stats['pcs']} PCS, {_stats['dcs']} DCS, {_stats['bcs']} BCS."
    )


if __name__ == "__main__":
    main()
