"""``ClientCard``: core's player card with BartleBot's client behind it.

What core's card does with a view is core's to test. These hold what BartleBot adds: the
reads that build the view, under the refresh contract, the debug verbs that open a dialog
on the card's own client, and the rows it starts with. The freecam pair is driven through
``ClientSection`` in ``sections/test_client_section.py``.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bartleby.qt.sidebar.player_card import PLACEHOLDER
from bartleby.runtime.game import wizard_names

from bartlebot.gui.sidebar import client_card
from bartlebot.gui.sidebar.client_card import XYZ_PLACEHOLDER, ClientCard


class _Client:
    """Enough of a client for a card's reads: a window, a zone, a position, a nearby mob."""

    def __init__(self, handle: int = 1, *, loading: bool = False) -> None:
        self.window_handle = handle
        self.is_loading = AsyncMock(return_value=loading)
        self.zone_name = AsyncMock(return_value="WizardCity/WC_Streets/Interior")
        self.body = SimpleNamespace(position=AsyncMock(return_value=SimpleNamespace(x=1.25, y=2.0, z=3.0)))
        mob = SimpleNamespace(object_name=AsyncMock(return_value="Rattlebones"))
        self.find_closest_mob = AsyncMock(return_value=mob)


def test_building_a_card_never_shows_a_parentless_widget(qapp, parentless_shows):
    ClientCard(_Client(), 0)

    assert parentless_shows == []


def test_a_card_starts_with_every_row_uncopyable(qapp):
    """Nothing has been read yet, so there is nothing on the card worth pasting anywhere."""
    card = ClientCard(_Client(), 0)

    assert list(card.rows) == ["Wizard", "Zone", "XYZ", "Enemy"]
    assert card.rows["XYZ"].display_text == XYZ_PLACEHOLDER
    assert not any(row.copyable for row in card.rows.values())


def test_a_tick_reads_the_zone_the_position_and_the_closest_enemy(qapp):
    card = ClientCard(_Client(), 0)

    asyncio.run(card.refresh())

    assert card.rows["Zone"].display_text == "WizardCity/WC_Streets/Interior"
    assert card.rows["XYZ"].display_text == "X=1.2  Y=2.0  Z=3.0"
    assert card.rows["XYZ"].copy_text == '{"x": 1.25, "y": 2.0, "z": 3.0}'
    assert card.rows["Enemy"].display_text == "Rattlebones"
    assert card.last_zone == "WizardCity/WC_Streets/Interior"


def test_the_name_is_the_cached_one_and_never_detected(qapp, monkeypatch):
    client = _Client()
    monkeypatch.setattr(wizard_names, "cached_name", lambda c: "Luke SkullHunter" if c is client else None)
    card = ClientCard(client, 0)

    asyncio.run(card.refresh())

    assert card.rows["Wizard"].display_text == "Luke SkullHunter"


def test_a_client_between_zones_is_not_read_and_keeps_what_it_showed(qapp):
    """Pointers are stale while a zone loads, and the reads are slow or garbage."""
    client = _Client()
    card = ClientCard(client, 0)
    asyncio.run(card.refresh())

    client.is_loading.return_value = True
    client.zone_name.reset_mock()
    asyncio.run(card.refresh())

    client.zone_name.assert_not_awaited()
    assert card.rows["Zone"].display_text == "WizardCity/WC_Streets/Interior"


def test_the_enemy_is_read_no_more_often_than_its_interval(qapp):
    client = _Client()
    card = ClientCard(client, 0)

    asyncio.run(card.refresh())
    asyncio.run(card.refresh())

    assert client.find_closest_mob.await_count == 1
    assert card.rows["Enemy"].display_text == "Rattlebones", "the last answer stays until the next read"


def test_a_stripe_that_lands_while_a_tick_reads_is_kept(qapp):
    """The tick draws onto the view as it is when the reads return, not as it was before them."""
    client = _Client()
    card = ClientCard(client, 0)

    async def zone_after_a_stripe():
        card.show_view(replace(card.view, color_key=3))
        return "Colossus Boulevard"

    client.zone_name = zone_after_a_stripe
    asyncio.run(card.refresh())

    assert card.color_key == 3
    assert card.rows["Zone"].display_text == "Colossus Boulevard"


def test_a_failed_read_shows_the_placeholder(qapp):
    client = _Client()
    client.zone_name = AsyncMock(side_effect=RuntimeError("disconnected"))
    client.body.position = AsyncMock(side_effect=RuntimeError("disconnected"))
    card = ClientCard(client, 0)

    asyncio.run(card.refresh())

    assert card.rows["Zone"].display_text == PLACEHOLDER
    assert card.rows["XYZ"].display_text == XYZ_PLACEHOLDER


def test_each_debug_verb_opens_its_dialog_on_this_card_s_client(qapp, monkeypatch):
    client = _Client(7)
    card = ClientCard(client, 0)
    opened: list[tuple[str, object]] = []
    monkeypatch.setattr(card, "_open_entity_browser", lambda: opened.append(("entities", card.client)))
    monkeypatch.setattr(card, "_open_ui_tree", lambda: opened.append(("ui_tree", card.client)))

    card.action_button("Entities").click()
    card.action_button("UI Tree").click()

    assert opened == [("entities", client), ("ui_tree", client)]


def test_a_card_offers_the_debug_verbs_as_core_s_product_verbs(qapp):
    card = ClientCard(_Client(), 0)

    assert card.verb_ids == (client_card.ENTITIES.id, client_card.UI_TREE.id)
