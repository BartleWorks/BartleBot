# WizWalker Reference

WizWalker is the foundational library. It exposes Wizard101's game state by reading/writing process memory via `pymem` and injecting small code hooks.

**Location:** `https://github.com/Deimos-Wizard101/wizwalker`
**Version:** 1.8.2
**Key dependencies:** pymem, loguru, aiofiles, click

---

## Client (`client.py`)

The `Client` class represents one connected Wizard101 game instance.

### Important properties (async reads from memory)

```python
client.stats          # CurrentGameStats — health, mana, gold, level, xp
client.body           # CurrentActorBody — position (XYZ), orientation (Orient)
client.duel           # CurrentDuel — battle state, phase, participants
client.quest_position # CurrentQuestPosition
client.client_object  # CurrentClientObject
client.root_window    # CurrentRootWindow — root of the UI widget tree
client.render_context # CurrentRenderContext
client.game_client    # CurrentGameClient
```

### Key methods

```python
await client.activate_hooks(wait_for_ready=True)
await client.teleport(position: XYZ)
await client.goto(position: XYZ)          # walk to position
await client.in_battle() -> bool
await client.get_base_entity_list() -> List[DynamicClientObject]
await client.get_base_entities_with_name(name: str) -> List[DynamicClientObject]
```

### Window handle / identity

```python
client.window_handle  # HWND integer
client.process_id     # OS PID
client.title          # Window title string
```

---

## ClientHandler (`client_handler.py`)

Manages a collection of `Client` objects across multiple game windows.

```python
handler = ClientHandler()
await handler.get_new_clients()       # discover new game windows
await handler.remove_dead_clients()   # prune closed windows
await handler.get_ordered_clients()   # sort by screen position
await handler.activate_all_client_hooks()
await handler.activate_all_client_mouseless()
```

---

## Memory System (`memory/`)

### Hook System (`memory/handler.py`, `memory/hooks.py`)

WizWalker injects a small code stub (the "autobot") into the game process and then sets up hooks that run on each game frame to capture live state. Hook types:

| Hook | Captured Data |
|------|--------------|
| `ClientHook` | General client state |
| `PlayerHook` | Player entity position/rotation |
| `PlayerStatHook` | HP, mana, gold |
| `QuestHook` | Active quest pointer |
| `RootWindowHook` | UI root window pointer |
| `RenderContextHook` | Camera/render state |
| `MovementTeleportHook` | Enables teleport injection |

### Memory Objects (`memory/memory_objects/`)

48 files, each representing a type of game object. All inherit from `MemoryObject` and use async property reads. Key examples:

| File | Class | Notable Fields |
|------|-------|---------------|
| `actor_body.py` | `ActorBody` | position (XYZ), orientation (Orient) |
| `combat_participant.py` | `CombatParticipant` | owner_id, pip_count, shadow_pips |
| `duel.py` | `Duel` | phase (DuelPhase), round_num, participants |
| `window.py` | `Window` | name, flags, children, parent |
| `game_object_template.py` | `GameObjectTemplate` | object_name, display_name |
| `npc_behavior.py` | `NpcBehavior` | — |
| `quest_data.py` | `QuestData` | goal_id, objectives |

---

## Combat System (`combat/`)

### `CombatHandler` (abstract base, `combat/handler.py`)

Subclass this and implement `handle_round()` to create a combat strategy.

```python
class MyCombat(CombatHandler):
    async def handle_round(self):
        cards = await self.get_cards()
        # choose and cast a card
        ...

handler = MyCombat(client)
await handler.handle_combat()   # blocks until combat ends
```

### Key methods

```python
await handler.handle_combat()         # main loop — waits for planning, calls handle_round, repeats
await handler.wait_for_planning_phase()
await handler.get_cards() -> List[CombatCard]
await handler.get_cards_with_predicate(pred) -> List[CombatCard]
await handler.get_cards_with_name(name) -> List[CombatCard]
await handler.get_members() -> List[CombatMember]
await handler.get_members_with_predicate(pred) -> List[CombatMember]
```

### `CombatCard` (`combat/card.py`)

```python
card.spell_id          # int
await card.name()      # str
await card.flags()     # set of flag strings: enchanted, treasure, item, cloaked
await card.cast(target: CombatMember)
await card.get_spell_effects()  # List[SpellEffect]
```

### `CombatMember` (`combat/member.py`)

```python
member.owner_id
await member.is_friendly() -> bool
await member.is_player() -> bool
await member.get_participant()  # CombatParticipant memory object
await member.get_stats()        # CurrentStats
```

### `DuelPhase` enum

```python
DuelPhase.planning
DuelPhase.executing
DuelPhase.ended
```

---

## Core Data Types (`utils.py`)

```python
XYZ(x, y, z)            # Named tuple with .distance_to(other) method
Orient(pitch, roll, yaw) # Named tuple
Rectangle(x1, y1, x2, y2)
Keycode                  # Enum of all keyboard key codes (used in scripts)
```

---

## File Readers (`file_readers/`)

| Module | Purpose |
|--------|---------|
| `cache_handler.py` | Read Wizard101 cache/wiz files |
| `nif.py` | Parse NIF geometry files |
| `wad.py` | Extract/modify WAD archives |

---

## Error Handling (`errors.py`)

Custom exceptions: `WizWalkerMemoryError`, `HookNotActive`, `ClientClosedError`, etc.

---

## Important Notes

- **All game reads are async** — always `await` property accesses on memory objects.
- Pattern scanning is used at hook setup time; hooks must be activated before reading state.
- The `MemoryReader` has typed read methods matching the `Primitive` enum (int8, int32, float, bool, string, etc.).
