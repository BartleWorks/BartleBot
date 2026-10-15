"""Tests for ``bartlebot.combat.dsl_syntax`` — the combat DSL's vocabulary, scanner and completion.

The vocabulary tests are the drift guard the module docstring promises: each word list
is checked against wizsprinter's grammar or combat code, so a keyword added upstream
fails here rather than going uncolored and unoffered.
"""

import inspect
import re

import pytest

from bartlebot.combat import dsl_syntax as ds
from bartlebot.combat.dsl_syntax import Context, Kind
from bartlebot.nodes.combat_runner import validate_dsl


def _grammar() -> str:
    from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import get_sprinty_grammar

    return get_sprinty_grammar()


def _keywords(rule_prefix: str) -> set[str]:
    """Every ``<rule_prefix>_x: _spaced{"word"}`` rule's word."""
    return set(re.findall(rf'(?m)^\s*{rule_prefix}_\w+:\s+_spaced\{{"(\w+)"\}}', _grammar()))


# ---------------------------------------------------------------------------
# Vocabulary against the grammar
# ---------------------------------------------------------------------------


def test_spell_types_match_the_grammar():
    assert _keywords("spell") == set(ds.SPELL_TYPES) | set(ds.HANGING_VERBS)


def test_heal_qualifiers_match_the_grammar():
    assert set(re.findall(r'spell_heal_\w+:\s+spell_heal _spaced\{"(\w+)"\}', _grammar())) == set(ds.HEAL_QUALIFIERS)


def test_targets_match_the_grammar():
    assert _keywords("target") == set(ds.TARGETS) | set(ds.TARGET_FORMS)


def test_move_keywords_match_the_grammar():
    # ``move_discard`` is unreachable in the grammar, but "discard" still works as a move:
    # it parses as a spell name that the combat code special-cases.
    assert _keywords("move") == set(ds.MOVE_KEYWORDS)


def test_discard_parses_as_the_move_the_combat_code_special_cases():
    from wizwalker.extensions.wizsprinter.combat_backends.combat_api import TargetType

    from bartlebot.nodes.combat_runner import _str_provider_class

    config = _str_provider_class()("discard @ spell(Tempest, any<aoe>)").config
    move = config.infinite_rounds[0].priorities[0]
    assert move.move.card.name == "discard"
    assert move.target.target_type is TargetType.type_spell


def test_condition_words_match_the_grammar():
    assert _keywords("cond_target") == set(ds.CONDITION_SUBJECTS)
    assert _keywords("cond_agg") == set(ds.AGGREGATES)


def test_hanging_kinds_match_the_combat_api():
    from wizwalker.extensions.wizsprinter.combat_backends.combat_api import hanging_type_aliases

    assert set(ds.HANGING_KINDS) == set(hanging_type_aliases())


def test_condition_attributes_are_the_ones_wizsprinter_accepts():
    from wizwalker.extensions.wizsprinter.combat_backends.combat_api import DECK_ATTRIBUTES, MEMBER_ATTRIBUTES

    assert set(ds.MEMBER_ATTRIBUTES) == set(MEMBER_ATTRIBUTES)
    assert ds.DECK_ATTRIBUTES == DECK_ATTRIBUTES


def test_member_attributes_are_numeric_reads_on_a_combat_member():
    from wizwalker.combat.member import CombatMember

    for name in ds.MEMBER_ATTRIBUTES:
        method = getattr(CombatMember, name)
        assert inspect.iscoroutinefunction(method), name
        assert inspect.signature(method).return_annotation in (int, float, bool, "int", "float", "bool"), name


@pytest.mark.parametrize(
    "lines",
    [
        [f"any<{t}> @ enemy" for t in ds.SPELL_TYPES] + [f"any<heal {q}> @ self" for q in ds.HEAL_QUALIFIERS],
        [f"any<{verb}({kind})> @ enemy" for verb in ds.HANGING_VERBS for kind in ds.HANGING_KINDS[:3]]
        + [f"any<gambit({kind}, 2)> @ enemy" for kind in ds.HANGING_KINDS],
        [f"pass @ {t}" for t in ds.TARGETS] + ["pass @ select(enemy, boss)", "pass @ enemy(last)"],
        [f"?({s}.health < 50%) pass" for s in ds.CONDITION_SUBJECTS if s != "deck"]
        + [f"?(deck.{attr} >= 1) pass" for attr in ds.DECK_ATTRIBUTES]
        + [f"?({a}({g}).mana > 1) pass" for a in ds.AGGREGATES for g in ds.AGGREGATE_GROUPS]
        + [f"?(self.{attr} >= 1) pass" for attr in ds.CONDITION_ATTRIBUTES],
        list(ds.MOVE_KEYWORDS) + ["discard @ spell(Tempest, any<aoe>)"],
    ],
    ids=["spell types", "hanging kinds", "targets", "conditions", "moves"],
)
def test_every_offered_word_parses_where_it_is_offered(lines):
    # One DSL per category rather than one per word: each parse is tens of milliseconds.
    assert validate_dsl("\n".join(lines)) is None


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def _spans(line: str, stack=(Context.MOVE,)) -> list[tuple[str, Kind]]:
    scan = ds.scan_line(line, stack)
    return [(line[s.start : s.start + s.length], s.kind) for s in scan.spans]


P, K, T, TG, N, S = Kind.PUNCT, Kind.KEYWORD, Kind.TYPE, Kind.TARGET, Kind.NUMBER, Kind.STRING


def test_one_word_is_colored_by_where_it_sits():
    assert _spans("any<aoe> @ aoe") == [("any", K), ("<", P), ("aoe", T), (">", P), ("@", P), ("aoe", TG)]


def test_spell_names_and_combatant_names_are_left_plain():
    assert _spans("Tempest @ Nightshade") == [("@", P)]
    # A digit in a spell name is part of the name, not a number.
    assert _spans("Storm Lord 2 @ aoe") == [("@", P), ("aoe", TG)]


def test_heal_self_is_a_spell_type_and_self_after_the_at_is_a_target():
    assert _spans("any<heal self> @ self") == [
        ("any", K),
        ("<", P),
        ("heal", T),
        ("self", T),
        (">", P),
        ("@", P),
        ("self", TG),
    ]


def test_a_condition():
    assert _spans("?(self.health < 50% && any(enemies).harmful_wards >= 2) pass") == [
        ("?(", P),
        ("self", TG),
        (".", P),
        ("health", T),
        ("<", P),
        ("50%", N),
        ("&&", P),
        ("any", K),
        ("(", P),
        ("enemies", TG),
        (")", P),
        (".", P),
        ("harmful_wards", T),
        (">=", P),
        ("2", N),
        (")", P),
        ("pass", K),
    ]


def test_a_round_a_quoted_spell_an_enchant_and_an_index():
    assert _spans('{3} "Tempest"[any<mod_damage>] @ enemy(last)') == [
        ("{", P),
        ("3", N),
        ("}", P),
        ('"Tempest"', S),
        ("[", P),
        ("any", K),
        ("<", P),
        ("mod_damage", T),
        (">", P),
        ("]", P),
        ("@", P),
        ("enemy", TG),
        ("(", P),
        ("last", K),
        (")", P),
    ]


def test_a_pipe_or_an_ampersand_starts_the_next_move():
    assert _spans("pass @ self | pass")[-1] == ("pass", K)
    assert _spans("Tempest @ aoe & draw")[-1] == ("draw", K)


def test_discard_is_a_keyword_and_its_spell_list_colors_like_any_other():
    assert _spans("discard @ spell(Tempest, any<aoe>)") == [
        ("discard", K),
        ("@", P),
        ("spell", K),
        ("(", P),
        (",", P),
        ("any", K),
        ("<", P),
        ("aoe", T),
        (">", P),
        (")", P),
    ]


def test_a_hanging_verb_takes_hanging_kinds():
    assert _spans("any<gambit(harmful_charms, 2)> @ enemy")[2:8] == [
        ("gambit", K),
        ("(", P),
        ("harmful_charms", T),
        (",", P),
        ("2", N),
        (")", P),
    ]


def test_an_open_bracket_carries_to_the_next_line_and_a_finished_line_does_not():
    assert validate_dsl("any<blade &\ndamage> @ self") is None  # the example is real DSL

    first = ds.scan_line("any<blade &")
    assert first.stack == (Context.MOVE, Context.SPELL_TYPE)
    assert _spans("damage> @ self", ds.next_line_stack(first.stack))[0] == ("damage", T)

    finished = ds.scan_line("pass @ self")
    assert finished.stack == (Context.TARGET,)
    # Carried over, the @ would color a spell named Self on the next line as a target.
    assert ds.next_line_stack(finished.stack) == (Context.MOVE,)


def test_a_bang_is_punctuation_and_colors_a_type_after_it_but_not_a_name():
    assert _spans("any<trap & !aoe>")[2:6] == [("trap", T), ("&", P), ("!", P), ("aoe", T)]
    assert _spans("any<trap & !feint>")[2:5] == [("trap", T), ("&", P), ("!", P)]
    assert _spans('any<trap & !"Feint">')[4:6] == [("!", P), ('"Feint"', S)]


def test_a_type_word_inside_an_excluded_name_is_part_of_the_name():
    assert validate_dsl("any<damage & !Fire Trap>") is None
    assert _spans("any<damage & !Fire Trap>")[2:6] == [("damage", T), ("&", P), ("!", P), (">", P)]


def test_negatable_types_parse_after_a_bang():
    assert validate_dsl("\n".join(f"any<damage & !{t}> @ enemy" for t in ds.NEGATABLE_TYPES)) is None


def test_a_stack_survives_the_block_state_round_trip():
    stack = (Context.MOVE, Context.ENCHANT, Context.SPELL_TYPE, Context.HANGING)
    assert ds.decode_stack(ds.encode_stack(stack)) == stack
    assert ds.decode_stack(-1) == (Context.MOVE,)  # Qt's "no state yet"


def test_an_unclosed_quote_is_reported():
    assert ds.scan_line('pass @ "Lord Night').open_string
    assert not ds.scan_line('pass @ "Lord Nightshade"').open_string


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------


def test_any_lt_offers_every_spell_type_before_anything_is_typed():
    completion = ds.complete("any<")
    assert completion.prefix == ""
    assert completion.eager
    assert completion.candidates[: len(ds.SPELL_TYPES)] == ds.SPELL_TYPES
    assert "gambit(" in completion.candidates


def test_a_bang_offers_the_types_it_can_negate():
    completion = ds.complete("any<trap & !")
    assert completion.eager
    assert completion.candidates == ds.NEGATABLE_TYPES
    assert "req_met" not in completion.candidates
    assert ds.complete("any<trap & !ao").candidates == ("aoe",)
    # A name is typed freely; nothing is offered for one.
    assert ds.complete("any<trap & !fei").candidates == ()


def test_a_typed_prefix_narrows_the_candidates_case_insensitively():
    completion = ds.complete("any<blade & DA")
    assert completion.prefix == "DA"
    assert completion.candidates == ("damage",)


def test_after_the_at_come_targets():
    completion = ds.complete("Tempest @ ")
    assert completion.eager
    assert completion.candidates[: len(ds.TARGETS)] == ds.TARGETS
    assert ds.complete("Tempest @ en").candidates == ("enemy", "enemies")
    assert ds.complete("pass @ select(boss, ").candidates == ds.TARGETS


def test_after_discard_the_at_offers_only_spell():
    completion = ds.complete("discard @ ")
    assert completion.eager
    assert completion.candidates == ("spell(",)
    assert ds.complete("{2} discard @ sp").candidates == ("spell(",)
    assert ds.complete("discard @ en").candidates == ()
    assert ds.complete("discard @ spell(").candidates == ("any<",)


def test_the_next_move_after_a_discard_offers_ordinary_targets_again():
    for before in ("discard @ spell(Tempest) | pass @ ", "discard @ spell(Tempest)\npass @ "):
        assert ds.complete(before).candidates[: len(ds.TARGETS)] == ds.TARGETS, before


def test_a_condition_offers_subjects_then_attributes():
    assert ds.complete("?(").candidates == ("self", "boss", "enemy", "ally", "deck", "any(", "all(", "avg(")
    assert ds.complete("?(self.").candidates == ds.CONDITION_ATTRIBUTES
    assert ds.complete("?(self.he").candidates == ("health",)
    assert ds.complete("?(self.health < 50% && bo").candidates == ("boss",)
    assert ds.complete("?(avg(").candidates == ds.AGGREGATE_GROUPS


def test_deck_offers_only_what_a_deck_can_read():
    assert ds.complete("?(deck.").candidates == ds.DECK_ATTRIBUTES
    assert ds.complete("?(self.health > 0 && Deck . re").candidates == ("remaining",)
    assert "remaining" not in ds.complete("?(self.").candidates


def test_heal_offers_its_two_qualifiers():
    assert ds.complete("any<heal ").candidates == ("self", "other")


def test_a_hanging_verb_offers_hanging_kinds():
    assert ds.complete("any<gambit(harm").candidates == tuple(k for k in ds.HANGING_KINDS if k.startswith("harm"))


def test_an_index_offers_last():
    assert ds.complete("any<damage> @ enemy(").candidates == ("last",)


def test_the_start_of_a_move_offers_moves_but_is_not_eager():
    for before in ("", "pass | ", "{2} ", "pass @ self\n"):
        completion = ds.complete(before)
        assert completion.candidates == ("any<", "pass", "willcast", "draw", "discard"), before
        assert not completion.eager, before


def test_an_open_bracket_on_an_earlier_line_still_decides_what_comes_next():
    completion = ds.complete("any<blade &\n")
    assert completion.eager
    assert "damage" in completion.candidates


def test_nothing_is_offered_for_a_spell_name_inside_a_string_or_after_a_digit():
    assert ds.complete("Temp").candidates == ()
    assert ds.complete('pass @ "en').candidates == ()
    assert ds.complete("any<damage> @ enemy(2nd").candidates == ()
