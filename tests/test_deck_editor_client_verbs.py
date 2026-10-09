"""Import from client and Apply to client on the Deck Presets page of the combat dialog."""

from __future__ import annotations

from bartlebot.gui.combat_strategies import deck_editor as deck_editor_module
from bartlebot.gui.combat_strategies.deck_editor import DeckPresetEditor
from bartlebot.gui.combat_strategies.deck_link import DeckClientLink
from bartlebot.gui.deck_content import APPLY_DECK, IMPORT_DECK
from bartlebot.shared.strategy_models import DeckData, DeckPreset


class _Clients:
    """A stand-in for the controller: records the requests, answers when told to."""

    def __init__(self, count: int = 1, accept: bool = True) -> None:
        self.count = count
        self.accept = accept
        self.imports: list[tuple[int, object]] = []
        self.applies: list[tuple[int, DeckData, object]] = []

    def link(self) -> DeckClientLink:
        return DeckClientLink(lambda: self.count, self._import, self._apply)

    def _import(self, slot, done) -> bool:
        if self.accept:
            self.imports.append((slot, done))
        return self.accept

    def _apply(self, slot, deck, done) -> bool:
        if self.accept:
            self.applies.append((slot, deck, done))
        return self.accept


def _deck(**normal: int) -> DeckData:
    return DeckData(normal=dict(normal))


def _choose_player(monkeypatch, index: int) -> list[tuple[int, str, str]]:
    """Stand in for the menu a click opens with two clients, choosing player *index*."""
    asked: list[tuple[int, str, str]] = []

    def pick(_anchor, players, verb, on_pick, *, preposition="from"):
        asked.append((players, verb, preposition))
        on_pick(index)

    monkeypatch.setattr(deck_editor_module, "pick_player", pick)
    return asked


def _editor(qapp, clients: _Clients | None, preset: DeckPreset | None = None) -> DeckPresetEditor:
    editor = DeckPresetEditor()
    editor.set_link(clients.link() if clients is not None else None)
    if preset is not None:
        editor.load(preset)
    return editor


def test_the_buttons_say_what_the_combat_tabs_deck_card_says(qapp):
    editor = _editor(qapp, _Clients(count=1))
    assert editor.import_button.text() == IMPORT_DECK
    assert editor.apply_button.text() == APPLY_DECK


def test_building_the_page_never_shows_a_parentless_widget(qapp, parentless_shows):
    _editor(qapp, _Clients(), DeckPreset(name="x", deck=_deck(Fireball=1)))
    assert parentless_shows == []


def test_with_no_client_both_buttons_are_off_and_say_why(qapp):
    editor = _editor(qapp, _Clients(count=0), DeckPreset(name="x", deck=_deck(Fireball=1)))
    assert not editor.import_button.isEnabled()
    assert not editor.apply_button.isEnabled()
    assert "No game client" in editor.import_button.toolTip()


def test_with_no_link_at_all_both_buttons_are_off(qapp):
    editor = _editor(qapp, None, DeckPreset(name="x", deck=_deck(Fireball=1)))
    assert not editor.import_button.isEnabled()
    assert not editor.apply_button.isEnabled()


def test_a_label_is_marked_only_when_a_click_asks_which_client(qapp):
    one = _editor(qapp, _Clients(count=1), DeckPreset(name="x"))
    two = _editor(qapp, _Clients(count=2), DeckPreset(name="x"))
    assert one.import_button.text() == IMPORT_DECK
    assert two.import_button.text() == f"{IMPORT_DECK} ▾"
    assert two.apply_button.text() == f"{APPLY_DECK} ▾"


def test_apply_needs_cards_and_import_does_not(qapp):
    editor = _editor(qapp, _Clients(), DeckPreset(name="new"))
    assert editor.import_button.isEnabled()
    assert not editor.apply_button.isEnabled()


def test_import_is_off_where_the_page_is_read_only(qapp):
    editor = _editor(qapp, _Clients(), DeckPreset(name="x", deck=_deck(Fireball=1)))
    editor.set_editable(False)
    assert not editor.import_button.isEnabled()
    assert editor.apply_button.isEnabled()  # applying edits nothing


def test_import_asks_for_the_chosen_slot_and_fills_the_preset(qapp, monkeypatch):
    clients = _Clients(count=2)
    preset = DeckPreset(name="x")
    editor = _editor(qapp, clients, preset)
    edits: list[bool] = []
    editor.changed.connect(lambda: edits.append(True))
    asked = _choose_player(monkeypatch, index=1)

    editor.import_button.click()
    assert asked == [(2, "Import", "from")]
    [(slot, done)] = clients.imports
    assert slot == 2
    assert not editor.import_button.isEnabled()  # waits for the job

    done(_deck(Fireball=3))
    assert preset.deck.normal == {"Fireball": 3}
    assert edits == [True]
    assert editor.import_button.isEnabled()
    assert editor.apply_button.isEnabled()  # it has cards now


def test_import_keeps_the_decks_archmastery_decision(qapp):
    clients = _Clients(count=1)
    preset = DeckPreset(name="x", deck=DeckData(normal={"Old": 1}, apply_archmastery=True))
    editor = _editor(qapp, clients, preset)

    editor.import_button.click()
    clients.imports[0][1](_deck(New=2))

    assert preset.deck.normal == {"New": 2}
    assert preset.deck.apply_archmastery is True


def test_a_failed_import_leaves_the_preset_and_wakes_the_buttons(qapp):
    clients = _Clients()
    preset = DeckPreset(name="x", deck=_deck(Keep=1))
    editor = _editor(qapp, clients, preset)
    edits: list[bool] = []
    editor.changed.connect(lambda: edits.append(True))

    editor.import_button.click()
    clients.imports[0][1]("timed out")

    assert preset.deck.normal == {"Keep": 1}
    assert edits == []
    assert editor.import_button.isEnabled()


def test_an_import_that_lands_after_another_preset_was_selected_is_dropped(qapp):
    clients = _Clients()
    first = DeckPreset(name="first", deck=_deck(A=1))
    second = DeckPreset(name="second", deck=_deck(B=1))
    editor = _editor(qapp, clients, first)

    editor.import_button.click()
    editor.load(second)
    clients.imports[0][1](_deck(Z=9))

    assert first.deck.normal == {"A": 1}
    assert second.deck.normal == {"B": 1}


def test_a_refused_request_leaves_the_buttons_on(qapp):
    clients = _Clients(accept=False)
    editor = _editor(qapp, clients, DeckPreset(name="x", deck=_deck(Fireball=1)))

    editor.import_button.click()
    editor.apply_button.click()

    assert editor.import_button.isEnabled()
    assert editor.apply_button.isEnabled()


def test_apply_sends_a_copy_of_the_presets_deck_to_the_chosen_slot(qapp, monkeypatch):
    clients = _Clients(count=2)
    preset = DeckPreset(name="x", deck=_deck(Fireball=2))
    editor = _editor(qapp, clients, preset)
    asked = _choose_player(monkeypatch, index=1)

    editor.apply_button.click()
    assert asked == [(2, "Apply", "to")]
    [(slot, sent, done)] = clients.applies
    assert slot == 2
    assert sent == preset.deck
    assert sent is not preset.deck
    assert not editor.apply_button.isEnabled()

    done(None)
    assert editor.apply_button.isEnabled()
