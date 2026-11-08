# Quicksell and keep lists

Quicksell sells your drops when a backpack fills, so a long run doesn't stop for lack of space. This page says what it sells and when, and the three ways to keep what you want: the game's item lock, the bot's keep list, and your own Always keep list.

!!! warning "Lock what you value before you turn quicksell on"

    Quicksell sells every drop your settings don't keep, Crowns items included, and a sale can't be undone. Lock the items you want to keep in the game first. Turn on **Backpack Item Lock** in the game's **Options › Gameplay Options 2**, then lock each item. Quicksell never sells a locked item.

## Turn quicksell on

Quicksell starts off. To turn it on for every bot, open **File › Settings…**, select **Bot**, and check **Quicksell**. To turn it on for one bot, open the bot's **Options** tab and switch on the **Quicksell** card's header.

When quicksell is off, nothing is sold. A full backpack overflows into the character's bank, and once the bank is full too, only Crowns items are kept.

## What quicksell sells, and when

A bot sells at the step its author placed for it. At that step, BartleBot checks each player's backpack and jewel pouch. If either is full, it opens the game's quicksell window, selects everything, unselects what your settings keep, and sells. A player whose backpack has room sells nothing.

These are never sold, since the game's quicksell window doesn't list them: reagents, snacks, spellements, mounts and treasure cards.

## Choose what to keep

BartleBot keeps an item when any of these keep it:

- **The game's item lock**, above.
- **The sell switches**: turn off **Sell Pets**, **Sell Socket Wrenches** or **Sell Skeleton Keys** to keep every item of that kind.
- **The bot's keep list**, in the **Quicksell** card of the bot's **Options** tab.
- **Your Always keep list**, in Settings › Bot, which every bot keeps.

### The bot's keep list

The **Quicksell Keep List** row shows the drops the bot can find, each with a tick box:

- **Check an item** to keep that item and nothing else.
- **Type a keyword** in the **Add a keyword** field to keep every item whose name contains it. "giver" keeps every Giver jewel.
- **Search**, the category filter and **Kept only** narrow the list. **Check all shown** and **Uncheck all shown** change every row the filters show.

Most Official bots come with a keep list their author picked. Once you change it, your list replaces the author's for that bot. The revert button puts the author's back.

### Your Always keep list

**Always keep**, in Settings › Bot, is for what you never want sold, in any bot. Separate entries with commas. Any item whose name contains an entry is kept, except pets. A bot's keep list shows the items your list keeps as checked and locked.

### Pets

A keep list can't keep a pet. The quicksell window names a pet by the name its owner gave it, which matches nothing on a list. To keep pets, turn off **Sell Pets**, which leaves every pet in the backpack.

## The quicksell warning before a run

Before a run that sells, the **Safety Check** explains what quicksell will sell, and lists each switch and the keep list with where its value comes from. Check **Don't show this quicksell explanation again** under it to stop the explanation. **Quicksell reminder** in Settings › Bot turns it back on.

One question stays even then. When a bot's author has it sell something your settings keep, the **Safety Check** names it, once for that bot. To keep those items, turn the switch it names off in the bot's **Options** tab.

## When a sale fails

The step fails when the quicksell window doesn't open or the sale doesn't finish, and the bot takes its failure path. [Troubleshooting](../../troubleshooting.md#quicksell-didnt-sell) has what to try.
