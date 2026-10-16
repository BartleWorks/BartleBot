"""The combat order's header gems follow art extracted while it is on screen (#239)."""

from __future__ import annotations

from bartlebot.gui.art_signal import announce_art_changed
from bartlebot.gui.sidebar.sections.combat_order_widget import CombatOrderWidget
from bartlebot.gui.widgets.cards.duel_slot_gem import DuelSlotGem
from tests.extracted_art import write_extracted_art


def test_new_art_shows_every_slots_gem(qapp):
    widget = CombatOrderWidget()
    gems = widget.findChildren(DuelSlotGem)
    assert len(gems) == 4
    assert all(gem.isHidden() for gem in gems)

    write_extracted_art(icons=[f"duel_slot_{slot}" for slot in range(1, 5)])
    announce_art_changed()

    assert sorted(gem.slot for gem in gems if not gem.isHidden()) == [1, 2, 3, 4]
