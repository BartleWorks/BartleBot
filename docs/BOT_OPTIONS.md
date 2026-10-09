# Bot options

Where a value lives, who owns it, and which of the four levels wins. The two surfaces that edit them — Option Values and Options — are documented with the rest of the sidebar in `bartlebot/gui/CLAUDE.md`.

An option is the user's, chosen before the run and never changed by it. A value the bot
learns *during* a run and reads back on its edges is a **variable**, not an option:
`docs/VARIABLES.md`. The two share the condition rows on an edge and nothing else.

## A value the user owns is never a node param

An app setting that a node param also expresses must never be applied by rewriting
the param on load. That is what `AppController._apply_bot_defaults` used to do to
quicksell's `sell_*` params, and it made the node param unreachable: a value stored
in a bot file was overwritten every time the file was opened, then silently written
back on the next save.

The fix was two special-purpose param types, `tri_bool` and `app_setting_str`, each
invented because a user's value had nowhere else to live. **Both are gone**, and so
is the reason for them: a user's value lives in `settings.json`, four-level
resolution decides who wins, and the vehicle is a **bot option with a null
declaration** — see *Bot options: whose value is it* below. A `ParamField` type for
this would be a third mechanism for a question two levels up already answers.

Prefer a bot option over a param whenever the value is not a fact about *this node
in this graph*:

| the value describes | where it goes |
|---|---|
| this node's place in this dungeon (a zone name, an XYZ, which player interacts) | a param |
| the user, or their machine (Safety Delay, what they will let quicksell sell, whether to spend gold on potions, whose wizard to port to) | a bot option, declared `default=None`, with an entry in `bot_options.APP_SETTING_KEYS` |
| this dungeon, but bot-wide (quicksell's keep list) | a bot option with a real default |

A bot option also reaches **every** node in the bot at once. That is not a detail:
quicksell's flags were per-node, so `hall_of_heroes` needed two quicksell nodes and
two mutually exclusive bool options to offer one choice between two keep lists. It
is one node now, declaring `["Merciless"]`: the choice is a value the user edits,
and `Enable Quicksell` already answers the third branch that skipped selling
altogether.

Three things that fall out of it:

- **Resolve once, at run time, and log which level answered.** `BotMachine.get_option`
  does the logging, once per option per run; `quicksell.resolve_options()` is the one
  place quicksell's five collapse into the plain dict everything below reads, which is
  what keeps the keep-list logic and the picker pure and testable without settings.
  `resolve_combat()`'s `source_label` is the older precedent.
- **Anything showing the user what will happen has to resolve the same way**, through
  the same gatherer, and say which level answered. `bartlebot/options/option_values.py` is that
  gatherer — `bot_options` stays pure and never imports `SettingsManager`, so the run
  loop, the Option Values rows and the safety dialog each hand it the same two impure inputs
  rather than each fetching them their own way.
- **Values already written into bot files have to be migrated**, or they start
  outranking the app setting the moment it stops rewriting them. Every bot file is in
  this repo, so migrate in place: `scripts/migrate_quicksell_options.py` moved
  quicksell's five, and `test_no_bot_carries_a_dead_quicksell_param` keeps them moved,
  because a param a node no longer reads is invisible rather than wrong.

`Safety Delay` was the first of these and is still the plainest: it describes the
machine's connection rather than the bot, so no stored value should outrank the app
setting.

## Bot options: whose value is it

`BotOptionModel.default` is the **author's** recommended value, never the user's
current one. The user's value lives in `settings.json` under
`bot_option_overrides: {bot_id: {option_name: value}}` — value only, no snapshot of
what it overrode — reached through `get_option_override` /
`set_option_override` / `drop_option_override`, which follow
`get_active_strategy`'s shape.

`machine.get_option()` resolves four levels, most local first, and logs which one
answered — once per option per run, because `get_option("Safety Delay")` fires on
nearly every node:

```
user override  ->  declaration (if non-null)  ->  app setting  ->  BotOptionSpec.default
```

The policy is `bartlebot/options/bot_options.py`: pure, like `bartlebot/clients/relog.py` — no Qt,
no settings, no client, so precedence is testable without any of them. The machine
gathers the four inputs; `resolve_option` decides between them and never raises.

Four things that are load-bearing:

- **A declaration of `None` means the author has no opinion.** Without it the app
  setting is unreachable, because a declaration is written for every required
  option — whichever level sits in the middle would be dead weight. This is the
  retired `tri_bool` param type generalised to a property of every declaration.
- **Absence is unset; a stored `[]` or `false` is a choice.** Never prune a falsy
  override. `exclude_none=True` in `BotConfig.save` sets the same trap in the bot
  file, which is what a null declaration relies on.
- **`BotConfig.id` is required, with no `default_factory`.** A minted id is
  indistinguishable from a real one, so a file missing it would take a fresh
  identity on every load and lose every value keyed to it. Save As mints a new id
  deliberately — a fork is a different bot, and inherits neither the overrides nor
  the combat strategy.
- **Make my own copy is the one copy that takes them (#149).** An Official bot is
  read-only, and asking to edit one makes the user's own copy (`bartlebot/content/bot_fork.py`).
  The copy is the same bot to the user, so `carry_over` copies every override and the
  accepted quicksell sales under the new id, duplicates every Bot Combat Strategy
  pointed at it, and maps the active pick to the duplicate. The Official bot keeps its
  own. Running an Official bot needs no copy: every value here lives in settings.
- **There is no prune sweep, and must not be one.** An option name is composed
  from whichever nodes are in the graph right now, on every graph edit, so cutting
  a node and pasting it back would drop the user's value in between. A dead row
  cannot affect a resolution either. Overrides go when the user deletes the option.

Nothing rewrites an option on load. `_apply_bot_defaults` used to overwrite Safety
Delay from the app setting on open, which is why `bots/` moved on every run;
`test_opening_and_saving_a_bot_changes_none_of_its_options` is what keeps it still.

## Beside the levels: quicksell's user keep list, and its warning

**The user's own keep list is not a level of `Quicksell Keep List`** (#34). It is
`quicksell_keep_list` on the Bot settings tab ("Always keep"), and every bot keeps it on
top of whatever the option resolved to. As a fifth level it would never be reached: a
resolution picks one winner, and nearly every bot declares a list, which outranks an
app setting. So `quicksell.resolve_options` takes it as a separate argument and carries
it as its own `user_keep_list` key, never merged into `keep_list`. The keep-list picker
writes `keep_list` back as this bot's value, and a merged list would copy the user's
keywords into every bot whose picker they touched. The picker shows a row the user's
list keeps as checked and locked, with a tooltip pointing to Settings → Bot.

**The quicksell safety warning's explanation can be turned off for good** (#145). Once
the templates reach the library nearly every bot sells, and a dialog on every run
teaches the user to click through it, along with the teleport and elixir warnings it
shares the dialog with. So the explanation carries a check box under it, *Don't show
this quicksell explanation again*, which turns off *Show Quicksell explanation before
each run* on the Bot settings tab. It is a check box under its own warning, the form
Windows and macOS use for suppressing an alert, rather than a footer button: the dialog
holds several warnings, and a button there reads as "don't show this dialog". The
switch is one answer for the whole app. What the explanation asks (lock what you value,
keep it on a list) is the same for every bot, and a per-bot answer would ask the same
question eighty times.

The switch does not reach the one case this dialog exists for. With the user's settings
as the baseline, a sale they set up themselves, in Settings or as a value for this bot,
needs no warning. The one level that can sell more than the user asked for without the
user touching anything is the bot file, so `quicksell.declared_sales` names the sell
flags a declaration turns on while the user's app setting keeps them, and the dialog
asks about each one once per bot, whatever the switch says. Tree of Life's Sell Skeleton
Keys is the case in the shipped library today. A declaration that keeps more than the
user would is never flagged, and nor is the keep list, since the user's own list is kept
whatever a bot declares.

Two things that are easy to get wrong:

- **Accepting a declared sale cannot be stored as an override.** Setting a value back
  to the one it resolves to drops the override (see the Option Values rules in
  `bartlebot/gui/CLAUDE.md`), so the per-bot answer lives apart, in
  `accepted_quicksell_sales: {bot_id: [option name, …]}`.
- **Both answers are written on accept only**: a declared sale through
  `SafetyWarning.acknowledge`, on any Run, and the explanation's check box through
  `SafetyWarning.opt_out`, only when ticked. A run the user cancelled records neither.
  Running past the explanation without ticking the box is not an answer, so it is shown
  again next run.

## A param gates an option the author knows about

Some choices only exist in some dungeons. Challenge Mode is in a handful of them,
Team-Up in rather more but far from all, and offering the user a switch for either in
the other sixty is noise they cannot act on. Two things are being asked, and they
belong to different people:

| the question | who answers | where it lives |
|---|---|---|
| does this sigil offer it, and is this a run to use it on? | the author | a **param** on the node |
| do I want it this time? | the user | a **bot option**, `required_when` that param |

`BotOptionSpec.required_when` names a bool param on the same node; the option is
composed into the bot only while some state has that param on, and one state is enough
for the whole bot. `offers_challenge_mode`/`Challenge Mode` and
`offers_team_up`/`Team-Up` are the two, and `Minimum Team Size` hangs off the second
through `depends_on` as well, because nothing it holds can matter while Team-Up is off.

`required_unless` is the mirror: the option is composed only while some state has that
param *off*, and an absent param counts as off. `teleport_to_friend` uses it for Friend
Name, which a port to a hooked player never reads. The one-state rule matters more here
than for Challenge Mode: a bot with one hooked-player port and one port by name still
needs the name, so the row stays.

Both halves are read at run time, and **both** have to be on:

```python
team_up = bool(params.get("offers_team_up")) and bool(machine.get_option("Team-Up", False))
```

Not belt and braces. An override outlives the option it was set on — the user turns
Team-Up on for one bot, and the value is keyed to the bot rather than to the node — so
the param alone is what stops a leftover `True` sending a client at a Team-Up button
that is not on this sigil. The param alone must not act either: a bot that *can* team
up still enters alone until the user says otherwise.

The param is also the only curation lever there is for Team-Up, and deliberately a
convention rather than an enforcement point. Whether a run is a reasonable one to bring
a matched stranger into is a judgement the bot's author can make and the app cannot;
what actually wrongs that stranger is what the bot does *after* entry, not how it got
in. The restricted-zone guard in `docs/RESTRICTED_ZONES.md` stays the only thing in the
codebase shaped like a no-bypass invariant — enforcing a soft norm the same way dilutes
what that guard signals.

## Which card an option is drawn in

Option Values and Options draw a bot's options as four folding cards, always in this order (#138):

| card | holds | first run |
|---|---|---|
| **This dungeon** | the author's own options, Challenge Mode | expanded |
| **Team** | Player Realms, Team-Up and Minimum Team Size, Level Scaling Players, Friend Name | expanded |
| **Quicksell** | Enable Quicksell as the header switch; the sell flags; the keep list | collapsed |
| **General** | Safety Delay, Refill Potions, Favorite Realm | collapsed |

The card is declared once, on the option: `BotOptionSpec.group`. An option no node declares
can only be about this dungeon, so it goes in *This dungeon*. `BotOptionSpec.heads_group`
marks the one option drawn as its card's switch rather than as a row; with it off, every row
on the card is disabled. The order is fixed rather than following the graph, so an option is
in the same place in every bot — even a bot with two options gets two cards, which is what
teaches where things are before a long list arrives. `bartlebot/options/option_groups.py` is the pure
half (grouping, the first-run folds, and what a folded card's summary says);
`gui/sidebar/option_card.py` draws it. Which cards are folded is the reader's, stored per
group in `settings.json` (`option_group_collapsed`), the same for every bot and both tabs.

## A declaration is derived, not persisted

An option a node requires is **not written to the bot file**. Its name, type,
default and description are composed from `NODE_SPECS` at load, by
`bot_options.compose_options()`; the file keeps only what is authorial — an
option the author hand-added, or a required one whose default they deliberately
changed. `BotConfig.save` prunes the rest through `storable_options()`.

Injection is what made a description fix leave 80 stale copies behind, and it put
`Challenge Mode` in 61 bots when 2 could act on it. It also never refreshed what
it had added: 26 files shipped a description no current NodeSpec produced, four
mutually contradictory texts for the one setting.

- **The prune matches by name, never by byte-identity.** Only 66 of 97 stored
  declarations agreed with a NodeSpec exactly; the other 31 differed only by
  carrying one of those stale injections, and byte-identity would have filed
  every one of them as author intent. So for a derived option, type and
  description are dropped unconditionally and the default survives only when it
  differs from the composed one.
- **`3` and `3.0` agree; `False` and `0` do not.** JSON cannot tell an int
  default from a float once written, so a pin equal to the node's default is
  dropped either way. `False == 0` in Python, and collapsing those would throw
  away a checkbox's real answer.
- **The file stops being self-describing, so the orphan check is not optional.**
  A transition guarding on an option nothing declares still runs —
  `_flag_<name>` resolves to `False` rather than erroring — so the bot quietly
  stops taking that edge. `orphaned_flag_options()` walks both flag forms and
  the editor reports it in the validation banner;
  `test_no_bot_guards_a_transition_on_an_option_nothing_declares` keeps the
  shipped library clean of them.
- **A hand-added option must not borrow a name any NodeSpec uses.** Both
  `compose_options` and `storable_options` treat every name in
  `system_option_names()` as derived, wherever it is declared — so an author's own
  option that happens to share a name with one some unrelated node requires is
  filed as dead weight and dropped on the next save, silently, from the only copy.
  Inherited from `_inject_required_options`, but sharper now the prune runs
  every save.
  `test_no_bot_declares_an_option_borrowed_from_a_node_it_does_not_run` is the
  guard; its two siblings —
  `test_no_bot_stores_a_declaration_that_says_nothing` and
  `test_no_bot_declares_an_option_nothing_reads` — catch a stale copy and a switch
  wired to nothing.
- **Editing a derived option in the Options surface materialises it into the
  file.** That surface edits *declarations*, and a declaration has to be in the
  file to exist; `OptionsSection._declaration_for` is where the transient
  composed row becomes a stored one. The save-time prune takes it back out if
  the user changes their mind back.

## A node cannot pin a bot-wide option

`StateModel.option_overrides` let one node override a bot-wide option for itself. It
is gone, with no shim, because it was this same confusion one level down: every
surviving use in the library pinned `Safety Delay`, so a bot pinning `0.0` gave a
user on a bad connection no slack at all and no way to get it back.

A pin is always a symptom, and naming the actual cause replaces it with something
cheaper as well as more honest. The three causes behind the sixteen the library had:

| the pin was buying | now |
|---|---|
| a shorter post-interact quiet period, on an interaction with no dialogue | `has_dialogue: false` — which returns *before* that wait, so 1.0 s becomes 0 |
| time for a cutscene | `animation_delay`, **added to** the resolved Safety Delay — the animation takes N seconds on any connection, and *then* the response needs the user's slack |
| retries against a slow shared object, in sequential mode | `object_use_delay` ("Delay Between Object Uses"), the gap `take_turns` already left between players and no node exposed |

`scripts/migrate_option_overrides.py` is the migration, and
`test_no_bot_pins_a_bot_wide_option_for_one_node` keeps the library clean — Pydantic
ignores an unknown key rather than rejecting it, so a re-introduced pin would be
dropped silently on the next save instead of failing.

## When no bot should ever store a value

Most user-owned values are "no bot *needs* to pin this". Some are stronger: storing
one in a bot file is always wrong, because the value describes the user and the file
is shared. `teleport_to_friend`'s friend name is the case — a character name in a
committed bot is both a privacy leak and a bot only its author can run.

That is still a bot option with a null declaration; what changes is that the
declaration must **stay** null — so `Friend Name` is in `USER_OWNED_OPTIONS` and the
Options tab has no row for it. `Favorite Realm` is the same shape for a different
reason: a realm in a shared bot file would send everyone who runs it to the same
realm, which is the crowding a favorite realm exists to escape. It is a `realm`
option — a `str` holding the realm's key, picked from the `bartlebot/game/data/realm_names.json`
snapshot rather than typed, because the node matches the key against every locale's
display text and a free-typed name has nothing to match. Nothing enforces that in the schema, so the guard is
`test_no_bot_stores_a_friend_name`, which checks both places a name could land: the
old `friend_name` param, and a *declaration* of the option that a hand-edited file
could still carry. Option Values — where a user actually sets it — writes an override and
cannot touch the file at all.

The run-time resolution, with its `source` label, is `teleport_to_friend.resolve_target`,
and it is pure: the resolved option value is passed *in*, so the precedence between it
and the bot file lives in one function that reads no settings.

A literal left in an older file still wins at run time, logged as deprecated, so
upgrading does not break a bot — but the editor drops it on the next save, and the
safety dialog stops offering the app-setting field while it is there, because editing
a value the run will ignore is worse than not offering it. The dialog withholds that
field for the same reason when a per-bot override is winning.

## An option type may be node-only

`bool` / `int` / `float` / `str` / `str_list` / `player_list` are what the Add Option
dialog offers. Two more exist that it deliberately does not: `drop_keep_list` (a
`str_list` shown as this bot's own drop table, one tick box per item) and
`friend_name` (a `str` with the online friends list behind a button).

They are types rather than special cases keyed on an option's name, so
`make_option_widget` stays the one option-type → widget mapping. Each degrades to the
plain control for the type underneath when its `get_drops` / `get_clients` is missing,
which is the right answer on a surface with no bot behind it. Neither is offered for
hand-adding, because a hand-added option has neither of those things behind it.
