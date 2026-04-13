# BartleBot Library

This file contains specifications for BartleBot's standard library nodes.

Each node spec includes a parameter table followed by implementation notes covering the WizWalker/WizSprinter
APIs used and the async flow. See `DEIMOS_PATTERNS.md` in `wizard101_api/` for concrete code patterns.
See `FSM_ARCHITECTURE.md` for how nodes integrate with the FSM.

---

# Node Index

## Action Nodes

| Node | Description | Status |
|------|-------------|--------|
| [`go_to_xyz`](#go_to_xyz) | Teleport clients to an XYZ within the current zone | `done` |
| [`enter_zone_gateway`](#enter_zone_gateway) | Transition clients between zones via gateway coordinates | `done` |
| [`enter_dungeon`](#enter_dungeon) | Enter a dungeon via its sigil | `done` |
| [`enter_gauntlet`](#enter_gauntlet) | Enter a gauntlet dungeon placed in a player's house | `done` |
| [`handle_combat`](#handle_combat) | Fight an enemy using WizSprinter combat DSL | `done` |
| [`interact`](#interact) | Interact with an NPC or world object | `done` |
| [`enter_zone_interact`](#enter_zone_interact) | Interact with an object that triggers a zone change | `done` |
| [`mark_location`](#mark_location) | Mark current location with failsafe handling | `done` |
| [`teleport_to_marked_location`](#teleport_to_marked_location) | Recall to an existing marked location | `done` |
| [`teleport_to_friend`](#teleport_to_friend) | Teleport clients to a named friend | `done` |
| [`teleport_to_world_hub`](#teleport_to_world_hub) | Teleport to the current world's hub zone | `done` |
| [`collect_wisps`](#collect_wisps) | Collect health/mana wisps after combat | `done` |
| [`apply_level_scaling`](#apply_level_scaling) | Apply the level-scaling elixir via NPC interaction | `done` |
| [`wait_for_health`](#wait_for_health) | Block until all clients' health reaches a threshold | `done` |
| [`wait_for_dialogue`](#wait_for_dialogue) | Block until all clients are out of dialogue | `done` |

## Condition Nodes

| Node | Description | Status |
|------|-------------|--------|
| [`check_if_dropped`](#check_if_dropped) | Check if a specific item has been dropped | `done` |
| [`check_level_scaling`](#check_level_scaling) | Check if clients are level scaled | `done` |
| [`check_zone`](#check_zone) | Check if all clients are in a specific zone | `done` |

## Advanced Behaviors

| Node | Description | Status |
|------|-------------|--------|
| [`handle_potions`](#handle_potions) | Use and optionally refill potions based on health/mana thresholds | `done` |
| [`quicksell`](#quicksell) | Quicksell backpack items when full, respecting a keep list | `done` |

---

# Node Library

## Action Nodes

Action nodes are nodes that execute in-game actions. They make up the core behavior of a BartleBot FSM.

### go_to_xyz

**Status:** `done`

Teleports clients to a given XYZ location within the same zone.

| Parameter Name | Parameter Type | Parameter Description |
|----------------|----------------|-----------------------|
| `zone`         | `str`          | The zone to transition from. All selected clients must be in this zone when the node is called.
| `xyz`          | `XYZ`          | The XYZ coordinates within `zone`.

<details>
<summary>Implementation</summary>

- APIs: `client.zone_name()`, `client.teleport(XYZ)`
- Flow:
  1. Verify all clients are in `zone`. If not, return `FAILURE`.
  2. `asyncio.gather` teleport all clients to `xyz`.
  3. Return `SUCCESS` if all clients are at the provided XYZ (or within a very small margin), else return `FAILURE`.
- `FAILURE` cases: clients not in `zone` at start; not all clients reached `xyz` after teleporting.

</details>

---

### enter_zone_gateway

**Status:** `done`

Transitions clients from one zone to another with failsafes to handle client zone desynchronization and loading failures.

| Parameter Name | Parameter Type | Parameter Description |
|----------------|----------------|-----------------------|
| `from_zone`    | `str`          | The zone to transition from. All selected clients must be in this zone when the node is called.
| `to_zone`      | `str`          | The zone to transition to. If not all selected clients are in this zone when the node is done, it returns FAILURE.
| `xyz`          | `XYZ`          | The XYZ coordinates within the zone transition bounding box. Teleporting clients to this location triggers the transition automatically — no key press needed.

<details>
<summary>Implementation</summary>

- APIs: `client.zone_name()`, `client.teleport(XYZ)`, `client.is_loading()`
- Flow:
  1. Verify all clients are in `from_zone`. If not, return `FAILURE`.
  2. `asyncio.gather` teleport all clients to `xyz`. The zone transition triggers automatically when clients enter the bounding box.
  3. Wait for all clients' zone to change away from `from_zone`, then wait for loading screens to finish.
  4. Check all clients are now in `to_zone`. If any are not (desync), return `FAILURE`.
  5. Return `SUCCESS`.
- `FAILURE` cases: clients not in `from_zone` at start; not all clients reached `to_zone` after transition.

</details>

---

### enter_dungeon

**Status:** `done`

Enters a dungeon via dungeon sigil. Handles client zone desynchronization, sigil detection, and loading failures.

| Parameter Name  | Parameter Type | Parameter Description |
|-----------------|----------------|-----------------------|
| `from_zone`     | `str`          | The zone to transition from. All selected clients must be in this zone when the node is called.
| `dungeon_zone`  | `str`          | The name of the dungeon zone. If not all selected clients are in this zone when the node is done, it returns FAILURE.
| `sigil_xyz`     | `XYZ`          | The XYZ coordinates of the dungeon sigil. The dungeon entrance prompt (not the dungeon warning) should appear when standing in this location.
| `reset_xyz`     | `XYZ`          | The XYZ coordinates of a location within `from_zone` that can be used to dismiss the dungeon entrance prompt. The prompt should **not** appear when standing here.

<details>
<summary>Implementation</summary>

- APIs: `client.zone_name()`, `client.teleport(XYZ)`, `client.is_loading()`, `client.is_in_npc_range()`, `client.send_key(Keycode.X)`, window tree helpers for prompt detection
- Flow:
  1. Verify all clients are in `from_zone`. If not, return `FAILURE`.
  2. Teleport all clients to `sigil_xyz`.
  3. Wait for the dungeon entrance prompt to appear (check via `client.is_in_npc_range()` or window visibility). The entrance prompt is distinct from the dungeon warning — X should only be sent after the entrance prompt is showing. If the prompt does not show after a short timeout, teleport all clients to `reset_xyz` and restart from step 2.
  4. Press X on all clients to interact with the sigil.
  5. Optionally wait a short timeout for a dungeon warning dialog and confirm it if it appears — do not wait indefinitely, as players can disable dungeon warnings in game settings.
  6. Wait for all clients' zone to change to `dungeon_zone` (with loading screen wait).
  7. If any clients did not enter `dungeon_zone` (desync) return `FAILURE`
  8. Return `SUCCESS` once all clients are in `dungeon_zone`; return `FAILURE` if retries are exhausted.
- `FAILURE` cases: clients not in `from_zone`; clients not in `dungeon_zone` after loading attempted.

</details>

---

### enter_dungeon_teamup

**Status:** `done`

Enters a dungeon via the team-up queue. Handles prompt detection, team size selection, anti-AFK inputs during the queue wait, and client zone desynchronization.

| Parameter Name       | Parameter Type | Parameter Description |
|----------------------|----------------|-----------------------|
| `from_zone`          | `str`          | The zone to transition from. All selected clients must be in this zone when the node is called.
| `dungeon_zone`       | `str`          | The name of the dungeon zone. If not all selected clients are in this zone when the node is done, it returns FAILURE.
| `sigil_xyz`          | `XYZ`          | The XYZ coordinates of the dungeon sigil. The dungeon entrance prompt must appear when standing here.
| `reset_xyz`          | `XYZ`          | The XYZ coordinates of a location within `from_zone` used to dismiss the entrance prompt. The prompt must **not** appear when standing here.
| `minimum_team_size`  | `int`          | Minimum number of players required before the dungeon starts. Valid values: `2`, `3`, or `4`. Defaults to `2`.

<details>
<summary>Implementation</summary>

- APIs: `client.zone_name()`, `client.teleport(XYZ)`, `client.is_loading()`, `client.send_key(Keycode.A)`, window tree helpers (`click_by_path`, `is_visible_by_path`)
- Flow:
  1. Validate `minimum_team_size` (must be 2, 3, or 4). Return `FAILURE` immediately if invalid.
  2. Verify all clients are in `from_zone`. Return `FAILURE` if not.
  3. Teleport all clients to `sigil_xyz`. If the dungeon entrance prompt was already visible beforehand, teleport to `reset_xyz` first to dismiss it.
  4. Wait for the dungeon entrance (team-up button) prompt to appear. If the prompt does not show after a short timeout, teleport to `reset_xyz` and retry (up to `MAX_RETRIES` attempts).
  5. Click the team-up button (`DUNGEON_ENTRANCE_TEAMUP_BUTTON_PATH`) on all clients.
  6. Wait for the team-up confirmation window to appear (`ENTER_TEAMUP_BUTTON_PATH` visible). Return `FAILURE` if it never appears.
  7. Click the appropriate team size checkbox on all clients.
  8. Click the enter team-up button on all clients to join the queue.
  9. Poll for zone change, sending a brief `A` keypress every 3 minutes to prevent idle disconnection. Timeout after 1 hour.
  10. Wait for loading screens to clear, then verify all clients are in `dungeon_zone`. Return `FAILURE` if any client is in the wrong zone.
- `FAILURE` cases: invalid `minimum_team_size`; clients not in `from_zone`; entrance prompt never appeared; team-up window never opened; queue timed out (1 hour); clients not in `dungeon_zone` after loading.

</details>

---

### enter_gauntlet

**Status:** `done`

Enters a gauntlet dungeon placed in a player's house. Unlike `enter_dungeon`, this node does not teleport clients — the player must walk to the gauntlet sigil before the node is called.

| Parameter Name | Parameter Type | Parameter Description |
|----------------|----------------|-----------------------|
| `dungeon_zone` | `str`          | Zone name all clients must be in after successfully entering the gauntlet.

**Bot Options:**

| Option Name    | Type    | Default | Description |
|----------------|---------|---------|-------------|
| `Safety Delay` | `float` | `3`     | Minimum seconds to watch for dialog windows after triggering an interaction before concluding that none will open.

<details>
<summary>Implementation</summary>

- APIs: `client.send_key(Keycode.X)`, `client.is_loading()`, window tree helpers for prompt detection
- Flow:
  1. Wait for the gauntlet entrance prompt to appear on all clients (detected via `node_helpers.all_at_dungeon_entrance()` with `GAUNTLET_ENTRANCE_PATH`). If the prompt does not appear within `INTERACT_TIMEOUT`, return `FAILURE`.
  2. Press X on all clients simultaneously to enter.
  3. Poll for a dungeon warning dialog for `Safety Delay` seconds; confirm it if it appears.
  4. Wait for all clients' loading screens to clear.
  5. Wait for all clients to arrive in `dungeon_zone`. If any client is not in `dungeon_zone` after `ZONE_CHANGE_TIMEOUT`, return `FAILURE`.
  6. Return `SUCCESS`.
- `FAILURE` cases: gauntlet entrance prompt never appeared within timeout; not all clients reached `dungeon_zone` after loading.

</details>

---

### handle_combat

**Status:** `done`

Performs combat with a specified enemy. If no enemy is specified, defaults to the closest valid enemy when the node is called. Combat nodes accept combat configurations specified with the WizSprinter combat DSL, which will be applied for that specific combat instance.

| Parameter Name  | Parameter Type | Parameter Description |
|-----------------|----------------|-----------------------|
| `enemy`         | `str`          | The entity name of the enemy to enter combat against. If omitted, uses the closest valid mob.
| `combat_config` | `str` (optional) | WizSprinter combat DSL config for this encounter. If omitted, falls back to the bot-level `default_combat_config`.
| `combat_order`  | `List[int]` (optional) | Order in which clients enter combat, as 1-based client numbers matching p1/p2/p3/p4 in the WizSprinter combat config. Example: `[2, 1]` sends client 2 first, then client 1. If omitted, all clients enter simultaneously.

<details>
<summary>Implementation</summary>

- APIs: `SprintyClient.find_closest_by_name()`, `SprintyClient.tp_to()`, `client.in_battle()`, `SprintyCombat`, `StrCombatConfigProvider`
- Flow:
  1. Resolve the target: if `enemy` is provided, use `sprinter.find_closest_by_name(enemy)`; otherwise `sprinter.find_closest_mob()`.
  2. If `combat_order` is specified: teleport clients to the target one at a time in the specified order, waiting for each to report `in_battle()` before sending the next. If omitted, teleport all clients simultaneously via `asyncio.gather`.
  3. Wait until all clients report `in_battle()`.
  4. Resolve combat config: use `params.combat_config` if present, otherwise `machine.bot_config.default_combat_config`.
  5. Run `SprintyCombat(client, StrCombatConfigProvider(config), True).wait_for_combat()` for all clients via `asyncio.gather`.
  6. Return `SUCCESS` once all clients exit combat.
- `FAILURE` cases: target entity not found; combat did not begin within timeout.

</details>

---

### interact

**Status:** `done`

Interacts with an NPC or interactable world item such as a lever, collectable, etc.

| Parameter Name  | Parameter Type | Parameter Description |
|-----------------|----------------|-----------------------|
| `zone`          | `str`          | Zone name all clients must currently be in when this node executes.
| `interact_xyz`  | `XYZ`          | The XYZ coordinates of the item to interact with. The interact prompt should show when standing in this location.
| `reset_xyz`     | `XYZ`          | The XYZ coordinates of a location that can be used to reset the interact prompt. The interact prompt should **not** show when standing in this location.

<details>
<summary>Implementation</summary>

- APIs: `client.teleport(XYZ)`, `client.is_in_npc_range()`, `client.is_in_dialog()`, `client.send_key(Keycode.X)`, `client.send_key(Keycode.SPACEBAR)`, window tree helpers for prompt detection
- Flow:
  1. Teleport all clients to `interact_xyz`.
  2. Wait for the interact prompt to appear (check via `client.is_in_npc_range()` or window visibility). If prompt does not appear after timeout, teleport all clients to `reset_xyz` and restart from step 1.
  3. Press X on all clients to begin interaction.
  4. While any client has a dialogue window visible (`client.is_in_dialog()`), repeatedly send SPACE to advance through dialogue. Continue until dialogue is dismissed on all clients.
  5. If the interact prompt never appeared: teleport clients to `reset_xyz` to clear state, then retry from step 1.
  6. Return `SUCCESS` once all dialogue is dismissed and interaction is complete.
- `FAILURE` cases: interact prompt never appeared after retries.

</details>

---

### enter_zone_interact

**Status:** `done`

Interacts with an NPC or world object that triggers a zone change (e.g., a snowglobe or portal). Unlike `enter_dungeon`, this node is for interactables that immediately begin loading without a sigil prompt or dungeon warning.

| Parameter Name  | Parameter Type | Parameter Description |
|-----------------|----------------|-----------------------|
| `from_zone`     | `str`          | Zone name all clients must currently be in when this node executes.
| `to_zone`       | `str`          | Zone name all clients must be in after this node completes.
| `interact_xyz`  | `XYZ`          | Coordinates of the object to interact with. The interact prompt should appear when standing here.
| `reset_xyz`     | `XYZ`          | Coordinates used to dismiss the interact prompt and reset state. The prompt must **not** appear when standing here.

<details>
<summary>Implementation</summary>

- APIs: `client.zone_name()`, `client.teleport(XYZ)`, `client.is_in_npc_range()`, `client.is_loading()`, `client.send_key(Keycode.X)`
- Flow:
  1. Verify all clients are in `from_zone`. If not, return `FAILURE`.
  2. Clear any pre-existing interact prompt by teleporting to `reset_xyz` if any client already has the prompt showing.
  3. Retry loop (up to 3 attempts): teleport all clients to `interact_xyz`, wait for the interact prompt to appear on all clients. If the prompt does not appear within the timeout, teleport to `reset_xyz` and retry.
  4. If the prompt never appeared after all retries, return `FAILURE`.
  5. Press X on all clients simultaneously.
  6. Re-press X for any client that has not yet entered a loading screen. Track which clients have *ever* been seen loading — a fast client can finish its loading screen before a slow client starts. Continue until all clients have been seen loading, or a 10-second timeout expires.
  7. Wait for all loading screens to finish.
  8. Verify all clients are in `to_zone`. If not, return `FAILURE`.
  9. Return `SUCCESS`.
- `FAILURE` cases: clients not in `from_zone`; interact prompt never appeared after retries; clients did not enter a loading screen within timeout; not all clients arrived in `to_zone`.

</details>

---

### mark_location

**Status:** `done`

Marks current location, including failsafes to handle edge cases such as a pre-existing mark or the mark cooldown timer.

| Parameter Name | Parameter Type | Parameter Description |
|----------------|----------------|-----------------------|
| `zone`         | `str`          | The zone to mark location within. All selected clients must be in this zone when the node is called.
| `xyz`          | `XYZ`          | The XYZ coordinates within `zone` to mark.

<details>
<summary>Implementation</summary>

- APIs: TODO
- Flow:
  1. Check if clients have enough mana to mark location. If any clients don't have enough mana, return `FAILURE`. 
  2. Check `zone` and `xyz` against current client zone and xyz. If `zone` is correct but `xyz` isn't, teleport clients to `xyz`. If `zone` is incorrect, return `FAILURE`.
  3. Wait until the UI path element to mark location is enabled.
  4. Mark location.
  5. Check if recall to mark UI path element is disabled. If so, that means that client had already marked in this spot and just reset its mark by marking again. If so, wait until mark UI path element is enabled again (30s timer after marking, but better to wait for UI element to be enabled) and then mark spot.

</details>

---

### teleport_to_marked_location

**Status:** `done`

Recalls to existing marked location.

| Parameter Name | Parameter Type | Parameter Description |
|----------------|----------------|-----------------------|
| `zone`         | `str`          | The zone to return to.
| `xyz`          | `XYZ`          | The XYZ coordinates within `zone` to return to.

<details>
<summary>Implementation</summary>

- Flow:
  1. Check if clients have enough mana to return to marked location. If any clients don't have enough mana, return `FAILURE`.
  2. Wait until the UI path element to return to the marked location is enabled. If it isn't enabled within 30 seconds (maybe wait 35 just to be safe), that means that no mark has been set and the node returns `FAILURE`.
  3. Teleport to mark and handle loading screen safely.
  4. Check that clients are in `zone` and at `xyz` (with small margin of error). Return `FAILURE` if false.

</details>

---

### teleport_to_friend

**Status:** `done`

Teleports one or more clients to a named friend on their friends list. Clients already in the destination zone are skipped. Handles the friend-busy / instance-unavailable error dialog.

| Parameter Name | Parameter Type | Parameter Description |
|----------------|----------------|-----------------------|
| `friend_name`  | `str`          | Wizard name to search for (case-insensitive). Must be online and on each client's friends list. |
| `to_zone`      | `str`          | Expected zone name after teleport. Clients already in this zone are skipped. Used to verify arrival. |

<details>
<summary>Implementation</summary>

- APIs: `wizwalker.extensions.scripting.teleport_to_friend_from_list(client, name=str)`, `client.zone_name()`, `client.is_loading()`, window tree helpers for dialog detection
- Friend-busy dialog path constant: `FRIEND_BUSY_DISMISS_PATH` in `bartlebot/nodes/paths.py`
- Note: `teleport_to_friend_from_list` uses `client.mouse_handler` internally; the node activates the mouse handler context for each client before calling it.
- Flow:
  1. Check if all clients are already in `to_zone`. If so, return `SUCCESS` immediately.
  2. Filter to only clients not in `to_zone`.
  3. Call `teleport_to_friend_from_list(client, name=friend_name)` for all clients-to-move simultaneously (each wrapped in `async with client.mouse_handler:`). `ValueError` (friend not found / no friends online) → `FAILURE`.
  4. "Ever seen" loading loop (up to `TRIGGER_TIMEOUT`): on each tick, check each not-yet-loading client for the friend-busy dialog — if visible, dismiss it and return `FAILURE`. Then check `is_loading()` for each; add newly-loading clients to the seen set. Break when all have been seen loading. On timeout, return `FAILURE`. For stragglers (not loading, no dialog), re-call `teleport_to_friend_from_list`. Sleep `POLL_INTERVAL` between iterations.
  5. Wait for all loading screens to finish.
  6. Verify all clients (including those already in `to_zone`) are in `to_zone` via `wait_for_zone`. Return `FAILURE` if any are not.
  7. Return `SUCCESS`.
- `FAILURE` cases: friend not found or not online; friend-busy/instance-unavailable dialog appeared; clients did not enter a loading screen within `TRIGGER_TIMEOUT`; not all clients arrived in `to_zone` after loading.

</details>

---

### teleport_to_world_hub

**Status:** `done`

Teleports to the hub zone for the current world.

| Parameter Name | Parameter Type | Parameter Description |
|----------------|----------------|-----------------------|
| `zone`         | `str`          | The hub zone to teleport to. If all clients are already in this zone, the node returns `SUCCESS` immediately. |

<details>
<summary>Implementation</summary>

- APIs: `client.zone_name()`, `client.send_key(Keycode.END)`, `client.is_loading()`, `window.is_control_grayed()`, window tree helpers for button detection
- Hub teleport button path: `['WorldView', 'windowHUD', 'compassAndTeleporterButtons', 'GoHomeButton']`
- Flow:
  1. Check if all clients are already in `zone`. If so, return `SUCCESS` immediately.
  2. Filter to only clients not already in `zone`. All remaining steps operate on this subset.
  3. Wait for the hub teleport button to be enabled (not grayed) on all clients to move. The button has a 30-second cooldown after each use — wait up to 35 seconds before returning `FAILURE`.
  4. Press `END` on clients to move simultaneously. Poll `client.is_loading()` for each; re-press `END` on any client that has not yet entered a loading screen. Continue until all clients to move are loading, or a 10-second timeout expires.
  5. Wait for all loading screens to finish.
  6. Check that all clients (including those already in the hub) are in `zone`. If not, return `FAILURE`.
  7. Return `SUCCESS`.
- `FAILURE` cases: hub teleport button did not become enabled within 35 seconds; clients did not enter a loading screen within 10 seconds of pressing END; not all clients arrived in `zone` after loading.

</details>

---

### collect_wisps

**Status:** `done`

Collects health and mana wisps after a battle if clients are below a specified health or mana threshold. Returns `FAILURE` if any client is still below a configurable fail threshold after wisps are exhausted, allowing the bot graph to route to a potion fallback node.

| Parameter Name        | Parameter Type | Parameter Description |
|-----------------------|----------------|-----------------------|
| `if_health_below`     | `float`        | Health threshold (0.0–1.0). Collect health wisps while the client's health ratio is below this value. Set to 0.0 to skip health wisp collection entirely. |
| `if_mana_below`       | `float`        | Mana threshold (0.0–1.0). Collect mana wisps while the client's mana ratio is below this value. Set to 0.0 to skip mana wisp collection entirely. |
| `fail_if_health_below`| `float`        | Health fail threshold (0.0–1.0). After collection, if any client's health ratio is still below this value, return `FAILURE`. Set to 0.0 to never fail on health. Default: `0.5`. |
| `fail_if_mana_below`  | `float`        | Mana fail threshold (0.0–1.0). After collection, if any client's mana ratio is still below this value, return `FAILURE`. Set to 0.0 to never fail on mana. Default: `0.5`. |

<details>
<summary>Implementation</summary>

- APIs: `SprintyClient.calc_health_ratio()`, `SprintyClient.calc_mana_ratio()`, `SprintyClient.tp_to_closest_health_wisp()`, `SprintyClient.tp_to_closest_mana_wisp()`
- Flow:
  1. For each client (in parallel), check health and mana ratios.
  2. While health is below `if_health_below`, teleport the client to the closest health wisp (`tp_to_closest_health_wisp()` returns `False` when no wisps are in range — stop the loop). Wait briefly between teleports to allow the wisp to register.
  3. While mana is below `if_mana_below`, teleport the client to the closest mana wisp. Stop when no more wisps are in range.
  4. After all clients finish collecting, re-check ratios. If any client's health ratio is below `fail_if_health_below` or mana ratio is below `fail_if_mana_below`, return `FAILURE`.
  5. Return `SUCCESS` if all clients are at or above both fail thresholds.
- `FAILURE` cases: any client is still below a fail threshold after wisps are exhausted.

</details>

---

### apply_level_scaling

**Status:** `done`

Interacts with the level-scaling NPC to apply the level-scaling elixir. Assumes clients are already in the correct dungeon zone. After pressing X to begin the interaction, clicks the option button in the NPCServicesWin menu that appears, then waits for the menu to close.

| Parameter Name    | Parameter Type | Parameter Description |
|-------------------|----------------|-----------------------|
| `zone`            | `str`          | Zone name all clients must be in when this node executes (the level-scaling dungeon).
| `interact_xyz`    | `XYZ`          | Coordinates of the level-scaling NPC. The interact prompt should appear when standing here.
| `reset_xyz`       | `XYZ`          | Coordinates used to dismiss the interact prompt and reset state. The prompt must **not** appear when standing here.
| `clients_to_scale`| `List[int]`    | 1-based client numbers to apply level scaling to (e.g. `[2, 3]`). Leave empty to scale all clients. Client numbers that exceed the number of connected clients are silently skipped.

<details>
<summary>Implementation</summary>

- APIs: `client.zone_name()`, `client.teleport(XYZ)`, `client.is_in_npc_range()`, `client.send_key(Keycode.X)`, `node_helpers.click_by_path()`, `node_helpers.is_visible_by_path()`
- Path constants: `LEVEL_SCALING_OPTION_BUTTON_PATH` from `bartlebot.nodes.paths`
- Flow:
  1. Verify all clients are in `zone`. If not, return `FAILURE`.
  2. Resolve `clients_to_scale`: convert each 1-based number to a 0-based index, silently skipping any that exceed the number of connected clients. If empty, use all clients. If the resolved set is empty, return `SUCCESS` immediately.
  3. Clear any pre-existing interact prompt on the scaled clients: if any already has the prompt showing, teleport them to `reset_xyz` and wait for it to clear.
  4. Retry loop (up to 3 attempts): teleport scaled clients to `interact_xyz`, wait for the interact prompt to appear on all of them (`client.is_in_npc_range()`). If the prompt does not appear within the timeout, teleport to `reset_xyz` and retry.
  5. If the prompt never appeared after all retries, return `FAILURE`.
  6. Press X on all scaled clients simultaneously.
  7. Wait for `LEVEL_SCALING_OPTION_BUTTON_PATH` to become visible on all scaled clients (the NPCServicesWin menu has appeared). If it does not appear within the timeout, return `FAILURE`.
  8. Click `LEVEL_SCALING_OPTION_BUTTON_PATH` on all scaled clients simultaneously (`node_helpers.click_by_path`).
  9. Wait for `LEVEL_SCALING_OPTION_BUTTON_PATH` to disappear on all scaled clients (menu dismissed, elixir applied).
  10. Return `SUCCESS`.
- `FAILURE` cases: clients not in `zone`; interact prompt never appeared after retries; NPCServicesWin option button never became visible.

</details>

---

### wait_for_health

**Status:** `done`

Blocks until all clients' health ratio is at or above a threshold, then returns `SUCCESS`. Use after combat or potion use to pause the bot until clients have recovered enough to continue safely. Set `timeout` to `0` to wait indefinitely.

| Parameter Name | Parameter Type | Parameter Description |
|----------------|----------------|-----------------------|
| `threshold`    | `float`        | Target health ratio (0.0–1.0). The node waits until all clients are at or above this value. |
| `timeout`      | `float`        | Maximum seconds to wait before returning `FAILURE`. Set to `0` for no time limit. Default: `120`. |

**Returns:** `SUCCESS` when all clients meet the threshold; `FAILURE` if the timeout expires first (unreachable when `timeout=0`).

<details>
<summary>Implementation</summary>

- APIs: `SprintyClient.calc_health_ratio()`
- Flow:
  1. Poll `calc_health_ratio()` on all clients simultaneously via `asyncio.gather`.
  2. If all ratios are at or above `threshold`, return `SUCCESS`.
  3. Otherwise sleep `POLL_INTERVAL` and repeat.
  4. If `timeout > 0` and it expires before the condition is met, return `FAILURE`.

</details>

---

### wait_for_dialogue

**Status:** `done`

Blocks until all clients are out of dialogue, then returns `SUCCESS`. Use after an `interact` node or cutscene to ensure dialogue has fully cleared before the next action. Set `timeout` to `0` for no time limit (equivalent to Deimos's `waitforfree`).

| Parameter Name | Parameter Type | Parameter Description |
|----------------|----------------|-----------------------|
| `timeout`      | `float`        | Maximum seconds to wait before returning `FAILURE`. Set to `0` for no time limit. Default: `60`. |

**Returns:** `SUCCESS` when all clients are out of dialogue; `FAILURE` if the timeout expires first (unreachable when `timeout=0`).

<details>
<summary>Implementation</summary>

- APIs: `SprintyClient.is_in_dialog()`
- Flow:
  1. Poll `is_in_dialog()` on all clients simultaneously via `asyncio.gather`.
  2. If no client is in dialogue, return `SUCCESS`.
  3. Otherwise sleep `POLL_INTERVAL` and repeat.
  4. If `timeout > 0` and it expires before the condition is met, return `FAILURE`.

</details>

---

## Condition Nodes

Condition nodes evaluate game state and return `TRUE` or `FALSE`. They have no in-game side effects — they exist purely to drive routing decisions in the bot graph. Like action nodes, they use named output ports; downstream edges connect from the `TRUE` or `FALSE` port as appropriate.

For simple bot-option checks (e.g. 'only follow this path if repeat mode is enabled'), use the `flag` field on a transition edge instead of a condition node. Condition nodes are for parameterized checks that evaluate live game data and need their own configuration panel.

### check_if_dropped

**Status:** `done`

Stops the bot if a drop is received.

| Parameter Name    | Parameter Type | Parameter Description |
|-------------------|----------------|-----------------------|
| `clients_to_check`| `List[int]`    | 1-based client numbers to check for the drop (e.g. `[1, 2]` checks clients 1 and 2).
| `item_names`      | `List[str]`    | Names of items to watch for.
| `require_all`     | `bool`         | Optional, defaults to `False`. If `True`, node returns `TRUE` only if all items in `item_names` have been dropped for all clients in `clients_to_check`. If `False`, returns `TRUE` as soon as any matching item is found.

**Returns:** `TRUE` if the drop condition is met, `FALSE` otherwise.

<details>
<summary>Implementation</summary>

- APIs: checks the drop table for the current run
- Flow:
  1. For each client in `clients_to_check`, scan `item_names`.
  2. If `require_all == False`, return `TRUE` as soon as any item in `item_names` is found for any client. If `require_all == True`, return `FALSE` as soon as any item check comes up empty for any client.
  3. Return `TRUE` if all checks passed, `FALSE` otherwise.
- Intended use: placed as a node that runs after each combat or dungeon run, allowing the bot to loop back if the item hasn't dropped.

</details>

---

### check_level_scaling

**Status:** `done`

Checks if a list of clients are all level scaled. Checks level scaling by checking UI element path for level scaling potion.

| Parameter Name    | Parameter Type | Parameter Description |
|-------------------|----------------|-----------------------|
| `clients_to_check`| `List[int]`    | 1-based client numbers to check for level scaling status (e.g. `[1, 2]` checks clients 1 and 2).

**Returns:** `TRUE` if all specified clients are level scaled, `FALSE` otherwise.

<details>
<summary>Implementation</summary>

- Flow:
  1. For each client in `clients_to_check`, check if the level scaling UI path element is visible.
  2. Return `FALSE` if any client is not level scaled, else return `TRUE`.

</details>

---

### check_zone

**Status:** `done`

Checks whether all specified clients are currently in a given zone.

| Parameter Name     | Parameter Type | Parameter Description |
|--------------------|----------------|-----------------------|
| `zone`             | `str`          | Zone name to check (e.g. `WizardCity/WC_Ravenwood`). |
| `clients_to_check` | `List[int]`    | 1-based client numbers to check (e.g. `[1, 2]`). Leave empty to check all clients. |

**Returns:** `TRUE` if all checked clients are in `zone`, `FALSE` otherwise.

<details>
<summary>Implementation</summary>

- APIs: `client.zone_name()`, `node_helpers.all_in_zone()`
- Flow:
  1. Resolve `clients_to_check` to 0-based indices (default: all clients).
  2. Call `node_helpers.all_in_zone()` which gathers zone names in parallel and checks all match.
  3. Return `TRUE` if all match, `FALSE` otherwise.

</details>

---

# Advanced Behavior Library

BartleBot includes a library of more advanced behaviors that combine multiple actions into sequences that are commonly used. Adding an advanced behavior to a bot will add a group of nodes with a specific configuration and endpoints.

### handle_potions

**Status:** `done`

Uses potions if clients are below a specified health or mana threshold. Refills potions if needed.

| Parameter Name   | Parameter Type | Parameter Description |
|------------------|----------------|-----------------------|
| `if_health_below`| `float`        | Health threshold (0.0–1.0) below which the client should use a potion.
| `if_mana_below`  | `float`        | Mana threshold (0.0–1.0) below which the client should use a potion.
| `refill_potions` | `bool`         | If `true`, navigate to refill potions when any client runs out.

<details>
<summary>Implementation</summary>

- APIs: `SprintyClient.calc_health_ratio()`, `SprintyClient.calc_mana_ratio()`, `SprintyClient.needs_potion()`, `SprintyClient.use_potion_if_needed()`, `SprintyClient.has_potion()`
- Flow:
  1. For each client, check health and mana ratios.
  2. If either ratio is below the threshold and the client has a potion, use it.
  3. If `refill_potions` is true and a client has no potions, begin navigation sequence to refill potions.
  4. Call mark_location node
  5. Teleport home
  6. After loading home, check zone. If zone is `WizardCity/Interiors/WC_Housing_Dorm_Interior` then we're set. If not, wait for the home teleport button to become available again and then teleport home.
    -  Almost all houses have a spiral door available, but there are niche cases (specifically, the Apartment series houses) that need to be handled. Rather than branch on these cases, let's just be 100% sure that we end up in a dorm in a near-infallible manner.
  7. Call enter_zone_gateway logic to go from the dorm to Ravenwood (`WizardCity/WC_Ravenwood`)
  8. Call enter_zone_gateway logic to go from Ravenwood to The Commons (`WizardCity/WC_Hub` via `{'x': 12.1, 'y': -2037.77, 'z': -130.64}`)
  9. Teleport to Hilda Brewer via go_to_xyz, then talk to her via interact.
  10. Navigate the potions menu following the buy_potions() logic in utils.py at C:/GitHub/Deimos
  11. Return `SUCCESS` if potions were bought successfully, `FAILURE` otherwise.

For Arcanum Apartment users, could potentially add a flag that lets them go to the Arcanum potion vendor instead.

</details>

---

### quicksell

**Status:** `done`

Checks if backpack is full. If full, quicksells items except for those specified in `keep_list`. Needs to be able to operate separately for different clients, since they will receive different drops. May also want 

| Parameter Name   | Parameter Type | Parameter Description |
|------------------|----------------|-----------------------|
| `keep_list`      | path           | A list of strings to search item names for when quickselling. If a match is found, the item is kept. Does not need to be a full title match, any word or part of a word will cause the item to be kept.

<details>
<summary>Implementation</summary>

- APIs: `client.backpack_space()`, window tree helpers for the quicksell UI
- Flow:
  1. Open backpack by sending `B` key.
  2. Check `client.backpack_space()` for each client. If not full, return `SUCCESS` immediately.
  3. For clients with a full backpack, open the quicksell UI by clicking path `['WorldView', 'DeckConfiguration', 'JewelSpellbookPage', 'windowForBtns', 'Layout', 'QuickSell_Item']`
  4. Switch to the `All` tab by clicking path `['WorldView', 'shopGUI', 'BackpackStuff', 'ShopCategory_All']`
  5. Click the `All` button by clicking path `['WorldView', 'shopGUI', 'buyWindow', 'AllAction']`
  6. 10 items appear on each quicksell page. Check the contents of each row, and if a match is found, uncheck the sell box.
    - Item title path for each row: `['WorldView', 'shopGUI', 'buyWindow', 'column0', 'shoplistX']` where `X` is the row number `0-9`
    - Sell checkbox path for each row: `['WorldView', 'shopGUI', 'buyWindow', 'column1', 'numX']` where `X` is the row number `0-9`
  7. Repeat for each page until the next page button is disabled.
    - Next page button path: `['WorldView', 'shopGUI', 'buyWindow', 'rightscroll']`
  8. Sell all items that did not meet the criteria by clicking the sell button.
    - Primary sell button path: `['WorldView', 'shopGUI', 'buyWindow', 'sellAction']`
  9. Click the `Sell` button in the confirm dialog.
    - Confirm sell button path: `['WorldView', 'shopGUI', 'QuickSellConfirmationWindow', 'SellButton']`
  10. Dismiss any warnings about exceeding the gold limit by clicking the `Ok` button.
    - `Ok` button path: `['MessageBoxModalWindow', 'messageBoxBG', 'messageBoxLayout', 'AdjustmentWindow', 'Layout', 'centerButton']`
  11. Wait to return to main backpack page by sleeping until backpack close button is visible.
    - Backpack close button path: `['WorldView', 'DeckConfiguration', 'Close_Button']`
  12. Close backpack by clicking the backpack close button path and confirming that it is no longer visible.
  13. Return `SUCCESS`.

</details>
