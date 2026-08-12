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


# Crowns shop modal (sometimes opens automatically on login)
CROWNS_SHOP_PATH = ["WorldView", "PermanentShopModalWindow", "permanentShop"]

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


# Friends list and player profile popup
#
# The profile popup (wndCharacter) is opened by clicking a friend's row in the
# friends list — see node_helpers.open_friend_profile(). Its ButtonLayout holds
# the per-player actions (go to friend, add/remove friend, add to group, …).
FRIENDS_LIST_BUTTON_PATH = ["WorldView", "windowHUD", "btnFriends"]
CHARACTER_PROFILE_PATH = ["WorldView", "windowHUD", "wndCharacter"]
CHARACTER_PROFILE_NAME_PATH = ["WorldView", "windowHUD", "wndCharacter", "lblCharacterName"]
CHARACTER_PROFILE_CLOSE_PATH = ["WorldView", "windowHUD", "wndCharacter", "btnCharacterClose"]

# The game has TWO entirely separate friends-list windows, each its own UI subtree. Only
# one is ever on screen, which is what made this confusing to diagnose:
#
#   NewFriendsListWindow  the compact list. Opened from nothing by pressing F or clicking
#                         btnFriends. This is the one node_helpers.open_friends_list()
#                         opens and the one the name/profile code reads.
#   wndFriendsList        the larger, more detailed list. Opened from the compact list by
#                         pressing F again, or clicking its "More Friends" button.
#
# F cycles: closed -> NewFriendsListWindow -> wndFriendsList -> closed. So "the friends
# list is open" means EITHER window is visible — see FRIENDS_LIST_WINDOW_NAMES, which
# node_helpers.close_friends_list() presses F against until both are gone.
#
# There is NO close-button path constant here on purpose. The obvious candidate,
# WorldView/windowHUD/wndFriendsList/btnFriendListClose, closes only the larger window and
# does NOT close NewFriendsListWindow: with nothing overlapping it, clicking it left the
# compact list open through a full retry cycle. It seemed to work in earlier testing only
# because both windows place a close button in the same spot, so the click sometimes hit
# whichever list happened to be showing. Verified with tools/probe_friends_list.py.
FRIENDS_LIST_WINDOW_NAME = "NewFriendsListWindow"
FRIENDS_LIST_WINDOW_NAMES = ("NewFriendsListWindow", "wndFriendsList")

# Group invite button in the friends-list profile popup.
#
# Note that wndCharacter has TWO children named "ButtonLayout", so this path is ambiguous
# by name — but only one of them contains btnInvite, and _get_window_from_path backtracks
# out of a branch that does not complete the path, so it resolves correctly.
INVITE_TO_GROUP_BUTTON_PATH = ["WorldView", "windowHUD", "wndCharacter", "ButtonLayout", "btnInvite"]

# Group invitation prompt shown to the invited player. This is its own window, NOT the
# shared MessageBoxModalWindow, so accepting it cannot be confused with any other dialog
# that happens to be open — no caption check is needed to disambiguate it.
GROUP_INVITE_ACCEPT_PATH = [
    "PartyInviteWindow",
    "messageBoxBG",
    "messageBoxLayout",
    "AdjustmentWindow",
    "Layout",
    "centerButton",
]

# Social systems window. It also opens by itself on both players after a group is created
# or joined, which is what confirms an invite was accepted.
#
# Opened by clicking the HUD button rather than its Ctrl+A shortcut: WizWalker's
# send_hotkey does not reliably deliver the modifier, so Ctrl+A arrives as a bare "A" and
# the character strafes instead of the window opening.
SOCIAL_BUTTON_PATH = ["WorldView", "windowHUD", "SocialSystemsContainer", "SocialButton"]
SOCIAL_WINDOW_PATH = ["WorldView", "SocialSystemsWindow"]
SOCIAL_GROUP_TAB_PATH = ["WorldView", "SocialSystemsWindow", "TabLayout", "Tab_Group"]
GROUP_WINDOW_CLOSE_PATH = ["WorldView", "SocialSystemsWindow", "CloseSocialSystemsButton"]

# NOTE: there is deliberately no "is the Group tab showing?" path here, and this is a
# settled negative result rather than a gap.
#
# GroupPageNumber (["WorldView", "SocialSystemsWindow", "GroupLayout", "GroupPageNumber"])
# is the obvious candidate — it lives in the Group page's own layout — but it reads
# visible=True on every tab. That is the hazard is_visible_by_path documents: a window's
# is_visible() flag is its own and ignores its parents.
#
# tools/probe_group_tab.py then swept EVERY tab, recording 13 properties for every window
# under SocialSystemsWindow, and found nothing that holds a value on the Group tab it does
# not also hold on some other tab (2026-08-11). So the selected tab cannot be read at all,
# and _open_social_window clicks Tab_Group unconditionally and then sleeps. Re-run the
# probe after a game update before assuming that is still true.

# Whether this wizard is in a group is read from the Leave Group button: the game HIDES it
# when ungrouped rather than graying it, so check visibility, not enabled-ness. Verified
# with tools/probe_group_ui.py.
#
# This is the UI replacement for SocialSystemsManager.group_gid(), which reads a dead
# object on the current game build (see tools/probe_group_offsets.py).
LEAVE_GROUP_BUTTON_PATH = ["WorldView", "SocialSystemsWindow", "LeftButtonLayout", "LeavePartyButton"]

# Shown to the INVITER when the player they invited is already in a group.
ALREADY_IN_GROUP_DISMISS_PATH = MESSAGE_BOX_RIGHT

# Character page (opened with the C key) — used to read this client's own wizard name.
# Located by name search rather than a fixed path: the page's parent chain is not recorded
# anywhere, and a guessed path would fail silently. See wizard_names.detect_wizard_name().
CHARACTER_PAGE_TITLE_WINDOW = "TitleScroll"

# Zone selection list UI (e.g., kiosk or NPC offering zone teleport options)
UNIVERSE_NEXT_PAGE = ["WorldView", "", "messageBoxBG", "ControlSprite", "optionWindow", "rightButton"]
UNIVERSE_PREV_PAGE = ["WorldView", "", "messageBoxBG", "ControlSprite", "optionWindow", "leftButton"]
UNIVERSE_TELEPORT_BUTTON = ["WorldView", "", "messageBoxBG", "ControlSprite", "teleportButton"]
UNIVERSE_CANCEL_BUTTON = ["WorldView", "", "messageBoxBG", "ControlSprite", "cancelButton"]


def universe_zone_option_path(index: int) -> list[str]:
    """Return the path for zone option index (0–3) in the zone selection list."""
    return ["WorldView", "", "messageBoxBG", "ControlSprite", "optionWindow", f"opt{index}"]
