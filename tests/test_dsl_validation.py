"""Tests for combat_runner.validate_dsl and the shared Lark parser cache.

validate_dsl exists so a malformed combat DSL is reported in the pre-run warning
dialog rather than raising mid-fight inside SprintyCombat.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from bartlebot.defaults.combat_config import BARTLEBOT_DEFAULT_COMBAT_CONFIG
from bartlebot.nodes.combat_runner import _lark_parser, _str_provider_class, dsl_error, validate_dsl

# Real DSLs taken from strategies/player/*.json.
_VALID_DSLS = [
    BARTLEBOT_DEFAULT_COMBAT_CONFIG,
    "any<blade>[sharp]@ally(2)",
    "feint mass | any<blade>[sharp]@ally(2)",
    "any<aura & out_damage> | any<aoe>[epic]",
]


@pytest.mark.parametrize("dsl", _VALID_DSLS)
def test_valid_dsl_returns_none(dsl):
    assert validate_dsl(dsl) is None


def test_builtin_default_is_valid():
    """The fallback every unassigned player runs must always parse."""
    assert validate_dsl(BARTLEBOT_DEFAULT_COMBAT_CONFIG) is None


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


def test_an_error_with_no_position_reports_zero_rather_than_lark_s_minus_one():
    error = dsl_error("any<blade")

    assert error is not None
    assert error.line >= 0
    assert error.column >= 0


def test_provider_class_is_cached():
    first = _str_provider_class()
    second = _str_provider_class()

    assert first is second


def test_provider_parses_without_a_client():
    """validate_dsl must be callable with no game running."""
    provider = _str_provider_class()("any<blade>[sharp]@ally(2)")

    assert provider.config is not None
    assert provider.filename == "<inline>"
