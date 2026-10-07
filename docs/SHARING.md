# Sharing: share files and Export

A user sends a bot, a Bot Combat Strategy, a subgraph, a duel strategy, a player
strategy or a deck to someone else as **one file that carries its own dependencies**.
Stage 4 of epic #173 (#170) built the format and Export; Import is Stage 5 (#171). The
plan, with the reasoning behind every rule here, is `docs/SHARED_CONTENT.md`'s page.

Code: `bartlebot/shared/share_file.py` (the vendor's format, and the three kinds
BartleBuddy shares), `bartlebot/share_export.py` (BartleBot's kinds, and following each
kind's links), `bartlebot/gui/export_dialog.py` (the export summary).

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

## Known limits

- A deck whose owner pinned `apply_archmastery`, or that still carries legacy `tc`
  cards, hashes differently in a file than at home. A later re-send of it is matched on
  import as a separate edit, not as a newer copy, so it imports as a copy: the safe
  outcome.
- Exporting several items at once, and a text form to paste into chat, are ideas for
  after 1.0.0.
