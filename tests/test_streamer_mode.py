"""Tests for Streamer Mode's name masking.

The setting is read through SettingsManager, which conftest's autouse
``isolated_settings`` fixture already points at a per-test directory — so these run
against a fresh, default-off configuration rather than whatever the developer has
enabled locally.
"""

from bartlebot import streamer_mode
from bartlebot.settings_manager import SettingsManager


def _enable() -> None:
    SettingsManager.instance().streamer_mode = True


def test_off_by_default():
    assert streamer_mode.enabled() is False


def test_shows_the_real_name_when_off():
    assert streamer_mode.display_name("Luke SkullHunter", 0) == "Luke SkullHunter"


def test_substitutes_a_positional_placeholder_when_on():
    _enable()
    assert streamer_mode.display_name("Luke SkullHunter", 0) == "Character 1"
    assert streamer_mode.display_name("Misha SkullSeer", 3) == "Character 4"


def test_the_placeholder_carries_nothing_of_the_name():
    """The whole point: two wizards sharing a name are still told apart, and neither
    contributes a fragment of itself to what is shown."""
    _enable()
    first = streamer_mode.display_name("Wolf", 0)
    second = streamer_mode.display_name("Wolf", 1)
    assert first != second
    assert "Wolf" not in first + second


def test_an_unknown_name_answers_the_placeholder_either_way():
    """Not knowing a name is not a privacy question, so the setting must not change it —
    and a masked placeholder here would claim a character that may not exist."""
    assert streamer_mode.display_name(None, 0) == "—"
    assert streamer_mode.display_name("", 0) == "—"
    _enable()
    assert streamer_mode.display_name(None, 0) == "—"
    assert streamer_mode.display_name("", 0) == "—"


def test_the_caller_chooses_the_unknown_placeholder():
    assert streamer_mode.display_name(None, 0, placeholder="(none)") == "(none)"


def test_the_setting_is_read_per_call_not_cached():
    """A settings toggle has to take effect without rebuilding the widgets that call
    this, which is the reason masking happens at display time rather than at the source."""
    assert streamer_mode.display_name("Luke SkullHunter", 0) == "Luke SkullHunter"
    _enable()
    assert streamer_mode.display_name("Luke SkullHunter", 0) == "Character 1"
    SettingsManager.instance().streamer_mode = False
    assert streamer_mode.display_name("Luke SkullHunter", 0) == "Luke SkullHunter"
