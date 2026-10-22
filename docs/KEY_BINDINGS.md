# Key bindings

Since r811339 (the Ahmarra Test realm) a player can rebind the game's keys, and BartleBot
presses keys: Interact, the Backpack, the Deck and five more. A node that presses X for
Interact on an account where Interact is K does nothing, or something else, silently. So
BartleBot presses an *action*, and finds the key for it in that client's own bindings.

## Where a client's bindings are

**With the account, not the install.** One install serves every account, each with its own
keys. The game keeps them server-side and writes `<install>/Data/GameData/LocalInputBindings.xml`
when a player saves, so that file holds one account's bindings at most, often not the one a
given client is on: a second client logging in left it holding the first account's. The
shipped defaults sit beside it, in `InputBindings.xml`, and live has had those since June
2026, in an older form with no `m_slot`.

**In each client's memory, one list.** The game holds the logged-in account's bindings as one
`InputBindingList`, whose `m_inputs` (0x48) is a list of `InputBinding`: `m_action` (0x48),
`m_input` (0x68), `m_slot` (0x94), on r811339. Two clients on two accounts each held their own,
and a rebind saved in game changed the list in place, the same address, at once.
`tools/probe_binding_memory.py` found all of this, and reruns it after a patch.

**Found once, read on every use** (`clients/game_keys.py`). Nothing in the game's image points
at the list, so finding it takes a scan of the process: the `InputBinding` holding
`NPCInteract`, whose name sits inside the object; the list nodes pointing at it; the ring
they are on; the list pointing at the ring's sentinel. About 4 s on a fresh client, nearer 10
once the game's key bindings page has been opened. The address is then kept per window and
every read goes through it, about 10 ms, which is what makes a rebind mid-session visible
without another scan. An address that no longer holds a list is found again.

**The bindings page leaves lists behind.** Opening the game's key bindings page builds an
`InputBindingData` per row, each with a one-binding `InputBindingList` of its own, and they
stay after the page closes, still holding whatever key the row had: a player who moved
Interact to K and back left a list saying K. So the ring chosen is the one with the most
bindings, the player's whole set, and a ring with one is never taken for it.

**A client that cannot be read gets the defaults.** `key_map()` never raises: a client whose
list cannot be found presses the shipped default keys, with one warning, and is not scanned
again until `key_map(refresh=True)`.

## From a key's name to the code that presses it

`m_input` is text: `keyboard.down.ctrl.shift.q`, the modifiers in the order alt, ctrl, shift,
then the key (`game/key_bindings.py`, `parse_input`). The key is named one of two ways:

- **By name**, for a key in the game's table (its strings sit together in
  `Bin/WizardGraphicalClient.exe`, after `keyboard.`): `escape`, `space`, `f5`, `num0`,
  `add`, `tilde`, `question`, `lsqbracket` and so on. `KEY_CODES` gives the code for each.
- **By its own code**, one character, for any other key, a letter lowercased: `x` is VK_X,
  `;` is the byte 0xBA, `-` 0xBD, `'` 0xDE. The numpad has names because its codes,
  0x60-0x6F, would read as a backquote and `a`-`o`. The file is therefore not UTF-8, and is
  read as latin-1. The Delete key is written `.` (0x2E), its code, not the table's `delete`.

Every name marked *in game* in `KEY_CODES` was bound by hand on r811339 and pressed with that
code (`tools/probe_key_bindings.py`). Two findings there need **rechecking on the build that
goes live**, before `ahmarra-test-realm` merges (`docs/GAME_UPDATES.md`, r811339):

- **`comma` and `period` are swapped.** The game names the `.` key `comma` and the `,` key
  `period`, and each name presses the key it was recorded from, so `KEY_CODES` follows the
  game: `comma` is 0xBE. A fix on KingsIsle's side is those two lines.
- **The numpad `.` (0x6E) may land on N's binding.** Sent, it toggled the compass, N's
  action; no numpad was at hand to bind it by hand.

## What a press can use

`KeyMap.choose(action)` takes the action's first slot that a press can use, then its second:

- **A bare keyboard key only.** A key sent as messages to a window never carries a held
  modifier: Ctrl+0 was sent three ways (wizwalker's `send_hotkey`, and `SendMessage` and
  `PostMessage` with real scan codes) and none reached the game. A mouse binding cannot be
  sent either.
- **Never a key the chat line shares.** The game lets one key sit on two actions, and a press
  does both. With `/` on the Housing page as well as its default, Open Chat, one press
  opened both, and the chat line then took every key after it as text. So a key shared with
  `ToggleChatBoxInputSlash` or `ToggleChatBoxInputEnter` is passed over; a key shared with
  anything else is used, and the other action is named (`Choice.shared_with`).

An action with neither slot usable has no key, and `Choice.problem` says why in words.

Space, which advances dialogue, and Escape, which closes a page (`CloseUI`, which the game
does not let a player rebind), are not actions BartleBot looks up: Space advances dialogue
whatever jump is bound to.

## Pressing an action

Nothing presses a game action by its letter: `clients/game_keys.press(client, action, seconds)`
looks the action up in that client's bindings, chooses its key, and sends it with
`client.send_key`, as every node did before. `GameAction` lists every action BartleBot presses:
Interact, Backpack and Open Jewels (quicksell), Character Sheet (reading a wizard's name),
Deck, Housing (the castle list), Friends (closing the friends list) and Move Left (the
anti-idle press while queued for a team-up). `test_no_game_action_is_pressed_by_a_fixed_key`
fails on a `Keycode` anywhere in `bartlebot/` but Space and Escape.

- **`Keycode` stops short of the punctuation keys.** wizwalker's enum has no member for
  0xBA-0xDE (`;` `=` `,` `-` `.` `/` `` ` `` `[` `\` `]` `'`), which a player can bind like any
  other, and `send_key` reads only the key's `.value`; so `keycode()` sends a `VirtualKey`
  carrying the code for those, with no change to wizwalker.
- **wizwalker's `DeckBuilder` presses P itself**, to open, close and refresh the deck page,
  always through `_press_spellbook_key`. `deck_io.deck_builder()` overrides that one method to
  press the player's Deck key; `test_the_deck_builder_presses_the_player_s_deck_key` fails if a
  wizwalker update renames it.
- **No key to press raises `KeyNotPressable`**, naming the action and why, and the node fails.
  The pre-run check refuses a run before that can happen, so it is a rebind made mid-run.

## Before a run

**Every action BartleBot presses is required of every player, whatever the bot**
(`clients/run_probe.py`). The run request reads each client's bindings in the same task as
its other memory reads, with `refresh=True` so a client that failed before is tried again,
and refuses to start, telling rather than asking, when any player has an action in
`GameAction` with no key a press can use: one sentence per action, naming the player and why
("Player 2: Interact is bound only to Ctrl+X, which BartleBot cannot press"). Narrower checks
were weighed and turned down: which actions a bot needs depends on its nodes, the subgraphs
they call and the ambient watchers, which press Interact for potions in any bot, and a list
kept per node goes stale silently, where a refusal the player fixes once does not.

A key shared with an action that is not the chat line is pressed, and logged at WARNING
before the run, since the other action happens too and the player may wonder why it opened.

**The scan is begun as a client arrives.** Finding a client's list takes seconds, so
`ClientManager` starts it, as a task of its own, once the client's hooks are up: by then a
character is in the world, and so are its bindings. A failure there is logged at DEBUG; the
pre-run read repeats it, and warns.
