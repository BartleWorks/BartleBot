# Sharing

A bot, a subgraph or a combat strategy goes to someone else as one share file, with everything it needs inside. This page covers what a file carries, exporting one, and importing one, including what happens when you already have a version of what's in it.

## What you can share { #what-you-can-share }

| What | The file ends in | What goes in with it |
|---|---|---|
| a bot | `.bbot` | the Custom subgraphs it uses, and those they use, and your Bot Combat Strategy for it, unless you leave it out |
| a Bot Combat Strategy | `.bbcombat` | the duel and player strategies it uses, its decks unless you leave them out, and its bot |
| a subgraph | `.bbsubgraph` | the Custom subgraphs it uses |
| a duel strategy | `.bbduel` | the Custom player strategies it uses |
| a player strategy | `.bbplayer` | nothing else |
| a deck | `.bbdeck` | nothing else |

**Official items are never sent.** Whoever opens the file has them from their own BartleBot, so the file only names them. A changed Official item, marked **Official (modified)**, is sent, and comes in as a copy of its own.

A bot made from a template keeps its link to the template. Someone who has that template gets its setup steps and updates. Someone who doesn't still has a bot that runs. Templates themselves can't be shared yet.

### What never goes in a file { #what-never-travels }

- **Your settings**: option values, which Bot Combat Strategy is active, favorites. Whoever imports a bot sets their own.
- **Anything that runs as a program.** A file holds data: the nodes' parameters, strategies and decks.

## Export { #export }

Export sends what's saved. Each item has **Export…** on its own menu:

| To share | Select **Export…** |
|---|---|
| a bot | on its card's right-click menu in the library, or **File › Export…** with the bot open |
| a subgraph | on its card's right-click menu in the library's **Subgraphs** folder, or **File › Export…** with it open |
| a Bot Combat Strategy | on the menu of the **Combat** tab's banner |
| a duel strategy, player strategy or deck | on its card's right-click menu in **File › Browse Combat Strategy Library…** |

**File › Export…** offers to save unsaved changes first. The summary then lists everything under three headings: "Sent in the file", "Official, so not sent: they have it already" and "Not in your library, so not sent". Each line shows the item's name and description, since those go to someone else. When the bot has a Bot Combat Strategy of yours, its export has two checkboxes, both on: "Include my Bot Combat Strategy", and "Include its decks".

Select **Save…** and choose where. BartleBot starts in Documents, names the file after the item, and adds the ending.

You can export an Official bot. Only what you add to it travels, such as your Bot Combat Strategy for it. When nothing in an export is yours to send, **Save…** is off, and the summary says why.

## Import { #import }

Three ways in:

- **File › Import…**.
- Drop a share file anywhere on BartleBot's window.
- Double-click a share file in Windows, once BartleBot is installed. BartleBot opens if it isn't running, and imports it once its window is up.

BartleBot doesn't import while a bot runs. Nothing is written before you select **Import** in the summary.

When nothing needs choosing, the summary is one line, such as "Import 'Boss Farmer', with 2 subgraphs and a Bot Combat Strategy?". When you already have an item in the file, BartleBot compares the two, and the item's row says what it found:

| The row says | What happens | Your choices, the first chosen for you |
|---|---|---|
| "New to you" | it's added | **Add** |
| "Already in your library" | nothing: it's the same | none |
| "A newer version of yours" | the sender changed your version | **Update mine**, **Import as copy**, **Keep mine** |
| "Yours is newer, so yours is kept" | yours is a later version of theirs | none |
| "Different from yours" | you both changed it | **Import as copy**, **Replace mine**, **Keep mine** |
| "A change to an Official one, so it comes in as a copy" | it's added as a copy, and your Official one stays | none |

The rows that need a choice are under "Needs your choice", and the rest under "Also in the file". An update or a replace names the bots of yours it would change. A file with nothing new for you says so, and writes nothing.

### Import as copy { #import-as-copy }

A copy is a separate item with a new identity, and a numbered name when its own is taken, such as "Loop (2)". Everything else in the file that uses the item is pointed at the copy, and your own bots stay on yours.

Importing the same file again finds the copy it made the first time and writes nothing. When the sender sends a later version, it updates that copy, so there's never a second one.

### Where an import goes { #where-an-import-goes }

- **A bot** goes in your own bots, in the same world folder as the sender's when your library has one.
- **A Bot Combat Strategy** joins its bot's. It becomes the one the bot fights with only when the bot has none, so an import never changes how a bot of yours fights.
- **Strategies and decks** go to the combat strategy library.

Nothing Official is ever changed by an import, and nothing in a file can make an item Official. Afterward, the library shows what you imported. An import that would replace the open bot is refused while it has unsaved changes, and opens it again after.

An import that can't be done is refused before anything is written, with the reason. A file from a newer BartleBot says "Made with a newer BartleBot", with its version, and to update first ([Troubleshooting](../../troubleshooting.md#made-with-a-newer-bartlebot)). A file naming a PvP zone is refused too ([PvP](../running/pvp.md)).
