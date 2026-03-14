# BartleBot Library

This file contains specifications for BartleBot's standard library nodes.

Each node spec includes a parameter table followed by implementation notes covering the WizWalker/WizSprinter
APIs used and the async flow. See `DEIMOS_PATTERNS.md` in `wizard101_api/` for concrete code patterns.
See `FSM_ARCHITECTURE.md` for how nodes integrate with the FSM.

## Script Library

BartleBot includes several standard scripts to help users create bots more easily.

### go_to_zone

Transitions clients from one zone to another with failsafes to handle client zone desynchronization and loading failures.

| Parameter Name | Parameter Type | Parameter Description |
|----------------|----------------|-----------------------|
| `from_zone`    | `str`          | The zone to transition from. All selected clients must be in this zone when the node is called.
| `to_zone`      | `str`          | The zone to transition to. If not all selected clients are in this zone when the node is done, it returns FAILURE.
| `xyz`          | `XYZ`          | The XYZ coordinates within the zone transition bounding box. Teleporting clients to this location triggers the transition automatically — no key press needed.

**Implementation:**
- APIs: `client.zone_name()`, `client.teleport(XYZ)`, `client.is_loading()`
- Flow:
  1. Verify all clients are in `from_zone`. If not, return `FAILURE`.
  2. `asyncio.gather` teleport all clients to `xyz`. The zone transition triggers automatically when clients enter the bounding box.
  3. Wait for all clients' zone to change away from `from_zone`, then wait for loading screens to finish.
  4. Check all clients are now in `to_zone`. If any are not (desync), return `FAILURE`.
  5. Return `SUCCESS`.
- `FAILURE` cases: clients not in `from_zone` at start; not all clients reached `to_zone` after transition.

### enter_dungeon

Enters a dungeon via dungeon sigil. Handles client zone desynchronization, sigil detection, and loading failures.

| Parameter Name  | Parameter Type | Parameter Description |
|-----------------|----------------|-----------------------|
| `from_zone`     | `str`          | The zone to transition from. All selected clients must be in this zone when the node is called.
| `dungeon_zone`  | `str`          | The name of the dungeon zone. If not all selected clients are in this zone when the node is done, it returns FAILURE.
| `sigil_xyz`     | `XYZ`          | The XYZ coordinates of the dungeon sigil. The dungeon entrance prompt (not the dungeon warning) should appear when standing in this location.
| `reset_xyz`     | `XYZ`          | The XYZ coordinates of a location within `from_zone` that can be used to dismiss the dungeon entrance prompt. The prompt should **not** appear when standing here.

**Implementation:**
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

### handle_combat

Performs combat with a specified enemy. If no enemy is specified, defaults to the closest valid enemy when the node is called. Combat nodes accept combat configurations specified with the WizSprinter combat DSL, which will be applied for that specific combat instance.

| Parameter Name  | Parameter Type | Parameter Description |
|-----------------|----------------|-----------------------|
| `enemy`         | `str`          | The entity name of the enemy to enter combat against. If omitted, uses the closest valid mob.
| `combat_config` | `str` (optional) | WizSprinter combat DSL config for this encounter. If omitted, falls back to the bot-level `default_combat_config`.
| `combat_order`  | `List[int]` (optional) | Order in which clients enter combat, as 0-based client indices. Example: `[0, 1, 2]` sends client 0 first, then 1, then 2. If omitted, all clients enter simultaneously.

**Implementation:**
- APIs: `SprintyClient.find_closest_by_name()`, `SprintyClient.tp_to()`, `client.in_battle()`, `SprintyCombat`, `StrCombatConfigProvider`
- Flow:
  1. Resolve the target: if `enemy` is provided, use `sprinter.find_closest_by_name(enemy)`; otherwise `sprinter.find_closest_mob()`.
  2. If `combat_order` is specified: teleport clients to the target one at a time in the specified order, waiting for each to report `in_battle()` before sending the next. If omitted, teleport all clients simultaneously via `asyncio.gather`.
  3. Wait until all clients report `in_battle()`.
  4. Resolve combat config: use `params.combat_config` if present, otherwise `machine.bot_config.default_combat_config`.
  5. Run `SprintyCombat(client, StrCombatConfigProvider(config), True).wait_for_combat()` for all clients via `asyncio.gather`.
  6. Return `SUCCESS` once all clients exit combat.
- `FAILURE` cases: target entity not found; combat did not begin within timeout.

### interact

Interacts with an NPC or interactable world item such as a lever, collectable, etc.

| Parameter Name  | Parameter Type | Parameter Description |
|-----------------|----------------|-----------------------|
| `interact_xyz`  | `XYZ`          | The XYZ coordinates of the item to interact with. The interact prompt should show when standing in this location.
| `reset_xyz`     | `XYZ`          | The XYZ coordinates of a location that can be used to reset the interact prompt. The interact prompt should **not** show when standing in this location.

**Implementation:**
- APIs: `client.teleport(XYZ)`, `client.is_in_npc_range()`, `client.is_in_dialog()`, `client.send_key(Keycode.X)`, `client.send_key(Keycode.SPACEBAR)`, window tree helpers for prompt detection
- Flow:
  1. Teleport all clients to `interact_xyz`.
  2. Wait for the interact prompt to appear (check via `client.is_in_npc_range()` or window visibility). If prompt does not appear after timeout, teleport all clients to `reset_xyz` and restart from step 1.
  3. Press X on all clients to begin interaction.
  4. While any client has a dialogue window visible (`client.is_in_dialog()`), repeatedly send SPACE to advance through dialogue. Continue until dialogue is dismissed on all clients.
  5. If the interact prompt never appeared: teleport clients to `reset_xyz` to clear state, then retry from step 1.
  6. Return `SUCCESS` once all dialogue is dismissed and interaction is complete.
- `FAILURE` cases: interact prompt never appeared after retries.

### stop_if_dropped

Stops the bot if a drop is received.

| Parameter Name    | Parameter Type | Parameter Description |
|-------------------|----------------|-----------------------|
| `clients_to_check`| `List[int]`    | 0-based indices of clients to check for the drop.
| `item_names`      | `List[str]`    | Names of items to watch for. Bot stops if any of these are received by any of the specified clients.

**Implementation:**
- APIs: backpack inventory snapshot diff (see drop detection pattern in `DEIMOS_PATTERNS.md`)
- Flow:
  1. Take a backpack snapshot for each client in `clients_to_check` at the start of the node.
  2. Compare to the current backpack contents.
  3. If any item in `item_names` appears in the diff for any checked client, signal the bot to stop and return `FAILURE` (or transition to a terminal stop state).
  4. Otherwise return `SUCCESS` (drop not yet received; bot continues).
- Intended use: placed as a node that runs after each combat or dungeon run, looping back if the item hasn't dropped.

## Behavior Library

BartleBot includes a library of more advanced behaviors that combine multiple actions into sequences that are commonly used.

### handle_potions

Uses potions if clients are below a specified health or mana threshold. Refills potions if needed.

| Parameter Name   | Parameter Type | Parameter Description |
|------------------|----------------|-----------------------|
| `if_health_below`| `float`        | Health threshold (0.0–1.0) below which the client should use a potion.
| `if_mana_below`  | `float`        | Mana threshold (0.0–1.0) below which the client should use a potion.
| `refill_potions` | `bool`         | If `true`, navigate to refill potions when any client runs out.

**Implementation:**
- APIs: `SprintyClient.calc_health_ratio()`, `SprintyClient.calc_mana_ratio()`, `SprintyClient.needs_potion()`, `SprintyClient.use_potion_if_needed()`, `SprintyClient.has_potion()`
- Flow:
  1. For each client, check health and mana ratios.
  2. If either ratio is below the threshold and the client has a potion, use it.
  3. If `refill_potions` is true and a client has no potions, navigate to the potion vendor and refill (vendor location TBD as implementation detail).
  4. Return `SUCCESS` always (this is a maintenance node, not conditional).

### collect_wisps

Collects health and mana wisps after a battle if clients are below a specified health or mana threshold.

| Parameter Name   | Parameter Type | Parameter Description |
|------------------|----------------|-----------------------|
| `if_health_below`| `float`        | Health threshold (0.0–1.0) below which the client should collect health wisps.
| `if_mana_below`  | `float`        | Mana threshold (0.0–1.0) below which the client should collect mana wisps.

**Implementation:**
- APIs: `SprintyClient.calc_health_ratio()`, `SprintyClient.calc_mana_ratio()`, `SprintyClient.tp_to_closest_health_wisp()`, `SprintyClient.tp_to_closest_mana_wisp()`
- Flow:
  1. For each client, check health and mana ratios.
  2. If health is below `if_health_below`, teleport the client to the closest health wisp. Repeat until health is above threshold or no more wisps are nearby.
  3. If mana is below `if_mana_below`, teleport the client to the closest mana wisp. Repeat until mana is above threshold or no more wisps are nearby.
  4. Return `SUCCESS` always (maintenance node).

### quicksell

Checks if backpack is full. If full, quicksells items except for those specified in `keep_list`.

| Parameter Name   | Parameter Type | Parameter Description |
|------------------|----------------|-----------------------|
| `keep_list`      | path           | A path to a `.txt` file containing names of items to keep when quickselling.

**Implementation:**
- APIs: `client.backpack_space()`, window tree helpers for the quicksell UI
- Flow:
  1. Check `client.backpack_space()` for each client. If not full, return `SUCCESS` immediately.
  2. For clients with a full backpack, open the backpack UI.
  3. Iterate items; for each item not in `keep_list`, sell it via the quicksell interface.
  4. Return `SUCCESS` once backpack is no longer full (or all sellable items have been sold).