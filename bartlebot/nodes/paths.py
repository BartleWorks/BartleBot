"""Wizard101 HUD UI window tree path constants.

These paths are used with node_helpers.is_enabled_by_path(),
is_visible_by_path(), and click_by_path() to interact with
in-game UI elements.

All Wizard101 modal message boxes share the same window tree structure.
Use MESSAGE_BOX_CENTER / MESSAGE_BOX_RIGHT / MESSAGE_BOX_LEFT instead of
defining per-context copies. Read MESSAGE_BOX_CAPTION_PATH to determine
which dialog is showing when the same path is shared by multiple contexts.
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

# Generic modal message box buttons — shared by all in-game message boxes.
# YES / OK typically use centerButton; NO / Cancel typically use rightButton.
# Read MESSAGE_BOX_CAPTION_PATH to identify which dialog is showing.
MESSAGE_BOX_LEFT = [
    "MessageBoxModalWindow",
    "messageBoxBG",
    "messageBoxLayout",
    "AdjustmentWindow",
    "Layout",
    "leftButton",
]
MESSAGE_BOX_CENTER = [
    "MessageBoxModalWindow",
    "messageBoxBG",
    "messageBoxLayout",
    "AdjustmentWindow",
    "Layout",
    "centerButton",
]
MESSAGE_BOX_RIGHT = [
    "MessageBoxModalWindow",
    "messageBoxBG",
    "messageBoxLayout",
    "AdjustmentWindow",
    "Layout",
    "rightButton",
]
MESSAGE_BOX_CAPTION_PATH = ["MessageBoxModalWindow", "messageBoxBG", "messageBoxLayout", "TextArea", "CaptionText"]

# Team-up paths
DUNGEON_ENTRANCE_TEAMUP_BUTTON_PATH = [
    "WorldView",
    "NPCRangeWin",
    "imgBackground",
    "TeamUpButton",
]  # Also used to detect dungeon entrance
TEAMUP_2_PLAYER_CHECKBOX = [
    "WorldView",
    "TeamUpConfirmationWindow",
    "TeamUpConfirmationBackground",
    "TeamSize2CheckBox",
]
TEAMUP_3_PLAYER_CHECKBOX = [
    "WorldView",
    "TeamUpConfirmationWindow",
    "TeamUpConfirmationBackground",
    "TeamSize3CheckBox",
]
TEAMUP_4_PLAYER_CHECKBOX = [
    "WorldView",
    "TeamUpConfirmationWindow",
    "TeamUpConfirmationBackground",
    "TeamSize4CheckBox",
]
ENTER_TEAMUP_BUTTON_PATH = ["WorldView", "TeamUpConfirmationWindow", "TeamUpConfirmationBackground", "TeamUpButton"]

# Gauntlet entrance prompt (no TeamUpButton — gauntlets use the parent window directly)
GAUNTLET_ENTRANCE_PATH = ["WorldView", "NPCRangeWin", "imgBackground"]

# Advance button inside the ambient NPC dialog (visible only when a dialog is open and advanceable)
ADVANCE_DIALOG_PATH = ["WorldView", "wndDialogMain", "btnRight"]

# Character select screen ("WorldView/mainWindow" is visible when on the char select screen)
CHAR_SELECT_PATH = ["WorldView", "mainWindow"]
CHAR_SELECT_PLAY_PATH = ["WorldView", "mainWindow", "btnPlay"]

# Realm selection UI — accessed via Spellbook > Preferences > Realms tab
SPELLBOOK_BUTTON_PATH = ["WorldView", "windowHUD", "btnSpellbook"]
SPELLBOOK_PREFERENCES_TAB_PATH = ["WorldView", "DeckConfiguration", "Preferences"]
SPELLBOOK_REALMS_BUTTON_PATH = ["WorldView", "DeckConfiguration", "SettingPage", "TabWindow", "RealmsButton"]
REALM_CURRENT_NAME_PATH = ["WorldView", "DeckConfiguration", "SettingPage", "RealmOptions", "txtYourRealmName"]
REALM_NEXT_PAGE_PATH = ["WorldView", "DeckConfiguration", "SettingPage", "RealmOptions", "btnRealmRight"]
REALM_PREV_PAGE_PATH = ["WorldView", "DeckConfiguration", "SettingPage", "RealmOptions", "btnRealmLeft"]
REALM_GO_TO_BUTTON_PATH = ["WorldView", "DeckConfiguration", "SettingPage", "RealmOptions", "btnGoToRealm"]
REALM_PAGE_LABEL_PATH = ["WorldView", "DeckConfiguration", "SettingPage", "RealmOptions", "txtRealmPage"]


def realm_name_path(row: int) -> list[str]:
    """Return the path to the realm name label for the given row (0–6)."""
    return [
        "WorldView",
        "DeckConfiguration",
        "SettingPage",
        "RealmOptions",
        "wndRealmPanel",
        f"btnRealm{row}",
        f"txtRealm{row}Name",
    ]


def realm_button_path(row: int) -> list[str]:
    """Return the path to the selectable realm button for the given row (0–6)."""
    return [
        "WorldView",
        "DeckConfiguration",
        "SettingPage",
        "RealmOptions",
        "wndRealmPanel",
        f"btnRealm{row}",
    ]


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
QUICKSELL_ALL_TAB_PATH = ["WorldView", "shopGUI", "BackpackStuff", "ShopCategory_All"]
QUICKSELL_PET_TAB_PATH = ["WorldView", "shopGUI", "BackpackStuff", "ShopCategory_Pet"]
QUICKSELL_ALL_BUTTON_PATH = ["WorldView", "shopGUI", "buyWindow", "AllAction"]
QUICKSELL_SELL_BUTTON_PATH = ["WorldView", "shopGUI", "buyWindow", "sellAction"]
QUICKSELL_CONFIRM_SELL_PATH = ["WorldView", "shopGUI", "QuickSellConfirmationWindow", "SellButton"]
QUICKSELL_NEXT_PAGE_PATH = ["WorldView", "shopGUI", "buyWindow", "rightscroll"]
QUICKSELL_CLOSE_BUTTON_PATH = ["WorldView", "shopGUI", "buyWindow", "exit"]


def quicksell_item_title_path(row: int) -> list[str]:
    """Return the window path to the item title label for the given quicksell row (0-9)."""
    return ["WorldView", "shopGUI", "buyWindow", "column0", f"shoplist{row}"]


def quicksell_item_checkbox_path(row: int) -> list[str]:
    """Return the window path to the sell checkbox for the given quicksell row (0-9)."""
    return ["WorldView", "shopGUI", "buyWindow", "column1", f"num{row}"]
