# GUI conventions

Conventions for everything under `bartlebot/gui/`. The app-wide rules are in the root `CLAUDE.md`; these are the ones that only bite inside a widget.

## GUI theming
Use `THEME` singleton from `bartlebot/gui/theme.py`. No hardcoded colors in widget code.

Anything a widget builds from `THEME` is a snapshot of whichever theme was active at
the time, and Qt never recomputes it: `QApplication.setStyleSheet()` delivers
`StyleChange` to every widget, but a stylesheet the widget set on *itself* always
wins and is left exactly as it was. The widget has to re-derive it.

**Inherit `ThemedMixin` (`bartlebot/gui/themed.py`) and put the work in
`apply_theme_styles`.** It is enforced: `tests/test_theme_refresh_guard.py` fails any
class that builds a `THEME`-derived value without either the mixin or a
`changeEvent` that handles `StyleChange`.

```python
class MyPanel(ThemedMixin, QWidget):          # mixin FIRST — see below
    def __init__(self) -> None:
        super().__init__()
        self._label = QLabel("hello")
        self.refresh_theme_styles()           # call this; implement the other

    def apply_theme_styles(self) -> None:
        self._label.setStyleSheet(f"color: {THEME.text};")
```

Four things that are not obvious:

- **`refresh_theme_styles()` is what you call; `apply_theme_styles()` is what you
  write.** The first carries a re-entrancy guard, and `setStyleSheet` on `self`
  delivers `StyleChange` synchronously back into `changeEvent`. Calling the hook
  directly applies twice, or recurses until the app dies in its own constructor.
- **The mixin goes first in the base list.** `class P(QWidget, ThemedMixin)` does not
  merely skip the mixin — sending `StyleChange` to such a widget aborts the process
  inside `sendEvent`, with no exception. The guard test checks base order statically
  because nothing can catch it at runtime.
- **A bare `svg_icon(name)` is themed.** Its `tint` defaults to `THEME.icon_tint`, so
  an icon set once and kept goes stale with no stylesheet anywhere to grep for. Pass
  `tint=""` for an icon that should keep its own colours.
- **A factory cannot restyle what it returns.** `field_label(text)` hands a label to a
  caller that has nothing to re-apply, so the staleness lands on the *caller* — which
  is why the guard blames a themed helper's callers. Return a
  `ThemedTextLabel` (`bartlebot/gui/widgets/themed_label.py`) instead; it owns its own
  colour, named by token so it is re-read rather than snapshotted.

Widgets that read `THEME` at paint time (e.g. `QGraphicsItem.paint()`, `_RowWidget`)
hold no baked value and need none of this.

**Never destroy a widget from inside `apply_theme_styles`.** A theme switch arrives as
`QApplication.setStyleSheet()`, which is iterating over every widget when it does, and
freeing a subtree mid-iteration aborts the process with no traceback. `_DetailPanel`
(`node_browser_dialog.py`) is the one place that rebuilds rather than restyles — its
`QScrollArea.setWidget()` deletes the old container outright — so it defers the rebuild
to the next event-loop turn via a `QTimer` parented to itself, which dies with the panel
instead of firing into a destroyed widget.

Two failure modes the guard cannot see, so check them by hand:

- **A colour inside rich text.** The required-field marker in `param_panel` is a
  `<span>` carrying `THEME.error` in the label's own *text*, so restyling means
  rewriting the text. The guard looks for `setStyleSheet`.
- **A widget styled only on the next switch.** The guard checks that
  `refresh_theme_styles()` is called *somewhere* outside `changeEvent`, not that
  construction reaches it. `WikiImportDialog` passed while its only caller was
  `_set_status`, and opened unstyled.
- **A hand-written `changeEvent` that only restyles some of the widget.** The guard
  asks whether a class handles `StyleChange`, not whether it handles all of it, so a
  partial handler passes and reads as done. `_ParamPanel` restyled three of its ten
  themed widgets that way, leaving the node help button in the old palette. Prefer
  the mixin — one hook that restyles everything is easier to keep complete than a
  handler someone extended once.

## Popup menus

Build every `QMenu` through `bartlebot/gui/menu_utils.py` — `popup_menu(anchor)` for a
menu built per invocation, `anchor_menu_to_window(QMenu(btn))` for a long-lived one
handed to `QToolButton.setMenu()`.

Qt gives a popup the `QWindow` of its parent's nearest *native* ancestor as transient
parent. Embedding a game client makes `ClientTile` native, and Qt then promotes its
siblings — the left sidebar, the canvas — to native windows of their own. A menu
parented to a button inside one resolves to a `QWidgetWindow` that is not top level,
which Qt refuses outright: `... must be a top level window`, leaving the popup
with no transient parent at all.

The helper swaps the menu onto its top-level window on `aboutToShow` and hands it back
on `aboutToHide`. The swap cannot happen at construction: a menu built in a widget's
`__init__` has no window yet, and `window()` would return the widget itself.

A menu with no parent, or one parented directly to a window or dialog, is already fine.

## Buttons: `ActionButton` and its four weights

Use `ActionButton` (`widgets/action_button.py`), not a bare `QPushButton`. Fusion's
default chrome is a beveled 3D frame sized to its own label, which flattens SVG icons
against it and makes a row of buttons size each one to its own text.

`ActionStyle` says how much weight a button carries, on Bootstrap's model and for its
reason — a row of buttons cannot say which one it expects unless they are drawn at
different weights:

| style | look | for |
|---|---|---|
| `PRIMARY` | filled with `accent` | the one thing the surface expects you to do |
| `SECONDARY` | outlined in `accent`, unfilled | a real alternative to it |
| `TERTIARY` | no accent — **the default** | everything else |
| `CAUTION` | filled with `error` | a verb that costs something to undo |

Three rules that are easy to get wrong:

- **`TERTIARY` is the default, and a style is opted into.** Most rows here are
  equal-weight verbs beside a destructive one (`+ New` / `Duplicate` / `Delete`), and
  nothing in such a row should look more important than the dangerous button next to
  it. At most one `PRIMARY` per surface.
- **A weight varies colour and nothing else.** Every style shares the padding, the
  radius, the height and one disabled look. A style that also changed the shape would
  stop being a weight.
- **`fill=False` for a trailing pair.** These expand by default so a row of them splits
  its width evenly. A dialog's `[Cancel] [Save]` sits *after* a stretch, and an
  expanding button there eats the stretch and spreads the pair across the whole footer.

Issue #47 is the rollout; `combat_strategies/dialog.py` is the worked example.

## Section headings

Three, and they are not interchangeable:

| widget | look | for |
|---|---|---|
| `SidebarSection` | disclosure triangle + bold label | a region that **folds** |
| `CardColumn`'s heading | centred bold label + full-width rule | a **column of cards** |
| `RuleHeading` (`widgets/rule_heading.py`) | left bold label + rule to the right | the regions of a **form** |

A triangle promises a fold, so do not use `SidebarSection` for a region that does not
have one; a centred heading over left-aligned fields reads as belonging to something
else. `RuleHeading` draws its rule from `THEME.border` rather than as a sunken `QFrame`
on purpose — Qt draws a sunken HLine from the palette's Mid/Light pair, which the dark
theme leaves all but invisible against `surface`.

It also carries its own leading gap, more above than below, so a surface using one gets
the rhythm without setting a spacer — and so the rule sits nearer what it introduces
than what it follows, which is what makes it read as a heading rather than a divider.

## Lines need room, or they read as walls

The commonest way a screen here looks crowded is not too many lines; it is lines with
nothing beside them. Two rules:

- **A line earns its place by separating things that are otherwise ambiguous.** Chrome
  *around* a group — a border on the list widget holding a column of cards, the box
  `QTabWidget::pane` used to draw around a whole page — separates nothing: the cards
  draw their own edges, and a splitter handle already says where a pane stops. The pane
  keeps only its `border-top`, which is load-bearing (it anchors the tab strip). Borders
  on the individual elements *stay* — a card, a chip, a field is a thing, and the outline
  is how it says so.
- **Inset the contents of anything bordered.** A tab page whose fields run edge to edge
  inside the pane reads as crowded however few lines are on it. `CombatStrategyDialog`
  is the worked example: a page inset on all four sides, and a wider gutter on the
  detail pane, because the gap the eye sees beside a splitter handle is the handle plus
  the margin while the gap beside a border is only the margin.

## Account colours, and what a coloured thing may claim

A palette slot per account (`shared/account_slots.py`), resolved to a colour against the
live theme (`gui/account_accent.py`). It is a **match target** — find the card wearing
the same hue — never an identifier, and three rules keep it one:

- **Hold the slot, not the hex.** A widget that stores `account_accent(3)` repaints
  stale after a theme switch, with nothing in any stylesheet to grep for.
- **Outline and tint, not a saturated fill.** Four filled pills in a row make the row
  the loudest thing on the screen when the point of the surface is a *name*. See
  `AccountPill`.
- **No account known means no colour, never a default one.** A colour is a claim about
  which account is behind something; picking one for an unidentified client, or for an
  account that has been deleted, puts a hue on screen that matches nothing anywhere
  else. `account_stripe_keys` returns `None` for exactly these, and callers must keep it.

A **player slot** is coloured but never renamed: `P1` stays `P1` on the pill and the
combat-order chip, with the account's name in the tooltip. A `DuelConfig` stores slots,
is authored offline and outlives whichever account is in slot 1 tonight — a pill reading
`Malvin` would be a claim the stored data does not make, and the same card would read
differently depending on what happened to be hooked. Issue #40 is the rollout.

On the Combat tab every `P#` is a `PlayerBadge` (`widgets/player_pills.py`) — the deck
cards, each assignment line and the combat order — and each deck card carries the same
left stripe as the player card for its slot. Colours are held **per slot, not per
position**, so a `P3 → P1` order puts slot 3's hue first. The tab polls its account
source on a timer (`PLAYER_COLOR_REFRESH_INTERVAL`) only while it is visible, because an
identity arrives after its client and nothing announces it; a card built between ticks
takes the last answer at construction, since a tick with an unchanged answer repaints
nothing.

The Players tab's `P#` pill is the same `PlayerBadge`, keyed off the card's stripe. The
game view's tile caption is painted (`client_tile.tile_pill_colors`) in the same capsule
shape and colours at its original size, and a focused window's border is its account's
colour, falling back to the accent. `GameView.sync_tiles` pushes the key from
`TileSlot.username` on every tick, alongside the captions.

## Never show a widget before it has a parent

`setVisible(True)` — or `show()` — on a widget with no parent makes that widget a
**top-level window**. Qt maps it there and then, and unmaps it again the moment the
next `addWidget` reparents it, so a list of rows assembled this way flashes one real
frame per row on screen. It is invisible in a test that only inspects the finished
tree, because by then nothing is a window any more.

Build first, then set visibility:

```python
row = build_gutter_row(bar, edit_btn, help_btn, body)   # this is what parents them
edit_btn.setVisible(self._editable)                     # only now
```

`setVisible(False)` is safe, and so is showing a child of a hidden parent — only the
parentless case opens a window. It bites hardest where a rebuild is on a hot path:
one click of a check box in Edit mode mapped and unmapped **eight** windows on a
shipped bot, because each row's description and Edit button were shown while still
loose. `test_building_a_row_never_shows_a_parentless_widget` is the guard, and the
way it catches it is worth reusing — an event filter on the `QApplication` recording
every `Show` delivered to a widget whose `parent()` is `None`.

The inverse trap sits one line away: **a hidden widget in a `QBoxLayout` reserves no
space at all.** The layout skips it outright rather than laying out a zero-size cell,
so a spacer or placeholder that is parented but never shown holds nothing open and
every widget after it lands a row early — with the layout, the size hints and the
geometry all internally consistent about the wrong answer, which is why it reads as an
arithmetic bug rather than a visibility one. `CardColumn._begin_drag` is the case:
the placeholder standing in for a lifted card has to be `show()`n by hand, after the
parent it was constructed with. (`setRetainSizeWhenHidden` is the other way, and is what
the launch cards' hover-revealed buttons use — but it only applies to a widget the layout
is still asked about.)


## Card art is optional, and missing is the ordinary case

Spell art is the game's own and is never in this repo: the user extracts it with
`tools/extract_card_art.py`, and `bartlebot/card_art.py` is the only thing that knows
where it lands. **Every lookup can return `None`** — most installs never run the
extractor, a few templates name a texture the game's own files do not contain, and the
names snapshot can be older than the game. So a surface showing art needs a form that
works without it, and must never log the absence as an error.

`_DeckCard` is the worked example: the strip is hidden outright when nothing resolves,
the summary line carries the counts either way, and a single card with no art is
skipped rather than given a placeholder — a placeholder per missing card turns an
install with no art at all into a grid of grey squares.

**A deck is listed by `DeckCardList` (`widgets/deck_card_list.py`), on both surfaces
that list one** — the sidebar card's unfolded rows and the dialog's Deck Presets page.
It was the sidebar's own rows against `QLabel(f"  {name} × {count}")` off the raw dict,
so the library page listed a deck unsorted, unheaded and unillustrated while the
sidebar three panels away did all three. The grouping and the order are
`deck_groups`' on both, never the surface's, and a row whose card has no art is the
row minus its tile — the count and the name still carry it.

Look art up by the **template name** (`deck_io`'s own key), never the display name: one
display name covers many templates, and a tiered variant draws its base spell's
texture, so two identical-looking tiles can be different cards. That is what the tile's
tooltip names the template for.

The same cache holds the school and archmastery icons, under `card_art.icon_path` and
logical keys rather than texture names. `ArchmasteryBadge` is the worked example, and
the fallback is the same shape: the deck card keeps its `Archmastery: Fire · always
set` line, on a row of its own, for every install that has not extracted them. Two
rules it is easy to lose:

- **Both icons or neither.** A lone archmastery spiral says the deck has an opinion
  without saying what it is, so `has_icons()` is false unless the mark *and* the
  medallion both resolved — which also covers an icon set missing the one school this
  deck happens to name.
- **The medallion names the school only when the school will actually be set.** A deck
  can carry a school it will never apply: the override resolves through an app setting
  whose shipped default is off. A Fire medallion on a card whose apply leaves
  archmastery alone is a claim about the wizard that the deck does not make, so the
  universal mark stands in. That means the badge asks `resolve_apply_archmastery`, not
  `deck.archmastery_school` alone.

The bot library's category list uses these too, under the same rule: each folder under
`bots/` is a game badge category (`bartlebot/bot_folders.py`), the row wears that
category's badge when one was extracted, and a row with none is its caption alone — at
the compact height, since only a row with an icon reserves room for one. Rows are looked
up by the folder name stored in `Qt.UserRole`, never by the caption, which comes from the
game's own wording.

Bot tags use them too (`card_art.tag_icon_path`, drawn at `TAG_ICON_PX` = 20 — 16 was
too small to read). Each surface degrades to what it was before: a picker row shows its
group dot, and a `TagChip` or `TagPill` its plain text form. With a medallion, both are
**drawn** by `paint_medallion_pill`: exactly `TAG_ICON_PX` tall, so the medallion *is* the
rounded left end, and the group-coloured body starts at its centre and carries the name.
An icon-only pill was tried and dropped, because the art alone was not enough to read a
card by. A stylesheet cannot do this shape: padding and border-radius will not put an icon
flush with a rounded end. The Type tags have no art, so they are always text, and a mixed
row is the normal case.

An icon is artwork in its own colours, so a widget drawing one reads no `THEME` value
and needs neither `ThemedMixin` nor a `changeEvent` — the guard test agrees, and this is
the same exemption the card art tiles take.

## A widget that wraps has to say so in its size policy

`FlowLayout` wraps correctly and the parent still reserves **one row**, because a
`QBoxLayout` asks the *size policy* whether an item's height depends on its width, and
a widget that has not said so answers no. The deck card's art strip laid out fourteen
tiles, drew seven and clipped the rest, in a card exactly as tall as a five-tile one —
with the layout, the size hints and the geometry all internally consistent about the
wrong answer, which is why it reads as a painting bug rather than a layout one.

Three things together, or none of them work: a `QSizePolicy` with
`setHeightForWidth(True)`, a `heightForWidth` that delegates to the layout, and a
`sizeHint` that answers for the width the widget *has* — `FlowLayout.sizeHint` is one
row of everything, which is what it would like rather than what it needs.
`test_the_strip_reports_a_taller_height_when_it_has_to_wrap` is the guard.

## The first offscreen render costs 150-200ms

A widget with a `QGraphicsEffect` on it, and a `QWidget.grab()` or `render()` for a
`QDrag` pixmap, are all painted into an offscreen surface rather than into the window.
Qt flushes the whole top-level's withheld move and resize events before the first such
render — it holds them back from hidden widgets, so every unopened tab, menu and dialog
page built at startup is sitting on a layout that has never run, and they all run at
once. It is the same 150-200ms whether the target pixmap is full size or **1x1**: the
cost is the layout flush, not the paint. The second one is free.

Left alone it lands wherever the first offscreen render happens to be, which is a
gesture — the first card drag of a session stalled for a fifth of a second while the
shadow went on, and no later one did. Call `warm_offscreen_render(widget)`
(`gui/offscreen_warmup.py`) a turn after the surface is shown instead; it is per window,
because the flush is, so a dialog does not inherit the main window's. `CardColumn.showEvent`
is the pattern. It is per window *and* once: the flag means the layouts outstanding at
that moment ran, not that the window is immune. Nothing warms the other offscreen sites
(`favorites_bar`, `combat_order_widget`, `duel_strategy_editor`) — if one ever stalls,
warm its window; do not add a call per widget class.

## qasync + Python 3.13 task re-entrancy rule

**Never call a blocking Qt method (e.g. `QDialog.exec()`, `QMessageBox.exec()`) from inside an `async` function that is running as an asyncio Task.**

Python 3.13 enforces that only one asyncio task may be executing at a time. `QDialog.exec()` re-enters Qt's event loop synchronously, but the calling task's frame remains on the Python call stack. While the dialog is open, any asyncio sleep or future that completes will attempt a `task_wakeup()` callback. Python 3.13 detects the calling task is still "being executed" and raises `RuntimeError: Cannot enter into task while another task is being executed`. qasync catches and logs this error, but the woken task is permanently stuck — its sleep Future is consumed and it never resumes.

**Rule:** If you need to show a blocking dialog before starting an async operation, do it in the synchronous Qt slot *before* calling `asyncio.create_task()`. Example pattern in `AppController._on_run_requested()` — the safety warning dialog runs there, in the slot, so no asyncio task exists yet when `.exec()` blocks.

This bug caused the client section sidebar (zone/XYZ display) to freeze for the entire duration of any bot run that included a node with `safety_warning` set in its `NodeSpec`. The `SafetyWarningDialog.exec()` call inside the async `_start_bot()` task was killing the `_refresh_loop` and `_client_loop` tasks on every run.

## Background task lifetime in GUI widgets

Any `asyncio.Task` or `QThread` started inside a `QWidget` or `QDialog` must be explicitly cleaned up in `closeEvent` to prevent callbacks from firing on a destroyed widget.

Pattern for asyncio tasks:
```python
def __init__(self):
    self._my_task: asyncio.Task | None = None
    self._closed = False

def closeEvent(self, event):
    self._closed = True
    if self._my_task:
        self._my_task.cancel()
    super().closeEvent(event)

async def _some_async_method(self):
    if self._closed:
        return
    # ... do work ...
```

For `QThread`, call `thread.quit()` and `thread.wait()` in `closeEvent` before letting the dialog be destroyed.

## How big a game window is, and what will not fit

`docs/WINDOW_SIZING.md` is the file. The short form, because both halves are easy to
undo by accident:

- **Resolution and UI size are one coupled setting.** The HUD is fixed-pixel, so a
  resolution below 800x600 needs the game's own UI scale clamped down to match, and the
  pairing is `bartlebot/game_sizing.py`'s ladder. `Resolution` is written to
  `preferences.xml`, `UISize` only to `config.xml`; neither file holds both. The user's
  own UI scale is captured **once** into `settings.json` and given back on quit — never
  re-read from `config.xml`, which after one session holds our clamp rather than their
  preference.
- **A configuration that will not fit cannot be launched**, and the entry that cannot
  launch is **disabled with the reason, never hidden**. `bartlebot/gui/screen_fit.py` is
  the one place that asks; the surfaces that must agree are `ProfileCard`/`AccountCard`,
  the Launch menu, `LauncherPopup`, and both resolution combos (`accounts_tab.py` and
  `profile_editor.py`). Disabled where a click would *launch* it, though: the same cards
  draw the Game settings tab and the profile editor, where a click edits or does nothing
  at all and the refusal is a note instead. That is the card's `CardMode` and the
  `refuses_when_unfit` that reads it, never a fresh test of the fit result.
- **The user never scrolls to see a game window.** Screen space caps the game size
  (configured layout, not the panel minimums), and `minimum_window_size` runs the other
  way to keep the window and the splitters off the game. Both are kept; they are not
  alternatives.
- **The budget is the larger of the configured layout and the *live* game area.**
  Sidebar widths and the canvas height are startup seeds — nothing persists a splitter
  drag — so without the live term a user who makes room by dragging is still refused.
  Re-judging on a resize calls `set_unavailable` on the cards that already exist; it must
  never rebuild them, or a drag destroys and recreates every card several times a second.

## Game view slots vs. player indices

A tile's position and a tile's caption come from **two different numbers**, and conflating
them is the bug class this split exists to prevent. The full reasoning is in
`bartlebot/tile_slots.py`; the short form:

| | is a position in | pinned by | decides |
|---|---|---|---|
| **slot** | `ClientManager._client_order` | `reserve_slots`, before any window exists | *where* a tile sits |
| **player** | `ClientManager.clients` (hooked only, compacted) | whichever clients are in game right now | what the tile is *called* |

They agree only while every account in the ordering is in game. Any window at character
select, crashed, or unhooked by hand pulls them apart.

**Which one to look at when something is wrong:** a tile in the wrong grid cell, or one
that moved when it should not have, is a *slot* problem — `_client_order` and
`build_tile_slots`. A wrong caption, or a wizard name on the wrong tile, is a *player*
problem — `ClientManager.clients` and whatever compacted it. A game window that flashes or
pops out of the grid is `plan_tile_changes`, which exists so a slot change is a re-key and
never a re-embed.

Things to preserve:

- **A window is embedded as soon as it is identified, not when it is in game.** Embedding
  is `SetParent` and `ClientTile` takes an hwnd; the account comes from the *window title*,
  which the launcher sets right after login. That is what makes `auto_select_character`
  off usable: every window arrives in the game view at character select and the user picks
  characters in one place. It is also why `_refresh_account_map` runs over
  `self._handler.clients` rather than the hooked list.
- **A window that is not in game has `player is None`, and is captioned by its account.**
  Not a fallback — a bot cannot route to it, so it is not Player anything yet. Do not
  invent a number for it; the sidebar and the FSM would disagree with it immediately.
- **`ClientManager.clients` stays hooked-only and contiguous.** Bot nodes address players
  by position in that list. A hole in it is not expressible, which is why the slot index
  could not simply replace it.
- **`_sync_tiles()` runs every detection tick, not only when the client list changes.** A
  window reaching character select changes no client list — it is not a client — and it is
  precisely the window that needs to appear. `sync_tiles` is a no-op when nothing moved.
- **Vacate every moving slot before filling any.** A player swap moves two tiles at once;
  clearing them one at a time throws away the tile the previous move just wrote. That bug
  was live in the first draft of `_apply_moves` and the swap test is what caught it.

## Client section refresh architecture

`ClientSection` (`bartlebot/gui/sidebar/sections/client_section.py`) is a plain `QWidget` filling `RightSidebar`'s Players tab, and runs a long-lived asyncio task (`_refresh_loop`) that polls zone/XYZ data for each connected client. It holds a `CardColumn` of `PlayerCard`s for the hooked clients and a `SidebarSection` of them for the unhooked, and it supplies the Players tab's *only* scroll area — the column's. Key design points:

- **Refresh rate is adaptive**, decided by the pure `client_refresh_interval(bot_running, players_visible)`: `CLIENT_REFRESH_INTERVAL_IDLE` (0.1 s) while idle, `CLIENT_REFRESH_INTERVAL_RUN` (0.5 s) during a bot run, and `CLIENT_REFRESH_INTERVAL_HIDDEN` (2.0 s) whenever the Players tab is not the current tab — visibility is checked *first*, so a hidden tab during a run polls at the slowest rate. `AppController._start_bot()` calls `set_bot_running(True/False)` at the bot lifecycle boundary; `RightSidebar.currentChanged` calls `set_players_visible(True/False)`, which restarts the loop on becoming visible rather than waiting out the in-flight 2 s sleep. Polling never stops entirely, because `AppController`'s zone-dropdown getter reads each widget's cached zone via `client_zones()`. These constants live in `bartlebot/nodes/timing.py`.
- **Per-widget timeout**: each `PlayerCard.refresh()` call is wrapped in `asyncio.wait_for(timeout=CLIENT_REFRESH_TIMEOUT)` inside the gather so a single slow client read can't stall the whole cycle.
- **Loading guard**: `PlayerCard._refresh()` checks `is_loading()` first and returns early if True, skipping the more expensive `zone_name()` and `body.position()` reads that produce stale/slow results during zone transitions.
- **WizWalker reads are synchronous**: despite the `async def` signatures, WizWalker memory reads (`zone_name()`, `body.position()`, etc.) call `ReadProcessMemory` synchronously on the event loop thread — they are not truly non-blocking. Keep this in mind when reasoning about latency or contention.
- **The wizard name costs nothing**: `PlayerCard._refresh()` reads it from `wizard_names.cached_name()`, a dict lookup. `ClientManager._client_loop` is what fills that cache, once per character. Do not make a sidebar label trigger detection — `resolve_names()` can open the character page, which would flash the game UI every refresh.
- **Two things besides the values are re-derived every tick**: the account stripe, because `ClientManager.account_for` learns an identity inside the client loop and signals nothing when it does; and nothing else. Anything resolved once in a card's constructor is resolved before the client is identified.
- **Cards are re-keyed, not rebuilt.** `set_all_clients` holds each card against its `hwnd` and re-uses it while that window is still in the same bucket, calling `set_index` rather than constructing a replacement — otherwise a drag-reorder resets the freecam toggle and blanks every row until the next tick. A client that crosses between hooked and unhooked *is* rebuilt: whether a card has a drag grip is fixed at construction.

## The canvas config can be swapped under every surface

`BotCanvas.replace_config(config, desc)` swaps the loaded `BotConfig` for another object
and rebuilds the scene, header and properties panel from it, then emits
`config_changed` so the controller and both sidebars follow — the same path undo takes
through `_restore`, and the same rule `AppController._on_config_changed` already
states: a surface holding a config object must re-bind on that signal or its edits go
nowhere. The pre-run subgraph recompose (`docs/SUBGRAPHS.md`) is the second caller
after undo. In EDIT mode a `desc` pushes the outgoing config onto the undo stack first,
so the swap is one undoable step; in RUN mode there is no stack and none is pushed.

## Locked members: ask `EditScene.is_locked`, never keep a second flag

A member of a linked subgraph region (`docs/SUBGRAPHS.md`) cannot be deleted, retyped or
re-parameterised outside an override, and no edge may be added or removed inside the
instance. Every surface that refuses one of those asks `EditScene.is_locked(state_id)`
(or `is_region_locked(region_id)` for membership) at the moment of the edit. Nothing
caches the answer: the scene is rebuilt from the config on undo, on `replace_config` and
on paste, and a cached lock would have to be rebuilt with it. The node's padlock glyph
and the pill's link glyph are *displays* of the query (`refresh_locks`,
`refresh_region_states`), re-derived on setup and after a detach, not a second source.

The one deliberate hole is `remove_instance`, which deletes locked members on purpose
and passes `allow_locked=True` to `_delete_node`. Anything else that wants to delete a
locked member is a bug.

## Which panel owns what

`LeftSidebar` is the bot's configuration and nothing else, and its tabs are
*rebuilt* per mode rather than hidden — a `QTabWidget` cannot hide one tab, and a
tab that stays put while changing meaning is the toggle the split above exists to
avoid. A tab the current mode does not offer is parked on a hidden child widget,
because `removeTab` otherwise leaves it parentless, which is to say a top-level
window.

Two bars, and which one a thing belongs to is decided by what it is *about*:

| | above | owns |
|---|---|---|
| **Bar A** (`game_view_panel.py`) | the game windows | the clients: how many are hooked, the launch profile, the node they are being driven through |
| **Bar B** (`canvas/bot_header.py`) | the bot's graph | the bot: name, version, file, author, tags, the run buttons, and the run status |

The **properties panel** (`canvas/param_panel.py`) is a page per selection: a node, an
edge, or — since the subgraph work — a *region*, reached by selecting a whole region's
members (the pill click). The region page owns the region's label and colour for every
region and the instance's inputs, overrides and link for a linked one; a locked member's
node page owns only its roles and its overrides. The **editing crumb**
(`canvas/subgraph_widgets.EditSubgraphCrumb`) is the canvas's, pinned under the focus
crumb, and its Save and Discard are the only verbs on a region being edited — the
region's menu offers none while the edit is open. `docs/SUBGRAPHS.md` has the flow.

Bar B is a three-column grid grouped by *subject*: everything about the bot on the
left, the run controls in the middle with the clock and the run state either side of
them, and the tools that act on the *view* — fit, failure-edge toggle — out on the
right with the mode toggle. Only the metadata column is two rows deep; both control
clusters span those two rows, which is what puts them on the bar's own vertical
centre and level with each other.

**Nothing is stacked in the centre or on the right, and that is the point.** The
status used to be a caption beneath the transport, which left the view tools as the
only cluster with an empty row under them — they centred in row 1 and read as
sitting high. Fixing that by spanning only the right column would put the two
clusters at different heights, which is worse. So the status moved onto the button
row and both columns span.

**The transport is a landmark: it stays in the centre in both modes, and is disabled
rather than replaced.** Add State and Auto-Layout used to occupy its exact pixels in
Edit mode, so the same target did two different things with no cue at the target
saying which. Every production tool with a transport treats it this way — Unity,
Unreal, Xcode, every NLE — because people aim at one without looking. The two are
graph-editing tools besides, so they belong with the mode toggle that summons them.

The right-hand end is three clusters, grouped by widening scope: the **document**
(add state, auto-layout), the **view** of it (fit, failure edges), then the way out
of the **mode** (edit, or done/save). Each is a fixed `_PAIR_WIDTH` slot and the edit
tools' Run-mode page is deliberately *empty*, so nothing there moves when the mode
changes; the mode toggle right-aligns inside its own slot and stays anchored to the
margin whether it holds one button or two.

**Set the spacing on every toolbar row explicitly** (`_BTN_GAP` within a cluster,
`_GROUP_GAP` between). The style's default is 6px on Windows, so a row that only
called `addSpacing(4)` came to 80px inside a 68px slot and clipped the Save button
off its right-hand end. `tests/test_canvas_toolbar_layout.py` checks that no button
overruns the slot it sits in, in either mode.

The two side columns take **equal stretch**, which is what keeps the centre column
near mid-bar. The cost is that the metadata is capped at roughly half the bar, so a
long filename elides and a heavily tagged bot wraps its byline in Edit mode. Both
degrade quietly; a Run button that drifts with the length of the bot's name does not.

The clock is **zeroed, not hidden**, while idle, and the clock and the status
**reserve equal, fixed widths** either side of the transport (`_size_status_slots`).
Both are the same concern: anything either side of those buttons that grows or
appears shoves them sideways at the one moment the user is watching them, and the
status word cycles `Editing` / `Idle` / `Running` / `Paused` as the run goes on.
Equal slots are also what centre the transport within its own column. The slot is a
*minimum* width, so an unforeseen longer word widens it rather than being clipped,
and it is re-measured on a theme switch because the clock is in `THEME.font_mono()`.
The status label's font size is set on the widget rather than in its stylesheet — a
stylesheet `font-size` never reaches `QWidget.font()`, so the metrics would be measured
at the wrong size.

Bot name and run status were in Bar A, which meant they vanished whenever the
game view was hidden. `BotHeader` takes two opaque widgets — the transport, and the
whole right-hand end — rather than naming each cluster, because which clusters exist
depends on the mode, and that is knowledge the header should not acquire.
`BotCanvas` writes the status straight into its own
header and *also* emits `bot_status_changed`, because the menu actions and Bar A's
active-node caption still need it; routing Bar B's own status out to MainWindow
and back would make the bar depend on a window that may not have connected yet.

Three things the header does not hold, and should not be given back:

- **`start_location`** is gone from the schema. Nothing in the engine ever read
  it, `go_to_world` and the bot templates make it moot, and a field naming a
  place the app will not take you to reads as a promise. Pydantic ignores it in
  an older file and `save()` drops it.
- **Initial State / Recovery State** are badges on the nodes themselves;
  the labels only restated the graph.
- **The description** heads Setup (read) and Options (edit) instead. Elided into
  a header it would hide exactly the text a user must read — four bots carry
  multi-line `REQUIREMENTS:` blocks saying things like "change the client
  resolution to…".

**Possible Drops** sits with the live drop tables in the right sidebar's Drops
tab: the two answer the same question at different times, what this run could
yield and what it has. It is the only authorial thing over there, which is what
`RightSidebar.config_changed` exists for. **Log** is the third tab there, so it
can be read without closing Setup.

## Two surfaces: Setup edits values, Options edits declarations

They are separate tabs of the left sidebar, and edit mode decides which pair is
there at all — Run shows **Setup** (`setup_section.py`) and Combat, Edit shows
**Options** (`options_section.py`) and Properties. Every production system that
solves this split separates the surfaces by context rather than by a toggle
inside one panel (Unity's Prefab Mode, Unreal's Class Defaults, VS Code's
settings UI against `package.json`), and edit mode is the context this app
already has.

- **Setup writes `settings.json`, never the bot file.** `values_changed` is
  deliberately *not* wired to `config_changed`: an override is not an edit to
  the bot, and a shared bot must stay byte-identical however its users tune it.
  `test_a_bot_file_is_untouched_by_a_value_edit` is the guard.
- **A row says where its value came from.** The four-level resolution runs for
  display too, through `bot_options.resolve_for_config` — the same gatherer
  `BotMachine.get_option` uses, so the panel and the run cannot disagree. The
  caption under a row names the level that answered, the gutter bar is the
  accent colour when the user is that level, and Reset's tooltip names what a
  reset falls back to. That tooltip is the mitigation for storing overrides as
  bare values: an author who later improves a declaration is outranked
  silently, so the one-way door has to be visibly openable.

  **There is one vocabulary for the four levels, and it is
  `bot_options.SOURCE_*`** — `Your value` / `Bot default` / `App settings` /
  `Default`, listed in `SOURCE_LEVELS`. `OptionResolution.source` *is* the
  caption: there is no caption map, because there were two sets of strings for
  the same four levels and they drifted, so the log said "from the app setting"
  under a row reading "App settings". The Reset tooltip lowercases the one word
  it shares with the column — a caption is a label, a tooltip is a sentence.
- **Setting a value back to the resolved one drops the override.** Reverting by
  hand and pressing Reset are the same act, so they have to leave the same state
  behind — a row marked as changed while holding the value it would have had
  anyway is a lie about what the user did, and it would go on to outrank an
  author who later improved the declaration it happens to match.
  `values_agree` is the comparison, shared with the save-time prune, so `3` and
  `3.0` agree and `False` and `0` do not.
- **A default edit goes out on `defaults_changed`, not `options_changed`.** The
  latter's handler recomposes and rebuilds *both* surfaces, which destroys the
  check box the click landed on and rebuilds it under the pointer — the same rule
  the next bullet states for Setup, which Options was breaking. No name can change
  on a default edit, so there is nothing on that surface to rebuild; it is routed
  to `metadata_changed` (dirty the file, rebuild nothing) plus a Setup refresh,
  because a declaration outranks the app setting Setup resolves through.
- **No chrome appears or disappears with the override state, and a value edit
  refreshes one row rather than rebuilding the list.** Both are the same
  concern: a spin box whose row is rebuilt on `valueChanged` vanishes from under
  the pointer on the first arrow click, and a revert button that only exists once
  a value is overridden moves everything under it as you type. So the bar
  changes colour rather than appearing, revert greys out rather than vanishing,
  every level including the user's own has a caption, and `_refresh_row` is what
  a value edit calls.
- **A row is a head line and a control, and the prose folds.** Measured on a
  shipped bot at a 400px sidebar, an ordinary option row was 41% node-authored
  description and 17% source caption, leaving the control the minority of its own
  row -- all of it at 9px, three quarters of the base size and small enough that
  Windows' subpixel antialiasing colours the strokes. So the caption moved onto
  the name's line, where it costs nothing, and the description folds behind a `?`
  and is read at the base font. This is the one thing on the surface that appears
  and disappears, and it does not contradict the rule above: it moves on a
  deliberate click of its own button, never on a value edit, and everything it
  moves is below the button that moved it. `show_option_descriptions` is the
  switch for reading the whole tab at once -- a property of the reader, so it
  lives in `settings.json` and not in the bot.
- **Every row's chrome lives in a fixed leading gutter, `[bar][action][?]`, on
  both surfaces.** The pieces are shared — `gui/sidebar/option_gutter.py` holds
  the bar, the `?`, the `Show/Hide descriptions` links and the row builder — so
  the two tabs' columns land at the same x and a mode switch does not read as a
  different row. Only the middle slot differs: Setup reverts a *value*, Options
  edits a *declaration*. Options never paints its bar, because the bar and revert
  are one affordance in Setup and a lone marker with no paired action would be a
  fact to correlate rather than something to act on; the width is still reserved,
  so the `?` columns line up and a meaning can be given to it later. It
  is the only placement that yields one column per button. Right-aligning the `?`
  gives *two* columns, because the head line's right edge sits at two different x
  values; a fixed-width name column fails on any long name, and on a bool row,
  which has no separate label at all. In the gutter both buttons top-align with
  the head line whether the row is 20px tall or 240, which deletes the branch that
  used to put Reset beside a short control and on the head line otherwise. It also
  co-locates two facts that are one fact: the bar going accent and revert going
  enabled are set by the same two lines of `_refresh_row`, and adjacent they read
  as one affordance. This is VS Code's settings gutter verbatim. The accepted
  costs, so a later session does not relitigate them: ~40px of permanent, mostly
  blank left inset, and an icon-only revert being less discoverable than the word
  — mitigated by `_reset_tooltip` naming the value and level a click falls back
  to, and by tinting the icon `THEME.accent` only while it can act.
  `test_the_gutter_buttons_line_up_across_every_shape_of_row` and
  `test_the_gutter_slots_line_up_across_both_surfaces` are the guards.
- **A rare button is an argument *for* the gutter, not against it.** Only 24 of
  the shipped library's 152 Options rows carry an Edit button and 64 of 80 bots
  carry none, so the slot is empty most of the time. Inline, a button on 16% of
  rows makes 16% of rows a different shape; in a reserved column every row is the
  same shape and the rare button appears where one is always looked for. Every
  slot keeps its size when hidden (`keep_slot_when_hidden`) — a gutter that closes
  up on the rows with nothing in them is not a column any more.
- **Delete lives inside the Edit dialog, not in the gutter.** The far slot is the
  irreversible one, and deleting an option is strictly worse than reverting a
  value — it takes the declaration, both transition flag forms, and the user's
  stored override. A 20px icon-only target a few pixels from the `?` was the wrong
  home for that, so the row's one button leads to both outcomes and the dialog
  gives Delete a labelled button, a confirmation and a Cancel. `_AddOptionDialog`
  accepts on delete too, so callers read `was_deleted` before `get_option`: an
  accepted dialog means the user *finished*, not that they saved.
- **Revert is furthest from the text, `?` is nearest it.** Two small targets a few
  pixels apart, one meaning "explain this" and the other "discard what I typed",
  and reverting has no undo — on the keep list the loss is a hand-picked selection.
  The ordering and the 20px targets are the mitigation.
- **The head line is the same shape on every row:** name on the left, source
  caption right-aligned in a fixed slot (`_SourceCaption`, measured from the font
  rather than a stylesheet — a stylesheet `font-size` never reaches
  `QWidget.font()`). A bool has no separate label, so its checkbox *is* the head
  line and the whole row fits on one.
- **Show descriptions is a link, not a check box.** A tick box captioned "Show
  descriptions" above a list of options reads as the bot's first option; a link
  whose text flips *Show* / *Hide* names its own effect. It sits at the top of the
  section body rather than on the header, which is a click-through row that
  toggles collapse. `LinkButton` (`gui/widgets/link_button.py`) is the shared
  control.
- **The route to the app settings is written once, at the far end of that same
  row.** Setup's rows name the level that answered, and one of the four —
  *App settings* — is a page the user can actually go to; a link in every hint
  that mentions it would be eight identical links to one destination. So Setup
  passes `DescriptionSwitches` a `trailing` link, separated from the two
  description commands by the stretch because it is not a third one of them.
  It emits `app_settings_requested` rather than opening the dialog: `exec()` may
  only be entered from a synchronous slot in `MainWindow`, which also refreshes
  the rows on `settings_applied` — a level Setup resolves *through* has moved,
  which is the same event as a declaration edit. Options has no trailing link;
  it edits declarations, and the app setting is not one.
- **A numeric option may declare bounds, and both ends enforce them.**
  `BotOptionSpec.minimum` / `maximum` / `suffix` are carried onto the composed
  declaration by `compose_options` — derived, never persisted, exactly like `type`
  and `description`. `make_option_widget` applies them, so both surfaces and the
  Add/Edit dialog get them for free; `resolve_option` applies them again, because
  a value can arrive from a hand-edited `settings.json` or bot file and that is
  the one funnel every consumer goes through. An out-of-range value is
  **discarded with a warning so the level below answers, never clamped** — coercing
  a hand-edited `-1` to `0` hides a wrong file. A negative Safety Delay is the case
  the feature exists for: it fails silently rather than loudly, since
  `asyncio.sleep` returns at once and every window polled *for* that many seconds
  expires on its first check.
- **The keep list's keywords are chips, not a comma-separated field.** At a 400px
  sidebar a 32-keyword CSV string shows only its own middle, and removing one
  entry means selecting text between two commas. The field only *adds* now
  (`parse_list_text` still reads a pasted CSV), and the value renders as
  `NeutralChip`s in a `FlowLayout`. Presentation only — nothing reading
  `_VALUE_PARAM_KEY` changed.
- **An option that another option switches off is a child of it: indented,
  greyed, never hidden.** `BotOptionSpec.depends_on` names a bool option on the
  same bot, and `compose_options` carries it onto the composed declaration the way
  it carries `type` and the bounds — derived, never persisted, and dropped when
  the named parent is not in this graph, because a row indented under a row that
  is not there reads as a child of whatever precedes it. Quicksell's three sell
  flags are the case: with `Enable Quicksell` off nothing is sold, so there is
  nothing for them to spare, and they were three live tick boxes that could not
  affect the run and gave no sign of it.

  Four things that are load-bearing, and all four are the same rule the rest of
  this surface follows:

  - **Only Setup indents.** Options edits declarations, and `Enable Quicksell` is
    user-owned, so the parent has no row there to be a child of.
  - **The indent is applied to the row's *body*, past the gutter, and the gutter
    is untouched** — `build_gutter_row(..., child=True)`. The indent says what the
    row belongs to and the gutter says what can be done to it; indenting both
    costs the one column property the gutter exists for.
    `test_a_child_row_is_indented_but_its_gutter_is_not` is the guard.
  - **Greyed, never hidden, and the value is never cleared.** Hiding moves every
    row beneath it the moment the parent is ticked, and it hides the fact that the
    user still has a value stored there. Only the body is disabled, so the
    override bar still says whose value it is and revert can still discard it.
  - **A `ChildBranch` rule fills the indent.** Indentation alone is only visible
    while two left edges are being compared.
- **A derived option has no Edit button, and so no Delete either.** It is
  re-composed from its NodeSpec on every load, so either edit would be undone by
  the round trip. Its *default* is still the author's to change, through the
  control itself.
- **Descriptions fold on both surfaces, at the base font, behind one remembered
  switch.** Options used to show every description unconditionally and at 9px —
  75% of base, where Windows subpixel antialiasing starts colouring the strokes —
  while Setup showed the identical node-authored prose folded and at full size.
  Two tabs disagreeing about the size of the same text reads as a disagreement
  about what the text *is*. `show_option_descriptions` is the one setting behind
  both, because folding the prose away is a property of the reader and should not
  have to be done twice.
- **An option may be required only when a param says so.** `BotOptionSpec.required_when`
  names a bool param on the same node; the option is composed only while that param is
  on. Challenge Mode is the case: a handful of dungeons offer one, and composing it
  unconditionally put a switch into all sixty-odd bots containing a sigil that their
  users could not act on. The split is the same one the params/options table draws —
  *whether this dungeon has a Challenge Mode* is a fact about the graph and so a param
  (`offers_challenge_mode`), *whether to use it* is the user's and so an option. The
  node gates on both, so a stale override cannot send a client at a toggle that is not
  there, and the composed default stays `False`: the author is saying it is available,
  not that it is wanted. They can still pin a default by declaring one.
  `required_unless` is the mirror, for an option one mode of a node ignores:
  `teleport_to_friend` composes Friend Name only while some state is *not* porting to a
  hooked player, so a bot mixing the two modes keeps the row.
- **The list is ordered by where an option comes from, not by whether it is in
  the file.** The author's own options first, then the derived ones grouped by the
  node that requires them. Ordering the file's options first instead made a row
  move the moment its value was edited — editing a derived default materialises a
  declaration, so the row the user had just touched jumped to the top — and it
  threaded one node's options through another's. `compose_options` is where that
  order is decided, so both surfaces get it.
- **A value only the user owns has no row on the author's surface at all.**
  `bot_options.USER_OWNED_OPTIONS` names them — Safety Delay, Friend Name, Enable
  Quicksell — and `OptionsSection` filters them out rather than showing them
  disabled, because a declaration of one outranks the app setting the user set it
  in. Safety Delay is the plain case: a declaration there is the per-node pin
  *A node cannot pin a bot-wide option* removed, one level up. Ownership is a
  property of the *option*, not of its type — Safety Delay is an ordinary float —
  so the registry is by name, and it is deliberately narrow: `Sell Pets` stays
  editable because a bot farming a pet has to be able to say so, and the keep list
  cannot spare an individual pet. `Level Scaling Players` is the near miss — eleven
  bots declared it, which read as intent until the values were looked at, and six
  were one `[2, 3, 4]` propagated by copy. Which of a user's wizards are
  underlevelled is a fact about their team, so all eleven were dropped; the option
  stays editable anyway, because a wrong answer there costs an elixir rather than
  an item, a name, or a run. Nothing prunes such a declaration on save: the
  resolution stays a plain four levels, a hand-edited file is still honoured, and
  `test_no_bot_declares_a_user_owned_option` is what keeps the library clean.
- **Renaming an option is three fixups, not one.** The flat `flags` list, the
  `flag_groups` the edge editor actually writes — which take precedence at run
  time, so a stale name there evaluates `False` in silence — and the override
  store. `OptionsSection._rename_option` does all three; deleting does the same
  three in reverse.
- **One option-type → widget mapping**, in `gui/widgets/option_widgets.py`, used
  by both surfaces and the Add/Edit dialog. `param_panel._make_widget` stays
  separate: it maps *param* fields, a larger vocabulary (zone, xyz, wiki_page)
  no bot option can have. Three copies of this mapping is how the dialog came to
  hardcode `type="bool"` while the rows rendered floats — pressing Edit on
  Safety Delay rewrote it as a checkbox.
- **A bool is a checkbox in a list and a radio pair on its own.** In a row the
  caption is the option's name and a tick plainly means yes; under a field
  labelled `Default` a lone box captioned "True" reads as though False were
  unavailable. `BoolChoice` is the second form, and its third state — neither
  radio — is how a null declaration is shown rather than silently rewritten.
- **The Add Option dialog never creates a null declaration.** Null means "use
  the level below me", and a hand-added option has no app setting and no
  NodeSpec default beneath it — so a null there resolves to `None` and
  `_flag_<name>` reads it as `False`. The dialog only declines to *destroy* a
  null it was handed, which is what `_was_null` and `_default_touched` are for:
  opening Edit to fix a description must not promote the declaration.
