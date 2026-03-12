# Bot File Schema (JSON)

BartleBot bots are saved as `.json` files. The schema is validated via pydantic models on
load and save. Validation errors are surfaced in the GUI.

## Top-Level Structure

```json
{
  "name": "My Bot",
  "description": "Optional description of what this bot does.",
  "version": "1.0",
  "clients": "all",
  "default_combat_config": "any<blade> @ self\nany<damage> @ enemy",
  "states": [ ... ],
  "transitions": [ ... ]
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Display name |
| `description` | string | no | Human-readable description |
| `version` | string | yes | Schema version (currently `"1.0"`) |
| `clients` | `"all"` or list of ints | yes | Which hooked clients to run on. `"all"` = all hooked clients. List of 0-based indices for a subset. |
| `default_combat_config` | string | no | WizSprinter combat DSL config applied to all `handle_combat` nodes that do not define their own `combat_config` param. |
| `states` | array | yes | List of FSM state (node) objects |
| `transitions` | array | yes | List of FSM transition (edge) objects |

## State Object

```json
{
  "id": "enter_dungeon_1",
  "node_type": "enter_dungeon",
  "label": "Enter Dungeon",
  "position": { "x": 100, "y": 200 },
  "params": {
    "from_zone": "WizardCity/WC_Streets/WC_Streets1",
    "dungeon_zone": "WizardCity/Interiors/WC_MtOlympus",
    "sigil_xyz": { "x": -1234.5, "y": 678.9, "z": 0.0 },
    "reset_xyz": { "x": -1100.0, "y": 600.0, "z": 0.0 }
  }
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | Unique identifier within this bot. Used in transitions. |
| `node_type` | string | yes | Matches a standard library node type or registered custom type. |
| `label` | string | no | Display label shown on the node in the GUI editor. Defaults to `node_type`. |
| `position` | `{x, y}` | no | GUI canvas position. Auto-assigned if omitted. |
| `params` | object | no | Node-specific parameters. Keys and types depend on `node_type`. |

### XYZ param format

```json
{ "x": -1234.5, "y": 678.9, "z": 0.0 }
```

## Transition Object

```json
{
  "trigger": "advance",
  "source": "enter_dungeon_1",
  "dest": "fight_first_mob",
  "conditions": [
    { "type": "last_result_is", "params": { "value": "SUCCESS" } }
  ],
  "label": "Dungeon entered"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `trigger` | string | yes | Always `"advance"` for normal FSM flow. |
| `source` | string | yes | Source state `id`, or `"*"` for any state. |
| `dest` | string | yes | Destination state `id`. |
| `conditions` | array | no | List of condition objects. All must be true for this transition to fire. |
| `label` | string | no | Display label on the edge in the GUI editor. |

## Condition Object

```json
{ "type": "last_result_is", "params": { "value": "SUCCESS" } }
```

| `type` | `params` | Description |
|---|---|---|
| `last_result_is` | `{ "value": "SUCCESS" \| "FAILURE" }` | True if the last node returned the specified result. |
| `all_in_zone` | `{ "zone": "zone/path/string" }` | True if all clients are in the specified zone. |
| `any_health_below` | `{ "percent": 0.3 }` | True if any client's health ratio is below the threshold. |
| `any_mana_below` | `{ "percent": 0.1 }` | True if any client's mana ratio is below the threshold. |

## Custom Node Types

Users can define custom node types. A custom type is a Python async function stored in a `.py`
file. The path is registered in the bot file:

```json
{
  "custom_node_types": [
    {
      "type_name": "my_custom_action",
      "file": "./custom_nodes/my_action.py",
      "params_schema": {
        "wait_seconds": { "type": "float", "default": 1.0 }
      }
    }
  ]
}
```

The `.py` file must define a coroutine named `run`:

```python
async def run(machine, clients, params) -> str:
    import asyncio
    await asyncio.sleep(params.get("wait_seconds", 1.0))
    return "SUCCESS"
```

---

## Full Annotated Example: Simple Dungeon Farming Loop

```json
{
  "name": "Mount Olympus Farmer",
  "description": "Farms Mount Olympus repeatedly.",
  "version": "1.0",
  "clients": "all",
  "default_combat_config": "any<blade> @ self\nany<damage> @ enemy",

  "states": [
    {
      "id": "go_to_sigil",
      "node_type": "go_to_zone",
      "label": "Go to Sigil Area",
      "position": { "x": 50, "y": 100 },
      "params": {
        "from_zone": "WizardCity/WC_Streets/WC_Streets1",
        "to_zone": "WizardCity/WC_Streets/WC_Streets1",
        "xyz": { "x": -1234.5, "y": 678.9, "z": 0.0 }
      }
    },
    {
      "id": "enter_dungeon",
      "node_type": "enter_dungeon",
      "label": "Enter Mt. Olympus",
      "position": { "x": 250, "y": 100 },
      "params": {
        "from_zone": "WizardCity/WC_Streets/WC_Streets1",
        "dungeon_zone": "WizardCity/Interiors/WC_MtOlympus",
        "sigil_xyz": { "x": -1234.5, "y": 678.9, "z": 0.0 },
        "reset_xyz": { "x": -1100.0, "y": 600.0, "z": 0.0 }
      }
    },
    {
      "id": "fight_cyclops",
      "node_type": "handle_combat",
      "label": "Fight Cyclops",
      "position": { "x": 450, "y": 100 },
      "params": {
        "enemy": "Cyclops"
      }
    },
    {
      "id": "fight_zeus",
      "node_type": "handle_combat",
      "label": "Fight Zeus",
      "position": { "x": 650, "y": 100 },
      "params": {
        "enemy": "Zeus",
        "combat_config": "any<blade> @ self\n{2} storm lord @ enemy"
      }
    },
    {
      "id": "collect_wisps",
      "node_type": "collect_wisps",
      "label": "Collect Wisps",
      "position": { "x": 850, "y": 100 },
      "params": {
        "if_health_below": 0.8,
        "if_mana_below": 0.5
      }
    },
    {
      "id": "exit_dungeon",
      "node_type": "go_to_zone",
      "label": "Exit Dungeon",
      "position": { "x": 1050, "y": 100 },
      "params": {
        "from_zone": "WizardCity/Interiors/WC_MtOlympus",
        "to_zone": "WizardCity/WC_Streets/WC_Streets1",
        "xyz": { "x": 0.0, "y": 0.0, "z": 0.0 }
      }
    }
  ],

  "transitions": [
    {
      "trigger": "advance",
      "source": "go_to_sigil",
      "dest": "enter_dungeon",
      "conditions": [{ "type": "last_result_is", "params": { "value": "SUCCESS" } }],
      "label": "At sigil"
    },
    {
      "trigger": "advance",
      "source": "enter_dungeon",
      "dest": "fight_cyclops",
      "conditions": [{ "type": "last_result_is", "params": { "value": "SUCCESS" } }],
      "label": "In dungeon"
    },
    {
      "trigger": "advance",
      "source": "fight_cyclops",
      "dest": "fight_zeus",
      "conditions": [{ "type": "last_result_is", "params": { "value": "SUCCESS" } }],
      "label": "Cyclops defeated"
    },
    {
      "trigger": "advance",
      "source": "fight_zeus",
      "dest": "collect_wisps",
      "conditions": [{ "type": "last_result_is", "params": { "value": "SUCCESS" } }],
      "label": "Zeus defeated"
    },
    {
      "trigger": "advance",
      "source": "collect_wisps",
      "dest": "exit_dungeon",
      "label": "Wisps collected"
    },
    {
      "trigger": "advance",
      "source": "exit_dungeon",
      "dest": "go_to_sigil",
      "conditions": [{ "type": "last_result_is", "params": { "value": "SUCCESS" } }],
      "label": "Loop back"
    }
  ]
}
```
