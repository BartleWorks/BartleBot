# Writing user-facing text

How to write every string a user reads in BartleBot: titles, descriptions, buttons,
headings, tooltips, placeholders, messages, status lines, setup's pages and the node
help. This does not cover log lines, which have their own rules in the root `CLAUDE.md`,
or developer docs.

The rules come from comparing BartleBot with VS Code, IntelliJ IDEA, Unreal Engine, Unity
and Adobe Premiere Pro / After Effects, for each kind of surface:

| our surface | compared with |
|---|---|
| Settings, bot options | VS Code's Settings editor, IntelliJ's Settings, Unity's Preferences, Unreal's Editor Preferences, Premiere's Preferences |
| Setup | Windows' wizard guidelines, Adobe's onboarding writing guide, VS Code's walkthroughs |
| Node help | Unreal's Blueprint node reference, Unity's Visual Scripting node reference, IntelliJ's inspection descriptions, After Effects' effect reference |

What they share everywhere:

- The label carries most of the meaning.
- Text follows a fixed form, so a reader can skim it.
- The reasons and the inner workings go in a manual.
- The voice is neutral and plain.

Each surface also has its own rules, because a different reader comes to it for a
different reason. Settings is scanned by someone looking for one switch. Setup is read
once, by someone who has never seen the app. Node help is read by a bot author who wants
to know exactly what a node does.

The general rules (T, S, D, V, W, C, M and X) apply everywhere, except where a surface's
own section says otherwise: F for setup, N for node help. Rules are numbered so a review
can cite them, for example "breaks D2".

## The three layers

A setting or a bot option has up to three pieces of text, and each one holds only its own
kind of fact.

| layer | says | length |
|---|---|---|
| **Title** | what the thing is | a few words |
| **Summary** (the line under the title) | what it does | one line, about 70 characters |
| **Details** (behind the `?`) | what the user can't predict but needs in order to choose | two sentences at most, about 35 words |

The reasons behind a design, the history, how it works inside and the edge cases go in
the user guide, not in the app. The `site/` docs are where that guide will live. Until
it exists, they stay in the developer docs, and the UI leaves them out.

A node's help page is the one place in the app that works like a manual, so it has its
own layers. See [Node help](#node-help).

### Titles

- **T1. Name the thing, in sentence case.** Examples: "Relog interval", "Game folder",
  "Default window size".
- **T2. A check box's title describes the state when it is checked.** Word it as the
  behavior ("Snap nodes to grid", "Maximize on open") or as the feature's own name
  ("Quicksell", "Auto-save"), never as "Enable X". The box already says on or off.
- **T3. One name per thing, everywhere.** A row title, the menu item that opens the same
  thing and a summary that mentions it all use the same words with the same
  capitalization.

### Summaries

- **S1. One sentence that fits on one line, ending with a period.**
- **S2. Use the form that matches the control:**

  | control | form | example |
  |---|---|---|
  | check box | an imperative verb, describing what happens while it is on | "Open the main window maximized." |
  | number, choice or text | a "How…" / "Which…" / "Who…" / "Where…" clause | "How long the camera takes to glide to a node." |
  | action button | an imperative verb, describing what the button does | "Copy card and school pictures from the game's files." |
  | state line | the current state, as a statement | "Found in a Steam library." |

- **S3. Describe the setting, not its surroundings.** A sidebar width's summary is about
  the width, not about what the sidebar contains.

### Details

- **D1. Include only what changes the user's choice:**
  - a cost: time, gold, disk space
  - when the change takes effect: the next launch, a restart, the next run
  - what overrides this setting, or what it overrides
  - a requirement: the friends list, a running game
  - what turning it off costs, when it costs something
- **D2. Never include:**
  - a defense of the design ("nothing writes over your bot behind your back")
  - history or measurements ("went from 23 to 32 seconds")
  - how it works inside ("pauses at a node boundary", the order of the folders searched)
  - anything that only concerns developer mode
- **D3. Leave details empty when the summary says it all.** An empty `?` is fine. One
  that repeats the summary is noise.
- **D4. Never put a fact the user needs only in a tooltip.** Tooltips are for controls
  that have no visible label.

## Voice

- **V1. Neutral and instructive.** Use the present tense and the active voice, with one
  idea per sentence. Describe what happens.
- **V2. Write in the positive.** Describe the On state. Mention Off only when D1 allows it.
- **V3. Don't use contrast for effect.** Avoid "X, never Y", "not A but B", "rather
  than", "either way", "whatever this says" and "the only way". State the fact once.
  Use "never" only for a guarantee the user would otherwise doubt, and that matters to
  them: safety, money, their data.
- **V4. Don't reassure or defend.** Don't answer a question the user didn't ask. If
  something is private or safe, state it once, plainly, where the user would look for it.
- **V5. Address the user as "you", sparingly, and never use "we".** BartleBot names
  itself only where "it" could mean the game. Setup speaks to the user more directly (F3).
- **V6. Use contractions:** "can't", "doesn't", "isn't". This is the Microsoft style the
  rest of Windows uses.
- **V7. Don't use em-dash asides or semicolons.** Split them into two sentences.
  Parentheses are for units and values only.
- **V8. Give every sentence a subject, and every "it" a clear noun.** Details are read
  apart from the title, so "it" at the start of them has nothing to point at. Name the
  thing: "the delay", "the minimap", "auto-dialogue". Describe a check box's off state
  as "When off,", matching the "Turn on" and "Turn off" used elsewhere. Avoid "Without
  it," and "With this off,".

## Words

- **W1. Use only words the user meets in the UI.** Avoid:
  - code: node keys (`teleport_to_friend`), param keys (`reset_xyz`), setting keys,
    constants, file paths, library names (SprintyCombat, Deimos)
  - "binding", "node boundary", "shipped", "this build"
  - "DSL", except on the surfaces where the user writes DSL

  Node help can use more terms than this, because its reader is a bot author (N1).
- **W2. Use game words the way the game uses them,** and pick one spelling. A common
  noun stays lowercase: "friends list", "backpack", "bank". The name of a game feature
  or place keeps its capitals: "Remember Realm", "the Commons". Get the game's facts
  right as well as its words: a character's bank and the shared bank are different
  things. Players teleport, never "port" or "TP".
- **W3. Use the capitals our own concepts have:** Duel Strategy, Player Strategy, Bot
  Combat Strategy, Safety Delay, Edit mode, Official. A feature name the user only reads
  in a row title is a common noun in running text: quicksell, auto-save, streamer mode,
  speed-authoring.
- **W4. Name another surface by its path, with `›`:** "Settings › Bot", "Help › Check for
  Updates…". Never use `→`. Never point to the surface the text is already on. "Above"
  and "below" are fine only within one card.
- **W5. Name another product only once the user can have it.** BartleBuddy is unreleased,
  so text that names it waits until it ships. The same goes for a node that no longer
  exists.
- **W6. Use numbers only when they are a cost or a range to choose from:** "about 35 MB",
  "3 to 5 seconds". Never use a number from one anecdote.

## Capitalization and punctuation

- **C1. Use title case only for window and dialog titles and menu items.** Everything
  else is in sentence case: tab names, section headings, row titles, check boxes,
  buttons, links, column headings, list items, placeholders, tooltips and messages.
  Most of the app's buttons already follow this, and so does Windows itself. Node names
  and param labels are the exception (N3).
- **C2. End a full sentence with a period.** Titles, headings, buttons, placeholders and
  fragment tooltips get none.
- **C3. End a button or menu item with "…" when it opens something that asks for more
  before acting:** Change…, Import…, Browse….
- **C4. End a form label with a colon.** Examples: "Username:", "Resolution:".
- **C5. Quote only literal text,** meaning what the user types or what the app matches:
  "Socket Wrench". UI names are written as they appear, without quotes.
- **C6. Write "and", not "&", in labels.**

## Messages

- **M1. A message dialog's title is the operation that was under way.** Use the
  dialog it came from ("Add Wizard101 Account") or the verb and object ("Delete
  Account"), never a bare adjective like "Invalid" or "Duplicate".
- **M2. The body says what happened, then what to do,** in that order. "Enter a username."
  beats "Username cannot be empty."
- **M3. A confirmation's buttons are the verb and Cancel,** "Delete" / "Cancel", never
  Yes / No. The body names what is lost.
- **M4. An error never blames the user and never just apologizes.**

## Text shared between surfaces

- **X1. Text built once for two surfaces must read correctly on both.** The rows in
  `players_rows.py`, `realm_delay_rows.py`, `quicksell_group.py` and `game_art_row.py`
  also appear on setup's pages, and the Safety Delay text is also a bot option's
  description. A sentence pointing at "Settings › General › Game art" is wrong when it
  appears there.

## Setup

Setup is a wizard, read once, by someone who hasn't used the app yet. Windows' wizard
guidelines and Adobe's onboarding guide agree on what that reader needs:
- one decision per page
- a heading that says what the decision is, in the user's words
- just enough text to choose well
- no welcome or congratulations pages
- nothing about how the app works inside

- **F1. The heading is the page's one question, in the user's words.** A choice page asks
  it ("How should your team fight bosses?"). A page where the user enters something says
  what to do ("Add your Wizard101 accounts"). Never use "Configure X settings" or
  "Select X mode": those name the app's feature, not the user's decision. Sentence case,
  with no period. A question keeps its question mark.
- **F2. The intro is at most two sentences:** what the answer changes for the user, then
  anything they must know to answer. A third sentence may say where to change it later
  (F4). Text that belongs to one control goes on that control, not in the intro.
- **F3. Talk to the user.** Use "you" and "your" freely, as Windows' wizards do. Never
  use "we" or "I". Lead with what the user gets, not with what BartleBot does.
- **F4. Say where to change an answer later, once per page, by its path:** "Change this
  later in Settings › Bot." Don't also say that it *can* be changed.
- **F5. Leave out what the app does by itself.** Don't describe how detection works or
  where files go unless the user has to act on it.
- **F6. State what skipping costs, once, where it applies:** "Without an account, bots
  can't relaunch the game after a crash." Say it in the control's own text, not in the
  intro and the control both.
- **F7. A progress page's heading is a verb phrase ending in "…":** "Extracting game
  art…". Its text says what the user can do while they wait, and nothing about the
  steps.
- **F8. Name a button only when what it does is invisible:** "Next makes the profile
  “Team 1”." That way the user knows Next does more than turn the page.
- **F9. A Settings row shown in setup keeps its Settings text (X1).** The intro must not
  repeat what the rows already say.

## Node help

The node browser's help page, the param panel's tooltips and hints, and the return
descriptions. Its reader is a bot author who wants to know exactly what a node does and
what will make it fail. That reader tolerates, and needs, more detail than a settings
user, like the readers of Unreal's Blueprint reference or IntelliJ's inspection
descriptions. So the details layer gets longer here, and moves to the usage notes.

| part | says | length |
|---|---|---|
| **Name** | what the node is | a few words |
| **Description** | what it does | one or two sentences |
| **Usage notes** | when to use it, what must be true first, what goes wrong and how to tell | a few short paragraphs, one topic each |
| **Param label** | what the param is | a few words |
| **Param help** | what the param controls, and when to change it | up to three sentences |
| **Param hint** | what happens when the param is left at its default | a fragment |
| **Return** | when that port fires | a sentence or a list of causes |
| **Safety warning** | what the bot will do that could cost the user something, then what to do | two or three sentences |

- **N1. Write for the bot author.** Nodes, ports, edges, zones, XYZ, the Entity Browser,
  the UI Tree Browser, recovery states and Record are all fine: the author meets them in
  the editor. Code isn't, and neither is anything the author can't see (W1).
- **N2. Name params by their labels and nodes by their names,** as the canvas shows them:
  "Reset XYZ", "Interact Menu", never `reset_xyz` or `interact_menu`.
- **N3. Node names and param labels are title case, like Unreal's node and pin names,**
  with short words lowercase: "Teleport to World Hub", "Enter Zone via Object". Ports
  are capitalized as the canvas shows them: SUCCESS, FAILURE, TRUE, FALSE. A unit
  in a label is "(s)", never "(seconds, 0 = no limit)". A special value goes in the
  help.
- **N4. The description starts with a present-tense verb and no subject,** as
  IntelliJ's inspections start with "Reports": "Teleports every player to…". Never
  "This node…". It says what the node does. How it does it goes in the usage notes, if
  the author needs it.
- **N5. Each usage note opens with its point.** Write "Place it before entering the
  dungeon.", then the reason. Say how the node works inside only when it changes what
  the author does.
- **N6. Param help uses the summary forms (S2).** A value says what it is ("The zone every
  player must be in when the node starts."). A bool says what On does ("When on, …"),
  and what Off does only when that isn't simply the opposite. Then say when to change
  it. The default goes in the hint, not the help.
- **N7. A hint is a lowercase fragment with no period:** "every player", "no text
  filter", "10 seconds".
- **N8. A return says when its port fires.** FAILURE lists its causes, most common
  first, separated by commas. Say "Always" for a port that always fires.
- **N9. Shared params and notes have one text,** in `bartlebot/nodes/help_text.py`. Write
  it once and use it everywhere, so the same thing never reads differently on two nodes. Examples: Zone, From Zone,
  To Zone, Reset XYZ, Interact XYZ, Sequential Interaction, Delay Between Object Uses,
  Animation Delay, and the note on players who don't arrive. The same goes for an
  option's description, which every node declaring it shows.
- **N10. Never write history,** such as "which is what this node has always done".
  Never mention a node that no longer exists.
- **N11. A safety warning is read before a run, by whoever runs the bot, not by its
  author.** Write it like a setting's details (D1): the risk, then what to do about it.
  Use "you" for that reader.

## Checklist for a new string

1. Which layer is it, and does it hold only that layer's kind of fact? (T, S, D, N)
2. Does a summary or param help use its control's form? (S2, N6)
3. Could the reader act on every sentence? If not, cut it. (D1, D2, F5)
4. Is there any "never", "not … but", "rather than" or "either way"? (V3)
5. Would every word be in the UI, or in the game, for this surface's reader? (W1, N1)
6. Is it sentence case, unless it's a title, a menu item, a node name or a param label?
   (C1, N3)
7. Is it shown anywhere else, and does it read right there? (X1, N9)
