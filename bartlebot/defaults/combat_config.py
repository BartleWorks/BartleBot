"""BartleBot built-in default combat configuration.

This is a generic single-client WizSprinter combat DSL config that works for
any school. It prioritises applying buffs and debuffs before dealing damage,
falling back gracefully when preferred cards are not in hand.

Users can override this at the bot level (via the "Combat Config" section in
the bot info panel) or at the node level (by unchecking "Use bot-level default
combat config" in the handle_combat node parameters).
"""

_SEPARATOR = " | "

# Define card choice priority from highest priority to lowest priority
_COMBAT_PRIORITY = [
    "any<trap & inc_damage>[potent] @ enemy",
    "any<trap & inc_damage & aoe>[potent]",
    "any<blade & out_damage>[sharp] @ self",
    "any<blade & out_damage & aoe>[sharp]",
    "any<global>",
    "any<aura & out_damage>",
    "any<shadow>",
    "any<damage & aoe>[any<mod_damage>]",
    "any<damage>[any<mod_damage>] @ enemy"
]

# Build the combat config by creating a string from the _COMBAT_PRIORITY list with _SEPARATOR between each entry
BARTLEBOT_DEFAULT_COMBAT_CONFIG = _SEPARATOR.join(_COMBAT_PRIORITY)
