"""Possible Drops: its category icons follow art extracted while it is on screen (#239)."""

from __future__ import annotations

from bartleby.qt.art_signal import announce_art_changed

from bartlebot.bot.schema import DropModel
from bartlebot.gui.sidebar.sections.possible_drops_section import _DropsListWidget
from tests.extracted_art import write_extracted_art


def test_new_art_gives_the_rows_their_category_icons(qapp):
    table = _DropsListWidget()
    table.set_items([DropModel(name="Sunstone", category="Reagents"), DropModel(name="Mystery Item")])
    rows = [table._list.topLevelItem(i) for i in range(table._list.topLevelItemCount())]
    assert all(row.icon(table.COL_CATEGORY).isNull() for row in rows)

    write_extracted_art(icons=["drop_reagents"])
    announce_art_changed()

    with_icon = [row.text(table.COL_NAME) for row in rows if not row.icon(table.COL_CATEGORY).isNull()]
    assert with_icon == ["Sunstone"]
