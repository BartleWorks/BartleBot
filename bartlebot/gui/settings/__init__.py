"""BartleBot's settings tabs, built from core's rows (``bartleby.qt.settings``).

Its folding groups are declared here, on import, since every tab that draws one is in this
package: core prunes the remembered fold of a group it was never told of.
"""

from bartleby.qt.settings.rows import declare_folds

declare_folds({"autosave": True, "quicksell": True, "grid": True})
