"""The tags field: its chips and pills follow art extracted while it is on screen (#239)."""

from __future__ import annotations

import pytest

from bartlebot.gui.art_signal import announce_art_changed
from bartlebot.gui.widgets.pills.tag_pill import TagChip, TagPill
from bartlebot.gui.widgets.pills.tags_field import TagsField
from tests.extracted_art import write_extracted_art


@pytest.mark.parametrize(("editable", "kind"), [(True, TagChip), (False, TagPill)])
def test_new_art_gives_the_shown_tags_their_medallions(qapp, editable, kind):
    field = TagsField()
    field.set_editable(editable)
    field.set_tags(["Gold"])
    assert [w.has_icon for w in field.findChildren(kind)] == [False]

    write_extracted_art(icons=["tag_gold"])
    announce_art_changed()

    shown = [w for w in field.findChildren(kind) if not w.isHidden()]
    assert [w.has_icon for w in shown] == [True]
    assert field.tags() == ["Gold"]
