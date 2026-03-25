"""Wizard101 HUD UI window tree path constants.

These paths are used with node_helpers.is_enabled_by_path(),
is_visible_by_path(), and click_by_path() to interact with
in-game UI elements.
"""

# compassAndTeleporterButtons group
HUB_BUTTON_PATH = ["WorldView", "windowHUD", "compassAndTeleporterButtons", "GoHomeButton"]
GOTO_DORM_BUTTON_PATH = ["WorldView", "windowHUD", "compassAndTeleporterButtons", "GotoDormButton"]
RECALL_BUTTON_PATH = ["WorldView", "windowHUD", "compassAndTeleporterButtons", "RecallButton"]
RECALL_TIMER_PATH = ["WorldView", "windowHUD", "compassAndTeleporterButtons", "RecallButton", "txtRecallTimer"]

# Level-scaling elixir indicator (visible when the elixir is active)
LEVEL_SCALING_ELIXIR_PATH = ["WorldView", "windowHUD", "wndElixirContainer", "sprtElixir1"]

# Level-scaling elixir service menu (appears after interacting with the scaling NPC)
LEVEL_SCALING_OPTION_BUTTON_PATH = [
    "WorldView",
    "NPCServicesWin",
    "ControlSprite",
    "optionsLayout",
    "NPCServicesOptionWindow",
    "OptionButton",
]

# Potion shop UI (appears after interacting with Hilda Brewer in the Commons)
POTION_SHOP_BASE_PATH = ["WorldView", "main"]
POTION_FILL_ALL_PATH = ["WorldView", "main", "fillallpotions"]
POTION_BUY_PATH = ["WorldView", "main", "buyAction"]
POTION_EXIT_PATH = ["WorldView", "main", "exit"]

# Potion usage button in the HUD
POTION_USAGE_PATH = ["WorldView", "windowHUD", "btnPotions"]

# Dungeon entry/exit confirmation dialog
DUNGEON_WARNING_PATH = [
    "MessageBoxModalWindow",
    "messageBoxBG",
    "messageBoxLayout",
    "AdjustmentWindow",
    "Layout",
    "centerButton",
]

# Dungeon entrance prompt (TeamUpButton is present only for dungeon sigils, not generic NPCs)
DUNGEON_ENTRANCE_PATH = ["WorldView", "NPCRangeWin", "imgBackground", "TeamUpButton"]

# Advance button inside the ambient NPC dialog (visible only when a dialog is open and advanceable)
ADVANCE_DIALOG_PATH = ["WorldView", "wndDialogMain", "btnRight"]

# Character select screen ('WorldView/mainWindow' is visible when on the char select screen)
CHAR_SELECT_PATH = ["WorldView", "mainWindow"]
CHAR_SELECT_PLAY_PATH = ["WorldView", "mainWindow", "btnPlay"]
