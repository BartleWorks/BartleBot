# Game updates

What to do when a Wizard101 update reaches the Test realm, so that BartleBot and our two
libraries are ready the day it reaches live. The tools are in `tools/game_update/`; each
one's docstring has its options. Run them from the repo root.

## The branches

An update gets one branch, named for it (`ahmarra-test-realm` for r811339), in this repo
and, if they need changes, in wizwalker and wizsprinter, cut from `master` and `dev`. **None
of them merges until the update is live.** Until then the live realm is what users run, so
everything shipped has to keep matching it: the snapshots in `bartlebot/game/data/`, the
type dump art extraction reads, and the offsets and patterns the libraries pin. On the day
it goes live: rerun the steps below against the live install (the Test realm build can
change before release), merge the library branches into `dev`, promote and pin them
(`docs/UPSTREAM.md`), then merge this branch.

The Test realm installs to `C:\ProgramData\KingsIsle Entertainment\Wizard101 Test` and
**downloads zone wads on demand**, so it holds a third of live's. Anything that reads every
zone's wad is wrong there until the install is complete, and the tools say so where it
matters.

## 1. What changed

```bash
uv run python tools/game_update/compare_installs.py --report <scratch>/root_wad.txt
```

The client revision, every `Bin/` file that differs, the wads only the new install has
(a new world shows up here first), and `Root.wad` entry by entry, hashed by content. The
summary leaves out the per-object folders (`ObjectData/`, `Spells/`, templates, quest text)
and lists the rest, which is the short list of tables a reader of ours might use. Search
the repo for each name in it.

## 2. The shipped game data

```bash
uv run python tools/game_update/diff_snapshots.py --out <scratch>/snapshots
```

Regenerates every snapshot from the Test realm into a scratch folder, never into
`bartlebot/game/data/`, and diffs each with the shipped one. The three that read every
zone wad (zone names, entity names, reagent nodes) need `--zone-wads` and a complete
install, so on the Test realm they wait.

What a diff usually means:

| the diff shows | change |
|---|---|
| a new world in `world_names`, `badge_categories` | the world's entry in `zone_nav.json`, `worlds.SPIRAL_DOOR_WORLDS`, its emblem in art extraction's `WORLD_TEXTURES` (the badge category names the texture), a Scales of Knowledge entry or `stat_caps.WORLD_LEVELS_WITHOUT_SCALES` once it has Challenge Mode |
| a new level in `stat_caps` | `stat_caps.MAX_LEVEL`, and what its comments say about the old max level |
| spells renamed | a search of `bots/`, `templates/`, `subgraphs/` and `strategies/` for the old names |

A new world's entry in `zone_nav.json` needs its door named (`test_zone_names.py`), and a
zone's name needs its own wad, so before the install is complete it declares only what can
be named. `tools/zone_graph/main_zones.py --game-data <Test GameData> --world <key>` reads
the world's hub and door from `Root.wad`, which a Test realm install has whole, and
proposes its main zones. `dump_zone_names.py --game-data <Test GameData> --out <scratch>`
then names the zones whose wads are there; merge only the new world's rows into the
shipped file, since every other zone the Test install lacks would drop out. The full
rerun on the day the update goes live replaces the merge.

## 3. Which hooks break

```bash
uv run python tools/game_update/check_hook_patterns.py
```

Every byte pattern in the installed wizwalker, scanned against the live and the Test realm
exe on disk; the code bytes are the same as in a running client, so no client is needed.
**broken** is a hook that will not install on the new build, **changed** a pattern whose
match count moved, which matters to a scan that takes the first match, and **new only** a
pattern written for the new build. The literals that match neither are mostly the code
wizwalker writes into the client, and say nothing. To check the update's wizwalker branch
rather than the installed pin, pass `--source ../wizwalker/wizwalker`: a fixed pattern moves
from broken to new only.

**A pattern that holds can still be wrong.** It says only that the bytes still occur, not
that they are still the site the code meant, nor that what the code does at an offset from
the match is still there. On r811339 the teleport hook's `event_dispatch` pattern held while
landing on a different block's `je`. So for every hook whose other patterns broke, and for
any pattern used with an offset (`+15`, `+24`) or whose overwritten bytes are replayed from
the bytecode, look at the code around the match on both builds.

`AUTOBOT_PATTERN` (`memory/handler.py`) is the one to look at first: it finds the code cave
every `SimpleHook` is written into, so when it breaks, every such hook does too.

## 4. The type dump

```bash
uvx --from wiztype==0.1.13 python tools/game_update/capture_type_dump.py
uv run python tools/typedump.py diff tools/r<old>_WizardDev.json tools/r<new>_WizardDev.json
```

The capture runs under `uvx` in wiztype's own environment, since wiztype pins an old
`pefile` that has no place in the project's. Open exactly one client of the build to dump;
it need not be logged in. The capture uses wiztype's own scan when it still matches, and
otherwise finds the type registry by the code that builds it, named on a reference exe
(the live one) where wiztype's scan still works. Its docstring has how; r811339 is the
build that needed it.

**Capture the live build too, before the update ships**, if its dump is not checked in.
Once live is patched there is no client of the old build left, and the dump is the only
record of the layout the current code was written against (`tools/CLAUDE.md`, *Type
dumps*).

A dump is named `r<number>_WizardDev.json` whichever realm it came from. The suffix is the
Test realm's: its `revision.dat` reads `r811339.WizardDev`, live's `r806919.Wizard_1_610`,
and the first dump checked in was a Test realm capture. Everything that finds a dump
matches that name, so the capture keeps it rather than following the realm.

Where a dump goes: `tools/` by default. On the update's branch, the new build's dump goes
in `bartlebot/game/data/` and the one it replaces moves to `tools/` with `git mv`, the rule
`tools/CLAUDE.md` gives, because the branch merges only once that build is live. Never put a
Test realm dump in `game/data/` on `master`: art extraction reads that one at run time,
against the live game's files.

Then the offsets, which a moved field breaks without failing anything: the read just
returns a neighbor's value.

```bash
uv run python tools/typedump.py stale ../wizwalker/wizwalker --old tools/r<old>_WizardDev.json
uv run python tools/typedump.py stale ../wizwalker/wizwalker --old tools/r<old>_WizardDev.json --fix
```

`stale` reads every `read_*`/`write_*` call given a literal offset, takes the property the
old dump has there on the game class of the wizwalker class's name (`GameStats` is
`WizGameStats`), and says where the new dump has it: **moved**, **gone**, or **raw**, an
unreflected member with properties within 16 bytes that moved, which the dump cannot settle.
`--fix` rewrites the moved ones in place, in each literal's own base. **Do not rerun `stale`
against the old dump after `--fix`**: the source then holds new offsets, and the old dump
names their old occupants. Check a fix by pairing each changed line's offset before
(`git show HEAD:`) on the old dump with the same line's after on the new one; every pair
should name one property.

What `stale` cannot see: the wizwalker classes with no reflected game class (the camera
controllers, the scene manager, the duel and quest managers, `GameClient`, the deck and spell
list entries), and BartleBot's own offsets, which are not in classes named for the game's:
`bartlebot/game/wizard_names.py` (`ClientWizPlayerNameBehavior` and `ClientObject`) and
`bartlebot/combat/deck_io.py` (the deck lists, a raw gap of `DeckListControl`). Check
those by hand with `typedump.py at` on both dumps, and the probes `tools/README.md` lists
under *Probes to rerun after a game patch*. wizsprinter reads no offsets of its own.

## 5. Replacing a broken pattern

Check Laurenz's wizwalker first (`docs/UPSTREAM.md`); she often has the fix before we
look. When she does not, `find_function.py` does the finding, with capstone for the
disassembly (not a project dependency, so `uv run --with capstone`):

```bash
uv run --with capstone python tools/game_update/find_function.py --pattern 'rb"<the old pattern>"'
uv run --with capstone python tools/game_update/find_function.py --string "<a string it names>"
```

The first says, on the live exe, which function the old pattern matched (its bounds from
`.pdata`, the x64 unwind table that lists every function), where in it, its callers, and
every string it names. The second finds the function naming that string on both builds,
and prints both openings side by side to write the new pattern from. Then:

- **Find what the old pattern matched on the live exe**, and what the code there does.
- **Anchor on what the code does, not on its bytes.** r811339 was rebuilt with different
  code generation: not one getter of the shape wiztype's target had survived, though the
  getter itself had. Constants, field offsets and the order of operations survive a
  recompile far better than prologues and register choices do.
- **A function that names no string** is found by what else survives: a field offset it
  reads that the type diff says did not move, the function it calls, its size and its
  number of callers. List every site of that kind on both builds; they tend to come in the
  same order, and the counterpart is the one whose size and callers agree.
- **Try the smallest change first.** Compare the old match's function with its new
  counterpart instruction by instruction: often one stack slot or register moved, and a
  wildcard on that byte gives a pattern that matches once on both builds, which keeps the
  library branch running against live too. Rewrite the pattern only when the code around
  the site was really recompiled.
- **Prove the new anchor on the live exe first**: it has to find what the old pattern
  found there, at the same address, before its answer on the new build means anything.
- **Count, don't assume.** Where an anchor matches several sites, take one by position
  only when both builds have the same number, in the same order, and say so in the code.
- **A pattern can carry a size, too.** Where the code assumes something about what the
  match finds beyond its start, check that against the new build. `AUTOBOT_SIZE` is the
  case: wizwalker zeroes that many bytes from the autobot's start, and on r811339 the
  function had shrunk below it, so the old size would have overwritten the code after it.
- **Then a test in wizwalker** that runs the new pattern over the prologue assembled with
  made-up operands, and one that it does not match the prologue alone, where that is
  shared (`tests/memory/test_handler.py` is the pattern). The game's own bytes stay out of
  the repo, as its art does.

## 6. Running against the update

The update's wizwalker branch is neither pushed nor pinned, so it goes into the venv by
hand, as a normal install of the checkout, never an editable one: wizsprinter installs
into `wizwalker/extensions/`, and an editable wizwalker hides it, which breaks every
wizsprinter import.

```bash
uv pip install --no-deps --reinstall ../wizwalker   # the checkout, on the update's branch
uv run --no-sync python tools/game_update/smoke_test_client.py
uv sync                                              # the pin again, when done
```

Every command needs `--no-sync` while it is in, or the first plain `uv run` puts the pin back
without a word (`docs/UPSTREAM.md`, *Bumping a pin*). That includes `uv run --with capstone`
for `find_function.py`: `--with` adds to the environment but still syncs it first, and its
"Uninstalled 1 package" is the branch going. Reinstall after each change to the checkout; the
install is a copy. Check which is in before trusting a run against a client:
`uv run --no-sync python -c "import wizwalker.memory.handler as h; print(h.HookHandler.AUTOBOT_SIZE)"`.

The smoke test hooks one client, in the world, and goes through each hook and offset group a
bot uses, saying which failed; `--in-duel`, run in a fight, reads every participant. Close
BartleBot first. Then a bot: start the Test realm client by hand, log in, and run BartleBot
with `uv run --no-sync python bartlebot.py`, which attaches to it.

**A client started by hand misses what BartleBot does at launch**, since BartleBot's game
folder is the live install: chiefly `QuickFriendTeleport = 0` in `Bin/config.xml`
(`launcher/launch_profile.py`). With the game's *Options › Gameplay Options 4 › Dblclick Go
To Friend* on, a click on a friends list row closes the list instead of opening the
friend's profile, and grouping and every friend port fail as if the friend were not on the
list. Turn it off on each Test realm character before a team run; it cost r811339 an
evening of looking for a patch fault that was not there.

## Record: r811339 (Ahmarra)

Found on the Test realm on 2026-10-21, against live r806919. Its dump was captured the same
day (`tools/r806919_WizardDev.json`); the one checked in before was r800294's.

- **Game data:** the world Ahmarra (124 zones; hub `AH_Z00_BaleFlotilla`;
  `AH_Z04_PrimalArchipelago` links to every world's spiral chamber), its badge category,
  the level cap raised to 190, 409 new spells and 182 renamed (nearly all Beastmoon `BG_`
  cards, none named by shipped content), four spells moved school. Nothing removed from
  the zone map. Key rebinding arrives: `Data/GameData/LocalInputBindings.xml`, and new
  `InputBindings.xml` and `InputActions.lang` in `Root.wad`.
- **Game data on the branch:** the `Root.wad` snapshots regenerated (badge categories,
  spell names and schools, stat caps, world names); `MAX_LEVEL` 190, so Darkmoor (180) now
  scales a level 190 player; Ahmarra on the World Gate list (`SPIRAL_DOOR_WORLDS`), with
  its emblem (`Button_World_Ahmarra`, the texture its badge category names). In
  `zone_nav.json` it has only its hub and door, Bale Flotilla, the one main zone whose wad
  the Test install had; its name is merged into `zone_names.json` from a Test realm dump
  (English only so far). Still waiting on the world's wads: the other main zones
  `main_zones.py` proposes (`AH_Z01_WesternSea`, `AH_Z02_FloodedCity`,
  `AH_Z03_IslandOfTheStorm` and its `_B`, `AH_Z04_PrimalArchipelago`, `AH_Z05_EasternSea`,
  `AH_Z06_ForbiddenCity`, `AH_Z07_AkashicCityB`, `AH_Z08_AkashicCityY`,
  `AH_Z09_AkashicCityW`, `AH_Z10_DarkWaterTrench`), the zone, entity and reagent snapshots,
  Ahmarra's World Gate and links, which need recording in game, a Scales of Knowledge entry
  once it has Challenge Mode, and a `bots/` folder once a bot for it exists (the folders
  after it renumber then).
- **wiztype:** its scan no longer matched; `capture_type_dump.py` was written for it. The
  registry is the first of four map globals built the same way in both builds
  (`0x3372898` live, `0x379c618` on r811339). 7,033 types, from 6,981 on live.
- **Type layout:** 168 classes changed from r806919. Among them `ClientObject`
  (`m_turnSpeedMultiplier` added), `CombatParticipant` (+8 up to `m_shadowSpellEffects`, -8
  from `m_deathActivatedEffects`), `ClientObjectInfo` (+8) and `ClientWizPlayerNameBehavior`
  (`m_isRaidVisitor` added). Against r800294 it is 219: 51 had already changed by r806919,
  among them the UI controls' `Grayed` fields renamed `Greyed`, which is why a diff runs
  from the live build's own dump.
- **In game, 2026-10-21:** `smoke_test_client.py` passed every check on a Test realm client
  (each hook, the moved `m_fScale`, a teleport of +40 that landed at +40.0 and back to 0.0),
  then BartleBot ran a solo bot through a fight, and a two-player team bot five times in a
  row: pre-run grouping added P2 and sync ported P2 through the friends list every time.
  The team failures before those five were the game's *Dblclick Go To Friend*, on in the
  hand-launched clients (section 6), not the update.
- **Offsets:** 127 in wizwalker moved, all in `combat_participant.py` (+8 up to
  `m_shadowSpellEffects`, -8 from `m_deathActivatedEffects`) and `core_object.py`
  (`m_nMobileID`, `m_fScale`, after the new `m_turnSpeedMultiplier`); `--fix` rewrote them
  and every pair checked. `CoreObject.parent()` at 208 is raw and stays: the block from
  `m_fScale` to `m_inactiveBehaviors` (224, unmoved) shrank by four bytes, which an 8-aligned
  pointer at 208 does not need. `WizGameStats` did not move. BartleBot's own (`m_characterId`
  at 0x1C0, `m_nameKeys` at 0x160, `m_eGender` at 0x1B0, `DeckListControl`'s layout) are
  unchanged; `m_isRaidVisitor` went into padding.
- **Hooks:** nine patterns broken. Their replacements:

| pattern | hook | replacement |
|---|---|---|
| `AUTOBOT_PATTERN` (`memory/handler.py`) | the code cave of every `SimpleHook` | The cave is `AutobotManager::Initialize`, found on both builds by the string naming it. Recompiled with a `mov rax, rsp` frame and an EH state store; the new pattern runs from that prologue to the body's first `cmp byte [rax+?], 0; je`, which it alone of 296 such prologues has. The function shrank from 4,188 bytes to 3,975, so `AUTOBOT_SIZE` went from 4,100 to 3,968; every hook at once reserves 1,050. |
| `ClientHook.pattern` | the current client | The site names no strings. Found by the field it reads, `[reg+0x1C0]` (`ClientObject.m_characterId`, which the update did not move), straight after the getter's result: six such sites on each build, in the same order, and the one in a function of 230 bytes and 8 callers on live is 240 and 8 on r811339. Only the stack slot of the next `mov rdi, [rsp+?]` moved (0x38 to 0x40), so that byte is a wildcard and the pattern matches once on both builds. |
| `MovementTeleportHook.pattern` | teleport | The client object's set-position function (`rcx` the object, `rdx` the position), found by its position store `movsd [rcx+0xA8]; ...; or dword [rcx+0x160], 1`. r811339 gave it a longer prologue, which the old site (`push rdi; sub rsp, 0x20`, replayed from the bytecode) sat in. The hook now goes on the first instruction after either prologue, `mov rbx, [rcx+0x1B8]`, 7 bytes with 2 `nop`s, and the bytecode replays whatever it overwrote, read from the site. Matches once on both builds. |
| `MovementTeleportHook` `inside_event_je` scan | teleport | The movement function has two collision blocks, each a `[rax+0x10]` call on `[rdi+0xA0]` and then an event dispatch `[rax+0x18]`; the patched one hands the call a stack buffer (`lea r8, [rsp+?]`), the other `r8 = r15`. The stack slots the old patterns named swapped between the blocks on r811339, so `event_dispatch`'s pattern still matched, on the **other** block's dispatch. Now the check is found by its `lea r8` (a fixed-width lookbehind, so the match is the `je`), and the dispatch as the first `[rax+0x18]` call's `je` after it. Both find exactly the live pair on r806919. The `je`s at +15 and +24 of `\x8B\x5F\x70\xF3` were checked too: the same on both. |
| `DropsToggleHook.pattern` | drops | The function that sends the "Loot" event, hooked at entry to return 0. Found by the string: an immediate (`mov dword [rsp+?], 'Loot'`) on r806919, a `lea rdx, [rip+"Loot"]` on r811339, which turned both of live's inline "Loot"s into `.rdata` references (two referrers became four). New pattern from r811339's prologue through that string's setup, which only it has; the jump overwrites `sub rsp, 0x68` and the EH state store, 13 bytes, replayed from the site. New build only: live's prologue is a different shape. |
| `RootWindowHook.pattern` (held) | the root window | Not broken, and wrong: its pattern wildcards the site, `mov rcx, [reg+0xD8]`, but its bytecode named `r13`, both reading the window and replaying the instruction, and r811339 loads from `r15`. It would have exported garbage and loaded `rcx` from it, ahead of a vtable call. The pattern now asks that the site load `rcx` (ModRM `0x88`-`0x8F`, not `0x8C`, which takes a SIB byte), and the hook replays the instruction as read and then exports `rcx`, naming no register. Found by comparing every hook's replayed bytes with its site on both builds; the other five matched. |
| `ChatHook.pattern` | chat | Deferred: neither BartleBot nor wizsprinter activates it (`activate_all_hooks` does not). |
| `ChatSendHook._SEND_PATTERN` | sending chat | Deferred, as `ChatHook`. |
| `ChatSendHook._BUDDY_PATTERN` | buddy requests | Deferred, as `ChatHook`. |
| `_SSM_CONSTRUCTOR_PATTERN` (`social_systems_manager.py`) | the social systems manager | Deferred: BartleBot reads group state from the UI instead, the memory route being dead already (`clients/group_manager.py`). |

- **Key rebinding:** bindings belong to the account, not the install, and each client holds
  its own in memory, one `InputBindingList` (`tools/probe_binding_memory.py`); the shared
  `LocalInputBindings.xml` holds one account's at most. A binding with a modifier did not
  reach the game by any of three ways of sending it (`tools/probe_key_bindings.py`).
  **Recheck both of these on the build that goes live, before this branch merges:** the game
  names the main `.` key `comma` and the `,` key `period`, swapped (seen binding by hand on
  2026-10-22, and pressing each code opened the other's action), which reads as a bug
  KingsIsle may fix; and whether the numpad `.` key, code `0x6E` with no name in the game's
  table, lands on N's binding, as sending `0x6E` toggled the compass (N) here. Untried so far
  for want of a numpad. `docs/KEY_BINDINGS.md` has the rest: how BartleBot reads a client's
  bindings, and what a press of an action can use.
