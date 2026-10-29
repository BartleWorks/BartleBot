"""The inspector and panel rows converted in issue #90, commit 1.

These surfaces are all rows of *equal* verbs — Refresh, Copy ID, Copy XYZ and Teleport
are four reads on one selection — so the thing worth guarding is the absence of a weight
rather than the presence of one. ``gui/CLAUDE.md`` is explicit that nothing in an
equal-weight row outranks its neighbors, and Teleport is the button that will tempt
someone: it is the most interesting verb in its row, which is not the same as the one the
surface expects.

``ActionButton.action_style`` is public precisely so a test can ask what a surface asked
for.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from bartleby.qt.widgets.buttons.action_button import ActionButton, ActionStyle


def _buttons(widget) -> list[ActionButton]:
    """*widget* stays in the caller's hands: a top-level widget held only by a temporary
    is freed out from under the buttons it just handed over — see tests/CLAUDE.md."""
    return widget.findChildren(ActionButton)


def _without_its_load(module, factory):
    """Build a browser dialog without the fetch it fires off in ``__init__``.

    Both of these start a load with ``asyncio.ensure_future``, which needs a current
    event loop — and whether there is one in a given xdist worker depends on which tests
    landed there first, so a plain construction passes alone and fails in the suite.
    Closing the coroutine rather than scheduling it also keeps "never awaited" out of the
    run. The buttons are built before the load either way, which is all this module reads.
    """
    with patch.object(module.asyncio, "ensure_future", lambda coro: coro.close()):
        return factory()


def _entity_browser():
    from bartlebot.gui.dialogs import entity_browser

    return _without_its_load(entity_browser, lambda: entity_browser.EntityBrowserDialog(MagicMock()))


def _ui_tree_browser():
    from bartlebot.gui.dialogs import ui_tree_browser

    return _without_its_load(ui_tree_browser, lambda: ui_tree_browser.UITreeBrowserDialog(MagicMock()))


def _possible_drops_section():
    from bartlebot.gui.sidebar.sections.possible_drops_section import PossibleDropsSection

    section = PossibleDropsSection()
    section.set_editable(True)
    return section


def _log_panel():
    from bartleby.qt.sidebar.log_panel import LogPanel

    from bartlebot.settings_manager import SettingsManager

    return LogPanel(SettingsManager.instance())


def _keep_list():
    from bartlebot.gui.canvas.drop_keep_list import DropKeepListWidget

    return DropKeepListWidget(lambda: [])


INSPECTORS = [
    ("entity_browser", _entity_browser, 4),
    ("ui_tree_browser", _ui_tree_browser, 7),
    ("possible_drops", _possible_drops_section, 5),
    ("log_panel", _log_panel, 2),
    ("keep_list", _keep_list, 2),
]


@pytest.mark.parametrize(("name", "factory", "count"), INSPECTORS, ids=[i[0] for i in INSPECTORS])
def test_the_row_is_action_buttons_all_the_way_across(qapp, name, factory, count):
    """A row half converted is the inconsistency this issue exists to remove."""
    widget = factory()

    assert len(_buttons(widget)) == count


@pytest.mark.parametrize(("name", "factory", "count"), INSPECTORS, ids=[i[0] for i in INSPECTORS])
def test_no_inspector_row_claims_a_weight(qapp, name, factory, count):
    """An inspector has no single expected verb, so none of these is PRIMARY — and none is
    CAUTION either: nothing in these rows touches a file."""
    widget = factory()

    styles = {b.action_style for b in _buttons(widget)}

    assert styles == {ActionStyle.TERTIARY}


def test_teleport_does_not_outrank_the_copies_beside_it(qapp):
    """The one that will tempt someone: it is the most interesting verb in the row, which
    is not the same thing as the one the surface expects."""
    browser = _entity_browser()

    teleport = next(b for b in _buttons(browser) if b.text() == "Teleport")

    assert teleport.action_style is ActionStyle.TERTIARY


@pytest.mark.parametrize(("name", "factory", "count"), INSPECTORS, ids=[i[0] for i in INSPECTORS])
def test_nothing_in_these_rows_expands(qapp, name, factory, count):
    """Every one of them sits beside a stretch, a stretchy search field or a pinned width,
    and an expanding button in any of those eats the space the row was sharing."""
    from PyQt6.QtWidgets import QSizePolicy  # noqa: PLC0415

    widget = factory()

    for button in _buttons(widget):
        assert button.sizePolicy().horizontalPolicy() is not QSizePolicy.Policy.Expanding, button.text()


def test_the_log_rows_buttons_stay_glyphless(qapp):
    """Their widths are pinned, and the right sidebar's floor is measured from this row:
    a 16px icon inside a 62px button leaves nothing for the label."""
    panel = _log_panel()

    for button in _buttons(panel):
        assert button.icon().isNull()
