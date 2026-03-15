"""Wizard101 HUD UI window tree path constants.

These paths are used with node_helpers.is_enabled_by_path(),
is_visible_by_path(), and click_by_path() to interact with
in-game UI elements.
"""

# compassAndTeleporterButtons group
HUB_BUTTON_PATH = ["WorldView", "windowHUD", "compassAndTeleporterButtons", "GoHomeButton"]
RECALL_BUTTON_PATH = ["WorldView", "windowHUD", "compassAndTeleporterButtons", "RecallButton"]
RECALL_TIMER_PATH = ["WorldView", "windowHUD", "compassAndTeleporterButtons", "RecallButton", "txtRecallTimer"]

# Level-scaling elixir indicator (visible when the elixir is active)
LEVEL_SCALING_PATH = ["WorldView", "windowHUD", "wndElixirContainer", "sprtElixir1"]

# Dungeon entry/exit confirmation dialog
DUNGEON_WARNING_PATH = [
    "MessageBoxModalWindow", "messageBoxBG", "messageBoxLayout",
    "AdjustmentWindow", "Layout", "centerButton",
]
