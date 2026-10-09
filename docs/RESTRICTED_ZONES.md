# Restricted zones (PvP)


BartleBot does not automate PvP. `bartlebot/game/restricted_zones.py` and its data file
`bartlebot/game/data/restricted_zones.json` hold the whole policy: the `Battlegrounds`
world (all of Beastmoon) and the twelve PvP match arenas. **Raids and every other
PvE zone are deliberately allowed** — automating PvE monotony is the point of the
tool, and a guard that bans farming content is just a broken tool.

Three enforcement points, and none of them is optional:

| where | what |
|---|---|
| `check_bot_config` → `AppController._check_restricted_zones` | refuses to *start* a bot naming a restricted zone; runs first in `_on_run_requested`, and unlike the other pre-run dialogs it has no "continue" |
| `ClientHealthMonitor._restricted_zone_watcher` | polls every 0.25 s during a run; stops the FSM and unhooks every client |
| `check_duel` | reads the current duel's own flags and team make-up, catching a PvP zone no rule names |

Things to preserve when touching this:

- **Match on `world` or `exact`, never a substring.** "Arena" appears in Darkmoor,
  DragonSpire, Krokosphinx, Wysteria and a dozen housing zones, all PvE and several
  of them prime farming targets. `WizardCity/WC_Duel_Arena` is the *lobby* and is
  allowed; `WC_Duel_Arena_New` is a match zone despite the name and is not.
- **The duel flags are only read while `in_battle()`.** Out of combat the duel struct
  holds whatever the last fight left in it, and a stale `pvp` would stop a bot standing
  in a questing zone.
- **Neither duel flag is a stop signal on its own.** `m_bPVP` and `m_bBattleground`
  select a *ruleset* — match timers, pass penalty — and Challenge Mode borrows it
  wholesale for ordinary PvE dungeons, setting *both* on a dungeon boss with no other
  player in the fight. Each must be corroborated by players on two different teams
  (`_players_on_both_teams`), which no PvE fight has; the flag then only decides how
  the stop is named. Do not re-promote either to a standalone signal — that bug shipped
  twice, once per flag, and both times it stopped a Marleybone farm mid-fight.
- **An unknown zone is allowed.** A client on a loading screen has no zone; failing
  closed there would fire on every transition in every bot.
- **Stopping the FSM is not enough — the clients get unhooked.** A stopped machine
  still leaves WizWalker's memory hooks in a process sitting in a PvP arena, which is
  the state the guard exists to prevent.
- **No setting, no override, no bypass.** Anyone can edit the JSON — this is
  open source — but the shipped build must have no supported path into PvP.

Zone paths come from the game's own `GameData` WAD filenames, where `-` separates the
segments `zone_name()` returns joined by `/`. That listing is how the set was
enumerated, and how to re-check it after a patch adds a world.
