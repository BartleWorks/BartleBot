"""Tests for combat_runner.validate_dsl and the shared Lark parser cache.

validate_dsl exists so a malformed combat DSL is reported in the pre-run warning
dialog rather than raising mid-fight inside SprintyCombat.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from bartlebot.nodes.combat_runner import (
    _lark_parser,
    _str_provider_class,
    dsl_error,
    dsl_warning_text,
    dsl_warnings,
    validate_dsl,
)
from bartlebot.shared.combat_defaults import LAST_RESORT_DSL

# Real DSLs taken from strategies/player/*.json.
_VALID_DSLS = [
    LAST_RESORT_DSL,
    "any<blade>[sharp]@ally(2)",
    "feint mass | any<blade>[sharp]@ally(2)",
    "any<aura & out_damage> | any<aoe>[epic]",
    # `!` inside any<…>: a negated type, a loosely excluded name, an exactly excluded one.
    "any<trap & !aoe>",
    "any<trap & !feint>",
    'any<trap & !"Feint">',
]


@pytest.mark.parametrize("dsl", _VALID_DSLS)
def test_valid_dsl_returns_none(dsl):
    assert validate_dsl(dsl) is None


def test_builtin_default_is_valid():
    """The fallback every unassigned player runs must always parse."""
    assert validate_dsl(LAST_RESORT_DSL) is None


@pytest.mark.parametrize("dsl", ["", "   ", "\n\n", "\t \n "])
def test_empty_dsl_is_treated_as_valid(dsl):
    """An empty DSL resolves to the built-in default and is never executed, so
    flagging it would fire on every newly created preset."""
    assert validate_dsl(dsl) is None


def test_malformed_dsl_returns_a_message():
    result = validate_dsl("@@@ not a dsl")

    assert result is not None
    assert isinstance(result, str)
    assert result


def test_error_message_is_a_single_line():
    """The message goes into a bulleted warning dialog, so it must not be a
    multi-line lark parse dump."""
    result = validate_dsl("@@@ not a dsl")

    assert "\n" not in result


def test_error_message_is_not_the_whole_traceback():
    result = validate_dsl("any<blade")

    assert result is not None
    assert "Traceback" not in result


def test_validation_does_not_mutate_the_input():
    dsl = "any<blade>[sharp]@ally(2)"
    validate_dsl(dsl)

    assert dsl == "any<blade>[sharp]@ally(2)"


# ---------------------------------------------------------------------------
# Parser caching
# ---------------------------------------------------------------------------


def test_lark_grammar_is_compiled_only_once():
    """CombatConfigProvider.parse_config() builds a fresh Lark (~77 ms) on every call
    and _run_combat makes one provider per client per fight, so the grammar must be
    compiled once and shared."""
    import lark

    real_lark = lark.Lark
    build_count = 0

    def counting_lark(*args, **kwargs):
        nonlocal build_count
        build_count += 1
        return real_lark(*args, **kwargs)

    _lark_parser.cache_clear()
    dsl_error.cache_clear()  # else validate_dsl's cache answers 4 of the 5
    try:
        with patch("lark.Lark", side_effect=counting_lark):
            for _ in range(5):
                assert validate_dsl("any<blade>[sharp]@ally(2)") is None

        assert build_count == 1
    finally:
        _lark_parser.cache_clear()
        dsl_error.cache_clear()


def test_parser_returns_the_same_object_every_time():
    _lark_parser.cache_clear()
    first = _lark_parser()
    second = _lark_parser()

    assert first is second


def test_repeated_validation_of_one_dsl_parses_it_once():
    """A parse plus a transform is ~60-90 ms, and the strategy dialog validates
    every entry's DSL each time it redraws a card. Without this cache, typing one
    character in the filter box cost seconds (#43)."""
    dsl = "any<blade>[sharp]@ally(2)"
    dsl_error.cache_clear()
    try:
        with patch("bartlebot.nodes.combat_runner._str_provider_class", wraps=_str_provider_class) as provider_class:
            for _ in range(10):
                assert validate_dsl(dsl) is None
                assert dsl_error(dsl) is None

        assert provider_class.call_count == 1
    finally:
        dsl_error.cache_clear()


def test_the_cache_does_not_confuse_two_dsls():
    dsl_error.cache_clear()
    try:
        assert validate_dsl("any<blade>[sharp]@ally(2)") is None
        assert validate_dsl("@@@ not a dsl") is not None
        assert validate_dsl("any<blade>[sharp]@ally(2)") is None
    finally:
        dsl_error.cache_clear()


def test_a_parse_error_says_where_the_parser_stopped():
    error = dsl_error("pass @ self\nany<damag> @ enemy")

    assert error is not None
    assert (error.line, error.column) == (2, 5)
    assert error.message == validate_dsl("pass @ self\nany<damag> @ enemy")


@pytest.mark.parametrize(
    "dsl, line, column",
    [
        ("pass @ self\n?(boss.hp > 0) pass", 2, 8),
        ("?(self.power_pips < 50%) pass", 1, 8),
        ("any<gambit(chrams)> @ enemy", 1, 12),
    ],
)
def test_an_error_found_while_transforming_is_placed_at_its_rule(dsl, line, column):
    """The editor underlines the misspelled name, not nothing (#214)."""
    error = dsl_error(dsl)

    assert error is not None
    assert (error.line, error.column) == (line, column)


def test_an_unknown_attribute_suggests_the_close_one():
    assert validate_dsl("?(boss.heath > 0) pass") == "Unknown condition attribute 'heath'. Did you mean 'health'?"


def test_an_error_with_no_position_reports_zero_rather_than_lark_s_minus_one():
    error = dsl_error("any<blade")

    assert error is not None
    assert error.line >= 0
    assert error.column >= 0


@pytest.mark.parametrize("dsl", ["any<!>", "any<trap & !>", "any<trap !aoe>"])
def test_a_bang_needs_a_type_or_a_name_after_it_and_an_ampersand_before_it(dsl):
    assert validate_dsl(dsl) is not None


def test_stacks_is_accepted():
    """The stacking check the shipped defaults order their buffs by (#219)."""
    assert validate_dsl("any<blade & stacks>[sharp] @ ally(last) | any<trap & stacks>[potent] @ boss") is None


def test_harmful_aura_and_stronger_are_accepted():
    """The negative aura on the boss that the supports cast (#224)."""
    assert validate_dsl("any<harmful_aura & inc_damage & stronger> @ boss | any<trap & !harmful_aura> @ boss") is None


@pytest.mark.parametrize(
    "dsl",
    ["any<trap & !gambit(charms)>", "any<damage & !req_met>", "any<blade & !stacks>", "any<aura & !stronger>"],
)
def test_only_a_spell_type_can_be_negated(dsl):
    """A hanging verb, req_met, stacks and stronger are checked after selection, so a negation of one
    could never exclude a card. The transformer says so, and the message is its own
    rather than Lark's wrapper naming the rule."""
    assert validate_dsl(dsl).startswith("Only a spell type can be negated")


def test_provider_class_is_cached():
    first = _str_provider_class()
    second = _str_provider_class()

    assert first is second


def test_provider_parses_without_a_client():
    """validate_dsl must be callable with no game running."""
    provider = _str_provider_class()("any<blade>[sharp]@ally(2)")

    assert provider.config is not None
    assert provider.filename == "<inline>"


# ---------------------------------------------------------------------------
# Spell names no card has — against the real snapshot
# ---------------------------------------------------------------------------


def _warned_names(dsl: str) -> list[str]:
    lines = dsl.split("\n")
    return [lines[w.line - 1][w.column - 1 : w.column - 1 + w.length] for w in dsl_warnings(dsl)]


def test_names_that_find_a_card_are_not_warned_about():
    dsl = "feint mass | feint[potent]@boss\nTempest | Storm Lord | any<blade>[Sharpened Blade] @ self"
    assert dsl_warnings(dsl) == ()


@pytest.mark.parametrize(
    ("dsl", "advice"),
    [
        # The usual opener: the AoE feint, then a single feint on the boss if needed.
        ("mass feint | feint[potent]@boss", "so write Feint Mass instead."),
        ("Incindiate @ aoe", "the card is named Detonate All."),
        ("Fire Elemental @ self", "the card is named Minion Fire."),
        ("Strom Lord @ aoe", "Did you mean Storm Lord?"),
    ],
)
def test_the_common_mistakes_are_warned_about_with_what_to_write(dsl, advice):
    (warning,) = dsl_warnings(dsl)
    assert warning.message.endswith(advice)


def test_a_warning_covers_the_name_where_it_is_written():
    dsl = "Tempest @ aoe\n  Strom Lord[Epic] @ enemy | pass"
    (warning,) = dsl_warnings(dsl)
    assert (warning.line, warning.column, warning.length) == (2, 3, 10)


# ---------------------------------------------------------------------------
# Lines are rounds
# ---------------------------------------------------------------------------


def _rounds(dsl: str) -> list[list[str]]:
    config = _str_provider_class()(dsl).config
    lines = [*config.specific_rounds.values(), *config.infinite_rounds]  # {n} lines, then the rest
    return [[getattr(p.move.card, "name", p.move.card) for p in line.priorities] for line in lines]


@pytest.mark.parametrize(
    "dsl",
    [
        "pass\nStorm Lord @ enemy",
        "{1} pass\n{2} Storm Lord @ enemy",
        "  pass\n  Storm Lord @ enemy",
        "pass\n\n\nStorm Lord @ enemy\n",
    ],
)
def test_a_multi_word_spell_on_a_later_line_stays_one_move(dsl):
    """The grammar once let a line end anywhere, and past the first line it ended one
    between the words of a spell name: ``Storm Lord`` ran as a round casting Storm and
    another casting Lord, shifting every round after it."""
    names = [name for round_ in _rounds(dsl) for name in round_]
    assert "Storm Lord" in names
    assert "Storm" not in names and "Lord" not in names


def test_moves_on_one_line_need_a_pipe_between_them():
    # Once two rounds from one line, silently; a newline or a | says which was meant.
    assert validate_dsl("Tempest @ aoe Storm Lord @ enemy") is not None
    assert validate_dsl("Tempest @ aoe | Storm Lord @ enemy") is None


def test_enchants_and_discard_lists_are_checked_but_combatants_and_keywords_are_not():
    dsl = "Tempest[Zzyzx] @ Lord Zzyzx | discard @ spell(Incindiate, any<aoe>) | pass"
    assert _warned_names(dsl) == ["Zzyzx", "Incindiate"]


def test_an_excluded_name_is_checked_but_a_negated_type_is_not():
    assert dsl_warnings("any<trap & !feint & !aoe> @ boss") == ()
    assert _warned_names("any<trap & !Fient> @ boss") == ["Fient"]


def test_a_number_in_an_unquoted_name_is_warned_about_across_the_whole_name():
    dsl = "Detonate All 200 @ aoe"
    (warning,) = dsl_warnings(dsl)
    assert "read a digit at a time" in warning.message
    assert _warned_names(dsl) == ["Detonate All 200"]


def test_a_dsl_that_does_not_parse_has_no_name_warnings():
    assert dsl_warnings("Strom Lord @ @") == ()


def test_each_warning_message_is_listed_once():
    warnings = dsl_warnings("Strom Lord @ aoe | Strom Lord @ boss")
    assert len(warnings) == 2
    assert dsl_warning_text(warnings) == warnings[0].message
