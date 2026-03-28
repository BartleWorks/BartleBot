"""Wizard101 HUD UI window tree path constants.

These paths are used with node_helpers.is_enabled_by_path(),
is_visible_by_path(), and click_by_path() to interact with
in-game UI elements.
"""

# compassAndTeleporterButtons group
HUB_BUTTON_PATH = ["WorldView", "windowHUD", "compassAndTeleporterButtons", "GoHomeButton"]
GOTO_DORM_BUTTON_PATH = ["WorldView", "windowHUD", "compassAndTeleporterButtons", "GotoDormButton"]
MARK_BUTTON_PATH = ["WorldView", "windowHUD", "compassAndTeleporterButtons", "MarkButton"]
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

# Backpack / Quicksell UI
BACKPACK_CLOSE_BUTTON_PATH = ["WorldView", "DeckConfiguration", "Close_Button"]

# Quicksell entry buttons — one per backpack type (main inventory vs. jewel pouch)
QUICKSELL_INVENTORY_OPEN_PATH = ["WorldView", "DeckConfiguration", "InventorySpellbookPage", "QuickSell_Item"]
QUICKSELL_JEWEL_OPEN_PATH = [
    "WorldView",
    "DeckConfiguration",
    "JewelSpellbookPage",
    "windowForBtns",
    "Layout",
    "QuickSell_Item",
]

# Jewel pouch capacity indicator — text is "X/Y" (e.g. "100/100" when full)
JEWEL_INVENTORY_SPACE_PATH = ["WorldView", "DeckConfiguration", "JewelSpellbookPage", "inventorySpace"]

# Shared quicksell shopGUI paths — same UI regardless of which page opened it
QUICKSELL_ALL_BUTTON_PATH = ["WorldView", "shopGUI", "buyWindow", "AllAction"]
QUICKSELL_SELL_BUTTON_PATH = ["WorldView", "shopGUI", "buyWindow", "sellAction"]
QUICKSELL_CONFIRM_SELL_PATH = ["WorldView", "shopGUI", "QuickSellConfirmationWindow", "SellButton"]
QUICKSELL_NEXT_PAGE_PATH = ["WorldView", "shopGUI", "buyWindow", "rightscroll"]

# Gold limit exceeded warning dialog (shares structure with dungeon warning but different context)
GOLD_LIMIT_OK_PATH = [
    "MessageBoxModalWindow",
    "messageBoxBG",
    "messageBoxLayout",
    "AdjustmentWindow",
    "Layout",
    "centerButton",
]


def quicksell_item_title_path(row: int) -> list[str]:
    """Return the window path to the item title label for the given quicksell row (0–9)."""
    return ["WorldView", "shopGUI", "buyWindow", "column0", f"shoplist{row}"]


def quicksell_item_checkbox_path(row: int) -> list[str]:
    """Return the window path to the sell checkbox for the given quicksell row (0–9)."""
    return ["WorldView", "shopGUI", "buyWindow", "column1", f"num{row}"]
