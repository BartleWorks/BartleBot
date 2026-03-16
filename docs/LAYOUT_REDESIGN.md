# Plan: Game Window Embedding (pip-mode)

## Context

BartleBot currently has a fixed 3-column layout (left sidebar | bot canvas | right sidebar) that
leaves most of the vertical sidebar space unused, and places the node properties panel awkwardly
in a small bottom-center tab. This feature adds embedded Wizard101 game window(s) directly into
BartleBot via Win32 SetParent, enabling bot development without alt-tabbing. The scope also
includes a companion layout redesign, a persistent settings system, account credential management,
and named launch profiles.

---

## 1. Layout Redesign

### Top-level structure

The window is divided into **two horizontal rows** via an outer vertical `QSplitter`:

```
┌──────────────────────────────────────────────────────────┐
│ Top row: QSplitter (Horizontal)                          │
│  ┌────────────┐  ┌──────────────────────┐  ┌──────────┐ │
│  │            │  │                      │  │          │ │
│  │   Left     │  │      GameView        │  │  Right   │ │
│  │  Sidebar   │  │  (embedded clients)  │  │ Sidebar  │ │
│  │            │  │                      │  │          │ │
│  └────────────┘  └──────────────────────┘  └──────────┘ │
├──────────────────────────────────────────────────────────┤
│ Bottom row: BotCanvas (full window width)                │
│                                                          │
│           Node Graph (FSM editor / viewer)               │
└──────────────────────────────────────────────────────────┘
```

- **Outer vertical QSplitter**: top widget = top-row splitter; bottom widget = BotCanvas
- **Top-row horizontal QSplitter**: left sidebar | GameView | right sidebar
- Sidebars span only the top half (they do NOT extend behind the node canvas)
- BotCanvas spans the **full window width**

### When no game embedded
- `GameView` is hidden (zero height / collapsed); BotCanvas expands vertically to fill the window
- The top-row splitter collapses when no game is embedded

### Left sidebar — tab-based redesign
Replace the current `QScrollArea`-with-sections with a `QTabWidget`:

| Tab | Content |
|-----|---------|
| **Bot** | BotInfoSection + OptionsSection + CombatConfigSection (existing collapsible sections, each scrollable internally) |
| **Properties** | `_ParamPanel` (moved from BotCanvas). Auto-focused when node/edge selected in Edit mode. Disabled in Run mode. |
| **Log** | `QPlainTextEdit` log (moved from BotCanvas). Populated during bot runs. |

### BotCanvas simplification
- Remove the inner vertical `QSplitter` and `_bottom_tabs` (Properties and Log move to left sidebar)
- `_view` (graph view) + `_toolbar_stack` are the sole contents of BotCanvas
- BotCanvas still handles mode switching (RUN / EDIT) and toolbar

### Right sidebar — unchanged

---

## 2. Game Window Embedding

### Resolution philosophy
BartleBot does **not** programmatically resize the game window. The user must set Wizard101 to
Windowed mode in **Options → Graphics** with a resolution matching the value chosen in Settings.
The setting is stored as a reference used to:
1. Enforce minimum window size (so the game fits)
2. Show a reminder string in the Settings → Game Embed tab

Valid Wizard101 resolutions (settings dropdown):
`800×600`, `1024×768`, `1280×720`, `1280×800`, `1280×960`, `1280×1024`, `1366×768`, `1600×900`, `1920×1080`

### Minimum window size
Derived from the configured game resolution + sidebar widths:
```
min_width  = left_sidebar_min + game_width  + right_sidebar_min + splitter_handles
min_height = game_height + botcanvas_min_height + menu/status bars
```
`SettingsManager` computes this and `MainWindow` applies it via `setMinimumSize()` on startup and
after settings change.

### `bartlebot/gui/game_embed/game_view.py` — `GameView(QWidget)`
- Uses system default background color
- Contains N `ClientTile` widgets arranged in a grid:
  - 0 clients → centered placeholder label ("No game window")
  - 1 client → 1×1
  - 2 clients → 1×2
  - 3–4 clients → 2×2
- `embed_client(hwnd, client_idx)` — creates/shows a tile for the given client
- `unembed_client(client_idx)` — unembed and restore the game window
- `toggle()` — show/hide GameView; notifies outer splitter to adjust
- `cycle_focus()` — moves OS focus to the next embedded client in index order
- On `resizeEvent`: recompute tile geometry for all tiles

### `bartlebot/gui/game_embed/client_tile.py` — `ClientTile(QWidget)`
- System default background (black bars visible where game doesn't fill tile)
- On embed:
  1. Save `orig_parent` and `orig_style` (for restoration)
  2. Strip window decorations via `SetWindowLongW`
  3. `SetParent(game_hwnd, tile_hwnd)` to reparent
  4. Center game at its **native resolution** (`MoveWindow` with offset, not resize)
- On `resizeEvent`: recompute centered offset, call `MoveWindow` (repositions, does not resize game)
- On unembed: `SetParent(game_hwnd, orig_parent)`, restore `orig_style`

Win32 logic (stdlib `ctypes` only — no pywin32):
```python
import ctypes
GWL_STYLE    = -16
WS_CHILD     = 0x40000000
WS_VISIBLE   = 0x10000000
WS_BORDER_MASK = 0x00CF0000  # clears WS_CAPTION | WS_BORDER | WS_DLGFRAME

user32    = ctypes.windll.user32
tile_hwnd = int(self.winId())

orig_parent = user32.GetParent(game_hwnd)
orig_style  = user32.GetWindowLongW(game_hwnd, GWL_STYLE)

user32.SetParent(game_hwnd, tile_hwnd)
new_style = (orig_style & ~WS_BORDER_MASK) | WS_CHILD | WS_VISIBLE
user32.SetWindowLongW(game_hwnd, GWL_STYLE, new_style)

gw, gh = native_game_w, native_game_h
x = (self.width()  - gw) // 2
y = (self.height() - gh) // 2
user32.MoveWindow(game_hwnd, x, y, gw, gh, True)
```

### Getting the game HWND
WizSprinter's `SprintyClient` exposes `client.window_handle` (a raw HWND integer, consistent with
the pattern in `launch_wizard101.py` where `Client(handle)` takes the same value).
`client_loop` in `bartlebot.py` already tracks all hooked clients → on each new client, call
`window.game_view.embed_client(client.window_handle, client_idx)`.

### Client visual indicators
Each `ClientTile` has:
- **Label overlay**: "Client 1", "Client 2", etc. painted in the top-left (semi-transparent dark
  pill via `QPainter` in `paintEvent`, drawn above the embedded HWND)
- **Focus border**: 2px colored border (Qt stylesheet or `paintEvent`) applied when
  `GetForegroundWindow()` returns the embedded HWND or any of its children; polled via a
  `QTimer` at ~250ms intervals in `GameView`

---

## 3. Settings System

### `bartlebot/settings_manager.py` — `SettingsManager`
- Singleton; loads/saves `%APPDATA%/BartleBot/settings.json`
- Creates `%APPDATA%/BartleBot/` on first run

Schema:
```json
{
  "game_embed": {
    "resolution": [800, 600]
  },
  "keybindings": {
    "toggle_embed":     "Ctrl+G",
    "next_client":      "Ctrl+Tab",
    "run_bot":          "F9",
    "pause_bot":        "F10",
    "stop_bot":         "F11",
    "launch_profile_0": null,
    "launch_profile_1": null
  },
  "accounts": [
    { "username": "joust", "alias": "StreamSafe" }
  ],
  "launch_profiles": [
    { "name": "Solo", "accounts": ["joust"] }
  ]
}
```

### `bartlebot/gui/settings/settings_dialog.py` — `SettingsDialog(QDialog)`
Modal dialog; opened from **Settings** menu entry (shortcut `Ctrl+,`). Four tabs:

| Tab | Contents |
|-----|----------|
| General | (reserved) |
| Game Embed | Resolution dropdown. Helper text: "Set Wizard101 to Windowed mode in Options → Graphics, with this resolution." |
| Accounts | Table: Alias / Censored Username / (hidden password). Add/Edit/Delete. Add/Edit opens an inline form: alias (optional), username, password. |
| Keybindings | Two-column table (Action / Shortcut). Click a shortcut cell → press keys to rebind. |

Sub-tab widgets:
- `bartlebot/gui/settings/game_embed_tab.py`
- `bartlebot/gui/settings/accounts_tab.py`
- `bartlebot/gui/settings/keybindings_tab.py`

---

## 4. Account Credentials

### `bartlebot/credentials.py` — `CredentialStore`
- `keyring` library (Windows Credential Manager backend on Win32)
- Service name: `"BartleBot"`
- `store(username, password)` → `keyring.set_password("BartleBot", username, password)`
- `retrieve(username)` → `keyring.get_password("BartleBot", username)`
- `delete(username)` → `keyring.delete_password("BartleBot", username)`

### Account display in GUI
- If alias set → show alias
- If no alias → censor: first char + `***` + last char
  - `joust` → `j***t`, `ab` → `a***b`, single char `x` → `x***`
- Password never stored in `settings.json`, never shown in GUI

---

## 5. Launch Profiles & Launcher

### `bartlebot/launcher/launch_profile.py`
Reuses `wizwalker.utils`: `get_all_wizard_handles`, `start_instance`, `instance_login`, `set_window_title`

```python
async def launch_account(username: str, password: str, display_name: str) -> None:
    # Poll for new HWND, activate root window hook,
    # wait for LoginWindow, call instance_login, set_window_title

async def launch_profile(profile_name: str, settings_mgr, cred_store) -> None:
    # Resolve accounts → credentials; asyncio.gather all launch_account calls
```

`launch_wizard101.py` kept intact as standalone CLI fallback.

### UI entry point
- Menu: **Tools → Launch…** → small non-modal popup listing one button per profile
- Each profile bindable in Settings → Keybindings (`launch_profile_0`, `launch_profile_1`, …)

---

## 6. Keybindings

### `bartlebot/gui/keybindings.py` — `KeybindingManager`
```python
def install(window, game_view, bot_canvas, launch_fn): ...  # creates QShortcut instances
def reload(): ...  # destroys old shortcuts, re-installs from updated settings
```
Actions:
- `toggle_embed` → `game_view.toggle()`
- `next_client` → `game_view.cycle_focus()`
- `run_bot / pause_bot / stop_bot` → `bot_canvas` toolbar methods
- `launch_profile_N` → `asyncio.ensure_future(launch_profile(profiles[N], ...))`

---

## 7. Critical Files

| File | Change |
|------|--------|
| `bartlebot/gui/main_window.py` | Rework `_build_central`: outer vertical splitter → top-row (L sidebar \| GameView \| R sidebar) + BotCanvas (full width). Add Settings menu. Wire `setMinimumSize` from settings. |
| `bartlebot/gui/canvas/bot_canvas.py` | Remove inner vertical splitter + `_bottom_tabs`. `_view` fills full canvas. Extract `_ParamPanel` and log widget (returned as separate objects for the sidebar to own). |
| `bartlebot/gui/sidebar/left_sidebar.py` | Rewrite as `QTabWidget`: Bot tab (existing sections), Properties tab, Log tab. |
| `bartlebot.py` | Wire `GameView.embed_client()` on client hook; wire `SettingsManager`, `KeybindingManager`, launcher callbacks. |
| `pyproject.toml` | Add `keyring = "*"`. |

### New files
```
bartlebot/
  settings_manager.py
  credentials.py
  keybindings.py
  launcher/
    __init__.py
    launch_profile.py
  gui/
    game_embed/
      __init__.py
      game_view.py
      client_tile.py
    settings/
      __init__.py
      settings_dialog.py
      game_embed_tab.py
      accounts_tab.py
      keybindings_tab.py
```

---

## 8. Implementation Order

1. `SettingsManager` — no UI deps, needed by everything
2. Layout redesign — `main_window.py`, `bot_canvas.py`, `left_sidebar.py`
3. `ClientTile` + `GameView` — Win32 embed, tiled grid, overlays, focus polling
4. Wire embedding in `bartlebot.py` — HWND extraction, embed/unembed on client hook/unhook
5. `CredentialStore` — keyring integration
6. `LaunchProfile` + launcher logic
7. `SettingsDialog` tabs — Game Embed → Accounts → Keybindings
8. `KeybindingManager` — install shortcuts on `MainWindow`

---

## 9. Dependencies to Add

- `keyring` — Windows Credential Manager (pure Python wrapper, no extra DLLs)
- No pywin32 needed — Win32 calls via stdlib `ctypes.windll`

---

## 10. Verification

- BartleBot launches with no game → GameView hidden, BotCanvas fills full window
- Hook Wizard101 at 800×600 windowed → game appears embedded, centered in tile, "Client 1" overlay visible
- Hook second client → grid updates to 1×2, both labeled
- Click a tile → focus border appears on that tile
- `Ctrl+G` → GameView hidden, BotCanvas expands; repeat → GameView reappears
- `Ctrl+Tab` → focus cycles to next embedded client
- Select node in Edit mode → left sidebar auto-switches to Properties tab
- Settings → Game Embed → change to 1280×720 → minimum window size updates accordingly
- Settings → Accounts → add "joust" (no alias) → shown as `j***t`; add with alias "StreamSafe" → shown as "StreamSafe"
- Create launch profile, bind `Ctrl+1` → press shortcut → Wizard101 launches and auto-logs in
- `F9` / `F10` / `F11` trigger Run / Pause / Stop
