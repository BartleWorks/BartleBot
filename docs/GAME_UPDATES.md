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

Then `typedump.py annotate <file>` over wizwalker's memory objects, and the offsets our own
code reads: `bartlebot/game/wizard_names.py` (`ClientWizPlayerNameBehavior`),
`bartlebot/combat/deck_io.py` (the deck lists), and the probes `tools/README.md` lists
under *Probes to rerun after a game patch*.

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

## Record: r811339 (Ahmarra)

Found on the Test realm on 2026-10-21, against live r806919. Its dump was captured the same
day (`tools/r806919_WizardDev.json`); the one checked in before was r800294's.

- **Game data:** the world Ahmarra (124 zones; hub `AH_Z00_BaleFlotilla`;
  `AH_Z04_PrimalArchipelago` links to every world's spiral chamber), its badge category,
  the level cap raised to 190, 409 new spells and 182 renamed (nearly all Beastmoon `BG_`
  cards, none named by shipped content), four spells moved school. Nothing removed from
  the zone map. Key rebinding arrives: `Data/GameData/LocalInputBindings.xml`, and new
  `InputBindings.xml` and `InputActions.lang` in `Root.wad`.
- **wiztype:** its scan no longer matched; `capture_type_dump.py` was written for it. The
  registry is the first of four map globals built the same way in both builds
  (`0x3372898` live, `0x379c618` on r811339). 7,033 types, from 6,981 on live.
- **Type layout:** 168 classes changed from r806919. Among them `ClientObject`
  (`m_turnSpeedMultiplier` added), `CombatParticipant` (+8 up to `m_shadowSpellEffects`, -8
  from `m_deathActivatedEffects`), `ClientObjectInfo` (+8) and `ClientWizPlayerNameBehavior`
  (`m_isRaidVisitor` added). Against r800294 it is 219: 51 had already changed by r806919,
  among them the UI controls' `Grayed` fields renamed `Greyed`, which is why a diff runs
  from the live build's own dump.
- **Hooks:** nine patterns broken. Their replacements:

| pattern | hook | replacement |
|---|---|---|
| `AUTOBOT_PATTERN` (`memory/handler.py`) | the code cave of every `SimpleHook` | The cave is `AutobotManager::Initialize`, found on both builds by the string naming it. Recompiled with a `mov rax, rsp` frame and an EH state store; the new pattern runs from that prologue to the body's first `cmp byte [rax+?], 0; je`, which it alone of 296 such prologues has. The function shrank from 4,188 bytes to 3,975, so `AUTOBOT_SIZE` went from 4,100 to 3,968; every hook at once reserves 1,050. |
| `ClientHook.pattern` | the current client | The site names no strings. Found by the field it reads, `[reg+0x1C0]` (`ClientObject.m_characterId`, which the update did not move), straight after the getter's result: six such sites on each build, in the same order, and the one in a function of 230 bytes and 8 callers on live is 240 and 8 on r811339. Only the stack slot of the next `mov rdi, [rsp+?]` moved (0x38 to 0x40), so that byte is a wildcard and the pattern matches once on both builds. |
| `MovementTeleportHook.pattern` | teleport | |
| `MovementTeleportHook` `inside_event_je` scan | teleport | |
| `DropsToggleHook.pattern` | drops | |
| `ChatHook.pattern` | chat | |
| `ChatSendHook._SEND_PATTERN` | sending chat | |
| `ChatSendHook._BUDDY_PATTERN` | buddy requests | |
| `_SSM_CONSTRUCTOR_PATTERN` (`social_systems_manager.py`) | the social systems manager | |
