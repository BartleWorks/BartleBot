"""A ``QLabel`` whose colour follows the theme, for the many small captions.

Most themed labels in the app say the same thing: one ``THEME`` colour, plus a size
and weight that never change. Written inline that is a one-line ``setStyleSheet``
that silently goes stale, and written correctly it is a ``changeEvent`` override per
label. This is the third option.

It matters most for the *factories* — ``field_label`` and ``_build_option_parts``
build a label and hand it back, so nothing can restyle the instance afterwards and
the staleness lands on whichever widget happened to call them. Returning one of these
fixes every caller at once, which is why the player card, ``SidebarField`` and
``OptionsSection`` needed no changes of their own.

The colour is named rather than resolved, because a resolved value is exactly the
snapshot this exists to avoid: ``THEME`` is a proxy, and the token has to be read
again on each apply.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QWidget

from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin


class ThemedTextLabel(ThemedMixin, QLabel):
    """A label that re-reads its colour token whenever the theme changes."""

    def __init__(
        self,
        text: str = "",
        *,
        color: str = "text_muted",
        css: str = "",
        parent: QWidget | None = None,
    ) -> None:
        """*color* is a ``THEME`` token name; *css* is appended verbatim."""
        super().__init__(text, parent)
        self._color_token = color
        self._css = css
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        self.setStyleSheet(f"color: {getattr(THEME, self._color_token)}; {self._css}")
