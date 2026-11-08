@../../bartleby-core/docs/UI_TEXT.md

# The site

`site/` is BartleBot's public site, `bartleworks.github.io/BartleBot`: the download page
and the manual. Zensical builds it from `zensical.toml`, and `.github/workflows/site.yml`
publishes it from `master` (`docs/RELEASING.md`, "The download site"). This file is how a
page is written. The voice is core's `docs/UI_TEXT.md`, imported above: its V, W and C
rules hold on every page, and F and N where a page speaks of setup or a node. The design
behind the site's shape is epic #25 (`docs/EPICS.md`, *User documentation*), and the rule
that a branch updates the pages it changes is the root `CLAUDE.md`'s.

## What goes on the site

| a fact | goes |
|---|---|
| what a user needs in order to choose, at the moment of choosing | in the app: a title, a summary or details, `UI_TEXT.md`'s three layers |
| how to do a task, what happens, why the app behaves as it does, and what to try when it doesn't | on the site |
| how it works inside, what must not break, and why the code is the way it is | in `docs/` |

A page never stands in for a missing summary or a control that confuses: fix the control.
The app's own text stays complete, and works offline.

**A page is written with the app open.** Write against `master` at the branch point, from
the developer doc and the app together. A doc says what the code should do; the app is
what the user sees. The As built or the merge message names which flows were walked in the
live app and which were read from code only.

**The site describes the latest release.** There is one version, since the app updates
itself. A page that describes a change not yet released says so in an `info` admonition
titled "Coming in the next release", removed by the release that ships it.

## The four kinds of page

| kind | is | where |
|---|---|---|
| Getting started | the one lesson: install to a first finished run, in the order a user meets each step | `pages/getting-started.md` |
| Guides | one task per page, read with the app open beside it | `pages/guides/<group>/<page>.md`: `running/` (running bots), `making/` (making bots), `combat/` |
| Reference | facts: one entry per node, bot option and Official subgraph | `pages/reference/`, written by a script and never by hand (*Generated pages*) |
| Troubleshooting | starts from a symptom | `pages/troubleshooting.md`, one `##` per symptom |

Besides those, **Home** (`pages/index.md`, the landing page, drawn by
`overrides/home.html`) and **Installing** (`pages/install.md`). `install.md` is also every
release's notes, which GitHub draws, so it stays plain markdown: no admonitions, no
attribute lists, no HTML, and it keeps its name (`tests/test_site.py`).

Background ("why does the bot do that?") goes in the guide it explains, in an `info`
admonition, not on a page of its own. A page that mixes a lesson, a task, facts and
background serves none of its readers.

**Adding a page** puts it in `zensical.toml`'s `nav` in the same branch: a page missing
from the nav fails `tests/test_site.py`. A group of guides is a section nested under
Guides, after `guides/index.md`:

```toml
{ "Guides" = [
  "guides/index.md",
  { "Running bots" = [
    "guides/running/running-a-bot.md",
  ] },
] },
```

A section's `index.md` lists each of its pages with a line on what it is for. Its "on its
way" admonition goes once the section holds a page.

**Troubleshooting** grows with each guide: a guide that tells the user to look something
up adds the symptom there. Each entry names the symptom as the user sees it, the steps to
try, and when to open an issue. Its last two sections, *Where the logs are* and *Opening an
issue*, stay last.

## Writing a page

- **The title** (`#`) is a noun phrase or a task, in sentence case: "Getting started",
  "Running a bot", "Quicksell and keep lists". The nav's label says the same words.
- **A heading** (`##`) names a task the reader does ("Run the bot", "Pause or stop"), or on
  Troubleshooting a symptom as they see it, quoting the app's message where it has one
  ("BartleBot can't press every action this run needs"). Never a feature's name alone
  ("Quicksell settings").
- **The page opens** with a sentence or two on what it gets the reader. No welcome, and no
  "In this guide we will".
- **Steps taken in order** are a numbered list, one action per step. Choices are a
  bulleted list or a table.
- **The app's names are bold, exactly as the app shows them**, capitals included:
  **Run bot**, **Options**, **File › Set up BartleBot…**. A path uses `›` (W4). A key is
  plain text: F5, Shift+F5. A message is quoted in quotation marks, as it reads.
- **No code** (W1): no node types, param or setting keys, modules, or paths inside the
  install. A path the user types or opens goes in a code span, `%APPDATA%\Bartleby\BartleBot\logs`,
  and log text in a `text` code block.
- **"You", never "we"** (V5), contractions (V6), and no em-dash asides or semicolons (V7).
  A colon may introduce a list or an explanation.
- **Players are Player 1, Player 2** (W7). P1 appears only in a quoted log line.
- **Game words as the game says them** (W2), and our concepts with their capitals (W3):
  Official, Safety Delay, Duel Strategy, World Gate.
- **Numbers only as a cost or a range** (W6): "about 35 MB", "3 to 5 seconds".
- **The app's words may be paraphrased to fit the page, but its facts never differ.**
  Where a page quotes the app's exact words, a test reads them from the code, as
  `test_getting_started_quotes_every_setup_page_as_the_app_shows_it` does for setup.

### Admonitions

Two kinds, always with a title in sentence case:

- `!!! info "…"`: background, the reason behind a behavior, a section still on its way,
  and a change not yet released.
- `!!! warning "…"`: what can cost the user items, gold or their account, and what to do
  first, as Getting started's "Before you turn quicksell on".

No `note`, `tip`, `danger` or folding ones. Two kinds read as a signal; seven read as
decoration.

### Links

- **Another page** is linked by its relative path to the `.md` file, with an anchor where
  it helps: `[Troubleshooting](troubleshooting.md#where-the-logs-are)`. The strict build
  fails a link to a page or an anchor that is not there.
- **An anchor** is the heading's text, lowercased, with punctuation dropped and spaces as
  hyphens: "Where the logs are" is `#where-the-logs-are`. A heading that other pages or the
  app link to keeps its anchor when its words change: give it an explicit id,
  `## Where the logs are { #where-the-logs-are }`.
- **A node, a bot option or an Official subgraph** is linked to its reference entry the
  first time a page names it, and is plain text after that. A node is on its node browser
  group's page, and its anchor is its type with hyphens:
  `[Collect Wisps](../../reference/combat-and-healing.md#collect-wisps)` from a guide. An
  option's anchor is its name, lowercased with hyphens, on `reference/options.md`
  (`#safety-delay`, `#team-up`), and an Official subgraph's is its name on
  `reference/subgraphs.md` (`#sync-players`).
- **Never into `docs/`, or to a source file on GitHub** (`/blob/`, `/tree/`, `/raw/`): a
  user reads the site, not the code. An issue, a release or the new-issue page is fine.
  `tests/test_site.py` fails either.
- **Outside the site**, link only where the step happens there: Windows' settings, the
  game's own pages.

### Screenshots on the site

- **Only where words can't say it**: the canvas, a dialog's layout, a game or Windows
  prompt. A menu path or a button is named in bold instead. Each one goes stale with the
  UI, so few is the aim.
- **WebP**, under `pages/images/<page>/`, named for what it shows:
  `images/getting-started/quick-launch.webp`. A page in a subfolder links it relatively,
  `../../images/…`. Install's PNGs predate the rule.
- **The dark theme**, at 100% display scale, cropped to what the step needs.
- **No player data**: no wizard, account or friend names, no chat, no character ids. Take
  it on a test account, with Settings › General › Streamer mode on, and crop out what that
  doesn't hide.
- **No game art as a file**, under the root `CLAUDE.md`'s "Game art is extracted, never
  shipped": a screenshot may show the game in its window, but never a card picture, icon
  or texture saved on its own.
- **Alt text** says what the picture shows, as the landing page's do.
- **A UI change that makes one wrong retakes it** in the same branch. Every image is shown
  by some page, and every image a page shows exists (`tests/test_site.py`).

## Generated pages

A page written by a script says so in its front matter, `generated: true`, with a comment
naming the script. `overrides/main.html` draws it without the edit link, since an edit
there is lost at the next render. Its text is fixed where it comes from and the script is
rerun. Only pages under `reference/` carry the mark (`tests/test_site.py`).

**The Reference is every page under `reference/`**, written by
`uv run python tools/build_site_reference.py` from `bartlebot/nodes/reference.py`: the
index, a page per node browser group in `NODE_CATEGORIES`' order, Bot options and
Subgraphs. Its words are the app's own, so a fix goes where the app reads it:

| what reads wrong | fix it in |
|---|---|
| a node's description, params, returns, usage notes, safety warning or see also | its `NodeSpec`, under core's `UI_TEXT.md` N rules |
| an option's description or default | the node's `BotOptionSpec`, or the shared text in `bot_options.py` |
| which Settings row an option names | `SETTING_ROWS` in `bartlebot/nodes/reference.py` |
| a subgraph's name, description, inputs or ports | the definition under `subgraphs/` |
| a page's own framing (openers, the four levels, labels) | `bartlebot/nodes/reference.py` |

Then rerun the tool and commit its pages in the same branch.
`test_the_committed_reference_is_current` fails until then, and names the command. The
committed output is what the site builds, since `site.yml` runs on Ubuntu with the `site`
group alone, and the node specs import wizwalker, which runs only on Windows. A new page
the tool writes goes in `zensical.toml`'s Reference section, in the order the tool gives
(`test_the_nav_lists_the_reference_pages_in_the_order_they_are_rendered`). The reference
adds no text of its own per node: a node that needs a sentence more gets it in its spec,
and the node browser shows it too.

## What never goes on the site

- Player data, in text or a picture.
- Game art as a file.
- Anything a visit would fetch from someone other than GitHub: web fonts (`font = false`),
  analytics, third-party scripts or images.
- Developer mode and authoring Official content (`BARTLEBOT_DEV`), which no user does.
- BartleBuddy, until a user can have it (W5).

## Checking a page

```powershell
uv run --group site zensical serve -f site/zensical.toml                # preview at localhost:8000
uv run --group site zensical build --strict --clean -f site/zensical.toml   # what site.yml runs
uv run pytest tests/test_site.py
```

Look at a new or changed page in the preview, in both themes (the toggle by the search
box), and at phone width. `tests/test_site.py` checks what a strict build doesn't: every
page in the nav and every nav entry a page, no link into the developer docs, every image
used and present, the edit link, the install page and the download button.
