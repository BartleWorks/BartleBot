# tools

Scripts run by hand from the repo root (`uv run python tools/<name>.py`), never by the
app. Nothing here ships in an install. `CLAUDE.md` in this folder covers the two offline
sources (the type dump and `Root.wad`) to check before writing a probe; read it first.

## Regenerating the shipped game data

Each rewrites one file in `bartlebot/game/data/` from the installed game, after a patch
or when a reader needs more. Names and ids only, never art (the root `CLAUDE.md`, *No
game assets, either*).

| tool | writes |
|---|---|
| `dump_badge_categories.py` | `badge_categories.json` |
| `dump_dialog_captions.py` | `dialog_captions.json` |
| `dump_entity_names.py` | `entity_names.json` |
| `dump_name_tables.py` | `name_tables.json` |
| `dump_reagent_nodes.py` | `reagent_nodes.json` |
| `dump_realm_names.py` | `realm_names.json` |
| `dump_spell_names.py` | `spell_names.json` |
| `dump_spell_schools.py` | `spell_schools.json` |
| `dump_stat_caps.py` | `stat_caps.json` |
| `dump_world_names.py` | `world_names.json` |
| `dump_zone_names.py` | `zone_names.json` |
| `scales_table.py` | a Scales of Knowledge entry in `scales_of_knowledge.json`, from a bot recorded in the app (`docs/LEVEL_SCALING.md`) |
| `zone_graph/` | the research behind the zone navigation map (`docs/ZONE_NAVIGATION.md`); its own `README.md` |

## After a game update

`game_update/`, the procedure in `docs/GAME_UPDATES.md`. Each compares the build the code
works with against the one coming, so a Test realm patch can be read before it is live.

| tool | does |
|---|---|
| `game_update/compare_installs.py` | compares two installs: revision, `Bin/` files, wads, and `Root.wad` entry by entry |
| `game_update/diff_snapshots.py` | regenerates every snapshot from another install into a scratch folder and diffs it with the shipped one |
| `game_update/check_hook_patterns.py` | scans wizwalker's byte patterns against two client exes on disk: which hooks a build breaks |
| `game_update/find_function.py` | finds the function a broken pattern matched on the old exe, and the same function on the new one by a string it names; disassembles with `uv run --with capstone` |
| `game_update/capture_type_dump.py` | captures a type dump from a running client with wiztype, finding the type registry itself when wiztype's scan no longer matches; run under `uvx --from wiztype==0.1.13` |
| `game_update/smoke_test_client.py` | hooks one running client and reads through every hook and offset group a bot uses, OK or FAIL for each; close BartleBot first |
| `game_update/pe_image.py` | the static exe reader the others share: sections, RVAs, calls and RIP-relative operands, functions from `.pdata`, strings and what refers to them |

## Reading the game's own data

| tool | does |
|---|---|
| `typedump.py` | queries and diffs the type dumps, `r<rev>_WizardDev.json` (tracked on purpose: `.gitattributes` says why) |
| `name_keys.py` | decodes a wizard's `m_nameKeys` into a name; `--verify` checks `name_key_samples.json`, the synthetic fixture |
| `extract_card_art.py` | extracts the game art into the local cache outside the repo, the same function setup runs |

## The bot library

| tool | does |
|---|---|
| `audit_bots.py` | checks every bot file for grid alignment, missing `when` conditions, thin drops and structural problems |
| `drop_categories.py` | surveys the drop categories across the library; rerun after importing bots, since quicksell must classify each one |
| `drop_name_patterns.py` | surveys the `(...)` suffixes on drop names that `bartlebot/game/wiki_drops.py` has no rule for |

## Building the app

| tool | does |
|---|---|
| `build_package.py` | builds a packaged BartleBot with PyInstaller, strips install records from its metadata, and runs its `--self-check` (`docs/RELEASING.md`) |
| `release_test_user.ps1` | creates, or recreates from nothing, the standard Windows account a release draft is tested in, with `docs/RELEASE_CHECKLIST.md` on its desktop; run it from an elevated PowerShell (`docs/RELEASING.md`, *Testing a draft*) |

## On live clients

These hook the game themselves, so **close BartleBot first**: two sets of hooks in one
game process corrupt each other.

| tool | does |
|---|---|
| `form_group.py` | forms an in-game group across every running client, through `group_manager.form_group` |
| `dry_run_potion_route.py` | drives `handle_potions`' route end to end without buying anything |
| `zone_nav_tester/` | maps zone transitions by playing, and tests routes on one client (`uv run python -m tools.zone_nav_tester`) |

## Probes to rerun after a game patch

Each checks something the code reads from the game that a patch can move: a memory
offset, a UI path, or a server behavior. The probe's docstring has what it found and how
to run it. A probe written to answer a one-time question is deleted once its answer is
recorded beside the code that depends on it.

| probe | what depends on it |
|---|---|
| `probe_aggro_radius.py` | `collect_wisps`' safe radius (`node_helpers.SAFE_RADIUS`, #117) |
| `probe_archmastery_ui.py` | the archmastery selector `deck_io` clicks |
| `probe_bare_deck_open.py` | how `deck_io` opens the deck page to read it |
| `probe_deck_behavior.py` | reading the equipped deck from memory (`ClientDeckBehavior`) with the window closed |
| `probe_deck_counts.py` | wizsprinter's `deck.remaining` condition |
| `probe_deck_page_index.py` | the deck page's scroll index offset (#48) |
| `probe_item_cards.py` | the `DeckListControl` entry offset and the item cards' on/off flag |
| `probe_spell_list.py` | how the spellbook names a tiered spell, which deck restore matches against |
| `probe_char_select.py` | auto-select reading the UI at character select (`launcher/auto_select.py`) |
| `probe_friends_list.py` | the two friends-list windows, and closing them |
| `probe_group_roster.py`, `probe_group_tab.py`, `probe_group_ui.py` | the social window reads `group_manager` makes |
| `probe_group_state.py`, `probe_group_offsets.py` | the memory route to group state, dead on the current build; rerun to see whether a later one revives it |
| `probe_housing_equip.py` | telling an equipped castle from an unequipped one (`nodes/housing.py`) |
| `probe_binding_memory.py` | each client's key bindings in memory, every `InputBindingList` and what holds it (`clients/game_keys.py`, `docs/KEY_BINDINGS.md`) |
| `probe_key_bindings.py` | the key code each of the game's key names means, bound by hand (`game/key_bindings.py`'s `KEY_CODES`); writes `probe_key_bindings.local.json`, gitignored |
| `probe_name_behavior.py` | reading the wizard name from `ClientWizPlayerNameBehavior`; `--record` writes `name_key_samples.local.json`, gitignored |
| `probe_same_place.py` | sightings: when one client has another loaded (`clients/sightings.py`, `docs/PLAYER_REALMS.md`) |
| `probe_untargetable.py` | wizsprinter skipping an untargetable enemy (#192) |

Never commit what a probe records about a real account. The root `CLAUDE.md`, *No real
player data*, lists where each one writes.

## Our own GUI

`probe_layout.py` builds a real `BotCanvas` offscreen and prints what Qt did to it: sizes,
the event stream, paint damage. No game client. `CLAUDE.md` in this folder, *Our own
widgets*, has how to use it. `export_logo.py` exports the logo's raster files from its
SVG for places outside the app (#26).
