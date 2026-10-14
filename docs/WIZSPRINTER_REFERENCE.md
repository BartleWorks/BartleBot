# WizSprinter Reference

WizSprinter extends WizWalker with practical high-level utilities for entity finding, safe teleportation, resource management, and a configurable combat system with its own DSL.

**Location:** `https://github.com/Deimos-Wizard101/wizsprinter`
**Version:** 0.5.5
**Key dependencies:** wizwalker, lark (parser)

---

## WizSprinter (`wiz_sprinter.py`)

Enhanced `ClientHandler` that returns `SprintyClient` objects instead of plain `Client`.

```python
sprinter = WizSprinter()
await sprinter.get_new_clients()      # returns List[SprintyClient]
await sprinter.get_ordered_clients()  # sorted by screen position
WizSprinter.upgrade_clients(clients)  # dynamically re-class existing clients
```

---

## SprintyClient (`sprinty_client.py`)

The primary interface for scripting. Extends `wizwalker.Client`.

### Entity Finding

```python
# All return List[DynamicClientObject]
await client.get_base_entities_with_predicate(predicate, excluded_ids=[])
await client.get_base_entities_with_vague_name(name, excluded_ids=[])   # substring match
await client.get_base_entities_with_behaviors(behaviors, excluded_ids=[])
await client.get_health_wisps()
await client.get_mana_wisps()
await client.get_mobs()
```

### Finding the Closest Entity

```python
# All return Optional[DynamicClientObject]
await client.find_closest_of_entities(entities, only_safe=True)
await client.find_closest_by_predicate(pred, only_safe=True, excluded_ids=[])
await client.find_closest_by_name(name, only_safe=True, excluded_ids=[])
await client.find_closest_by_vague_name(name, only_safe=True, excluded_ids=[])
await client.find_closest_health_wisp()
await client.find_closest_mana_wisp()
await client.find_closest_mob()
```

`only_safe=True` filters out entities near mobs (uses `find_safe_entities_from`).

### Teleportation

```python
await client.tp_to(entity)
await client.tp_to_closest_of(entities)
await client.tp_to_closest_by_name(name)
await client.tp_to_closest_by_vague_name(name)
await client.tp_to_closest_health_wisp()
await client.tp_to_closest_mana_wisp()
await client.tp_to_closest_mob()
```

### Resource / Potion Management

```python
await client.calc_health_ratio() -> float    # 0.0–1.0
await client.calc_mana_ratio() -> float      # 0.0–1.0
await client.has_potion(health_threshold, mana_threshold) -> bool
await client.use_potion(health=True, mana=True)
await client.use_potion_if_needed()
```

### Safety Utilities

```python
await client.find_safe_entities_from(entities, safe_distance=700.0) -> List
# Returns entities that are at least safe_distance away from all mobs
```

---

## SprintyCombat (`sprinty_combat.py`)

Configurable combat handler extending `wizwalker.CombatHandler`. Uses a backend to decide which spell to cast each round.

```python
from wizsprinter import SprintyCombat
from wizsprinter.combat_backends.config_backend import CombatConfigProvider

provider = CombatConfigProvider("my_config.txt")
combat = SprintyCombat(client, provider)
await combat.handle_combat()
```

### Effect Categorization

SprintyCombat classifies spell effects to match them against config requirements:

| Category | Effect Types |
|----------|-------------|
| Damage | damage, damage_no_crit, damage_over_time, instant_kill, steal_health |
| Heal | heal, heal_by_ward, heal_over_time, heal_percent |
| Outgoing buff | modify_outgoing_damage, modify_outgoing_damage_flat, modify_outgoing_heal |
| Incoming debuff | modify_incoming_damage, modify_incoming_damage_flat, modify_incoming_armor_piercing |
| Special | blade, charm, ward, trap, aura, global, shadow, polymorph, pierce, prism, dispel |

### Key Internal Methods

```python
await combat.get_inner_card_effects(card)         # expands compound/conditional spell effects
await combat.is_enchantable(card) -> bool
await combat.is_req_satisfied(effect, spell_type, template, allow_aoe) -> bool
```

---

## Combat Backends (`combat_backends/`)

### Core Data Types (`combat_api.py`)

```python
class TargetType(Enum):
    type_self    # cast on yourself
    type_boss    # cast on boss enemy
    type_enemy   # cast on any enemy
    type_ally    # cast on any ally
    type_aoe     # area-of-effect (hits all enemies)
    type_named   # cast on a specific named target

class SpellType(Enum):
    damage, heal, blade, charm, ward, trap, enchant, aura, global_
    polymorph, shadow, shadow_creature, pierce, prism, dispel
    mod_damage, mod_heal, mod_pierce
    # 22 total types

@dataclass
class NamedSpell:
    name: str
    is_literal: bool = False    # True = exact name match, False = substring

@dataclass
class TemplateSpell:
    required_types: Set[SpellType]   # card must have ALL of these types

@dataclass
class Move:
    card: NamedSpell | TemplateSpell
    enchant: Optional[NamedSpell | TemplateSpell] = None

@dataclass
class MoveConfig:
    move: Move
    target: TargetType

PriorityLine = List[MoveConfig]   # try each in order until one succeeds

@dataclass
class CombatConfig:
    specific_rounds: Dict[int, PriorityLine]   # keyed by round number
    repeating_rounds: List[PriorityLine]        # cycles after specific rounds exhausted
```

### CombatConfigProvider (`config_backend.py`)

Reads a `.txt` config file using the DSL grammar and exposes `PriorityLine` per round.

```python
provider = CombatConfigProvider("path/to/config.txt")
# or
provider = StrCombatConfigProvider(config_string)  # Deimos variant

priority_line = provider.get_round(round_number)   # returns PriorityLine
```

### CombatConfigGenerator (`generator_backend.py`)

Programmatic config builder — alternative to the text DSL.

---

## Combat Config DSL

The config DSL is parsed by a Lark grammar in `combat_config_parser.py`.

### Syntax

```
# Round-specific lines (optional round number)
{1} move_expr @ target | move_expr @ target | pass
{2} move_expr @ target
# Infinite repeating lines (no round number)
move_expr @ target | move_expr @ target
```

### Move Expression Syntax

```
# Named spell (substring match by default)
"Tempest" @ aoe

# Template spell (matches by effect types)
any<damage> @ enemy
any<heal> @ self
any<trap & inc_damage> @ enemy        # AND of types
any<blade & out_damage>[sharp] @ self  # with enchant

# Negation with ! (combined with &)
any<trap & !aoe> @ enemy              # a trap, but not an AoE one
any<trap & !feint> @ boss             # a trap, but no card whose name contains "feint"
any<trap & !"Feint"> @ boss           # a trap, but not the card named exactly Feint

# Enchantment variants
any<damage>[epic] @ enemy             # enchant with any<enchant> named "epic"
any<heal>[potent] @ self
```

`!word` negates a spell type when `word` is one, and excludes a card by name otherwise.
An unquoted name is the same loose, case-insensitive substring match a bare spell move
uses, so `!feint` also rules out Mass Feint (`Feint Mass - Amulet`); a quoted name is
exact. A negated type rules out every card with that effect, AoE or not.

`&` is an AND over the card's effects, not within one effect: each type needs *some*
effect that satisfies it. `aoe` is about whom an effect targets, not what it does — it
matches any effect aimed at a whole team, either side: AoE damage, a mass trap or charm,
a team blade, ward or heal (not an aura, which targets self, or a global). So
`any<trap & aoe>` is a mass trap, `any<blade & aoe>` a team blade, and `any<damage & aoe>`
an AoE hit. Blade, charm, ward and trap match single-target and team effects alike, so
bare `any<trap>` includes mass traps; write `any<trap & !aoe>` for single-target only.
With `aoe` in the template, a card with a single-target damage effect on an enemy is
rejected (Storm Beetle), and the matches are ranked by AoE damage, which leaves a
non-damage clause in hand order.
`stacks` keeps a buff only when it would add to what already hangs where it lands:
`any<blade & stacks>[sharp] @ ally(last)`. Two effects with the same spell template id and
the same enchant template id don't stack, whoever cast them, and the move's own `[...]`
enchant counts. A mass card stacks while one living member of its side lacks it.
wizsprinter's `CONDITIONS.md` has the rules.
`aura` is an aura on the caster (Amplify, Stabilize, Mend); `harmful_aura` is one cast at
an enemy (Attenuate: +20% incoming damage for four rounds). A trap has no rounds, so
neither matches one. `stronger` keeps an aura card only when it beats, for the caster's
side, the aura its target already holds: a participant holds one aura and a new one
replaces it, so `any<harmful_aura & inc_damage & stronger> @ boss` replaces the boss's own
Frenzy or a weaker Attenuate, and never a stronger one.
`req_met`, `stacks`, `stronger` and the hanging verbs (`gambit(…)` and its siblings) cannot be
negated — they are checked after a card is picked, so there is nothing for the negation
to test.

### Target Types

| Token | Meaning |
|-------|---------|
| `self` | Cast on yourself |
| `enemy` | Cast on any single enemy |
| `boss` | Cast on the boss specifically |
| `ally` | Cast on any ally |
| `aoe` | Area-of-effect (all enemies) |
| `Nightshade`, `"Lord Nightshade"` | Cast on the combatant whose name contains it (there is no `named:` prefix) |

### Separator / Priority

`|` separates moves in priority order. First viable move is cast. `pass` skips the round.

### Multi-Client Format (Deimos extension in `src/config_combat.py`)

```
### p1
{1} any<damage>[epic] @ enemy
any<blade> @ self

### p2
{1} any<heal>[potent] @ self
any<trap> @ enemy
```

Each `### p<n>` section is parsed separately and assigned to that client number.

---

## Navigator (`wiz_navigator.py`)

```python
from wizsprinter import toZone, toZoneDisplayName

zone_id = toZone("WizardCity/WC_Hub")
display_name = toZoneDisplayName(zone_id)
```

---

## Notes

- `SprintyClient` dynamically replaces the `__class__` attribute of existing `Client` objects — no re-instantiation needed.
- `find_safe_entities_from` uses a default `safe_distance=700.0` game units.
- The combat backend system is pluggable — you can write your own by subclassing `BaseCombatBackend`.
