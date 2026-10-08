"""What the Run-mode Variables section shows: each value as it is now, and which just changed (#188).

Qt-free, so the marking rule is tested without a widget. The section feeds it the
machine's two callbacks, ``on_variable_change`` and ``on_state_change``, and draws what
it says.

**A changed value stays marked through the first node after it that touches the game.**
Marking it only until the next node starts would mark nothing anyone could see: a
``set_variables`` write is followed by the next node within milliseconds, and so is the
``branch`` that usually reads it. Game nodes take seconds, so the mark is on screen for
the whole of the node the write led to, and is cleared when the one after that starts.
Variable-only nodes (``variables.VARIABLE_ONLY_NODE_TYPES``) never count toward it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from bartlebot import variables
from bartlebot.bot.schema import VariableModel

#: How many game nodes may start after a write before its mark goes: the first is the
#: node the write led to, which shows it; the second clears it.
_MARK_GAME_NODES = 1


class LiveVariables:
    """The bot's variables as a run has left them, with the ones that just changed marked."""

    def __init__(self) -> None:
        self._values: dict[str, Any] = {}
        # name -> (the value before the write, game nodes started since it)
        self._marks: dict[str, tuple[Any, int]] = {}

    def reset(self, declarations: Iterable[VariableModel]) -> None:
        """Every value back to its declaration's initial value, and no marks: before a run, and as one starts."""
        # As the machine starts them: a name declared twice, which validation refuses, takes the last.
        self._values = {decl.name: decl.initial_value() for decl in declarations}
        self._marks.clear()

    def change(self, name: str, old: Any, new: Any) -> None:
        """A write the machine made, which marks ``name``.

        Two writes before a game node has shown the first, as an add then a set on the way
        to one node, are one change: the mark keeps the value from before both.
        """
        self._values[name] = new
        mark = self._marks.get(name)
        before = mark[0] if mark is not None and mark[1] == 0 else old
        self._marks[name] = (before, 0)

    def node_started(self, node_type: str | None) -> set[str]:
        """A node is starting. Returns the names whose mark this clears."""
        if node_type in variables.VARIABLE_ONLY_NODE_TYPES:
            return set()
        cleared = set()
        for name, (before, seen) in list(self._marks.items()):
            if seen >= _MARK_GAME_NODES:
                del self._marks[name]
                cleared.add(name)
            else:
                self._marks[name] = (before, seen + 1)
        return cleared

    @property
    def values(self) -> Mapping[str, Any]:
        return dict(self._values)

    def value(self, name: str) -> Any:
        return self._values.get(name)

    def is_marked(self, name: str) -> bool:
        return name in self._marks

    def before(self, name: str) -> Any:
        """What a marked value was before it changed, or None when it is not marked."""
        mark = self._marks.get(name)
        return mark[0] if mark else None
