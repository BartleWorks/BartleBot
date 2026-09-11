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

# Sigil entrance mode toggle. Dungeons that offer Challenge Mode show this button on the
# entrance prompt; it cycles the sigil's mode. The prompt title is what says which mode is
# selected — it gains the word "Challenge" — so read the title rather than the button, which
# carries no readable state.
SIGIL_MODE_TOGGLE_BUTTON_PATH = ["WorldView", "NPCRangeWin", "sigilModeToggleButton"]
SIGIL_ENTRANCE_TITLE_PATH = ["WorldView", "NPCRangeWin", "wndTitleBackground", "NPCRangeTxtTitle"]

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

# Return to the character-select screen, on the same spellbook settings page the realm
# controls live on. Reachable from SPELLBOOK_BUTTON_PATH -> SPELLBOOK_PREFERENCES_TAB_PATH.
#
# The Realms tab is the one tab that hides this button, and REALM_CURRENT_NAME_PATH above
# is how that is detected — a settings page opened fresh always shows it, so closing the
# spellbook and reopening is the reliable way back to it.
SETTINGS_QUIT_BUTTON_PATH = ["WorldView", "DeckConfiguration", "SettingPage", "QuitButton"]

# The "Remember Realm" option, on the third gameplay-options tab of the same settings
# page. Its value is localized text ("Yes"/"No", "Ja"/"Nein", ...) -- read it through
# bartlebot.realms.yes_no(), never by comparing to English. The right arrow flips it;
# OK commits the page. There is no config.xml or preferences.xml tag for it, which is
# why it is toggled through the UI after login rather than patched before launch.
SETTINGS_MORE_GAMEPLAY_TAB_PATH = ["WorldView", "DeckConfiguration", "SettingPage", "TabWindow", "MoreGameplayButton"]
REMEMBER_REALM_VALUE_PATH = [
    "WorldView",
    "DeckConfiguration",
    "SettingPage",
    "MoreGameplayOptions",
    "OptionRememberRealm",
    "OptionControl",
    "OptionValue",
]
REMEMBER_REALM_TOGGLE_PATH = [
    "WorldView",
    "DeckConfiguration",
    "SettingPage",
    "MoreGameplayOptions",
    "OptionRememberRealm",
    "OptionControl",
    "OptionButtonRight",
]
SETTINGS_OK_BUTTON_PATH = ["WorldView", "DeckConfiguration", "SettingPage", "OkButton"]


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


# Archmastery school selector, on the deck page.
#
# "Guage" is the game's own misspelling of "Gauge". Paths match on name, so it
# has to be spelled the game's way -- do not "fix" it.
#
# Clicking the gauge opens a small menu with one icon per school. The menu is
# invisible to the window tree: nothing appears, vanishes, or changes text or
# visibility when it opens, because the pip is drawn as a texture. What *does*
# change is the icons' geometry -- they are parked stacked behind the gauge
# while closed and animate out to clickable positions when open, so their
# spread is the only signal available. See deck_io.archmastery_menu_is_open.
ARCHMASTERY_PANEL_PATH = [
    "WorldView",
    "DeckConfiguration",
    "DeckConfigurationWindow",
    "ControlSprite",
    "DeckPage",
    "SchoolPipPanel",
]
ARCHMASTERY_GAUGE_PATH = [*ARCHMASTERY_PANEL_PATH, "Guage"]


def archmastery_school_path(school: str) -> list[str]:
    """Path to the menu icon for one school, e.g. "Storm" -> .../SC_Storm.

    ``school`` is capitalised the way wizwalker's school table spells it, which
    is also how the game names these windows.
    """
    return [*ARCHMASTERY_PANEL_PATH, f"SC_{school}"]


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
LARGE_FRIENDS_LIST_WINDOW_NAME = "wndFriendsList"
FRIENDS_LIST_WINDOW_NAMES = (FRIENDS_LIST_WINDOW_NAME, LARGE_FRIENDS_LIST_WINDOW_NAME)

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

# Group roster on the Group tab: one line per member, each carrying a NameText label.
#
# This is what makes "are these clients in the SAME group?" answerable at all. The Leave
# Group button above is a per-client yes/no and two clients in two different groups both
# read True, so membership alone cannot distinguish one group from two.
#
# GROUP_ROSTER_LINES is Wizard101's group cap. Reading a line beyond it simply returns no
# window, so an over-estimate costs a wasted read rather than a wrong answer — but see
# tools/probe_group_roster.py, which checks for a Line beyond the cap after a game update.
GROUP_ROSTER_LINES = 4


def group_member_name_path(line: int) -> list[str]:
    """Return the path to the wizard name on group roster line `line` (1-based)."""
    return ["WorldView", "SocialSystemsWindow", "GroupLayout", "SubGroupLayout", f"Line{line}", "NameText"]


# Shown to the INVITER when the player they invited is already in a group.
ALREADY_IN_GROUP_DISMISS_PATH = MESSAGE_BOX_RIGHT

# Housing page (opened with the U key), Castles tab.
#
# handle_potions uses this to temporarily unequip a non-dorm primary residence, so that
# GotoDormButton lands in the dorm on the first press instead of costing a full
# COMPASS_TELEPORT_TIMEOUT wait for the teleport cooldown before a second press.
#
# TabBackground is the Castles tab button, and doubles as the marker that the housing page
# is actually open — no other spellbook page has it. Equip_Item appears only on the Castles
# tab, so its presence (visible, not enabled — it is grayed until a row is selected) is what
# says the right tab is showing. Item_1 is the first row.
#
# Row order is only stable while the window stays open. The equipped castle sorts first, but
# closing and reopening the page resorts the list alphabetically — so a castle unequipped
# before a teleport is NOT Item_1 when the page is reopened afterwards, and re-equipping by
# position would equip whichever castle sorts first. Anything that unequips must cache the
# name and search for it on the way back.
HOUSING_CASTLES_TAB_PATH = ["WorldView", "DeckConfiguration", "FurnitureSpellbookPage", "TabBackground"]
HOUSING_EQUIP_BUTTON_PATH = [
    "WorldView",
    "DeckConfiguration",
    "FurnitureSpellbookPage",
    "windowForBtns",
    "Layout",
    "Equip_Item",
]
HOUSING_PAGE_PATH = ["WorldView", "DeckConfiguration", "FurnitureSpellbookPage"]

# Paging controls for the castle list. Hidden when everything fits on one page.
HOUSING_SCROLL_LEFT_PATH = HOUSING_PAGE_PATH + ["leftscroll"]
HOUSING_SCROLL_RIGHT_PATH = HOUSING_PAGE_PATH + ["rightscroll"]


def housing_castle_row_path(row: int) -> list[str]:
    """Return the path to castle list row `row` (1-based; the page shows 8 at a time).

    The row window's own text is the castle's name, so get_text_by_path on this path reads
    it directly — there is no separate label child. An empty string means an empty slot.
    """
    return HOUSING_PAGE_PATH + [f"Item_{row}"]


def housing_castle_equipped_icon_path(row: int) -> list[str]:
    """Return the path to the "equipped" fist icon on castle list row `row`.

    is_visible on this icon is what distinguishes an equipped castle from an unequipped one
    — Equip_Item is a toggle with no readable label state, so without this a player with
    castles but none equipped is indistinguishable from one with a castle equipped, and
    clicking the toggle would EQUIP a castle for somebody who wants none.

    Confirmed by tools/probe_housing_equip.py (2026-08-13): visible=True on the equipped
    row, False on every other row and on every row when nothing is equipped. Note that the
    icon's is_control_grayed and maybe_checked ALSO differ between those two states and are
    both traps — the same probe caught them flipping at random on unrelated hidden windows
    between two otherwise identical runs, so they are uninitialised memory, not signal.
    """
    return housing_castle_row_path(row) + ["fist"]


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
