# Sharing: share files, Export and Import

A user sends a bot, a Bot Combat Strategy, a subgraph, a duel strategy, a player
strategy or a deck to someone else as **one file that carries its own dependencies**.
Stage 4 of epic #173 (#170) built the format and Export; Stage 5 (#171) built Import.
The plan, with the reasoning behind every rule here, is `docs/SHARED_CONTENT.md`'s page.

Code: `bartlebot/shared/share_file.py` (the vendor's format, and the three kinds
BartleBuddy shares), `bartlebot/share_export.py` (BartleBot's kinds, and following each
kind's links), `bartlebot/gui/export_dialog.py` (the export summary);
`bartlebot/shared/share_match.py` (the vendor's matching rules),
`bartlebot/share_import.py` (checking, matching and writing),
`bartlebot/gui/import_dialog.py` (the import summary) and `bartlebot/gui/share_drop.py`
(dropping a file on the window).

## The format

Plain JSON, so a file can be read and diffed:

```json
{
  "format": 1,
  "app": "BartleBot",
  "app_version": "1.0.0",
  "main": {"kind": "bot", "id": "…"},
  "items": [
    {"kind": "bot", "id": "…", "sha256": "…", "history": ["…"], "content": {"…": "…"}},
    {"kind": "subgraph", "id": "…", "sha256": "…", "history": [], "content": {"…": "…"},
     "overrides_official": false}
  ],
  "requires": [
    {"kind": "subgraph", "id": "…", "sha256": "…"}
  ]
}
```

| field | what it is |
|---|---|
| `format` | `share_file.SHARE_FORMAT`, the vendor's to raise. A product refuses a file whose format is newer than it knows: the models drop fields they don't know, so the first save would lose data. |
| `app`, `app_version` | the product that wrote it, for the message that asks a receiver to update first |
| `main` | what the file is for. It sets the extension, and import reads it, never the extension alone |
| `items` | every **Custom** item the main item needs, packed, the main item included when it is Custom |
| `requires` | every **Official** item it needs, named by id and the hash of the Official item as the sender has it, never packed |

A packed bot also carries `folder`, the library folder it is in (`01_wizard_city`), so
the receiver's copy lands in the same one. It sits beside `content`, not in it, so it is
not hashed, and it is left out of the file when there is none.

An item's `content` is what its own file would say, less the two fields that record its
lineage (`content_hash.HISTORY_FIELDS`): `history` travels beside it, and an override's
`official_sha256` never travels. `content` has been through JSON, so it is exactly what
the file holds. `sha256` is `content_hash.content_sha256` of exactly that content, so a
receiver can check it.

An **Official (modified)** item is packed with `overrides_official` set. The receiver
gets it as a Custom copy, never as an override of their own Official item. An
unchanged Official item is never packed: the receiver has it from their own install,
and a file can't make anything Official.

## The kinds

| main item | `kind` | extension | owned by | what rides inside |
|---|---|---|---|---|
| bot | `bot` | `.bbot` | BartleBot | its Custom subgraphs, nested ones included; optionally the sender's Bot Combat Strategy for it, on by default |
| Bot Combat Strategy | `bot_combat_strategy` | `.bbcombat` | BartleBot | the library duel and player strategies it and its own duels link; optionally its decks, on by default; its bot |
| subgraph | `subgraph` | `.bbsubgraph` | BartleBot | its nested Custom subgraphs |
| duel strategy | `duel_strategy` | `.bbduel` | the vendor | its Custom player strategies |
| player strategy | `player_strategy` | `.bbplayer` | the vendor | nothing |
| deck | `deck` | `.bbdeck` | the vendor | nothing |

The `bb` prefix is the family's (BartleBot, BartleBuddy, Bartleby), so a sorted folder
groups them. The three vendor kinds' extensions, nouns and file type names are in
`shared/share_file.py`, which imports nothing from `bartlebot` outside `shared`;
`share_export.EXTENSIONS` adds BartleBot's. `FILE_TYPES` holds the description each
type gets ("BartleBot bot", "Bartleby duel strategy"): the save dialog's filter now, and
the installer's file type registration in #27.

What each export follows:

- **Bot:** every region with a `source` names a subgraph, and every definition's own
  linked regions name more. Each is packed or named once, however many links reach it.
  The bot's `template` link travels as it is: a receiver who has that template gets
  setup steps and updates, and one who doesn't still has a bot that runs.
- **Bot Combat Strategy:** its own duel and player strategies ride inside it. The library
  duels its fights and default link are packed or named, with their player strategies;
  so are the library player strategies its own duels link. Its `bot_id` names its bot,
  which is packed (with its subgraphs) or named like any other dependency.
- **Duel strategy:** its player strategies. Exported from a Bot Combat Strategy's own
  section, its links are found there first, as a run finds them, and those are packed
  as Custom items.

A link the sender's library can't resolve, such as a subgraph deleted since a bot linked
it, is neither packed nor named, and the summary lists it under *Not in your library*. A
bot still runs the snapshot it carries.

### Decks

A deck file holds one preset: its name, description, tags, schools and cards
(`share_file.shareable_deck`). The legacy `tc` field is left out. So is
`apply_archmastery`: whether applying the deck sets the school is the owner's decision,
and the receiver becomes the owner. A Bot Combat Strategy's own decks travel the same
way, or not at all when the user clears *Include its decks*. The archmastery school a
deck was imported with is a fact about the deck and travels.

## What never travels

**Anything from settings.** Which Bot Combat Strategy is active, every option value,
favorites. Nothing that writes a share file imports `settings_manager` or
`option_values`. A bot's export offers the Bot Combat Strategy that is the active pick
for it, so the pick chooses what is *offered*, but nothing in the file says which one
was active. `test_no_settings_value_can_reach_a_share_file` exports every kind with
settings empty and again with them full of a sentinel, and the files must be identical.

**Anything that executes, permanently.** Node params, the combat DSL and decks are data.
Any future feature that would put executable content in a share file is ruled out by
this, not left to be weighed again.

## Export

**Export…** is on each item's own menu:

| item | where |
|---|---|
| bot | a bot library card's right-click, and File › Export… with the bot open |
| subgraph | a Subgraphs card's right-click, and File › Export… with the definition open |
| Bot Combat Strategy | the Combat tab banner's menu |
| duel strategy, player strategy, deck | a card's right-click in the strategy dialog's Duel, Player and Deck tabs, in every mode |

Export sends what is saved. File › Export… offers to save first; the strategy dialog's
menu turns Export off, with the reason, on an entry with unsaved edits; the Combat tab
writes every change as it is made, and its export rereads the file.

**The summary** (`gui/export_dialog.py`) lists every item in three groups: *Sent in the
file*, *Official, so not sent*, and *Not in your library, so not sent*. Each line shows
the item's name and description, since those are the user's own text going to someone
else. A bot's export has two checkboxes, both on: its Bot Combat Strategy, and that
strategy's decks (off with it). A Bot Combat Strategy's export has the second. When
nothing would be packed (an Official item with nothing Custom riding along), Save is
off and the summary says why. Save asks where, starting in Documents with a file name
made from the item's, and adds the kind's extension if the name given lacks it.

An Official bot can be exported: it is named, and only what rides with it, its Bot
Combat Strategy, is sent. In developer mode nothing is Official, so everything is packed.

Templates aren't shared until after 1.0.0 (#165); File › Export… on a draft says so.

## Import

**Ways in:** File › Import…, dropping a share file anywhere on the window, and a share
file named on the command line, which is how a double-clicked file will arrive once the
installer registers the types (#27). Handing a file to an app that is already running is
#27's too. Import reads `main`, never the extension; the extension only decides which
files a drop or the open dialog offers.

### Refused before anything is written

| the file | what it says |
|---|---|
| a `format` newer than this build's, an item kind it doesn't know, or a library item whose own `format` is newer | "Made with a newer BartleBot (1.2.0). Update BartleBot, then import again." The models drop fields they don't know, so importing anyway would lose data. |
| an Official item in `requires` that is missing here, or older here: its hash is neither the receiver's nor in the receiver's Official item's history | the same |
| an item that fails validation, holds its id twice, or is a template | why |
| a bot or subgraph using a node type this build doesn't have | the type, and the update message |
| a bot or subgraph naming a restricted zone | the list a run start gives (`restricted_zones.describe_found`), and that it cannot be turned off |

A newer receiver is fine: an Official item whose history holds the sender's hash is newer
than what the sender had, and nothing released is ever removed.

### Matching

Every packed item is matched **by its id** against the receiver's copy of that id, using
the content hashes and histories every item keeps (`docs/SHARED_CONTENT.md`, Stage 3).
Nobody is ever asked for an id.

| the incoming item | outcome | choices, default first |
|---|---|---|
| has the same hash as the receiver's copy | Already in your library | none |
| was imported as a copy before | Already in your library, as a copy | none |
| has an id the receiver has as Official, or `overrides_official` | comes in as a copy, so it never shadows the Official item | none |
| has an id the receiver doesn't have | New | Add |
| has the receiver's hash in its history | a newer version of yours | **Update mine**, Import as copy, Keep mine |
| has a hash the receiver's history contains | yours is newer | none: yours is kept |
| matches none of these | different from yours | **Import as copy**, Replace mine, Keep mine |

A conflict defaults to Import as copy, never to Replace: an import that overwrote by id
without asking is how Grafana users lost dashboards. A library item the receiver holds
in a newer format is treated as newer.

Items are written with **the history they arrived with**, never through `record_save`:
the item is the sender's, and so is its past. A copy starts with none, as Duplicate's do.

### Import as copy

A copy gets a new id and, when its name is taken, a numbered name (`Loop (2)`). Its id
is not random: `share_match.copy_id` derives it from the kind, the id and the hash it was
sent with. Importing the same file again finds the copy it made the first time and writes
nothing, and the same item arriving in two files becomes one copy, not two.

Items are matched in the order their links run: player strategies, duel strategies,
subgraphs (nested ones before the ones that nest them), bots, Bot Combat Strategies,
decks. When one comes in as a copy, every later item **in the file** that links it is
rewritten to the copy before it is matched:

| link | rewritten in |
|---|---|
| a subgraph node's `source.subgraph_id` | bots, and definitions that nest one |
| a player assignment's `player_combat_strategy_id` | duel strategies, and a Bot Combat Strategy's own duels |
| a fight's or the default's duel strategy id | Bot Combat Strategies |
| `bot_id` | Bot Combat Strategies |

A Bot Combat Strategy that comes in as a copy also gives its own strategies new ids, as
`combat_profile.duplicate_profile` does. The receiver's own items are never rewritten, so
their bots stay on their original. Keep mine leaves the file's links on the receiver's
item of that id.

### Where things go

A bot goes to the user's own folder, in the world folder the file names when the
receiver's library has one of that name, and at the top otherwise; Update and Replace
write over the receiver's file. A deck goes to the library; loading it into a slot uses
the existing controls for taking only the cards the receiver owns. An imported Bot
Combat Strategy is added to its bot's, and becomes the **active pick only when the bot has
none** (`share_import.assign_active_picks`), so an import never changes how an existing
bot fights. Nothing is ever written over an Official item, and nothing a file says makes
anything Official.

### The summary

When nothing needs deciding, it is one line and Import: "Import 'Boss Farmer', with 2
subgraphs and a Bot Combat Strategy?". Changes to Official items are named under it,
since they come in as copies. An update or a conflict opens the rows that need a choice,
defaults set, with every other item listed under *Also in the file*; an update or
conflict row names the receiver's bots it would change ("Used by …"). Changing a choice
re-matches the file. A file with nothing new says "You already have 'Boss Farmer' and
everything that comes with it." and writes nothing.

Afterward the library selects the main item: a bot or subgraph in the bot library (the
library dialog opens on it while a document is open), a Bot Combat Strategy's bot, and a
duel strategy, player strategy or deck in the strategy library. An import that would
replace the open bot reopens it after, and refuses while it has unsaved changes; an open
definition being replaced is closed first. A write that fails part way (the disk, or
another product changing a shared library file since the summary was shown) stops there
and says so; what was written before it stays.

## Known limits

- A deck whose owner pinned `apply_archmastery`, or that still carries legacy `tc`
  cards, hashes differently in a file than at home. A later re-send of it is matched on
  import as a separate edit, not as a newer copy, so it imports as a copy: the safe
  outcome.
- An item whose links were rewritten to a copy hashes differently from what the sender
  sent, so a later version of it from the same sender is matched as a conflict rather
  than an update, and arrives as a copy again. Safe, and only after a first conflict.
- Exporting several items at once, and a text form to paste into chat, are ideas for
  after 1.0.0.
