"""One-time migration: stamp the hand-authored type tags onto every shipped bot.

Tags (``bartlebot/bot_tags.py``) say what a bot is *for*, which is what the library
panel's chip row filters on. They are not derivable: nearly every boss drops jewels
and treasure cards, so a rule keyed on ``drops[].category`` would tag almost every
bot with almost every tag. ``TAGS`` below is therefore a table, reviewed line by line
with the library's author, and this script is what puts it on disk — one reviewable
diff rather than 77 hand edits.

``tags`` is inserted immediately after ``authors`` (or after ``start_location`` when
a bot has no ``authors``), matching the field order in ``BotConfig`` so the next save
from the editor does not reorder the file.

Run from the project root. Reports only by default; pass ``--apply`` to write:

    uv run python scripts/migrate_bot_tags.py
    uv run python scripts/migrate_bot_tags.py --apply

Idempotent: a second run is a clean no-op.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bartlebot.bot_tags import BOT_TAGS  # noqa: E402

PROJECT_ROOT = Path(__file__).parent.parent

# Keyed by path relative to bots/, using forward slashes. Every non-test bot must
# appear — test_integration.py's library guard fails otherwise.
TAGS: dict[str, list[str]] = {
    # ── 01_wizard_city ────────────────────────────────────────────────
    "01_wizard_city/alicane_swiftarrow.json": ["Boss"],
    "01_wizard_city/crab_alley_stone_block.json": ["Reagents", "Gathering"],
    "01_wizard_city/foulgaze.json": ["Gear", "Animus", "Boss"],
    "01_wizard_city/harvest_lord.json": ["Gear", "Boss"],
    "01_wizard_city/king_detritus.json": ["Gear", "Reagents", "Boss"],
    "01_wizard_city/lord_nightshade.json": ["Gear", "Mounts", "Boss"],
    "01_wizard_city/nightside_flax.json": ["Reagents", "Gathering"],
    "01_wizard_city/prince_viggor.json": ["Gear", "Reagents", "Boss"],
    "01_wizard_city/sergeant_skullsplitter.json": ["Gear", "Boss"],
    "01_wizard_city/waterworks.json": ["Gear", "Snacks", "Dungeon"],
    # ── 02_krokotopia ─────────────────────────────────────────────────
    "02_krokotopia/biti_nirini.json": ["Spellements", "Seeds", "Boss"],
    "02_krokotopia/house_of_scales.json": ["Gear", "Dungeon"],
    "02_krokotopia/krokopatra.json": ["Gear", "Spellements", "Boss"],
    "02_krokotopia/selenopolis_parchment.json": ["Reagents", "Gathering"],
    "02_krokotopia/throne_room_of_fire.json": ["Gear", "Dungeon"],
    # ── 03_marleybone ─────────────────────────────────────────────────
    "03_marleybone/barkingham_palace_main_hall.json": ["Gear", "Pets", "Dungeon"],
    "03_marleybone/barkingham_palace_rooftop.json": ["Gear", "Pets", "Dungeon"],
    "03_marleybone/barkingham_palace_state_wing.json": ["Gear", "Pets", "Dungeon"],
    "03_marleybone/bellows.json": ["Gear", "Boss"],
    "03_marleybone/ironworks.json": ["Gear", "Dungeon"],
    "03_marleybone/katzensteins_lab.json": ["Gear", "Dungeon"],
    "03_marleybone/ransacked_throne_room.json": ["Gear", "Spellements", "Pets", "Dungeon"],
    "03_marleybone/scrap_iron_collector.json": ["Reagents", "Gathering"],
    "03_marleybone/spike_the_crusher.json": ["Gear", "Boss"],
    "03_marleybone/sprockets.json": ["Gear", "Boss"],
    # ── 04_mooshu ─────────────────────────────────────────────────────
    "04_mooshu/crimson_fields.json": ["Gear", "Dungeon"],
    "04_mooshu/do_daga.json": ["Spellements", "Boss"],
    "04_mooshu/jade_oni.json": ["Gear", "Spellements", "Mounts", "Boss"],
    "04_mooshu/oakheart.json": ["Gear", "Boss"],
    "04_mooshu/oyotomi_the_defiler.json": ["Spellements", "Boss"],
    "04_mooshu/tree_of_life.json": ["Gear", "Dungeon"],
    "04_mooshu/youkai.json": ["Gear", "Boss"],
    # ── 05_dragonspyre ────────────────────────────────────────────────
    "05_dragonspyre/the_great_spyre.json": ["Gear", "Reagents", "Spellements", "Dungeon"],
    "05_dragonspyre/the_necropolis_deep_mushroom.json": ["Reagents", "Gathering"],
    # Tagged for what it should farm; wants a level-scaling node first (see #56).
    "05_dragonspyre/valerik_brightsword.json": ["Gear", "Reagents", "Boss"],
    # ── 06_grizzleheim ────────────────────────────────────────────────
    "06_grizzleheim/mirkholm_keep_ore.json": ["Reagents", "Gathering"],
    # ── 07_celestia ───────────────────────────────────────────────────
    "07_celestia/achaelos.json": ["Gear", "Housing", "Boss"],
    # Same level-scaling caveat as valerik_brightsword.
    "07_celestia/big_salgio.json": ["Gear", "Reagents", "Spellements", "Boss"],
    "07_celestia/praetor_mako.json": ["Gear", "Boss"],
    "07_celestia/queen_calypso.json": ["Gear", "Boss"],
    "07_celestia/the_portico_pearl.json": ["Reagents", "Gathering"],
    "07_celestia/trial_of_the_spheres.json": ["Gear", "Dungeon"],
    # ── 09_zafaria ────────────────────────────────────────────────────
    "09_zafaria/mirror_lake.json": ["Gear", "Snacks", "Dungeon"],
    # ── 10_avalon ─────────────────────────────────────────────────────
    "10_avalon/harlequin_knight.json": ["Spellements", "Boss"],
    "10_avalon/jabberwock.json": ["Housing", "Boss"],
    "10_avalon/the_wild_deep_mushroom.json": ["Reagents", "Gathering"],
    # ── 11_azteca ─────────────────────────────────────────────────────
    "11_azteca/guillermo_shatterer.json": ["Treasure Cards", "Boss"],
    "11_azteca/pyramid_of_mother_moon_tartarus_workaround.json": ["Spellements", "Dungeon"],
    "11_azteca/the_zocalo_red_mandrake.json": ["Reagents", "Gathering"],
    # ── 13_polaris ────────────────────────────────────────────────────
    "13_polaris/vigilant_jarilo.json": ["Jewels", "Boss"],
    # ── 14_arcanum ────────────────────────────────────────────────────
    # Max-level spellements, so no level scaling is needed — the one exception to
    # "spellement farm implies a level-scaling node".
    "14_arcanum/mr_stone.json": ["Spellements", "Boss"],
    # ── 15_mirage ─────────────────────────────────────────────────────
    "15_mirage/advisor_fancy_fries.json": ["Jewels", "Boss"],
    "15_mirage/eerkala.json": ["Spellements", "Boss"],
    "15_mirage/fortee_thieves.json": ["Jewels", "Mobs"],
    "15_mirage/mongu_refuge.json": ["Jewels", "Reagents", "Boss"],
    # ── 16_empyrea ────────────────────────────────────────────────────
    "16_empyrea/aeriel_shores_aethyr_burst.json": ["Reagents", "Gathering"],
    "16_empyrea/aeriel_shores_silver_chests.json": ["Reagents", "Gathering"],
    "16_empyrea/monkey_spiders.json": ["Jewels", "Seeds", "Mobs"],
    "16_empyrea/secret_tunnel.json": ["Reagents", "Seeds", "Dungeon"],
    # ── 17_karamelle ──────────────────────────────────────────────────
    "17_karamelle/copy_qhat.json": ["Gear", "Spellements", "Boss"],
    # ── 18_lemuria ────────────────────────────────────────────────────
    "18_lemuria/hall_of_heroes.json": ["Gear", "Spellements", "Gold", "Dungeon"],
    "18_lemuria/scion_of_the_old_one.json": ["Gear", "Spellements", "Boss"],
    # ── 20_wallaru ────────────────────────────────────────────────────
    "20_wallaru/crash_the_bandit_coot.json": ["Gear", "Reagents", "Spellements", "Housing", "Boss"],
    "20_wallaru/mr_cane.json": ["Jewels", "Boss"],
    # ── 21_darkmoor ───────────────────────────────────────────────────
    "21_darkmoor/heretical_academy.json": ["Gear", "Spellements", "Dungeon"],
    "21_darkmoor/hierophant_of_destruction.json": ["Gear", "Spellements", "Boss"],
    "21_darkmoor/sister_sanderson.json": ["Gear", "Spellements", "Boss"],
    "21_darkmoor/sister_sanderson_teamup.json": ["Gear", "Spellements", "Boss", "Team-Up"],
    # ── aquila ────────────────────────────────────────────────────────
    "aquila/cronus.json": ["Gear", "Boss"],
    "aquila/gladiator_dimachaerus.json": ["Gear", "Boss"],
    "aquila/mt_olympus.json": ["Gear", "Spellements", "Dungeon"],
    "aquila/sand_squid_tentacles.json": ["Gear", "Boss"],
    # ── castle_darkmoor ───────────────────────────────────────────────
    "castle_darkmoor/castle_darkmoor.json": ["Gear", "Dungeon"],
    "castle_darkmoor/graveyard.json": ["Gear", "Dungeon"],
    # ── gauntlets ─────────────────────────────────────────────────────
    "gauntlets/grand_tourney_arena_gauntlet.json": ["Gear", "Gauntlet"],
    "gauntlets/hidden_temple_gauntlet.json": ["Gear", "Gauntlet"],
    "gauntlets/spiral_cup_gauntlet.json": ["Gear", "Gauntlet"],
}

_TAG_ORDER = {tag: i for i, tag in enumerate(BOT_TAGS)}


# ---------------------------------------------------------------------------
# Pure transform (no disk I/O — this is what the tests exercise)
# ---------------------------------------------------------------------------


def insert_tags(data: dict, tags: list[str]) -> dict:
    """Return *data* with ``tags`` set, positioned to match BotConfig's field order.

    Rebuilds the dict rather than assigning, so the key lands after ``authors``
    (or ``start_location``) instead of at the end — a save from the editor writes
    ``model_dump()`` order, and a mismatch would show up as diff noise on the first
    unrelated edit to any bot.
    """
    ordered = sorted(set(tags), key=_TAG_ORDER.__getitem__)
    anchor = "authors" if "authors" in data else "start_location"
    rebuilt: dict = {}
    for key, value in data.items():
        if key == "tags":
            continue  # re-inserted at the anchor below
        rebuilt[key] = value
        if key == anchor:
            rebuilt["tags"] = ordered
    if "tags" not in rebuilt:
        # Neither anchor present — fall back to leading, after id/name.
        rebuilt["tags"] = ordered
    return rebuilt


def validate_table(bot_files: list[str]) -> list[str]:
    """Return problems with ``TAGS`` itself: unknown tags, or bots it misses/invents."""
    problems: list[str] = []
    for rel, tags in TAGS.items():
        unknown = [t for t in tags if t not in _TAG_ORDER]
        if unknown:
            problems.append(f"{rel}: unknown tag(s) {unknown}")
        if not tags:
            problems.append(f"{rel}: no tags")
    shipped = {f for f in bot_files if not Path(f).name.startswith("test_")}
    for rel in sorted(shipped - set(TAGS)):
        problems.append(f"{rel}: shipped bot missing from TAGS")
    for rel in sorted(set(TAGS) - shipped):
        problems.append(f"{rel}: in TAGS but no such bot file")
    return problems


# ---------------------------------------------------------------------------
# Disk I/O
# ---------------------------------------------------------------------------


def _write_atomic(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix=path.stem + "_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write changes (default is a dry run)")
    args = parser.parse_args()

    bots_dir = PROJECT_ROOT / "bots"
    if not bots_dir.exists():
        print(f"ERROR: bots/ directory not found at {bots_dir}")
        sys.exit(1)

    bot_files = sorted(p.relative_to(bots_dir).as_posix() for p in bots_dir.rglob("*.json"))

    problems = validate_table(bot_files)
    if problems:
        print("TAGS table does not match the library:")
        for p in problems:
            print(f"  {p}")
        sys.exit(1)

    print(f"Mode: {'APPLY (writing changes)' if args.apply else 'DRY RUN (no files will be modified)'}\n")
    changed = 0
    for rel, tags in sorted(TAGS.items()):
        path = bots_dir / rel
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        migrated = insert_tags(data, tags)
        if list(migrated.items()) == list(data.items()):
            continue
        changed += 1
        print(f"  [ok]   {rel}  ->  {', '.join(migrated['tags'])}")
        if args.apply:
            _write_atomic(path, migrated)

    print(f"\n{changed} of {len(TAGS)} bot file(s) need tags.")
    if changed and not args.apply:
        print("Dry run — re-run with --apply to write these changes.")


if __name__ == "__main__":
    main()
