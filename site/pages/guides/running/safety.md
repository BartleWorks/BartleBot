# Safety

Running a bot carries risks for your account, your items and the players around you. This page covers what automating Wizard101 risks under the game's terms, what Safety Delay is for, and what each warning in the **Safety Check** before a run means.

## Know what a bot risks for your account

!!! warning "Automating the game can cost you your account"

    KingsIsle's Terms of Use and Code of Conduct govern how Wizard101 may be played. A program that plays the game for you, reading and driving the game as BartleBot does, can break them, and KingsIsle can suspend or close an account that does. BartleBot isn't made by or affiliated with KingsIsle Entertainment, and nothing in it hides a bot from the game. Read the Terms of Use, linked at the bottom of [wizard101.com](https://www.wizard101.com), and run bots only on an account you're ready to risk.

## Be fair to other players

- **Run collecting bots on a quiet realm.** A bot that teleports to reagents or items takes them from anyone nearby. When a bot changes realm to find fresh spawns, it picks among the game's quieter realms.
- **Team-Up brings in players who aren't yours.** With [**Team-Up**](../../reference/options.md#team-up) on, a bot joins the game's queue, and strangers fight beside it. Turn it on only for a run you'd be glad to have company on.
- **Stay out of PvP.** BartleBot doesn't automate PvP, and there's no way to turn that off. [PvP](pvp.md) has what happens if a run reaches it.

## Set Safety Delay for your connection

[**Safety Delay**](../../reference/options.md#safety-delay) is how long the game server gets to answer an action before it counts as failed: a prompt, a menu, a zone change or a mark. On a slow connection, actions fail before the game catches up, and the bot takes failure paths it shouldn't.

Raise it on a slow connection: 3 to 5 seconds in North America, 5 to 10 elsewhere. Set it for every bot as **Default Safety Delay**, in Settings › Bot, or for one bot in its **Options** tab. A longer delay makes a run slower.

## Read the Safety Check before a run

When a bot could cost you something, **Safety Check** lists it before the first step. Read each warning, then select **Run Bot** or **Cancel**. **Run Bot** isn't highlighted, so you stop and read, but Enter still runs. What you change or check in the dialog is saved only when you run.

| The warning says | What to do |
| --- | --- |
| "This bot sells drops when your backpack is full." | Lock what you value in the game, or keep it on a keep list. [Quicksell and keep lists](quicksell.md) has how. |
| "This bot's author has it sell…" | The bot sells something your settings keep. Turn off the switch it names in the bot's **Options** tab to keep it. You're asked once per bot. |
| "With quicksell off, nothing is sold." | Your backpack fills, then your bank. Clear space, or turn quicksell on. |
| "This bot teleports to collect…" | Run it on a quiet realm. |
| "This bot teleports your players to another wizard…" | Check the name shown. The wizard must be online and on every player's friends list. To fix the name, type it in **Change Friend Name** in the dialog. It sets your [Friend Name](../../reference/options.md#friend-name), which changes it for every bot. |
| "Team-Up is on with more than one player." | Each player queues alone and may land in another copy of the dungeon. Turn off **Team-Up**, or run anyway. |
| "A level scaling player already has an elixir active." | Cancel the elixir in the game to start everyone together. [When something goes wrong in a run](when-something-goes-wrong.md#level-scaling-elixirs-at-the-start-of-a-run) has why. |
| "The game's Dblclick Go To Friend option is on…" | Turn it off in the game's **Options › Gameplay Options 4**, in each player it names. |

## Keep your accounts out of screenshots

Turn on **Streamer mode**, in Settings › General, before you share a screenshot or stream. BartleBot's window then shows Character 1, Character 2 and so on in place of your wizards' names. Crop out chat and anything else in the game window that names your account. Logs call your players P1, P2 and so on, and never name them.
