"""Hand wizwalker the game folder ``shared.game_install`` finds (epic #215, Stage 2).

Every launch, and every read of the game's ``Bin`` files, goes through wizwalker's
``get_wiz_install()``, which looks only where the KingsIsle launcher installs. Its
``override_wiz_install_location`` makes it return a folder of ours instead, so the app
calls :func:`apply_install` at startup, and again whenever setup or Settings changes the
folder. With nothing found the override is cleared, and wizwalker's own checks apply.
"""

from __future__ import annotations

import logging

from wizwalker.utils import override_wiz_install_location

from bartlebot.shared.game_install import SOURCE_NAMES, FoundInstall, InstallPlaces, find_install
from bartlebot.shared.preferences import PreferenceStore

logger = logging.getLogger(__name__)


def apply_install(prefs: PreferenceStore | None = None, where: InstallPlaces | None = None) -> FoundInstall | None:
    """Point ``get_wiz_install()`` at the folder :func:`find_install` finds, and return it."""
    found = find_install(prefs or PreferenceStore(), where)
    if found is None:
        # None, not a path: wizwalker treats a falsy override as none set.
        override_wiz_install_location(None)
        logger.info("Wizard101's install was not found; launching uses the KingsIsle launcher's own folder")
        return None
    override_wiz_install_location(str(found.path))
    logger.info("Wizard101 is installed in %s, found in %s", found.path, SOURCE_NAMES[found.source])
    return found
