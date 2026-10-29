"""Hand wizwalker the game folder ``shared.game_install`` finds (epic #215, Stage 2).

Every launch, and every read of the game's ``Bin`` files, goes through wizwalker's
``get_wiz_install()``, which looks only where the KingsIsle launcher installs. Its
``override_wiz_install_location`` makes it return a folder of ours instead, so the app
calls :func:`apply_install` at startup, and again whenever setup or Settings changes the
folder. With nothing found the override is cleared, and wizwalker's own checks apply.

A folder found only from the game running now is stored as the folder chosen, so the next
launch finds it whether or not the game is running then.
"""

from __future__ import annotations

import logging

from bartleby.shared.game_install import (
    SOURCE_NAMES,
    FoundInstall,
    InstallPlaces,
    InstallSource,
    find_install,
    set_install_dir,
)
from bartleby.shared.preferences import PreferenceStore
from wizwalker.utils import override_wiz_install_location

logger = logging.getLogger(__name__)


def apply_install(prefs: PreferenceStore | None = None, where: InstallPlaces | None = None) -> FoundInstall | None:
    """Point ``get_wiz_install()`` at the folder :func:`find_install` finds, and return it.

    Found from the game running now, it is stored too: nothing else would find it again.
    """
    prefs = prefs or PreferenceStore()
    found = find_install(prefs, where)
    if found is None:
        # None, not a path: wizwalker treats a falsy override as none set.
        override_wiz_install_location(None)
        logger.info("Wizard101's install was not found; launching uses the KingsIsle launcher's own folder")
        return None
    override_wiz_install_location(str(found.path))
    logger.info("Wizard101 is installed in %s, found in %s", found.path, SOURCE_NAMES[found.source])
    if found.source is InstallSource.RUNNING:
        if set_install_dir(prefs, found.path):
            found = FoundInstall(found.path, InstallSource.STORED)
        else:
            logger.warning("Could not store the game folder found from the running game; it is used for now")
    return found
