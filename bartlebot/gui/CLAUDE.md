# GUI conventions

Conventions for everything under `bartlebot/gui/`. The app-wide rules are in the root `CLAUDE.md`; these are the ones that only bite inside a widget.

## GUI theming
Use `THEME` singleton from `bartlebot/gui/theming/theme.py`. No hardcoded colors in widget code.

### Font sizes come from a token, and are always px

`THEME.font_body_px` is the app font and the one number the rest hang off:
`font_heading_px`, `font_small_px`, `font_subtext_px` and `font_micro_px` are read
relative to it, so moving the body size moves all of them. Node text is the exception
(`font_node_px`, `font_node_sub_px`): it is drawn in scene units against fixed node
geometry, so it does not follow the body size.

A size no token names is an *offset* from one at the call site —
`f"font-size: {THEME.font_body_px + 1}px"` — never a literal, and never a
`_FONT_PX` constant in the module. `tests/gui/theming/test_font_sizes.py` fails the build on any
of those, on a size in points, and on the two traps below. The sizes lived as literals
in about eighty places before, which is why they had drifted into six values doing
four jobs.

- **Never read a size back off a font.** `pointSize()` returns `-1` on a font sized in
  px, so `font.setPointSize(font.pointSize() + 1)` silently resolves to 0, and a
  `pointSizeF()` read into a `max(..., 6.0)` floor silently resolves to that floor.
  Both shipped here. Size from a token: `font.setPixelSize(THEME.font_body_px + 1)`.
- **A stylesheet `font-size` never reaches `QWidget.font()`.** A widget that measures
  its own text — anything eliding, sizing a slot, or growing to fit lines — must set
  the size on the font as well, or it measures at the app font and lays out for the
  wrong size.

Use `THEME.ui_font(px, bold=...)` to build one. For text drawn at a fractional scale
(the canvas region labels), `THEME.scaled_ui_font(px, scale)` keeps the fractional
size that `setPixelSize`'s whole pixels would step between.

There is deliberately **no user setting for font size**, so nothing re-reads these at
run time: a stylesheet built once in a constructor is fine.

Anything a widget builds from `THEME` is a snapshot of whichever theme was active at
the time, and Qt never recomputes it: `QApplication.setStyleSheet()` delivers
`StyleChange` to every widget, but a stylesheet the widget set on *itself* always
wins and is left exactly as it was. The widget has to re-derive it.

**Inherit `ThemedMixin` (`bartlebot/gui/theming/themed.py`) and put the work in
`apply_theme_styles`.** It is enforced: `tests/gui/theming/test_theme_refresh_guard.py` fails any
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

Five things that are not obvious:

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
  `tint=""` for an icon that should keep its own colors.
- **Write the rule through `set_style_sheet(widget, sheet)`, not `setStyleSheet`.**
  A re-assignment is never local: Qt re-parses the rule and delivers `StyleChange` to
  the whole subtree, every themed child answers by re-deriving *its* rule and setting
  it, and that broadcasts again. The re-entrancy guards stop it recursing forever;
  they do not stop it being quadratic in the depth of a card. Re-deriving from `THEME`
  almost always reproduces the string already there, so the helper compares first and
  writes only on a real change — 132 tests in `test_combat_tab.py` were paying 40,151
  `setStyleSheet` and 288,522 `changeEvent` calls for it. A genuine theme switch does
  change the string, so the cascade still runs the one time it has to. The guard test
  knows both spellings; if you add a third, teach `_direct_themed` about it or the
  rename silently empties the guard.
- **A factory cannot restyle what it returns.** `field_label(text)` hands a label to a
  caller that has nothing to re-apply, so the staleness lands on the *caller* — which
  is why the guard blames a themed helper's callers. Return a
  `ThemedTextLabel` (`bartlebot/gui/widgets/labels/themed_label.py`) instead; it owns its own
  color, named by token so it is re-read rather than snapshotted.

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

- **A color inside rich text.** The required-field marker in `param_panel` is a
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
parent. Embedding a game client makes `ClientTile` native, and its ancestors with it —
`GameView`, its panel, the splitter. A menu parented to a button inside one resolves
to a `QWidgetWindow` that is not top level, which Qt refuses outright: `... must be a
top level window`, leaving the popup with no transient parent at all.

Qt would also promote every *sibling* of those to a native window — the left sidebar,
the canvas — and `bartlebot.py` turns that off with `AA_DontCreateNativeWidgetSiblings`
(`gui/native_widgets.py`). Leave it on. With promotion on, this helper's reparenting
made every child of the canvas's view native, and a native `ViewFrame` took every
click on the canvas (#126). A widget that has to draw over the tiles asks for its own
native window with `WA_NativeWindow`, as `GameView`'s overlay button does.

The helper swaps the menu onto its top-level window on `aboutToShow` and hands it back
on `aboutToHide`. The swap cannot happen at construction: a menu built in a widget's
`__init__` has no window yet, and `window()` would return the widget itself.

A menu with no parent, or one parented directly to a window or dialog, is already fine.

## The title bar is ours, not Windows'

The main window has no native caption. `TitleBar` (`title_bar/title_bar.py`) is the one row
above the panes, holding the logo, the menu bar, the title, the pane toggles and the
window buttons, and `NativeFrame` (`title_bar/native_frame.py`) is what makes it a title bar:
Qt's `ExpandedClientAreaHint` for the frame, plus the four things Qt leaves undone. Its
docstring has each one and the spike that found it. The rules:

- **A control in the bar is a `QAbstractButton` or the `QMenuBar`.** `is_control_at`
  decides by class, and everything else in the bar is caption: it drags the window, and a
  click on it never reaches the widget. A new kind of control needs adding there.
- **Never `menuBar()` or `setMenuBar()` on `MainWindow`.** The menu bar is the title bar's
  child, installed with `setMenuWidget`. `QMainWindow.menuBar()` would quietly create a
  second, empty one.
- **Every `WM_NCHITTEST` is answered by `NativeFrame`, never by Qt.** Qt's answer for the
  expanded area fakes a mouse press from inside the hit test; with a menu bar in the strip,
  File never opened. Outside the bar the answer is Windows' `DefWindowProc`.
- **A pane shown or hidden in code goes through its View action**, or the title bar's
  toggle and the menu's check mark go stale: `show_game_view`, `show_bot_canvas`, or
  `_sync_pane_actions` after changing several at once, as Maximize Game View does.
- **Probing it from another process needs per-monitor DPI awareness.** A DPI-unaware sender
  has the coordinates it sends scaled by the display scale, so at 125% every answer lands
  1.25x inside where it should, and a posted click misses the window. Call
  `SetProcessDpiAwarenessContext(-4)` first.

## Buttons: `ActionButton` and its four weights

Use `ActionButton` (`widgets/buttons/action_button.py`), not a bare `QPushButton`. Fusion's
default chrome is a beveled 3D frame sized to its own label, which flattens SVG icons
against it and makes a row of buttons size each one to its own text.

`ActionStyle` says how much weight a button carries, on Bootstrap's model and for its
reason — a row of buttons cannot say which one it expects unless they are drawn at
different weights:

| style | look | for |
|---|---|---|
| `PRIMARY` | filled with `accent` | the one thing the surface expects you to do |
| `SECONDARY` | outlined in `accent` over a faint wash of it | a way forward that is not the commit |
| `TERTIARY` | no accent — **the default** | everything else |
| `CAUTION` | filled with `error` | a verb that costs something to undo |

Five rules that are easy to get wrong:

- **`TERTIARY` is the default, and a style is opted into.** Most rows here are
  equal-weight verbs beside a destructive one (`+ New` / `Duplicate` / `Delete`), and
  nothing in such a row should look more important than the dangerous button next to
  it. At most one `PRIMARY` per surface — but a `SECONDARY` does not imply a `PRIMARY`.
  Most of them sit on surfaces that have no commit at all (`assignment_card`'s *Save as
  Player Strategy…*, the toolbar's *New Bot*): the weight means "worth reaching for, but
  not the default thing to do here", which needs nothing beside it to be true.
- **A weight varies color and nothing else.** Every style shares the padding, the
  radius, the height and one disabled look. A style that also changed the shape would
  stop being a weight. That rule is why `SECONDARY` carries a wash rather than a 2px
  outline: drawn unfilled it read as a tinted `TERTIARY` on the light theme's near-white
  field, and thickening the border to fix it would have been a shape change.
- **A disabled button has no outline.** Its border is drawn in `surface_disabled`, the
  same color as its fill, because an enabled `TERTIARY` (`surface`) sits only ~3% off
  that fill — losing the outline is what actually separates off from on. It matters most
  on the inspector surfaces, where most buttons start disabled.
- **Derive, never hardcode.** `SECONDARY`'s wash is mixed from `THEME.accent` over
  `THEME.bg` at style time, and how much of it shows through is read off `bg`'s own
  lightness. A custom light theme is not required to be `#ffffff`, so a literal
  comparison there would silently give it the dark theme's wash.
- **`fill=False` for a trailing pair.** These expand by default so a row of them splits
  its width evenly. A dialog's `[Cancel] [Save]` sits *after* a stretch, and an
  expanding button there eats the stretch and spreads the pair across the whole footer.

Issue #47 is the rollout; `combat_strategies/dialog.py` is the worked example.

`tests/gui/test_button_guard.py` is what keeps it from eroding: it fails on a bare
`QPushButton` that is not on its allowlist, on a second `PRIMARY` in one module, and on
QSS that targets a compound widget's subcontrols. Every allowlist entry carries the
reason it is exempt, and the failure prints it — so adding one is writing down a
decision, not silencing a check.

### A dialog's footer comes from `dialog_footer()`

Never lay a `[Cancel] [Save]` row out by hand, and never reach for `QDialogButtonBox` —
none is left under `gui/`. `dialog_footer()` (`widgets/layout/dialog_footer.py`) builds
`[extras…] <stretch> [Cancel] [Commit]` and returns the buttons, so a dialog that
disables or retitles its commit still can.

What it is really for is the keyboard. `QDialogButtonBox` owned the platform button
order, the Esc wiring and the default button, and the last of those is a trap: Qt hands
"default" to the first `autoDefault` button *constructed*, and a button acquires
`autoDefault` when it is reparented into a dialog. So the helper sweeps every other
button in the dialog **on show**, not at build time — at build time most of a dialog's
widgets are still parentless, sitting in layouts nobody has installed yet.

Two things follow:

- **The reject slot is "the button that does not commit", not "the quiet one."**
  `recovery_dialog`'s Discard All takes that slot at `CAUTION`, because it permanently
  drops every recovered unsaved bot file.
- **`safety_warning_dialog`'s Run Bot is `TERTIARY` on purpose**, the one commit in the
  app that declines the accent: that dialog exists to make the user hesitate, and
  filling the button that dismisses a safety warning argues with its own purpose. It is
  still the default button — Enter runs. Raise it on issue #89 before changing it.

`tests/gui/widgets/layout/test_dialog_footer.py` presses Enter and Esc on every converted dialog.

### What is *not* an `ActionButton`

`ActionButton` is specifically *a labeled verb that expands to share a row*. Roughly a
seventh of this app's buttons are none of those, and forcing one into the action
vocabulary stretches a 22px affordance across a toolbar. Two other named classes cover
them, and both follow the same conventions — `ThemedMixin` first in the base list, all
`THEME` work in `apply_theme_styles()`:

| widget | look | for |
|---|---|---|
| `IconButton` (`widgets/buttons/icon_button.py`) | fixed square, glyph, no label | a remove `✕`, a close `×`, a help `?`, a favorite star |
| `LinkButton` (`widgets/buttons/link_button.py`) | accent text, no frame, underline on hover | a control sitting inline in prose or under a card |

Three things about them:

- **An `IconButton`'s tooltip is a required keyword argument.** It carries no label, so
  without one it is unusable to anyone who does not already recognize the glyph, and
  making it required is what stops that being an oversight rather than a decision.
- **`IconTint` varies color and nothing else**, the same rule the four weights follow:
  the size is the caller's to choose, because a toolbar's 44px and a flag row's 24px are
  both right. `DANGER` is what makes a remove read as one; `ACCENT_WHEN_ENABLED` is the
  Option Values gutter's revert, where *being* enabled is the signal.
- **A `LinkButton` underlines on hover, not always.** The accent alone says "clickable"
  while the pointer is elsewhere.

Four `QPushButton`s stay bare on purpose, and the reason is the same for all of them:
**their background is the data they display.** Any shared class writes `background`,
which paints over the one thing they exist to show. Those are the color swatches in
`settings/theme_tab.py`, `canvas/param_panel.py` and `canvas/edit_scene.py`, and
`bot_canvas`'s `_warning_pill` — a status pill that happens to be clickable.

**`theme_tab.py`'s "Reset to System Default" is the last one, and it is the sharpest
case**: it keeps a hardcoded stylesheet so that it stays legible *whatever* the user has
done to the running theme. It is the escape hatch from a custom theme that has made the
app unreadable, and `ActionButton` derives everything from `THEME` — which is precisely
what would break it. Leave the literal colors and the comment above them alone;
`tests/gui/widgets/buttons/test_surface_button_weights.py` says so too.

## Section headings

Four, and they are not interchangeable:

| widget | look | for |
|---|---|---|
| `SidebarSection` | disclosure triangle + bold label | a region that **folds** |
| `OptionCard`'s band (`sidebar/option_card.py`) | bordered card, surface band, muted triangle + semibold title | a **group of options that folds**, inside a section |
| `CardColumn`'s heading | centered bold label + full-width rule | a **column of cards** |
| `RuleHeading` (`widgets/labels/rule_heading.py`) | left bold label + rule to the right | the regions of a **form** |

A triangle promises a fold, so do not use `SidebarSection` for a region that does not
have one; a centered heading over left-aligned fields reads as belonging to something
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

## Account colors, and what a colored thing may claim

A palette slot per account (`shared/account_slots.py`), resolved to a color against the
live theme (`gui/theming/account_accent.py`). It is a **match target** — find the card wearing
the same hue — never an identifier, and three rules keep it one:

- **Hold the slot, not the hex.** A widget that stores `account_accent(3)` repaints
  stale after a theme switch, with nothing in any stylesheet to grep for.
- **Outline and tint, not a saturated fill.** Four filled pills in a row make the row
  the loudest thing on the screen when the point of the surface is a *name*. See
  `AccountPill`.
- **No account known means no color, never a default one.** A color is a claim about
  which account is behind something; picking one for an unidentified client, or for an
  account that has been deleted, puts a hue on screen that matches nothing anywhere
  else. `account_stripe_keys` returns `None` for exactly these, and callers must keep it.

A **player slot** is colored but never renamed: `P1` stays `P1` on the pill and the
combat-order chip, with the account's name in the tooltip. A `DuelConfig` stores slots,
is authored offline and outlives whichever account is in slot 1 tonight — a pill reading
`Malvin` would be a claim the stored data does not make, and the same card would read
differently depending on what happened to be hooked. Issue #40 is the rollout.

On the Combat tab every `P#` is a `PlayerBadge` (`widgets/pills/player_pills.py`) — the deck
cards, each assignment line and the combat order — and each deck card carries the same
left stripe as the player card for its slot. Colors are held **per slot, not per
position**, so a `P3 → P1` order puts slot 3's hue first. The tab polls its account
source on a timer (`PLAYER_COLOR_REFRESH_INTERVAL`) only while it is visible, because an
identity arrives after its client and nothing announces it; a card built between ticks
takes the last answer at construction, since a tick with an unchanged answer repaints
nothing.

The Players tab's `P#` pill is the same `PlayerBadge`, keyed off the card's stripe. The
game view's tile caption is painted (`client_tile.tile_pill_colors`) in the same capsule
shape and colors at its original size, and a focused window's border is its account's
color, falling back to the accent. `GameView.sync_tiles` pushes the key from
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

**A widget in a layout nothing has installed yet has no parent either.** `addWidget`
on a free-standing layout parents nothing; the widgets in it get a parent only when
that layout is set on a widget, or added to a layout that already is. So the builder
in the example above is only one way to get here. The other is three lines apart in
one constructor:

```python
text = QVBoxLayout()                 # free-standing
text.addWidget(self._blurb)          # still no parent
self._blurb.setVisible(bool(blurb))  # a window, once per card
row.addLayout(text)                  # too late
```

Attach the layout before putting anything in it (`row.addLayout(text)` first), or pass
the parent to the layout's constructor. The New Bot dialog flashed one window per
template card this way.

`setVisible(False)` is safe, and so is showing a child of a hidden parent — only the
parentless case opens a window. It bites hardest where a rebuild is on a hot path:
one click of a check box in Edit mode mapped and unmapped **eight** windows on a
shipped bot, because each row's description and Edit button were shown while still
loose. `test_building_a_row_never_shows_a_parentless_widget` is the guard, and the
way it catches it is the `parentless_shows` fixture in `tests/conftest.py` — an event
filter on the `QApplication` recording every `Show` delivered to a widget whose
`parent()` is `None`. **A new dialog, card or row builder gets a test that asks for
it**, builds the surface and asserts the list is empty; reading the code for this bug
has missed it more than once.

The same window opens on the way *out*. **Discard a widget with `discard_widget`
(`widgets/layout/discard.py`)**, never a bare `setParent(None)` + `deleteLater()`: a widget
shown earlier in the same event-loop turn still has that show to come, and once it is
unparented the show maps it as a window. Import all redrew each deck card once per slot
in one turn, and every art tile from the discarded redraws flashed in the corner (#120).
The helper hides first, which is the whole fix. `test_discard_widget.py` guards it.
When a flash appears that you can't place, run with `BARTLEBOT_DEV=1`:
`gui/debug/window_show_trace.py` logs each stray window's class and geometry, which usually
names it, plus the Python stack. A deferred show has no Python caller, so for one of those
the stack ends at the event loop. It also logs windows shown by the game processes.

The inverse trap sits one line away: **a hidden widget in a `QBoxLayout` reserves no
space at all.** The layout skips it outright rather than laying out a zero-size cell,
so a spacer or placeholder that is parented but never shown holds nothing open and
every widget after it lands a row early — with the layout, the size hints and the
geometry all internally consistent about the wrong answer, which is why it reads as an
arithmetic bug rather than a visibility one. `DragReorder._begin_drag` is the case:
the placeholder standing in for a lifted card has to be `show()`n by hand, after the
parent it was constructed with. (`setRetainSizeWhenHidden` is the other way, and is what
the launch cards' hover-revealed buttons use — but it only applies to a widget the layout
is still asked about.)


## Card art is optional, and missing is the ordinary case

Spell art is the game's own and is never in this repo: the user extracts it with
`tools/extract_card_art.py`, and `bartlebot/game/card_art.py` is the only thing that knows
where it lands. **Every lookup can return `None`** — most installs never run the
extractor, a few templates name a texture the game's own files do not contain, and the
names snapshot can be older than the game. So a surface showing art needs a form that
works without it, and must never log the absence as an error.

`_DeckCard` is the worked example: the strip is hidden outright when nothing resolves,
the summary line carries the counts either way, and a single card with no art is
skipped rather than given a placeholder — a placeholder per missing card turns an
install with no art at all into a grid of gray squares.

**A deck is listed by `DeckCardList` (`widgets/cards/deck_card_list.py`), on both surfaces
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
`bots/` is a game badge category (`bartlebot/content/bot_folders.py`), the row wears that
category's badge when one was extracted, and a row with none is its caption alone — at
the compact height, since only a row with an icon reserves room for one. Rows are looked
up by the folder name stored in `Qt.UserRole`, never by the caption, which comes from the
game's own wording.

Bot tags use them too (`card_art.tag_icon_path`, drawn at `TAG_ICON_PX` = 20 — 16 was
too small to read). Each surface degrades to what it was before: a picker row shows its
group dot, and a `TagChip` or `TagPill` its plain text form. With a medallion, both are
**drawn** by `paint_medallion_pill`: exactly `TAG_ICON_PX` tall, so the medallion *is* the
rounded left end, and the group-colored body starts at its center and carries the name.
An icon-only pill was tried and dropped, because the art alone was not enough to read a
card by. A stylesheet cannot do this shape: padding and border-radius will not put an icon
flush with a rounded end. The Type tags have no art, so they are always text, and a mixed
row is the normal case.

Drop categories use them in the Category column of both drop tables (Possible Drops and
the quicksell keep-list picker), through `drop_tree.drop_icon`, drawn at `DROP_ICON_PX` = 20
for the same reason as the tags. The icon sits beside the category text, never in place of
it, and an uncategorized row or an install with no art keeps the text alone. A pin row's
icon depends on its name (`card_art.drop_icon_name`), so pass the item name as well as the
category.

**The combat order is drawn right to left, and it is not a style choice.** A duel seats
four wizards around a circle and turns run clockwise, which puts a team's own players
right to left: slot 1 is the rightmost, and the game's own "Set Sigil Slot Preference"
dialog lists 4, 3, 2, 1 in that order with a gold gem on each. `duel_slot_gem.py` holds
the reasoning and the lookup; `CombatOrderWidget` (where an order is set) and
`PlayerBadgeRow(duel_order=True)` (where it is summarized) both mirror, and
`strategy_summary.format_combat_order` mirrors the text with them. **All four have to
agree** — a half-mirrored app is worse than the unmirrored one, because two surfaces then
disagree about which end enters first.

Only the *drawing* is mirrored. The value stays an ordinary first-to-last list everywhere
it is stored, passed or read, and the reversal happens once, at the point of layout. A
widget that also reversed its value would silently invert every bot file it saved.

Three things follow, and each has already been got wrong once in review. The gem names the
**position**, not the player in it, so on a reorder the gem stays put and the `P` number
moves. Hiding slots down to a duel's `max_players` hides them **by slot number**, not by
layout position — hiding "the last few" cells hides slots 1 and 2, the two a two-player
duel actually has. And the ordinal text stays beside the gem: the symbols are the *game's*
vocabulary, nothing on screen teaches them, and most installs have no gem at all.

**Art is a `QIcon` holding the full-size image, painted with `QIcon.paint`** — never a
`QPixmap` scaled to the logical size in advance. `card_art_tile.art_icon` is the one way to
load it. Users run any display scaling, and one machine can mix two (125% and 100% here), so
the resolution has to be chosen as each screen paints: `paint` asks for the logical size
times that device's pixel ratio and Qt scales down from the original, caching per ratio. A
20px pixmap is stretched to 25 physical pixels at 125% and drew soft with a jagged ring;
one pre-scaled for a single ratio is wrong on every other. So lay out by the size constant
(`TAG_ICON_PX`, `ROW_ICON_PX`, `SECTION_ICON_PX`), not by the image's own size, and where a
`QLabel` would have held the pixmap use `ArtIconView`.

An icon is artwork in its own colors, so a widget drawing one reads no `THEME` value
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

## A scroll area whose content grows under the user reserves its gutter

An as-needed vertical bar takes its width out of the viewport when the content
overflows and hands it back when it shrinks, so every card in the pane narrows and
widens by the bar's width — on a folding card, under the very click that folded it
(#112). A scroll area of cards, or of a form with sections that fold, calls
`reserve_scrollbar_gutter` (`widgets/layout/scroll_gutter.py`), the Qt stand-in for CSS
`scrollbar-gutter: stable`. Text editors, trees and tables keep the as-needed bar:
re-wrapping text costs nothing, and a permanent gutter there only costs width.

Do not "simplify" it into a viewport margin toggled with the bar's visibility. Qt
decides whether the bar is needed against the viewport *less its margins*, so the two
states measure different widths and content in the gap between them loops; the module
docstring has the detail.

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
because the flush is, so a dialog does not inherit the main window's.
`DragReorder.watch_for_show` is the pattern, and every card list that drags gets it. It is per window *and* once: the flag means the layouts outstanding at
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
  pairing is `bartlebot/game/game_sizing.py`'s ladder. `Resolution` is written to
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
`bartlebot/clients/tile_slots.py`; the short form:

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

## A folded region is drawn as one node: go through display ids

Two kinds of region are drawn as one `FoldedNodeItem`, keyed in `EditScene.node_items`
by its *region* id, with members in the config but no item at all: a subgraph instance
(`CallNodeItem`: side bars and link badge) and a collapsed plain region (#67,
`CollapsedRegionItem`: a single border in the region's color and a boxed plus). Which
regions fold is `subgraphs.folded_regions`. So an item lookup by state id misses every
member. Ask the scene instead: `display_id(state_id)` is the id it is drawn as,
`folded_node_of(state_id)` its node, and `state_ids_of(display_ids)` expands a
selection back to state ids. **Mind which you mean**: `is_folded`/`folded_nodes` are
"drawn as one node", `is_call_node`/`call_nodes` are "linked to a library definition" --
open definition, detach, link badge. An edge is drawn per `(source, when, dest)` in
display ids (an exit's port id stands in for `when`), and `transitions_of(edge)` gives
the flat transitions behind it -- one, or for an exit one per `(member, outcome)`
feeding it. `edge_for(transition)` is the way back. The ports come from `ports_of`
(`FoldPorts`): an instance's from its snapshot's port map, a collapsed region's
inferred from the edges crossing it (`subgraphs.boundary_ports`, the same rule *Save
region as subgraph* uses), so an edge added or deleted there can change them --
`_refresh_folds`, on `graph_changed`, rebuilds when it does. Nothing caches these: the
scene is rebuilt from the config on undo, `replace_config`, paste, detach and delete,
and the mapping is rebuilt with it.

What a member does rolls up onto its node: its errors (`refresh_errors`; an instance's
filtered by `member_warnings`, a collapsed region's not, since its gaps are the bot's
own), the run highlight (`BotCanvas.set_state` titles the node with the running member),
and search. An instance's member is never edited from a bot: its values come from the
instance's inputs, set on the call node's page, and anything else is the definition's,
edited in its own document. A collapsed region's members are edited by stepping in.

## Stored positions are the expanded layout: go through the fold

Collapsing a region moves nothing. `canvas/fold.py`'s `FoldMap` hides the grid columns
and rows only collapsed members used -- inside a collapsed span, past its node's own
column, with nothing drawn in them -- so the rest of the graph slides in, the way an
editor folds code. **Every read or write of a stored position in the scene goes through
`EditScene.stored_position` / `drawn_position`**; an item's `pos()` is where it is
drawn. With nothing collapsed the map is the identity, which is why a missed call
survives every test that collapses nothing. A collapsed node sits at its members'
top-left (`subgraphs.region_anchor`) and moving it moves them. `layout.auto_layout`
inserts a collapsed region's columns behind it, so the stored layout stays a valid
expanded one and the folded view has no gap. Stepping into a collapsed region opens it
(`set_expanded`), which makes its members visible and so unfolds its columns with
nothing more.

**A move is the one exception: never convert moved items through the map one by one**
(#159). The map is not a translation -- hidden columns between two items make one drag
a different stored distance for each -- so two side-by-side collapsed regions dragged
together expanded on top of each other, and so did one shifted by an insert's
`_open_column_at`. `_flush_positions` hands everything drawn to `fold.settle_move`,
which cuts and pastes a moved region the way an editor moves a folded block: the hidden
lines it leaves are deleted, and its span is opened as whole columns and rows where it
lands. Both are hidden, so the screen shows exactly what was dropped, and the stored
layout stays a valid expanded one -- the invariant `auto_layout` keeps. It has to hold at
the drop: Run mode's collapses are never written, so an expand has nowhere to make room.

**Collapsing works in both modes, and means something different in each.** In Edit
mode it sets the region's saved flag, as one undoable step. In Run mode it is a view:
`EditScene`'s collapse overrides sit over the saved flags, are never written, and are
dropped on entering Edit mode, which is where the saved state is decided. So "is this
region collapsed" is `EditScene.is_region_collapsed` (or `subgraphs.is_collapsed` with
the overrides), never `region.collapsed` read directly. `BotCanvas.set_region_collapsed`
and `set_all_regions_collapsed` pick the meaning from the mode; everything that asks
for a collapse -- the label's boxed minus, the collapsed node's boxed plus, the region
page, the menus, and Bar B's *Collapse all* / *Expand all* -- goes through them. The two
canvas buttons ask through `collapse_toggle_requested`, deferred a turn, since acting
rebuilds the item that was clicked. The boxed plus is a button only when it is drawn:
zoomed out past the compact level of detail, a collapsed node has no expand button.
**Never decide a hit from what the last `paint()` drew**: the minimap paints every item
too, at a tiny scale, straight after the canvas does, so "the last paint" is always the
minimap's. Ask the view the event came through (`event.widget().parentWidget()`) for
its level of detail instead -- the plus did the former first, and was never a button.

## A definition is a document of its own

Opening a definition (double-click a call node, *Open definition*, or the library's
Subgraphs folder) sets the open document aside with `BotCanvas.take_document()`, which
takes its config, undo history, camera and compose report. `restore_document()` puts it
back. The controller keeps the stack (`AppController._doc_stack`), and Save, Close,
autosave and crash recovery branch on `config.is_definition`. Going back recomposes
the document underneath from the library, as a run does. The definition's own signature
(inputs, panels, id and version) rides on `BotConfig.definition`, so undo and autosave
carry it with no extra code.

## The node pages are cards of compact rows (#142)

The node page and the subgraph node page are the same shape: the label once, as an
editable title (`node_cards.TitleEdit`); one gray line saying what the node is; Show /
Hide descriptions and Collapse all / Expand all (`PageFoldBar`); then #138's
`OptionCard`s. The node page's are Parameters, Connections, Notes and Role, and the
whole page scrolls as one -- before, only the params scrolled and the other three sat
pinned under them. The subgraph node page's are the ungrouped inputs, one per input
group, and Role. What a card *says* is `bartlebot/options/param_cards.py`, which needs no Qt.

- **One row builder, `_DynamicParamForm`, draws both pages' rows**, behind the option
  surfaces' `[bar][reset][?]` gutter. A row is a head line and a control. Actions on a
  field sit at the *end* of its control line as glyph-only `ActionButton`s (Fill
  current, Go to zone, TP; `row_actions` adds one more, the wiki import), never in a
  row of their own under it and never revealed on hover. Copy and Paste are on a
  coordinate box's right-click menu. Prose -- a param's help, its default hint, the
  glob hint -- folds behind `?` or lives in the placeholder and tooltip.
- **Reset means different things on the two pages.** On a subgraph node it drops the
  node's value so the definition's applies (`resettable` given), and the bar is lit
  while there is one. On a node it puts the node type's default back, grays out with
  "No default" on a required param whose default is its blank, and the bar is never
  lit -- as on Options, the column is reserved without claiming a meaning.
- **An empty required field is gray until it has been left empty.** Its asterisk and
  border turn red once focus leaves its row with it still empty, or when a save or a
  release is refused (`show_required_errors`), and clear the moment it is filled. A run
  is never refused -- Run is disabled while the bot has errors -- which is why it is
  not one of the triggers. Meanwhile the card's band says "N to fill", gray, folded or
  not, and turns red with its rows.
- **Record is on the band of the card it fills**, never a row inside it: it works with
  the card folded and stays easy to find. The card's first line says what Record fills
  (`recording.recording_hint`), then the recorder's own messages. Recording opens a
  folded card without storing that as the reader's fold.
- **Folds are the reader's, in `ui_state.json`** (`UiStateFoldStore`). The node page is
  one surface with four groups, the same on every node. A subgraph node page is a
  surface per definition, never per placed node; a card the reader has not touched
  starts open while it holds an empty required field and folded once complete --
  decided when the page is built, never while the user is working in it. Role opens on
  a node that holds a role. There is no author setting to keep a card folded: input
  groups had "Starts closed", and since a complete card folds anyway it only ever hid a
  field still to fill (#155).
- **Connections is Returns merged in.** Every route off the node by return value, from
  `param_cards.outgoing_routes`, which follows the engine's rules: a stop, a node with
  no edges, the unrouted FAILURE that goes to the recovery state with no edge drawn
  ("recovery · not drawn"), and an unrouted return nothing covers, in the warning
  color. Each return's description is behind its row's `?`.

## The signature page is a list with a detail editor (#155)

A definition's page with nothing selected (`canvas/signature_page.py`) is #142's cards --
Description, Ports, Inputs, one fold surface for every definition -- and the Inputs card is
one tree: the ungrouped inputs, each input group with its members indented, and each
promoted node as its heading alone. Selecting a line opens its editor in place under it,
one at a time. `bartlebot/content/subgraphs/signature_tree.py` is the half that needs no Qt.

- **Membership lives in one place, the tree.** An input belongs to the nearest group
  heading above it, so a drag (or Alt+Up/Down) is the only thing that moves an input into
  or out of a group. Do not add a per-input group dropdown or a per-group member list back:
  that was the page before, with one fact editable in two places. The menu's *Move to
  group* is the same move, not a second control.
- **The tree is derived, never stored.** `signature_tree.reorder` writes an arrangement
  back to `inputs` and `panels`; the widget holds no copy of the order or the membership,
  so undo and autosave carry every change through `BotConfig.definition`.
- **What a drop may do is `signature_tree.allowed`**, handed to `DragReorder` as `accepts`,
  with `carried` as `carry`: no input under a promoted node, no move that changes another
  input's group, a heading travels with its members. The keyboard steps past a refused slot
  by the same rule, so the two cannot disagree.
- **The editor is part of the line's item**, so the lines stay contiguous in the layout --
  what `DragReorder` needs -- and **pressing a grip closes it**, so a drag moves one-line
  items. Selection is page state that survives a rebuild and leaving the page; an input
  linked from a node's page comes back selected.
- **The tree is one Tab stop.** Up/Down move the keyboard's place, Enter/Space open,
  Alt+Up/Down move, the Menu key opens the line's menu.
- **Selecting reaches into the canvas without selecting there.** The page asks the canvas
  to light what the selection sets (`EditScene.set_link_lit`, a ring beneath the search
  ring) and a link row to center its node (`BotCanvas.center_on`). A selection would swap
  the page for the node's, the trip back Setup was moved out of Properties to avoid.
  Leaving the page for any other puts the lights out.
- **An unlinked input is an error, but only in `subgraphs.definition_problems`**, which
  blocks saving -- never in the structural rules a definition *file* is held to, or a file
  holding one would stop loading. Unlinking an input's last param is an ordinary step.

## Every drag-reordered list takes the keyboard too

A drag is not reachable from the keyboard, so every list `DragReorder` hosts also takes
**Alt+Up / Alt+Down** and offers **Move up / Move down** first in a card's right-click menu
(#155). Both go through `DragReorder.move`, the path a drop takes, so a host needs nothing
for it; `set_cards` hands each card the route (`CardFrame.set_reorder_route`). A new card
list gets it by using `DragReorder` -- do not give one its own arrows.

- **The route holds the engine and the card weakly.** A card keeps its route, and a strong
  reference closed a cycle the collector broke by clearing the engine's attributes while
  Qt still had it filtering the grips' events -- an `AttributeError` in `eventFilter`, in
  an unrelated test, only under `-n auto`.
- **A Move entry's keys are text in its label** (`add_move_actions`), never the action's
  shortcut: the card already owns Alt+Up as a `QShortcut`, and two bindings for one key
  are ambiguous, so Qt fires neither.

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
edge, a plain *region* (reached by selecting a whole region's members, the pill click,
or its collapsed node; label, color, Collapse / Expand -- disabled with the reason when
`collapse_blocker` refuses -- and a collapsed region's fights), a *call node* (`canvas/call_page.py`: label, its inputs and input groups as cards with
Record on a promoted node's band, roles by entry port, Open definition, Detach -- see the
section above), or in a definition document with nothing selected
the *signature* (`canvas/signature_page.py`: ports, and the inputs as a tree with a detail
editor -- see the section above). A definition's node
page links a param to a subgraph input (**Link**, one param) or puts the whole node on the
subgraph node (**Promote to input group** / **Demote**). A subgraph node nested in a
definition offers the same **Link** on each of its named inputs
(`BotCanvas.link_nested_input`): the binding names the nested region and the input, never
one of its members, so the value goes in through the inner signature and composition
forwards it (`subgraphs.forwarded_inputs`). A row of a promoted node's input group on that
nested node links the same way, under the key the node stores it by (`<member>.<key>`);
`subgraphs.resolve_instance_key` is the one place the two kinds of key are told apart, and
where a third would be added. A linked row carries the value up, not the Record button. A
linked param *is* its input's
default: `subgraphs.sync_linked_values` keeps them equal both ways, and a subgraph node
follows the default until its own page changes the value (Reset goes back). A row shows
status and action in one control: an unlinked row's Link button appears only on hover or
focus, and a linked row's chip reads as the link and turns into Unlink under the pointer --
except on a promoted node, whose chips are locked. In the UI an instance is a "subgraph node", a panel an "input group", and a definition's origin reads "Source: Official / Official (modified) / Custom" (`store_roots.source_label`, shared with the combat strategy cards); the code keeps `CallNodeItem` and `SubgraphPanel`. In a definition, Bar B shows the
version read-only; line 2 says where it comes from and how many bots use it, then the
definition's categories (#172) in the same `TagsField`, handed `SUBGRAPH_VOCABULARY`.
**Line 2 stays in a definition**, so the bar is the same height in both documents — hiding the row made every step in or out move the
graph 22px, and the bar was caught mid-change on screen. Open a document straight into
its mode with `setup(config, mode=...)`, never `setup` then `enter_edit_mode`, which
laid the canvas out in Run mode first.
`docs/SUBGRAPHS.md` has the flow.

**Variables have three editors, and each owns one thing** (#187, `docs/VARIABLES.md`):
the declarations are the **Variables section**, under Options on the Edit-mode Options tab
-- declarations, like options, and never the Properties page; a variable's reads are the
edge page's **Conditions** rows; its writes are the **`assignments`** rows on a
`set_variables` node's page and on an edge's page, as **When taken, set** (#189). The two row editors read the
declarations the canvas hands them each time a page is shown, never a copy of their own,
and share one value control (`widgets/inputs/variable_widgets.VariableValueField`), which only
ever offers a value the type takes. Because the Options tab *replaces* the Properties page
while the author declares, a declaration edit re-shows the page
(`BotCanvas.reshow_properties`): a page left showing a renamed variable's old name writes
it back on its next edit. A new surface that edits what a page was built from needs the
same.

Their **values** are a fourth surface, which edits nothing: the Run-mode Variables section
under Option Values (`variable_values_section.py`, #188), showing what each holds now and
marking what just changed. It is fed by the machine's callbacks through `AppController`,
never by reading the machine, and never logs a write -- the machine's own line is already
in the in-app log.

## Source: Official, Official (modified), Custom

Every list of bots, templates, subgraphs or strategies says where each item comes from,
in **the same three words** (`store_roots.SOURCE_LABELS`) and **the same pill**
(`widgets/pills/source_pill.source_pill`): Official filled with the accent, a modified one
outlined in it, Custom gray (#149). Never "Built-in", which says where a file is rather
than what it promises: Official means shipped, and so tested in game. It is worked out
from the root a file is in (`bot_roots.is_shipped`, `Origin`) and never read from the
file. A list long enough to need it has a Source filter beside it, offering the same
words (`tag_picker.source_vocabulary`): the bot library, whose filter also narrows the
Subgraphs folder, the node browser's Subgraphs tab, and File › Templates… (#164), whose
gallery is New Bot's cards with a last line added. New Bot's gallery has pills only.
Beside it on both lists of subgraphs is a *Category* filter (#172); in the bot library it
takes the bot tag pickers' place while the Subgraphs folder is selected, and a bot tag
left selected is kept, out of sight, rather than applied to subgraphs.
`docs/SUBGRAPHS.md` has the vocabulary.

**File › Templates… decides nothing itself** (`gui/dialogs/templates_dialog.py`). Which verbs a
card allows comes from `template_gallery.GalleryEntry`, and a verb a card does not allow
is disabled with the reason as its tooltip, in the footer row and on the card's
right-click menu alike. Edit, New and Duplicate end the dialog with a request the
controller acts on; Rename and Delete are callables the controller hands in, because
either can touch the open draft, and the gallery reloads after each.

A modified Official subgraph or strategy whose Official item has changed since the
override was made reads **Official (modified) · Official updated**
(`source_pill(..., official_updated=True)`, text from `store_roots.pill_text`), with a
tooltip saying what Revert does (#169). The Source filter still offers only the three
words, so the suffix never becomes a chip. Revert sits beside it: a *Revert…* button on
the Subgraphs folder card, the definition header's Revert, the strategy dialog's
*Revert to default* with a note in its About pane. Whether an item is out of date comes
from `SubgraphLibrary.official_updated` and `StrategyWorkingSet.official_updated`, never
from the widget.

**Export… sits on each item's own menu** (#170): a bot or Subgraphs card's right-click
in the library, a card's right-click in every tab of the strategy dialog, the Combat
tab banner's menu, and File › Export… for the open bot or definition. Every one ends in
`export_dialog.run_export`, which takes a `build(Choices)` callable rather than a plan,
so ticking a checkbox re-gathers the file and the summary always shows what Save
writes. Export sends what is saved: a surface that holds unsaved edits says so rather
than exporting them (`StrategyWorkingSet.has_unsaved`, the controller's
`_saved_for_export`). `docs/SHARING.md` has the rest.

**Import has three ways in, and one path** (#171): File › Import…, a share file dropped
anywhere on the window, and a file named on the command line. All three reach the
controller's `_import_share_file`. A drop is caught by `gui/share_drop.ShareFileDrops`,
an event filter on the *application*, because a drag goes to the widget under the cursor
and the canvas, the favorites bar and the combat order list all accept drops of their
own; any drag without a share file goes on to its widget untouched. The summary
(`import_dialog.ImportDialog`) is built from a `build(choices)` callable, like Export's,
so changing one row's choice re-matches the whole file: a copy rewrites the links of
everything after it. It never asks for typed text. A rebuild runs inside a combo's own
signal, so the old rows are `takeWidget()`-ed and `deleteLater()`-ed, never replaced by
`setWidget`, which deletes them at once.

**An Official bot is read-only.** `BotCanvas.set_read_only` keeps it in Run mode: every
way into Edit mode goes through `enter_edit_mode`, which emits `copy_requested` instead,
and the controller offers Make my own copy (`gui/dialogs/make_copy_dialog.py`). A new way into
Edit mode must go through `enter_edit_mode` too, and check the mode after it.

## One crumb bar, and the levels behind it

`canvas/subgraph_widgets.CrumbBar` is the one overlay that says where the canvas is, in
three states (`CrumbMode`): **Focus Mode** on a region of a bot (the region's color, a
single-line `ViewFrame`, "Focus Mode — <region>"), a subgraph node's **body viewed in Run
mode** (read-only), and a **definition** open as a document. The last two are a path in
the accent with the link glyph and a double-line frame, which the call node's side bars
echo: double means shared. The path is the documents under this one (the controller
hands them over in `set_definition_context`), this one, then each *level* in.

`BotCanvas._levels` is those levels: a region focused (either mode) or an instance
opened (Run mode only). A collapsed region is stepped into by double-click in either
mode; it stays collapsed behind the level, and one collapsed inside the focused region
pushes a level of its own ("Focus Mode › Outer › Inner") instead of replacing it.
Opening an instance or a collapsed region sets `EditScene.set_expanded`
(`BotCanvas._opened_regions`), which draws its members as nodes of their own at their
stored positions; everything else
is Focus Mode's visibility filter over them, and the run highlight lands on them by id
with nothing added. Every scene rebuild goes through `_after_scene_rebuild`, which prunes
levels an undo or a recompose took away and re-applies the filter — a rebuild that
skips it leaves hidden items visible under a crumb that says otherwise.

Stepping out: the close button and Alt+Left go one level, out of a definition when
nothing is focused. **Esc dismisses before it leaves** — the search bar, then a level,
then the selection, and only then the definition — so a stray Esc never lands on the
Save / Discard prompt. An earlier segment steps straight back to it, prompting at each
definition closed on the way (`AppController._back_to_document`).

A definition's crumb carries **Save**, **Save as…** and, only for an Official
(modified) one, **Revert**. The labels are short on purpose: the bar sits over the graph.
Save is `PRIMARY` with `on_accent=True`, which swaps its fill and text, because an
accent-filled button on an accent bar drew weaker than the tertiary ones beside it.
Save as makes the document the copy, at a new id and version 1, and Revert deletes the
user copy; both reopen the document rather than editing it, since its undo history
belongs to a definition with a different id or content.

Bar B is a three-column grid grouped by *subject*: everything about the bot on the
left, the run controls in the middle with the clock and the run state either side of
them, and the tools that act on the *view* — fit, failure-edge toggle — out on the
right with the mode toggle. Only the metadata column is two rows deep; both control
clusters span those two rows, which is what puts them on the bar's own vertical
center and level with each other.

**Nothing is stacked in the center or on the right, and that is the point.** The
status used to be a caption beneath the transport, which left the view tools as the
only cluster with an empty row under them — they centered in row 1 and read as
sitting high. Fixing that by spanning only the right column would put the two
clusters at different heights, which is worse. So the status moved onto the button
row and both columns span.

**The transport is a landmark: it stays in the center in both modes, and is disabled
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

**The failure-edge eye is a three-state cycle, not a toggle**, and the glyph is the
only thing reporting which state it is in — a clear eye, an eye behind a broken slash,
an eye behind a solid one. So it is deliberately not `setCheckable`: a checked look
cannot tell two of the three apart, and the button carries no label to do it instead.
`IconButton.set_icon_name` is the swap, which is exactly the "not driven by this
button's own checked state" case it exists for. `docs/RETURN_ROUTING.md` says what the
middle state hides and why.

**Bar B's height must not depend on the mode**, and each metadata field is a
`_Swappable` — a `QStackedLayout` holding its label and its editor — for exactly that
reason. A stack reserves the taller of the pair in every state, so the Run/Edit swap
cannot move the bar. Laying the two out one above the other and toggling `setVisible`
instead is what caused #83's toolbar flicker: `setVisible` on two children is two steps,
so a half-finished swap asked for the *sum* of both, and the canvas laid the bar out at
that intermediate height and painted it there. The bar being ~14px taller than Run mode
strictly needs is the price, and it is the right way round.

That is the general rule for anything the canvas lays out: **remove the dependence rather
than re-measure afterwards.** A parent resolves a child's height from a `sizeHint` that is
one event-loop pass stale — `LayoutRequest` is a *posted* event, and the parent's is served
before the child's — so an intermediate height is the one that reaches the screen, and the
child's later re-measure is never propagated back up. Leaving Edit mode with no tags left
Bar B 18px too tall until an unrelated resize. Forcing the re-measure was tried and was not
even reliable: it landed on the right pixel for two cycles and then stopped.

Two consequences worth knowing before touching a wrapping widget:

- **`QWidgetItem` answers `heightForWidth` from the widget's *layout*, never from the
  widget's own override.** So a widget holding a `FlowLayout` — `_TagsField`, any pill or
  chip flow — cannot floor its height through `sizeHint` or a `heightForWidth` override;
  the parent reads straight past both. `setMinimumHeight` is the floor a parent honors,
  and `_TagsField` sets it on every rebuild because the buttons are sized by the theme's
  font.
- **A pill or chip flow is the one thing that legitimately changes the bar's height**, when
  a heavily tagged bot's tag row wraps in Edit mode. That is a content change, not a layout
  bug, and a fix that suppressed it would be worse than the flicker. Give it the width
  instead — see "the tag row gets line 2 to itself" below.
- **`QStackedLayout` has no alignment of its own** — it ignores both `setAlignment`
  overloads — and gives a page its natural height at the *top* of the cell. Invisible for
  a page as tall as the cell, wrong for one that is not: a slot sized to the editor it is
  not showing left the 13px version pill four and a half pixels above the bot name. A
  one-child box layout (`_centered`) is the alignment the stack will not do.
- **A `FlowLayout` hangs its rows from the top of whatever rect it is given**, so a flow
  handed more height than its rows need leaves the slack underneath. That is the right
  default — a keyword list or a card grid in a tall panel belongs at the top of it — so
  centring is opt-in, via `flow_container(center_rows=True)`. Reach for it only where the
  rect is taller than the rows for a reason of its own, as Bar B's tag row is: the field
  reserves the editable form's height in both modes, and the spanning button clusters make
  the grid row taller still.

`tools/probe_layout.py` measures all of this — reach for it before theorising about a
layout or repaint bug; `tools/CLAUDE.md` has how to read a trace.

**Set the spacing on every toolbar row explicitly** (`_BTN_GAP` within a cluster,
`_GROUP_GAP` between). The style's default is 6px on Windows, so a row that only
called `addSpacing(4)` came to 80px inside a 68px slot and clipped the Save button
off its right-hand end. `tests/gui/canvas/test_canvas_toolbar_layout.py` checks that no button
overruns the slot it sits in, in either mode.

The two side columns take **equal stretch**, which is what keeps the center column
near mid-bar. That equality *is* the centring — it is not a stretch factor to retune, and
any weight that favours the metadata moves the transport off center by half the
difference. The cost is that the metadata is capped at roughly half the bar, so a long
filename elides and a heavily tagged bot wraps its tag row in Edit mode. Both degrade
quietly; a Run button that drifts with the length of the bot's name does not.

**The tag row gets line 2 to itself**, and the author rides on line 1 beside the name,
version and filename. The tag row is the only thing in the bar that grows with the bot —
everything else is a fixed width or elides — so it gets the width, and the author's fixed
180px field costs nothing on a line where the filename absorbs the slack. Measured against
the 87 shipped bots at 1600px, an ordinary window: 83 of them wrapped with the author on
line 2, and 13 without. At 1400 it is 86 against 35, and Run-mode wrapping goes away
entirely above 1200. The cost is that line 1 is now the crowded one — below about 1300px
in Edit mode the name field shrinks below its 240px — which is the right way round.
**Do not move the author back down to save line 1 a few pixels.**

The clock is **zeroed, not hidden**, while idle, and the clock and the status
**reserve equal, fixed widths** either side of the transport (`_size_status_slots`).
Both are the same concern: anything either side of those buttons that grows or
appears shoves them sideways at the one moment the user is watching them, and the
status word cycles `Editing` / `Idle` / `Running` / `Paused` as the run goes on.
Equal slots are also what center the transport within its own column. The slot is a
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
  it, `sync_in_zone` and the bot templates make it moot, and a field naming a
  place the app will not take you to reads as a promise. Pydantic ignores it in
  an older file and `save()` drops it.
- **Initial State / Recovery State** are badges on the nodes themselves;
  the labels only restated the graph.
- **The description** heads Option Values (read) and Options (edit) instead. Elided into
  a header it would hide exactly the text a user must read — four bots carry
  multi-line `REQUIREMENTS:` blocks saying things like "change the client
  resolution to…".

**Possible Drops** sits with the live drop tables in the right sidebar's Drops
tab: the two answer the same question at different times, what this run could
yield and what it has. It is the only authorial thing over there, which is what
`RightSidebar.config_changed` exists for. **Log** is the third tab there, so it
can be read without closing Option Values.

**Setup** is a fourth tab there, present only while it has something to say
(`sidebar/setup_panel.py`): the setup steps a bot made from a template still has to do, or
a template draft's step editor, and the review of a template update for a bot behind its
template. It is in the right sidebar and not a page of Properties so that the list and the
node a step points at are on screen together. As a Properties page, acting on a step hid
the list. Four rules:

- **A step card edits nothing.** Clicking one selects its node, and the fill-in is filled
  on that node's page, the one place it is editable. The drops step is the exception,
  because the drop list has no node: it lists the bot's fights, each with its own Import.
- **The tab is decided by `MainWindow._sync_setup_tab`.** Shown for a draft, for a bot
  with steps left, and for a bot whose template has released since its base. A bot that
  finishes its steps keeps the tab until it is closed, a bot opened with them all done gets
  none, and Bar B's template chip brings it back.
- **A change card asks; the canvas does.** The review (`docs/TEMPLATES.md`) is drawn above
  the steps: one `ChangeCard` per change under each release's note, with **Apply**, or
  **Use template's** / **Keep mine** on a conflict, and no button on one that can't be
  applied. A card edits nothing itself, as a step card does not: the panel emits
  `apply_requested`, and `BotCanvas.apply_template_change` makes the change as one undo
  step. **Finish review** advances the bot's base, which is what remembers a skip, and
  **Later** puts the review away until the bot is next opened. A subgraph definition opened
  over the bot is a visit, not another document: `_sync_setup_tab` keeps the bot's state
  through it, so coming back neither forgets a Later nor pulls the sidebar to Setup. The
  review is asked for on every refresh, so an applied or undone change shows at once, but
  `BotCanvas.template_review` works the diff out again only when the bot's own dump has
  changed. `template_ref_changed` is emitted after `config_changed`, never before: the
  controller reads the new base from the config it holds, which it swaps on `config_changed`.
- **A draft's steps are `CardFrame` cards**, like every other ordered list here: a grip to
  reorder, a cross to remove, and a dashed Add card at the end. The tab's edits go to the
  canvas (`begin_setup_edit`, `begin_setup_change`, `setup_edited`), which owns the undo
  history. A param row on a draft's node page offers *Add to setup step* on hover, and a
  row that is a fill-in wears a "Setup step N" chip that removes it.

- **A drop's category is a closed dropdown, never typed.** `_DropEditDialog` offers
  `quicksell.DROP_CATEGORIES` — derived from that node's two classification sets, which
  are already the vocabulary the wiki import produces and the art extractor has icons
  for. A typed category is one no icon, no sellability rule and no picker filter knows,
  so it reads as a broken row rather than as a new word; a genuinely new one is added by
  classifying it in `quicksell.py`, which `tools/drop_categories.py` already demands. A
  category a bot file carries that the list does not is offered anyway, so editing such
  a drop's *name* cannot silently strip it. Uncategorized is an entry rather than the
  absence of one, and name and category go through one `edit_drop` because `match_name`
  depends on both.
- **The live tables filter to the keep list**, which is what a user actually wants off a
  finished run: not every drop, but whether anything they said to keep came out of it.
  The rule is `quicksell.matching_keyword`, so a row shown is a row the node would spare,
  and the list is the *effective* one — a socket wrench kept by a `sell_*` bool shows up
  beside what was typed. The checkbox is disabled, not hidden, for a bot that never sells
  (no quicksell node, or Enable Quicksell off): there the filter can only hide everything,
  and a control that appears for some bots is one nobody finds. The keep list is a bot
  option edited over in Option Values and nothing reports here, so `DropSection.refresh_keep_list`
  re-resolves it when the Drops tab comes forward.

## Two surfaces: Option Values edits values, Options edits declarations

Both are **labeled "Options"** in the app: to a user it is one tab, holding values in Run
mode and declarations in Edit mode. "Option Values" is this file's and the code's name
for the Run-mode one (`OptionValuesSection`); the label was "Setup" until that word went
to the template's setup steps in the right sidebar. Never write "Option Values" in text a
user reads.

They are separate tabs of the left sidebar, and edit mode decides which pair is
there at all — Run shows **Option Values** (`option_values_section.py`) and Combat, Edit shows
**Options** (`options_section.py`) and Properties. Every production system that
solves this split separates the surfaces by context rather than by a toggle
inside one panel (Unity's Prefab Mode, Unreal's Class Defaults, VS Code's
settings UI against `package.json`), and edit mode is the context this app
already has.

- **Both surfaces draw their rows in folding cards** (#138): *This dungeon*, *Team*,
  *Quicksell*, *General*, in that order in every bot. `bartlebot/options/option_groups.py`
  decides which option goes where (`BotOptionSpec.group`), `sidebar/option_card.py` draws
  a card, and `CardFolds` there owns the folding for a surface. The rules a later change
  is most likely to break:
  - **The fold state is the reader's, and one answer.** Per group, in `settings.json`
    (`option_group_collapsed`), the same for every bot and shared by both tabs. Only one
    tab is on screen at a time, so each re-reads it in `showEvent` rather than the two
    being wired together. A card never folds itself: a click on its band *asks*.
  - **A fold never hides something the user must see.** The band's stripe is lit while
    any value on the card is the user's, folded or not (Unity's rule for a component
    with prefab overrides), and a row's warning — pets the keep list cannot spare —
    becomes a `⚠` on the band, folded or not, as on the assignment card.
  - **Card headers have no option gutter; rows keep theirs.** A header holding only a
    title would carry fifty pixels of empty gutter before it, while every row's revert
    and `?` still sit in one column.
  - **Enable Quicksell is a switch in the Quicksell band, not a row** (`heads_group`).
    A switch turns a feature on or off; a check box is an option in a form. It has no
    revert — flipping a two-state control back to the inherited value already drops the
    override — and no `?`: its description is its tooltip, and the top of the card while
    descriptions are shown. Options has no switch, since Enable Quicksell is user-owned.
  - **Collapse all and Expand all are drawn, not shown, on hover.** `HeadingAction`
    blanks its glyph rather than hiding, because a hidden widget leaves the Tab order and
    could never be focused into view. Hidden outright only while the section is folded.
  - **The pages reserve their scrollbar gutter, with no gap** (`gap=0`): a fold would
    otherwise jolt every card sideways (#112), and the page's own content margin already
    keeps the cards off the bar. The card's 14px of border and padding is on the left
    sidebar's floor — see `docs/WINDOW_SIZING.md`.
- **Option Values writes `settings.json`, never the bot file.** `values_changed` is
  deliberately *not* wired to `config_changed`: an override is not an edit to
  the bot, and a shared bot must stay byte-identical however its users tune it.
  `test_a_bot_file_is_untouched_by_a_value_edit` is the guard.
- **A row says where its value came from.** The four-level resolution runs for
  display too, through `bot_options.resolve_for_config` — the same gatherer
  `BotMachine.get_option` uses, so the panel and the run cannot disagree. The
  caption under a row names the level that answered, the gutter bar is the
  accent color when the user is that level, and Reset's tooltip names what a
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
  the next bullet states for Option Values, which Options was breaking. No name can change
  on a default edit, so there is nothing on that surface to rebuild; it is routed
  to `metadata_changed` (dirty the file, rebuild nothing) plus an Option Values refresh,
  because a declaration outranks the app setting Option Values resolves through.
- **No chrome appears or disappears with the override state, and a value edit
  refreshes one row rather than rebuilding the list.** Both are the same
  concern: a spin box whose row is rebuilt on `valueChanged` vanishes from under
  the pointer on the first arrow click, and a revert button that only exists once
  a value is overridden moves everything under it as you type. So the bar
  changes color rather than appearing, revert grays out rather than vanishing,
  every level including the user's own has a caption, and `_refresh_row` is what
  a value edit calls.
- **A row is a head line and a control, and the prose folds.** Measured on a
  shipped bot at a 400px sidebar, an ordinary option row was 41% node-authored
  description and 17% source caption, leaving the control the minority of its own
  row -- all of it at 9px, three quarters of the base size and small enough that
  Windows' subpixel antialiasing colors the strokes. So the caption moved onto
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
  different row. Only the middle slot differs: Option Values reverts a *value*, Options
  edits a *declaration*. Options never paints its bar, because the bar and revert
  are one affordance in Option Values and a lone marker with no paired action would be a
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
  gives Delete a labeled button, a confirmation and a Cancel. `_AddOptionDialog`
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
  toggles collapse. `LinkButton` (`gui/widgets/buttons/link_button.py`) is the shared
  control.
- **The route to the app settings is written once, at the far end of that same
  row.** Option Values's rows name the level that answered, and one of the four —
  *App settings* — is a page the user can actually go to; a link in every hint
  that mentions it would be eight identical links to one destination. So Option Values
  passes `DescriptionSwitches` a `trailing` link, separated from the two
  description commands by the stretch because it is not a third one of them.
  It emits `app_settings_requested` rather than opening the dialog: `exec()` may
  only be entered from a synchronous slot in `MainWindow`, which also refreshes
  the rows on `settings_applied` — a level Option Values resolves *through* has moved,
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
  (`parse_list_text` still reads a pasted CSV), and the keywords render as
  `KeywordChip`s in a `FlowLayout`. **Only keywords are chips**, each with how many
  sellable drops it keeps: an item ticked in the table is stored as `=Name` and
  matches that name alone (`quicksell.ITEM_PREFIX`), so its row is what shows it.
  A chip for it beside the keywords read as one more keyword, which is the
  confusion item entries exist to end.
- **An option that another option switches off is a child of it: indented,
  grayed, never hidden.** `BotOptionSpec.depends_on` names a bool option on the
  same bot, and `compose_options` carries it onto the composed declaration the way
  it carries `type` and the bounds — derived, never persisted, and dropped when
  the named parent is not in this graph, because a row indented under a row that
  is not there reads as a child of whatever precedes it. Quicksell's three sell
  flags are the case: with `Enable Quicksell` off nothing is sold, so there is
  nothing for them to spare, and they were three live tick boxes that could not
  affect the run and gave no sign of it.

  Four things that are load-bearing, and all four are the same rule the rest of
  this surface follows:

  - **Only Option Values indents, and only under a parent that is a row.** Options edits
    declarations, and `Enable Quicksell` is user-owned, so the parent has no row there
    to be a child of. On Option Values the sell flags are not indented either: their parent is
    the switch heading their card, and the card already says what they belong to.
    Minimum Team Size, under Team-Up, is the child that is. A row whose parent heads
    its card is still disabled while the switch is off, and so is every other row on
    that card — the keep list has no parent and is only a list while nothing is sold.
  - **The indent is applied to the row's *body*, past the gutter, and the gutter
    is untouched** — `build_gutter_row(..., child=True)`. The indent says what the
    row belongs to and the gutter says what can be done to it; indenting both
    costs the one column property the gutter exists for.
    `test_a_child_row_is_indented_but_its_gutter_is_not` is the guard.
  - **Grayed, never hidden, and the value is never cleared.** Hiding moves every
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
  75% of base, where Windows subpixel antialiasing starts coloring the strokes —
  while Option Values showed the identical node-authored prose folded and at full size.
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
- **Within a card, the list is ordered by where an option comes from, not by
  whether it is in the file** (tall controls, the keep list, go last in their card). The author's own options first, then the derived ones grouped by the
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
  resolution stays a plain four levels, a hand-edited file is still honored, and
  `test_no_bot_declares_a_user_owned_option` is what keeps the library clean.
- **Renaming an option is three fixups, not one.** The flat `flags` list, the
  `flag_groups` the edge editor actually writes — which take precedence at run
  time, so a stale name there evaluates `False` in silence — and the override
  store. `OptionsSection._rename_option` does all three; deleting does the same
  three in reverse.
- **One option-type → widget mapping**, in `gui/widgets/inputs/option_widgets.py`, used
  by both surfaces and the Add/Edit dialog. `param_panel._make_widget` stays
  separate: it maps *param* fields, a larger vocabulary (zone, xyz, wiki_page)
  no bot option can have. Three copies of this mapping is how the dialog came to
  hardcode `type="bool"` while the rows rendered floats — pressing Edit on
  Safety Delay rewrote it as a checkbox.
- **A bool is a checkbox in a list and a radio pair on its own.** In a row the
  caption is the option's name and a tick plainly means yes; under a field
  labeled `Default` a lone box captioned "True" reads as though False were
  unavailable. `BoolChoice` is the second form, and its third state — neither
  radio — is how a null declaration is shown rather than silently rewritten.
- **The Add Option dialog never creates a null declaration.** Null means "use
  the level below me", and a hand-added option has no app setting and no
  NodeSpec default beneath it — so a null there resolves to `None` and
  `_flag_<name>` reads it as `False`. The dialog only declines to *destroy* a
  null it was handed, which is what `_was_null` and `_default_touched` are for:
  opening Edit to fix a description must not promote the declaration.
