@../../../bartleby-core/bartleby/qt/CLAUDE.md

# GUI conventions

Conventions for everything under `bartlebot/gui/`. The family's GUI rules are core's
`bartleby/qt/CLAUDE.md`, imported above; these are BartleBot's own: its canvas, its
documents, and which of its panels owns what.

## Never parse a combat DSL on the GUI thread

A DSL check is an Earley parse, up to ~800 ms for a long strategy. `combat_runner.dsl_check`
caches answers in memory and on disk, but a strategy just written or imported, or every
strategy after a wizsprinter bump, has none, and asking for it from a widget freezes the
window -- once per card in a list, and once per *keystroke* in a DSL box, since every
edit redraws the strategy's card. A surface drawing health passes
`DeferredDslChecks.validator(key)` to `summarize_duel`/`summarize_player` and calls
`request_unknown()` afterwards; an editor's debounce calls `checker().request(text)` and
listens for `checked`. Both live in `gui/combat_strategies/dsl_checks.py`, which also has
why the checks run in a child process rather than a thread (the interpreter lock).

One known exception, kept on purpose: loading a strategy into `PlayerStrategyEditor`, or
refreshing an `AssignmentCard`, still parses an unknown DSL on the GUI thread, because a
loaded strategy shows its error at once and tests hold it to that. It costs up to ~800 ms
only for a DSL with no answer yet (just imported, or after a wizsprinter bump).

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

## A position is a node's top-left, and every first port is PORT_Y0 below it

A stored position (`StateModel.position`, an instance's `anchor`) and an item's `pos()`
are both its top-left corner, whatever its height, and the grid snaps that corner to
multiples of `(GRID_W, GRID_H)`. Every shape -- plain node, call node, collapsed region,
Entry/Exit pill -- puts its first port on each side `PORT_Y0` below its top, and each
later one `PORT_PITCH` further down (`node_item.py`), so level tops mean level wires
whatever the heights. A collapsed node's port rows sit under its title and above its
sub-line for that reason. Snapping by the center, as the grid once did, lifted a taller
node's ports by half its extra height.

Every rule that places a node uses the same corner: the snap, `stored_position` /
`drawn_position`, `find_empty_grid_cell`, `free_position_near`, the fold. One that used
another point put every insert a row above its neighbors (#85).

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

**The Combat tab's banner names where the fights' strategies come from** (epic #201, Stage
5), because a fallback is shown, never asked about, and this is where it is shown. Its
words are `combat_resolution.describe_source`'s, which resolves as a run would and writes
nothing, so the banner cannot name a source the run would not use. Five states:

| state | caption · name | line under it | face verbs |
|---|---|---|---|
| the user's pick | Bot Combat Strategy · its name | its summary; with its default unset, *Fights with no duel strategy: Team: …* is the tooltip, since beside the summary it elides and the default card shows it | Change…, Rename…, New… |
| an Official one, nothing picked | Bot Combat Strategy · its name, with the **Official** pill | which default its uncovered fights use, if any | Create Bot Combat Strategy, Change defaults…, Change… |
| the defaults | No Bot Combat Strategy · Default Duel Strategies | *Team: Default Team Duel (recommended)*, or *(yours)* | Create Bot Combat Strategy, Change defaults… |
| no file readable | No Bot Combat Strategy · Default Duel Strategies | *Team: the built-in DSL, …* | the same |
| a pick whose file is gone | Bot Combat Strategy · *File missing*, in `error` | *Every run stops to ask until the pick is cleared*; the tooltip is the fault in the dialog's words, and what a clear would use | Clear pick, Create Bot Combat Strategy, and Change… with an Official one |

The team size is the clients connected (`set_client_count`); with none, both Solo and
Team are named. What was passed over on the way is the line's tooltip, in the log's words.
Change defaults… asks the main window for Settings › Combat, which re-reads the banner
on OK (`refresh_source`). The default card, while its Bot Combat Strategy has no default,
draws the default that answers instead of a bare label, with an **Official** or **Your
default** pill beside its Default badge (`StrategyPanel.set_fallback`); with nobody
connected and the two from different sources, it has no pill, since one would be wrong.
The tab never falls back to an Official Bot Combat Strategy as the pick, on load or after
a delete (`_own_bcs`): that is level 2, which answers without being picked, and picking it
for the user would make it level 1. A pick whose file is gone is the one fault the banner
shows, since every run stops on it: naming the defaults there instead (as Stage 5 did)
left the user looking at a valid setup and a dialog on every run. Only Clear pick forgets
it, never loading, which would write `settings.json` for a file that may be back on the
next start (epic #201 review, R3).

**A fight fled on the default is marked where the fight is drawn** (epic #201, Stage 7;
`docs/COMBAT_CONFIG.md` has what counts). `CombatTab._refresh_fled` asks
`flee_marks.fled_on_default` which of the session's marks still apply, and puts each in
one of three places:

| the fight | its mark | the fix, already first |
|---|---|---|
| exposed, on the default card | its own `ActionPill`, `set_flagged`: *Lost Soul · fled 2×* | the pill's click |
| not listed in the sidebar, with a Bot Combat Strategy | a `WarningPill` on the default card's pill row, after the source pills: *Fled 1× on the default* | Change… |
| any, with no Bot Combat Strategy | a `WarningPill` on its own row under the banner's line | Create Bot Combat Strategy |

The fill is `warning` with `pill_text`, the canvas's validation pill, so the color means
the same thing in both places; `error` stays for what does not work. The banner's mark
has its own row because the name beside the Official pill elides first. The marks are
redrawn by `refresh_source` and after each `_save_bcs`, never during the rebuild before
it: the pick is resolved from its file, so a fight just given a strategy would still read
as on the default.

**Setup is core's frame and pages, with BartleBot's own after them** (epics #201 and #215,
Stage 11 of #167). The dialog, `FirstRunPage` and the pages both products ask are core's
`bartleby/qt/dialogs/first_run_dialog.py`, and so are the rules for a page: core's
`bartleby/qt/CLAUDE.md`, *Setup is a list of pages*. BartleBot's
`gui/dialogs/first_run_dialog.py` holds its own four pages, its texts for the Accounts page,
the area a launch profile's resolution is judged in (`window_panes.setup_area`), and
`setup_pages`, its list; `docs/FIRST_RUN.md` lists the pages. It is opened from
`AppController._after_window_shown`, a timer's synchronous slot, before a file named at
launch is imported: each opens a modal, and a second timer would fire inside the first
one's `exec()`. File › Set up BartleBot… opens it with `rerun=True`.

Every test's `%APPDATA%` is a first run, so `tests/conftest.py` makes
`FirstRunDialog.exec` fail the test that calls it. Drive the dialog by its buttons, as
its tests do.

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

**A bot card's menu has Export… and Delete Bot…** (#268). Delete is grayed out with the reason on an
Official bot, and refused while the bot is open. Nothing links a bot, a fork included (`forked_from` is
provenance only), so the confirmation names no other bot; it counts the Bot Combat Strategies that go with it,
and the option values, accepted sales and active pick keyed on its id go too (`content/bot_delete.py`).

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
  (no quicksell node, or Quicksell off): there the filter can only hide everything,
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
  - **Quicksell is a switch in the Quicksell band, not a row** (`heads_group`).
    A switch turns a feature on or off; a check box is an option in a form. It has no
    revert — flipping a two-state control back to the inherited value already drops the
    override — and no `?`: its description is its tooltip, and the top of the card while
    descriptions are shown. Options has no switch, since Quicksell is user-owned.
  - **Collapse all and Expand all are drawn, not shown, on hover.** `HeadingAction`
    blanks its glyph rather than hiding, because a hidden widget leaves the Tab order and
    could never be focused into view. Hidden outright only while the section is folded.
  - **The pages reserve their scrollbar gutter, with no gap** (`gap=0`): a fold would
    otherwise jolt every card sideways (#112), and the page's own content margin already
    keeps the cards off the bar. The card's 14px of border and padding is on the left
    sidebar's floor — see `bartleby-core/docs/WINDOW_SIZING.md`.
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
  both surfaces.** The pieces are shared — core's `bartleby/qt/sidebar/option_gutter.py` holds
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
  flags are the case: with `Quicksell` off nothing is sold, so there is
  nothing for them to spare, and they were three live tick boxes that could not
  affect the run and gave no sign of it.

  Four things that are load-bearing, and all four are the same rule the rest of
  this surface follows:

  - **Only Option Values indents, and only under a parent that is a row.** Options edits
    declarations, and `Quicksell` is user-owned, so the parent has no row there
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
