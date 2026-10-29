"""Patch a node helper where every caller reads it: BartleBot's ``node_helpers`` and core's.

Since Stage 3 of #167 the helpers that need no bot are bartleby-core's
(``bartleby.runtime.actions.node_helpers``), and BartleBot's ``node_helpers`` imports them.
A node calls ``node_helpers.click_by_path`` through BartleBot's module, but core's own
helpers call each other through core's, so a name patched on one module alone is seen by
half the callers. :func:`patch_helper` and :func:`set_helper` patch both with the same
object, which is what patching the one module did before the move. A name core does not
hold is patched on BartleBot's module only.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import DEFAULT, patch

from bartleby.runtime.actions import node_helpers as core_helpers

from bartlebot.nodes import node_helpers

_MISSING = object()


class patch_helper:  # noqa: N801 - used where patch.object was, and reads like it
    """``patch.object(node_helpers, name, ...)``, on core's module too. A context manager."""

    def __init__(self, name: str, new=DEFAULT, **kwargs) -> None:
        self._name = name
        self._new = new
        #: What it patches in, as on a ``patch.object`` patcher.
        self.new = new
        self._kwargs = kwargs
        self._stack: ExitStack | None = None

    def __enter__(self):
        self._stack = ExitStack()
        mock = self._stack.enter_context(patch.object(node_helpers, self._name, self._new, **self._kwargs))
        if getattr(core_helpers, self._name, _MISSING) is not _MISSING:
            self._stack.enter_context(patch.object(core_helpers, self._name, mock))
        return mock

    def __exit__(self, *exc) -> bool:
        assert self._stack is not None
        return bool(self._stack.__exit__(*exc))

    def start(self):
        return self.__enter__()

    def stop(self) -> None:
        self.__exit__(None, None, None)


def set_helper(monkeypatch, name: str, value) -> None:
    """``monkeypatch.setattr(node_helpers, name, value)``, on core's module too."""
    monkeypatch.setattr(node_helpers, name, value)
    if getattr(core_helpers, name, _MISSING) is not _MISSING:
        monkeypatch.setattr(core_helpers, name, value)
