"""A ratio from 0.0 to 1.0, shown and edited as a whole percent.

A health or mana threshold is a share of a whole. The bot file stores it as the float a
node compares against (``0.5``), which is the wrong thing to ask a person to type: a
plain decimal field took ``5.0`` and ``-1`` as readily as ``0.5``. This shows ``50%``
and cannot leave 0% to 100%, and the file is none the wiser.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QSizePolicy, QSpinBox, QWidget


def _percent(ratio: float) -> int:
    return round(ratio * 100)


class RatioSpinBox(QSpinBox):
    """A spin box over 0% to 100% whose value, as a param, is the ratio (:meth:`ratio`).

    Only as wide as its widest value. A spin box's size hint is already worked out
    from its range and suffix in its own font, so holding it to that hint is what
    follows a font size or a display scale; no width is spelled out here.
    """

    def __init__(
        self,
        ratio: float = 0.0,
        minimum: float | None = None,
        maximum: float | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setSuffix("%")
        low = 0.0 if minimum is None else max(0.0, float(minimum))
        high = 1.0 if maximum is None else min(1.0, float(maximum))
        self.setRange(_percent(low), _percent(high))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._stored = 0.0
        self.set_ratio(ratio)

    def set_ratio(self, ratio: float) -> None:
        """Show *ratio*, held to the range. What :meth:`ratio` returns until the user moves it."""
        self._stored = float(ratio)
        self.setValue(_percent(self._stored))

    def ratio(self) -> float:
        """The value as the file stores it.

        The stored value itself while the control still shows it, so a ratio finer than
        a whole percent (``0.125``) is not rounded merely by its form being read back.
        """
        if _percent(self._stored) == self.value():
            return self._stored
        return self.value() / 100
