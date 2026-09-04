# Relogging

Why a client slows down, and the two mechanisms that clear it.


A game client gets slower the longer it stays logged in. Measured over an 801-fight
overnight run, the same fight went 23 s -> 32 s, and the cost is a page-fault storm inside
the game process at end of fight (#143). Our event loop, our process state, chat log size,
entity and window counts, handles, GDI, working-set trim and a pinned minimum working set
have all been ruled out. **There is no lever on it from outside the game.** Logging out
clears it, so this is a mitigation and always will be.

Two mechanisms, and they are not interchangeable:

| | how | clears | who triggers it |
|---|---|---|---|
| **hard** | `ClientManager.relog_clients()` -> `force_close_client` -> the ordinary crash-recovery chain | the slowdown, and GDI | the watch loop: a timer (#163) and the GDI ceiling (#164) |
| **soft** | the `relog` node: spellbook -> Preferences -> `QuitButton` -> character select -> Play | the slowdown only | a bot, wherever its author put the node (#89) |

The soft path never closes the process, so hooks, embedding and the account session all
survive and it costs seconds rather than minutes -- but it cannot help a client too wedged
to reach its own menu, and it cannot clear GDI, which needs the process gone.

What must not be broken:

- **`recovery_gaps()` gates every scheduled relog.** A crash happens whether or not
  recovery can cope; a scheduled relog is a healthy run being broken on purpose, and
  `_reconnect_clients` stops the bot when it cannot log a client back in. Without the
  pre-check the feature is a net loss.
- **Wait for `wait_until_parked()`, not `is_paused`.** `pause()` clears an event and
  returns, so `is_paused` goes true while the current node is still running -- for the
  whole of a fight, in the worst case. Also check `is_ambient_combat_active`: that watcher
  runs *alongside* the run loop, so a parked loop is not an idle fleet.
- **Suspend the health monitor for any client being relogged.** A client at character
  select fails `zone_name()` and shows loading screens, which is exactly what
  `_client_status_watcher` and `_loading_screen_watcher` exist to catch -- they would
  force-close a client that is relogging perfectly well. `suspend_health_checks_for()`
  flags them; a relog inside a node must call `resume_health_checks_for()` on the way out,
  because nothing calls `resume()` for it.
- **Close, never unhook.** Killing the process sidesteps `HookHandler._rewrite_autobot`'s
  fixed 0.5 s sleep, which is a hazard on exactly the lagging clients this exists for.
- **Recover the fleet as one batch, not a client at a time.** A relog closes every
  client, and the relaunch goes through `_run_recovery_queue`, which drains
  `_pending_recovery` in batches through `launch_accounts` -- one config.xml patch, the
  instances spawned back-to-back, the login screens waited for once, and every drive
  overlapped. Sequentially it is a full launch per client. Two things this rests on:
  `force_close_client` is gathered so the windows die together rather than a
  process-exit wait apart, and `_settle_recovery_batch` waits out a relog still closing,
  because a window is only noticed missing on the next `_client_loop` poll. Clients
  wanting *different* resolutions still cannot share a batch -- the game reads one
  config.xml at startup -- so those are grouped and launched in turn.
- **A relog is not a disconnect in the log.** It travels the same removal path, and filing
  scheduled maintenance as a fault is the double-meaning #156 exists to prevent --
  `_relogging_hwnds` is what keeps the wording honest.
- **A scheduled relog re-enters at the bot's recovery state; the node does not.** Logging
  out is not position-preserving -- quitting inside a dungeon or a house puts the character
  back where it stood before entering. The timer fires at a moment nobody chose, so it goes
  through recovery like a crash. The node runs where its author put it and advances
  normally; that caveat belongs in its help text, not in engine machinery.

Policy lives in `bartlebot/relog.py` (`relog_due`, `gdi_relog_due`) as pure functions, so
it is testable without a clock, a client, or Qt -- `client_refresh_interval()` is the
precedent.
