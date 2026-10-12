"""BartleBot built-in default combat configuration.

The generic single-wizard WizSprinter DSL now lives in
:mod:`bartlebot.shared.combat_defaults` as ``LAST_RESORT_DSL``, where every product
by this vendor can read it. This module keeps the old name until the four-level
resolution retires it (Stage 4 of #201).

Users can override this at the bot level (via the "Combat Config" section in
the bot info panel) or at the node level (by unchecking "Use bot-level default
combat config" in the handle_combat node parameters).
"""

from bartlebot.shared.combat_defaults import LAST_RESORT_DSL

BARTLEBOT_DEFAULT_COMBAT_CONFIG = LAST_RESORT_DSL
