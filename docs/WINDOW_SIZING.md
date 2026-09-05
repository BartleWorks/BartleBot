# Window sizing

How big a Wizard101 client is launched, why that is one setting and not two, and why an
unfittable configuration cannot be launched at all. The code is `bartlebot/game_sizing.py`
(the arithmetic, pure) and `bartlebot/gui/screen_fit.py` (the same question asked from a
widget, with a real screen behind it). Read this before changing either, or before adding
a resolution to `VALID_RESOLUTIONS`.

The problem it solves: at 800x600 — the lowest the game's own options menu offers — two
embedded clients do not fit side by side on a 1920x1080 screen, so the whole game-embedding
feature was unusable for a 2-4 client user on a standard monitor (#101).

## The engine has no 800x600 floor; its options menu does

`Resolution` is a free-form `WxH` string in `preferences.xml` and the engine renders
whatever is in it. Verified against a live client by measuring `GetClientRect`:

| requested | measured client |
|---|---|
| 800x600 | 800x600 |
| 640x480 | 640x480 |
| 512x384 | 512x384 |
| 400x300 | 400x300 |

**If you repeat that measurement, do it from a process with per-monitor DPI awareness
set** (`SetProcessDpiAwarenessContext(-4)`). A DPI-unaware process on a scaled display
reads back virtualized coordinates — on a 125% display every result comes back at exactly
0.8x, *including the 800x600 control*, which looks convincingly like the engine clamping.
Qt sets awareness itself, so app code such as `ClientTile.game_size` already measures true
physical pixels.

## Resolution and UI size are one setting

The HUD does not scale with the window. Shrink the resolution alone and the interface
keeps its original pixel size and overflows the client area. The compensation is the
game's own UI scale, which goes down to **Micro** — a step below anything the in-game
options menu offers.

So 400x300 *renders* correctly paired with Micro — every control draws, including the
Archmastery selector — and is broken at the default scale. The two must never be
independently selectable, or users land in the broken combination.

The verified render ladder — exactly 80x60 apart, all 4:3:

| UI size | `UISize` value | render floor |
|---|---|---|
| Micro | 5 | 400x300 |
| Tiny | 0 | 480x360 |
| Small | 1 | 560x420 |
| Regular | 2 | 640x480 |
| Large | 3 | 720x540 |
| Huge | 4 | 800x600 |

**Rendering is not the whole story, and this ladder alone is not safe to launch
against.** A second, higher floor sits under each rung — see "The HUD draws before it is
reachable" below. 400x300 is not offered at all as a result, and `UI_SIZE_LADDER` carries
both floors per rung.

**The stored values are not in size order.** Micro was appended after the original five
and took the next free number. `UI_SIZE_LADDER` is in ascending *visual* order and that
is the only order anything may use — nothing may sort or compare these numerically.

**The game does not clamp UI size against resolution.** A UI too large for the client
area is reachable in-game and simply renders broken. That very likely explains the stock
800x600 floor: it is the lowest resolution at which the *largest* UI size still works, so
the game never has to police the pairing. Going below it is not fighting the engine — it
is relaxing a conservative floor by controlling the pairing ourselves.

## The HUD draws before it is reachable

**The failure is a click that silently misses, not a rendering glitch.** The HUD anchors
the compass and teleporter buttons (`compassAndTeleporterButtons`) to a screen edge and
centres the interact prompt. On a narrow client the prompt is wide enough to cover the hub
and dorm buttons — and those buttons are clicked *by coordinate*: `node_helpers.click_by_path`
resolves the window and hands its centre to wizwalker's `mouse_handler.click_window`, so
whatever is drawn on top takes the click. A covered button therefore fails **quietly**:
`click_by_path` returns True (the window exists and is not grayed), the click lands on the
prompt, and the node burns its `TRIGGER_TIMEOUT` waiting for a loading screen that never
starts. Four nodes ride on that button group — `teleport_to_world_hub`, `wizard_city`'s
go-home (which `go_to_world` is built on), `mark_location` and `teleport_to_marked_location`.

There are two ways out, and the ladder uses both.

### Narrow: take one scale less

At 4:3 the fix is simply to run **one UI scale below what the render floor allows**,
measured on a live client with a sigil prompt up. That is `min_narrow_resolution`:

| UI size | render floor | narrow floor |
|---|---|---|
| Micro | 400x300 | 480x360 |
| Tiny | 480x360 | 560x420 |
| Small | 560x420 | 640x480 |
| Regular | 640x480 | 720x540 |
| Large | 720x540 | 800x600 |
| Huge | 800x600 | **1280x960** |

So 640x480 takes Small, 800x600 takes Large, and **1024x768 takes Large rather than
Huge** — Huge still occludes there, which is the measurement that puts Huge off the
one-step pattern the other five follow. Nothing 4:3 is offered between 1024x768 and
1280x960, so the true Huge boundary is somewhere in that gap and untested; measure it
rather than guessing if a size in it is ever added.

**400x300 is therefore not offered at all.** It renders at Micro, but the prompt covers
the buttons and there is no scale below Micro to step down to. `has_unoccluded_ui_size`
is the guard, and a test holds all of `VALID_RESOLUTIONS` to it — that check, not the
render floor, is the bar a new entry has to clear. A settings file that still holds
400x300 is migrated on read by the `game_resolution` getter rather than launched.

### Wide: take the width instead

`widescreen_form` derives a second resolution per rung — **the same height, the width
widened to 16:9** — which moves the edge-anchored buttons away from the centred prompt
and so keeps the render floor's scale:

| UI size | 16:9 | scale |
|---|---|---|
| Micro | 534x300 | Micro |
| Tiny | 640x360 | Tiny |
| Small | 746x420 | Small |
| Regular | 854x480 | Regular |
| Large | 960x540 | Large |

**Widening, never shortening — that is what makes these safe to offer.** More width at the
same height cannot take a HUD back below a floor it already cleared, so the 16:9 form
inherits the render floor's verification. Deriving the 16:9 size from the *width* instead
(400x225) would cut the height below the floor, which is exactly the broken pairing the
ladder exists to prevent.

`is_widescreen` draws the line at **16:10**, compared by cross-multiplying integers so
1280x800 — the one offered resolution sitting exactly on it — is not decided by a float.
16:9 is what was measured; 16:10 is a judgement that it is close enough, and 1280x800 is
the only resolution where that judgement changes an answer (Huge rather than Large). If
it turns out to be wrong, that is the one entry to look at.

The aspect ratio itself costs nothing. `Resolution` is free-form and the engine sets its
own frustum at launch, which is how the 16:9 sizes already in `VALID_RESOLUTIONS`
(1280x720, 1600x900, 1920x1080) work. That is a separate question from the runtime frustum
correction discussed under Deimos below, which only arises when something resizes a
*running* client.

`VALID_RESOLUTIONS` is a sorted union rather than a concatenation, because the two sets
interleave: a rung's 16:9 form is wider than the stock floor, so 854x480 and 960x540 sort
above 800x600 while still being sub-floor sizes in the sense that matters — they take a UI
scale below Huge.

## Where the two values live

Not guessable, and the single most likely thing for a later change to get wrong:

| value | file | note |
|---|---|---|
| `Resolution` | `preferences.xml` | also present in `config.xml`, but that copy is a mirror the game rewrites at launch |
| `UISize` | `config.xml` | absent from `preferences.xml` entirely |

Both are patched by `_patch_window_config` in `bartlebot/launcher/launch_profile.py`,
which is also where `WindowedX`/`WindowedY` and `QuickFriendTeleport` go.

## The UI scale is the user's, and is given back

They play Wizard101 outside BartleBot, so a scale clamped for the tiled layout must not
be left behind.

- The clamp only ever goes **down**. A resolution that can carry the user's preferred
  scale gets it untouched; nothing is ever raised.
- The original is captured **once**, before the first patch, into
  `settings.json` (`game_embed.original_ui_size`). It cannot be re-read from
  `config.xml` later, because after one clamped session that file holds *our* value —
  reading it back would adopt the clamp as the preference and lose the real one. Deimos
  hit the identical bug shape with camera frustums, where treating a previously-modified
  value as native compounded the error on every cycle.
- `restore_ui_size()` puts it back and clears the capture. It runs on quit **and** on the
  next start, because a session that crashed never reached the quit path and nothing else
  would ever undo it.
- A launch at a resolution that *can* carry the preference writes it back rather than
  doing nothing. Without that, one launch at 534x300 leaves Micro behind for every launch
  after it.

The known gap: a user who changes their UI scale in-game while a clamp is captured has
that change overwritten on quit. Re-reading the file to detect it is exactly the thing
that cannot be done, for the reason above.

## Screen space caps the game size

**The user must never have to scroll to see a game window.** So available screen space
sets a *maximum* game size, and only resolutions at or below it may be launched:

```
width  = screen_avail_w - left_sidebar_width - right_sidebar_width - 2*SPLITTER_HANDLE_PX
height = screen_avail_h - bottom_section_height - MENU_AND_STATUS_PX
```

Per tile: `w + 2*FOCUS_BORDER` wide, `h + LABEL_RESERVE + 2*FOCUS_BORDER` tall. A tier
fits when `cols*tile_w <= width` and `rows*tile_h <= height`.

This reads the user's **configured** layout values (`left_sidebar_width`,
`right_sidebar_width`, `bottom_section_height`), **not** `SIDEBAR_MIN_WIDTH` and
`BOTCANVAS_MIN_HEIGHT`. Sizing against the minimums assumes the user wants their bot
canvas crushed to its floor, which nobody does — it would offer a resolution that only
fits as long as they never touch a splitter.

### ...but the live game area counts too

Those three values are startup **seeds**, written only by the General settings tab.
Nothing connects `splitterMoved` and nothing persists a window resize, so the configured
layout can describe something the user dragged away from hours ago — which is why fixing
the layout by hand used to leave a launch still refused, and only editing the defaults
unlocked it.

So `screen_fit.budget_for` takes the **larger of each axis** of two terms:

| term | is | why it is in the max |
|---|---|---|
| screen | `layout_budget(available screen)` | the floor of what is always achievable — embedding raises the game view's minimum and Qt grows the window to satisfy it, so a launch bigger than the current window corrects itself provided it fits the screen |
| live | `GameView.game_area_size` | the only thing that sees a splitter drag, and exact where the screen term estimates: sidebars, canvas, menu, status bar and the panel's own header are already subtracted, because they are simply not in it |

Taking the max rather than the live term alone is what stops a merely *small* window
refusing a resolution the screen could carry — including at construction, before the
window has been shown and the game view still holds a default size. Dragging a sidebar
*wider* therefore costs nothing, which is correct: the window minimum pushes back at
launch anyway.

Re-judging is deliberately **not** a rebuild. `_GamePlaceholder.refresh_fit()` calls
`set_unavailable` on the cards it already has, and that returns immediately when the
reason has not changed — so a drag costs a few integer comparisons per card per event and
restyles only on the frame a verdict flips. Rebuilding the cards instead would
`deleteLater` and recreate every widget several times a second for the length of a drag.
It is driven from `GameView._recompute_geometry` rather than from the placeholder's own
`resizeEvent`, because Qt defers a resize event to a hidden widget until it is shown and
the launch screen is hidden for the whole life of an embedded client.

The live area is **passed in**, never looked up: `MainWindow.live_game_area` feeds the
Launch menu and the launch guards directly, `LauncherPopup` reads it from its parent, and
`SettingsDialog(..., live_game_area=...)` carries it down to the Game tab's two resolution
pickers — so the settings combos and the Launch menu cannot disagree. A settings tab
reaching back up its parent chain to find the main window would be the coupling this
avoids.

Worked example, 1080p at the default layout (budget 1304x800), over the ladder rungs:

| clients | grid | largest that fits | scale |
|---|---|---|---|
| 1 | 1x1 | 1280x720 | Huge |
| 2 | 1x2 | 640x480 | Small |
| 3-4 | 2x2 | 534x300 | Micro |

Micro is not an edge case — it is what a four-client 1080p user actually runs.

Three and four land on Micro's **16:9** form, the smallest offered resolution. Ranking is
by area, so the widescreen form wins there while still fitting the 2x2 grid — 2*(534+12) =
1092 against a 1304 budget. Two clients keep the 4:3 640x480, which shows more game than
640x360; the 16:9 forms only ever win where they are genuinely bigger. Note that 640x480
now reads **Small**, not Regular: it is 4:3, so the narrow floor binds.

This is the **inverse** of `SettingsManager.minimum_window_size()`, which derives a window
minimum from a game size already chosen. Both are kept, and they ask opposite questions:

| | direction | measured against |
|---|---|---|
| `layout_budget` / `screen_budget` | screen → game size | the user's *configured* layout |
| `minimum_window_size` | game size → window floor | the panel *minimums* |

Once resolutions are capped to the screen, the minimum the second one derives is always
satisfiable, so it stops being able to refuse to open a usable window and does its real
job: stopping the window and the splitters being dragged to where the game gets clipped.
The splitter half of that works only because `GameViewPanel` wraps `GameView` in a layout,
so the view's minimum becomes the panel's and `QSplitter` honours it —
`test_the_game_view_s_minimum_reaches_the_panel_that_sits_in_the_splitter` is the guard.

## Prevent, do not warn; disable, do not hide

An unfittable configuration is **not launchable**, rather than launchable with a warning.
Both surfaces check the *resulting* client count:

- a **profile**, against its own account count, its resolution and the screen;
- an **additional client**, against the count it would produce — adding a third client to
  a fitting pair is refused when the 2x2 grid it forces would not fit at the running
  resolution.

Resolution is fixed at launch and a running client cannot be resized without relaunching
it, so the client arriving is the one that has to be refused. This is what removes the
need for the overflow behaviours considered earlier — warn-and-proceed, auto-detach,
relaunch-everything-smaller. Nothing has to degrade gracefully into a state that cannot
be reached.

**An entry that cannot launch is disabled with the reason, never hidden.** A profile that
silently vanishes leaves the user hunting for it with nothing on screen saying why, which
is worse than a greyed row reading "2 clients at 800×600 needs 1624×651; there is
1304×800 available — lower the resolution, launch fewer clients, or make the game area
larger." That third way out is real, not padding: the budget includes the live game area,
so dragging a sidebar narrower widens it there and then. The same applies to
the resolution pickers: a resolution too large for the current screen and client count
appears disabled with the reason, rather than being quietly dropped from the list.

The surfaces that must stay consistent: `ProfileCard`/`AccountCard`
(`gui/game_embed/launch_cards.py`), the Launch menu (`gui/main_window.py`),
`LauncherPopup` (`gui/launcher_popup.py`), and both resolution combos
(`gui/settings/accounts_tab.py`).

An earlier suggestion of a preferred size plus a scroll area is explicitly rejected: the
user should never have to scroll to see a game window.

## Beyond that, stay out of the way

Nothing picks a resolution on the user's behalf. Running four clients at 534x300 on a 4K
monitor to save the machine some work is a legitimate and probably common choice — small
resolutions are a performance lever, not only a screen-space workaround — so nothing
should push users toward "the biggest that fits". `largest_fitting_resolution` exists only
to *say* what would work.

## Why not Deimos' runtime resize

Deimos added freeform client resizing (`src/client_resizing.py`, ~760 lines, plus two asm
hooks in their vendored wizsprinter): a `WS_THICKFRAME` sizing border with a `WM_NCCALCSIZE`
WndProc hook, two pattern-scanned asm hooks that force the engine's backbuffer at runtime,
and camera-frustum correction so the 3D projection matches. Reviewed and **not adopted**.

The two efforts solve complementary halves, and the half they solved is not the blocker:

| | Deimos | XML patching |
|---|---|---|
| render at arbitrary size | yes (asm hooks) | yes (`preferences.xml`) |
| at runtime, no relaunch | yes | no — launch time only |
| per-account sizing | yes | no — one global setting |
| arbitrary aspect ratio | yes (frustum correction) | no — 4:3 and 16:9, fixed at launch |
| **HUD usable when small** | **no** | **yes (`UISize`)** |

They never touch `UISize` — no reference to it anywhere in their source — and their floor
is `_MIN_W, _MIN_H = 320, 240` with no UI-usability basis behind it. Their resize produces
a crisp, correctly hit-tested, undistorted render at 320x240 with a HUD that overflows the
client area. **Do not copy that 320x240 number.**

The cost would be two pattern-scanned asm hooks, a build-specific struct offset, and an
in-process WndProc hook — their own `VideoManagerHook` docstring records the function
being recompiled in a 2026-06 client update and needing a new pattern. That is the
per-patch offset chasing `docs/UPSTREAM.md` exists to manage, for a feature XML patching
already covers from 534x300 through 4K.

Cheap lessons kept from that review:

- **The backbuffer must equal the client size, or every click is offset.** The game maps
  clicks against the backbuffer, so a mismatch silently shifts all of them. This is what
  rules out the tempting shortcut of stretching the game window to fill its tile —
  WizWalker clicks would land in the wrong place, failing subtly rather than loudly.
  `ClientTile`'s 1:1 contract is load-bearing.
- **If an aspect ratio is ever changed at *runtime*, frustum correction must be restored
  on teardown.** This does not apply to the 16:9 rungs above — those are set in
  `preferences.xml` before launch and the engine builds its own frustum — only to a hook
  that reshapes a running client. Their code assumes first-seen extents are native, so a
  corrected frustum left behind makes the next hook read it as native and the POV creeps
  further on every cycle. We unhook and rehook routinely (`ClientHealthMonitor`,
  relogging, `_rehook_one`), so this would bite considerably harder here.
- **Exactly one backbuffer forcer per client**, if this is ever revisited: a second one
  pattern-scans `setMode` after the first hooked it, finds a `jmp` where the original
  bytes were, and fails with `PatternFailed`.
- **Per-account window config** is the model to copy if per-client resolution ever
  matters (their `apply_account_window`).

Runtime resolution change remains genuinely attractive — it would let
`_group_by_resolution` and the launch-a-batch-per-resolution constraint go away, and let
tiles resize on a splitter drag instead of requiring a relaunch. It is the expensive path
and belongs in its own issue.

## Still open

- **Splitter positions are not persisted.** They are seeds, by design — the fit check
  reads the live geometry instead, so this costs nothing there, but a dragged layout is
  still forgotten at restart.
- **Which client count a cap is sized against.** The launch surfaces size against the
  resulting count, which is exact at the moment of launching. The *settings* combos have
  no such moment, so the global one is sized against the largest profile that follows it
  and the per-profile one against that profile's own accounts.
- **Monitor changes.** The budget depends on which screen the window is on and that
  screen's scale factor, so moving the window between monitors re-evaluates fit. What
  happens to an already-running configuration that no longer fits after such a move is
  unanswered — the one path into an unfittable state that prevention at launch does not
  close.
- **`game_resolution` is still a single global setting**, with a per-profile override.
  Per-*account* resolution would need the settings model to grow; launch batching
  (`_group_by_resolution`) already anticipates it.
