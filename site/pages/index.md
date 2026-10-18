---
template: home.html
tagline: A Wizard101 botting and automation tool
hide:
  - navigation
  - toc
---

## Why BartleBot?

- **Robust failure recovery** Bots reliably run overnight, even through failed zone loading screens, internet disconnects, and game crashes.
- **Combat system overhaul** New features and improvements to combat behavior, configurable per-bot combat profiles that include duel and player-specific strategies, and saving/loading decks.
- **Code-free state machine editor** Create bots by recording in-game actions instead of writing code, while retaining the ability to perform advanced behaviors via configurable options and control flow features.
- **Extensive reuse** Use libraries of common flows and actions, automatic updates, and other features from modern architectures.
- **Quality-of-life features** Quicksell handling, auto-grouping, and more!

<div class="bb-feature bb-feature--wide" markdown>
<div markdown>

## Build bots by drawing them

Create bots on the node-based canvas by choosing steps from a library of actions. Every step has a success path and a failure path, so
you can see, and choose, what happens when something goes wrong.

</div>

![A bot on the canvas: a chain of steps, each with a success and a failure edge, looping back to the start](images/home/canvas.webp){ .bb-shot }

</div>

<div class="bb-feature" markdown>
<div markdown>

## Start from a template, not a blank canvas

Skip the setup and go straight to the important part with BartleBot templates. Templates handle the skeleton of a bot for a variety of common use cases, so all you need to do to create a new bot is record the unique steps. Official templates are tested in game, and when one improves, any bots you've created from the template can opt in and receive the updates too.

</div>

![The New Bot window: a blank bot, or one of the Official templates](images/home/new-bot.webp){ .bb-shot }

</div>

<div class="bb-feature bb-feature--flip" markdown>
<div markdown>

## Combat that fits the fight

Decide who casts what, and in what order, for any fight. The defaults cover solo and team fights, as well as common cheating boss scenarios. Change any of them and BartleBot keeps your edits in your own copy.

</div>

![The Combat Strategies window: a list of duel strategies, and the selected one's combat order and player strategies](images/home/duel-strategies.webp){ .bb-shot }

</div>

<div class="bb-feature" markdown>
<div markdown>

## Sell the junk, keep the good stuff

Quicksell clears out the drops you don't want, so you never run out of backpack and bank space. Each bot lists its possible drops, so you can pick the drops you want to keep at the click of a button and sell the rest.

</div>

![Quicksell's options: what to sell, and the keep list of every drop the bot can find](images/home/quicksell.webp){ .bb-shot .bb-shot--narrow }

</div>

!!! info "BartleBot is currently in public beta"

    If something goes wrong, please [open an issue](https://github.com/BartleWorks/BartleBot/issues) with as much information as possible, including any log files from the session where the issue occurred.
