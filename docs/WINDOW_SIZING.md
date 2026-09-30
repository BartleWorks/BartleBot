# Window sizing

How big a Wizard101 client is launched, why that is one setting and not two, and why an
unfittable configuration cannot be launched at all. The code is `bartlebot/game_sizing.py`
(the arithmetic, pure) and `bartlebot/gui/screen_fit.py` (the same question asked from a
widget, with a real screen behind it). Read this before changing either, or before adding
a resolution to `VALID_RESOLUTIONS`.

The problem it solves: at 800x600 — the lowest the game's own options menu offers — two
embedded clients do not fit side by side on a 1920x1080 screen, so the whole game-embedding
feature was unusable for a 2-4 client user on a standard monitor.

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

That table also says the engine's `Resolution` is *physical* pixels, which is why
`tile_size` divides it by the screen's scale factor — a 2048x1536 client is 1638x1229
logical points of tile at 125%. It holds only because the client runs DPI-aware. The exe
declares no `dpiAware` manifest entry and imports no `SetProcessDPIAware`, so what makes
it aware on the machine this was measured on is the per-exe compatibility flag
(`HKCU\...\AppCompatFlags\Layers` -> `HIGHDPIAWARE`, the "Override high DPI scaling
behavior: Application" checkbox). Without it Windows virtualizes the process and the same
2048x1536 request becomes a 2560x1920 physical window — bigger than the tile the fit
arithmetic reserved, not smaller. Unverified on a machine without the flag; if tiles ever
clip on a scaled display, this is the first thing to measure.

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
against.** A narrow client has to run *below* its render floor — see "The HUD draws
before it is reachable" below. 400x300 is not offered at all as a result.

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
centers the interact prompt. On a narrow client the prompt is wide enough to cover the hub
and dorm buttons — and those buttons are clicked *by coordinate*: `node_helpers.click_by_path`
resolves the window and hands its center to wizwalker's `mouse_handler.click_window`, so
whatever is drawn on top takes the click. A covered button therefore fails **quietly**:
`click_by_path` returns True (the window exists and is not grayed), the click lands on the
prompt, and the node burns its `TRIGGER_TIMEOUT` waiting for a loading screen that never
starts. Four nodes ride on that button group — `teleport_to_world_hub`, `wizard_city`'s
go-home (which `go_to_world` is built on), `mark_location` and `teleport_to_marked_location`.

There are two ways out, and the ladder uses both.

### Narrowness is priced in ladder steps

How far below the render floor a client has to run depends on its **aspect ratio, and
nothing else**:

| band | steps below the render floor |
|---|---|
| 16:10 or wider | 0 |
| 4:3 | 1 |
| narrower than 4:3 | 2 |

Every pairing below was driven on a live client with a sigil prompt up, checking whether
the prompt covered the hub and dorm buttons. `MEASURED_PAIRINGS` in
`tests/test_game_sizing.py` is this table, and the model is held to it — **if a change and
a measurement disagree, the measurement wins.**

| resolution | ratio | max scale | | resolution | ratio | max scale |
|---|---|---|---|---|---|---|
| 534x300 | ~16:9 | Micro | | 480x360 | 4:3 | Micro |
| 640x360 | 16:9 | Tiny | | 560x420 | 4:3 | Tiny |
| 746x420 | ~16:9 | Small | | 640x480 | 4:3 | Small |
| 854x480 | ~16:9 | Regular | | 720x540 | 4:3 | Regular |
| 960x540 | 16:9 | Large | | 800x600 | 4:3 | Large |
| 1128x634 | ~16:9 | Huge | | 1024x768 | 4:3 | Large |
| 1280x720 | 16:9 | Huge | | 1600x1200 | 4:3 | Large |
| 1680x1050 | 8:5 | Huge | | 2048x1536 | 4:3 | Large |
| 1920x1080 | 16:9 | Huge | | 1280x1024 | 5:4 | Regular |

**It is the ratio, not the size — that is the whole finding.** 2048x1536 is 4:3 with 1536
lines of height and still will not take Huge. So no amount of screen makes a 4:3 client
outgrow the occlusion, and **no 4:3 resolution reaches Huge at all**: Huge is the top rung,
and one step down from the top rung is Large.

This replaced a per-rung `min_narrow_resolution` column that stored the same thing as a
floor. Every entry in that column was, exactly, the *next* rung's render floor — Micro's
was 480x360, which is Tiny's floor, and so on for all five. Only Huge's could not be
derived that way, because there is no rung above it, and the invented value put there
(1280x960) was the one number in the file that was wrong. It made Huge look reachable at
4:3 given enough pixels, which is what 1600x1200 and 2048x1536 disprove. **Do not
reintroduce a per-rung narrow floor**; it is one subtraction, and the column existing is
what let a guess sit next to five measurements looking like data.

1280x1024 is the other correction from the same round. It had been recorded as Huge on the
reasoning that 5:4 is "close enough to 4:3, and it is big anyway". It is Regular.

**400x300 is therefore not offered at all.** It renders at Micro, but it is 4:3, and Micro
has no step to give. `has_unoccluded_ui_size` is the guard, and a test holds all of
`VALID_RESOLUTIONS` to it — that check, not the render floor, is the bar a new entry has to
clear. A settings file that still holds 400x300 is migrated on read by the
`game_resolution` getter rather than launched.

### Wide: take the width instead

`widescreen_form` derives a second resolution per rung — **the same height, the width
widened to 16:9** — which moves the edge-anchored buttons away from the centerd prompt, so
the rung pays no steps and keeps the render floor's scale:

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

**These are the game's own series, continued downward.** `widescreen_form` rounds the width
to an even pixel, and fed the heights from the game's own dropdown it returns the game's
own entries: 634 → 1128x634, 990 → 1760x990, 720 → 1280x720, 1080 → 1920x1080. So the
sub-floor widescreen rungs are not an aspect ratio of our invention — they are the rule the
game already applies above 800x600, applied below it.

`is_widescreen` draws the band at **16:10**, compared by cross-multiplying integers so a
ratio sitting exactly on the line cannot be decided either way by a float. This is a
**measurement**: 1680x1050 is 8:5 and takes Huge with the prompt clear.

**Do not tighten the band toward exact 16:9.** The even-pixel rounding above lands some
derived rungs fractionally *under* 16:9 — 746x420 is 1.7762 against 1.7778 — so an exact
16:9 test would price them as narrow and cost each one a scale it does not owe. 7:4 is the
tightest safe boundary if it ever has to move.

The aspect ratio itself costs nothing. `Resolution` is free-form and the engine sets its
own frustum at launch, which is how the 16:9 sizes already in `VALID_RESOLUTIONS`
(1280x720, 1600x900, 1920x1080) work. That is a separate question from the runtime frustum
correction discussed under Deimos below, which only arises when something resizes a
*running* client.

## What is offered, and why those

`VALID_RESOLUTIONS` is a sorted union rather than a concatenation, because the two sets
interleave: a rung's 16:9 form is wider than the stock floor, so 854x480 and 960x540 sort
above 800x600 while still being sub-floor sizes in the sense that matters — they take a UI
scale below Huge. "Sub-floor" there is about the *scale*, not the size: the 4:3 entries at
and above 800x600 top out at Large too.

Every entry has one of exactly two provenances, and a test enforces it: the game's own
dropdown offers it, or it is a ladder rung run through `widescreen_form`.

The dropdown offers seventeen sizes. Four are left out as sizes no monitor is — 1128x634,
1760x990, 3072x1728, 3200x1800 — rather than padding every picker with entries nobody
picks. Two entries that were *not* on the dropdown have been removed: 1280x960, which is
where the mistaken Huge narrow floor came from, and 1280x800, which existed mostly as the
one resolution sitting exactly on the 16:10 boundary. That boundary still has a live case
in 1680x1050, and a measured one.

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

Re-judging is deliberately **not** a rebuild. `card_columns.refresh_column_fit()` calls
`set_unavailable` on the cards each column already has, and that returns immediately when the
reason has not changed — so a drag costs a few integer comparisons per card per event and
restyles only on the frame a verdict flips. Rebuilding the cards instead would
`deleteLater` and recreate every widget several times a second for the length of a drag.
It is driven from `GameView._recompute_geometry` rather than from the placeholder's own
`resizeEvent`, because Qt defers a resize event to a hidden widget until it is shown and
the launch screen is hidden for the whole life of an embedded client.

The live area is **passed in**, never looked up: `MainWindow.live_game_area` feeds the
Launch menu and the launch guards directly, `LauncherPopup` reads it from its parent, and
`SettingsDialog(..., live_game_area=...)` carries it down to the Game tab's resolution
picker *and* to the fit pass over its two card columns — so the settings combos and the
Launch menu cannot disagree. The tab is handed that size rather than measuring itself:
the dialog is not the game area, so measuring the tab would answer a question about the
settings window. A settings tab
reaching back up its parent chain to find the main window would be the coupling this
avoids.

Worked example, 1080p at the default layout (budget 1214x800), over the ladder rungs:

| clients | grid | largest that fits | scale |
|---|---|---|---|
| 1 | 1x1 | 960x540 | Large |
| 2 | 1x2 | 560x420 | Tiny |
| 3-4 | 2x2 | 534x300 | Micro |

Micro is not an edge case — it is what a four-client 1080p user actually runs.

Three and four land on Micro's **16:9** form, the smallest offered resolution. Ranking is
by area, so the widescreen form wins there while still fitting the 2x2 grid — 2*(534+12) =
1092 against a 1214 budget. Two clients keep a 4:3 form, which shows more game than the
16:9 of the same rung; the 16:9 forms only ever win where they are genuinely bigger.

The rungs here are one lower than they used to be, and the sidebar width bump is why: the left sidebar's
floor went from a bare 220px to a measured 390, and 170px off the budget costs a solo user
1280x720 and a pair 640x480. That is intrinsic to any honest floor above ~312px rather
than an artifact of where this one rounded — the old number simply described a panel whose
content was overlapping itself.

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

### The floor is only a floor if the panes honour it

`required_window_size` is not a description of the window — it is a *claim* about it, and
Qt holds the window to the larger of the claim and what the layout inside actually needs.
So every term in it has to be a size the corresponding pane will really go down to:

```
width  = left_sidebar_floor() + right_sidebar_floor() + game_w + 2*SPLITTER_HANDLE_PX
height = game_h + GAME_PANEL_CHROME_PX + BOTCANVAS_MIN_HEIGHT + MENU_AND_STATUS_PX
         + SPLITTER_HANDLE_PX
```

where `game_w, game_h` is `required_game_area` — the tiles' own chrome included, which the
old sum left out.

A pane that lets its *content* set its floor breaks the claim silently. Both sidebars used
to, and the widest thing either can hold is not in it at construction: the tab sets are
mode-swapped, so the 562px parameter panel only arrives when the user switches to edit
mode. On a 3840x2160 screen a 2560x1440 launch therefore passed the fit and produced a
window with a 3770x2100 floor — against a 2081px work area, which Windows refused with
`QWindowsWindow::setGeometry: Unable to set geometry` and a clipped window.

The fix is that each pane sets the arithmetic's term on itself with `setMinimumSize`. That
is not the same as being merely narrow: an explicit minimum is the one input that wins
outright over a layout's own minimum in Qt's `qSmartMinSize`, so it caps the pane rather
than competing with its content. Below that width the content clips, or scrolls where a
`QScrollArea` is already in the way, which is the right trade for a size only reachable by
dragging a splitter to its stop.

Two of the terms are floors with nothing on the other side of them, so they carry their own
constraints: `SIDEBAR_MIN_HEIGHT` must stay under the shortest tile any offered resolution
produces, and `BOTCANVAS_MIN_WIDTH` under the narrowest window the ladder implies —
otherwise a sidebar or the canvas, which have no height and width term respectively, become
the floor instead of the game view. `tests/test_window_minimum.py` holds all of it together,
including the 4K case above as a regression.

### The sidebar floors are measured, not fixed

Both of them. The right sidebar's player card ends in a row of three buttons — `Entities`,
`UI Tree`, `Unhook` — and those, their padding and the scrollbar beside them are all font
metrics: the layout that fits in 300px at 100% system text needs 375 at 125%. A pixel
constant would have to choose which machine to be wrong on. Pinned low it clips the row on
a scaled display, which is how this came up; pinned high it charges every 1080p user 75px
of sidebar for a text size they are not running, and 75px there is a whole rung of the
ladder — 1280x720 solo becomes 960x540.

So `RIGHT_SIDEBAR_MIN_WIDTH` is the floor **at the reference metrics** (Segoe UI 9pt at
100% under `windowsvista`), and `game_sizing.right_sidebar_floor(log_row)` re-derives it
from what the panel measures here, never below the reference. 300 is deliberately the old
default sidebar width, so a machine at the reference sees no change at all.

**A floor is the forcing row plus the chrome around it**, and only the row is measured:

```
right_sidebar_floor = measured_log_header_row + RIGHT_SIDEBAR_CHROME_PX   # 19
left_sidebar_floor  = measured_filter_row     + LEFT_SIDEBAR_CHROME_PX    # 97
```

The chrome — margins, frames, the scroll bar, the gutter, the pinned control widths — is
**added, never scaled**, because a font does not widen any of it. Both floors used to be
the whole reference width multiplied by the larger of a text-height ratio and the row's
own, which charged the font's growth against all of that as well. It over-charged by
roughly half: raising the body text from 12px to 14px grew the left row 11% and its floor
19%, and grew the right floor 19% against content that had not moved a pixel. That was
129px of sidebar between the two panels, and on a 1080p screen it took three- and
four-client launches away outright.

**The row carries the style as well as the font**, which is what the old text-height term
was reaching for. A `QPushButton` is never narrower than its style's own minimum width,
and that is not a font metric: 75px under `windowsvista`, 80 under Fusion, 81 under
`windows11`. Measuring a row built from real widgets picks that up wherever it lands,
including on a style no Windows version here has yet — so one measurement answers both
questions and the two-term scale is gone.

**The right sidebar's forcing row is the Log tab's header**: the `Min level:` caption, the
level combo, `Export` and `Clear`. It used to be the player card's tool row, and that row
no longer exists — the card carries icon verbs and elided rows now — so the floor was a
button's width standing in for a row that had been gone for a while.
`gui/sidebar/log_panel.header_row_floor()` measures it, built from the panel's own widgets
so the two cannot drift. Mostly pinned widths, so it barely moves with the font: 281px at
the reference, 289 at 14px body text.

Everything inside the running app takes those numbers from **`gui/screen_fit.right_sidebar_floor()`**,
which measures `QApplication.font()` — never a particular widget's. The width the sidebar
enforces and the width `layout_budget` subtracts have to be the same number, and two call
sites measuring two different fonts is the same class of bug one level down. `RightSidebar`
re-measures on `FontChange`, because the theme is applied to the application after the
widget is built.

`test_the_player_card_fits_the_right_sidebar_under_every_available_style` sweeps
`QStyleFactory.keys()` so the question CI was asking gets asked locally too.

**The left sidebar is the same shape of problem**. Most of what it holds elides or
scrolls — which is what its old 220px constant was justified by — but the quicksell
keep-list picker's filter row does not: a search field, a category `QComboBox` and a
`Kept only` `QCheckBox` side by side, and the last two carry a `Minimum` size policy,
which has no shrink flag. At 220px the checkbox was drawn over the dropdown. So
`LEFT_SIDEBAR_MIN_WIDTH` is 390 — the row's 293px at the reference metrics plus the 97px of
option gutter, scroll content margins, scroll area frame and scrollbar that
`LEFT_SIDEBAR_CHROME_PX` holds — and `game_sizing.left_sidebar_floor(filter_row)` adds the
two the same way, from `gui/screen_fit.left_sidebar_floor()`. Everything else on that
surface elides or wraps by design, so it sets no floor; that is why there is no
text-height term.

Its probe is built from the *real* widget types, including the actual `SearchLineEdit`
with its clear button and the widest label the combobox can carry. That is not fussiness:
a bare `QLineEdit` stood in for the search field first and reported 27px less, which held
the measured scale at 1.0 under `windows11` while the row it had to hold grew by 10px —
the identical failure to the one above, found by the identical style sweep.

**Still an estimate on the screen side.** `screen_budget` does *not* subtract
`GAME_PANEL_CHROME_PX`, the horizontal splitter handle, or the window frame — roughly 78px
of height. It is why the worked example above offers 1280x720 to a 1080p screen that can
only just carry it. Tightening it would re-tier the offered ladder, so it is left as the
known estimate `budget_for` already describes it as.

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

**A surface showing many entries at once says the reason once, not once per entry.**
`fit_details` returns a `FitProblem` rather than a string, so a caller can take the half
it needs: `numbers` ("Needs 1624×651 · 1304×800 available") belongs to the entry, and the
ways out belong to the surface, because they are properties of the layout rather than of
any one profile. `fit_problem` still returns the whole sentence, which is what a tooltip
and a disabled menu entry take — those are read one at a time and have nothing to repeat.

Each column carries a `_FitBanner` under its header (`gui/game_embed/card_columns.py`),
linking to the resolution setting so advice the user has to go hunting to follow is not
half an answer. **The two columns are worded separately, and must stay that way** — they
refuse different things:

| column | the banner says | the cards say |
|---|---|---|
| Accounts | "Your default game resolution (2048×1536) needs 1650×1279, but only 1200×800 is available. Either lower the default game resolution or increase the size of the game view panel." | nothing — they stay grey and keep showing their characters |
| Launch Profiles, none fitting | "No launch profiles fit in the game view panel at its current size (1304×800). Lower the resolution they launch at, increase the size of the game view panel, or launch fewer clients." | each its own "Needs …" |
| Launch Profiles, some fitting | "Some launch profiles do not fit in the game view panel at its current size (1304×800) and have been disabled." | each its own "Needs …" |

Three things in that table are the point rather than phrasing:

- **The accounts banner offers two ways out, not three.** "Launch fewer clients" cannot be
  taken there — an account card launches exactly one — and a remedy the user cannot act on
  reads as the app not knowing what it is refusing.
- **The accounts banner names the *default* resolution**, because that is the setting to
  change: an account card never launches at a pinned one.
- **The profiles banner is sized against the panel, not a resolution.** Profiles may each
  pin their own, so naming one would point at a setting that is not the problem for the
  cards beside it. It is also why the numbers stay on the profile cards, where they differ,
  and come off the account cards, where every one of them is the same number — that
  repetition is the bug this shape exists to prevent, and it is easy to reintroduce by
  "simplifying" the two columns into one message.

The surfaces that must stay consistent: `ProfileCard`/`AccountCard`
(`gui/game_embed/launch_cards.py`), the Launch menu (`gui/main_window.py`),
`LauncherPopup` (`gui/launcher_popup.py`), and both resolution combos — the global one in
`gui/settings/accounts_tab.py` and the per-profile one in
`gui/settings/profile_editor.py`, which share `mark_unfittable_resolutions`.

The cards themselves are drawn on three surfaces, and only one of them refuses. The Quick
Launch screen greys an unfittable card and swallows its clicks; the Game settings tab and
the profile editor show the same card at full contrast with the banner above it, because
a click there edits — or, in the editor, does nothing at all — and editing is what the
refusal is asking for. That split is the card's `CardMode`, and `refuses_when_unfit` is
what reads it — never condition on the fit result itself to decide whether to grey
something.

### Which client count the settings combos judge against

The launch surfaces size against the count they are about to produce, which is exact.
The two settings combos have no such moment, and they answer it differently:

| combo | sized against | why |
|---|---|---|
| a profile's own resolution | that profile's account count | it is the number of clients this value will actually have to hold |
| the global default | **one client** | the common case, and the only one it is the sole answer for |

The global one used to be sized against the largest profile that inherits it. That is
defensible — those are launches this value decides — but it greys out most of the list for
anyone who merely keeps a four-account profile around, and four accounts is rare where one
or two is not. A user running several clients has a launch profile, and that profile has
its own combo judged against its own accounts. Judging the global default by the rarer case
made the common one look broken.

This is safe because neither combo is a gate. A profile that inherits a resolution too
large for its account count is still refused at launch, by `ProfileCard` and the Launch
menu, with `fit_problem`'s sentence naming both numbers and all three ways out.

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
- **Monitor changes.** The budget depends on which screen the window is on and that
  screen's scale factor, so moving the window between monitors re-evaluates fit. What
  happens to an already-running configuration that no longer fits after such a move is
  unanswered — the one path into an unfittable state that prevention at launch does not
  close.
- **`game_resolution` is still a single global setting**, with a per-profile override.
  Per-*account* resolution would need the settings model to grow; launch batching
  (`_group_by_resolution`) already anticipates it.
