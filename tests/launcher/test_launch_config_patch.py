"""Tests for the config.xml / preferences.xml patching done before a client launches."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bartlebot.launcher import launch_profile
from bartlebot.settings_manager import SettingsManager

# Shaped like the game's own file: a declaration it parses strictly, tags carrying
# attributes, and neighbors that must survive untouched.
CONFIG_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    "<config>\n"
    '    <QuickFriendTeleport TYPE="INT">1</QuickFriendTeleport>\n'
    '    <LastPlayer TYPE="GID">12345678901234567</LastPlayer>\n'
    '    <WindowedX TYPE="INT">258</WindowedX>\n'
    '    <WindowedY TYPE="INT">137</WindowedY>\n'
    "</config>\n"
)


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "config.xml"
    path.write_text(CONFIG_XML, encoding="utf-8")
    return path


def _settings(**kwargs):
    """Patch SettingsManager as the launcher sees it."""
    sm = patch("bartlebot.launcher.launch_profile.SettingsManager")
    mock = sm.start()
    mock.instance.return_value = MagicMock(**kwargs)
    return sm


def test_patch_rewrites_only_the_named_tag_text(config):
    launch_profile._patch_xml_fields(config, {"WindowedX": "50"})
    text = config.read_text(encoding="utf-8")
    assert '<WindowedX TYPE="INT">50</WindowedX>' in text
    # Declaration, attributes and neighbors are byte-identical.
    assert text == CONFIG_XML.replace(">258<", ">50<")


def test_patch_skips_a_tag_the_file_does_not_have(config, caplog):
    launch_profile._patch_xml_fields(config, {"NotAThing": "0"})
    assert config.read_text(encoding="utf-8") == CONFIG_XML
    assert "NotAThing" in caplog.text


def test_setting_on_forces_quick_friend_teleport_off():
    sm = _settings(disable_quick_friend_teleport=True)
    try:
        assert launch_profile._launch_config_updates() == {"QuickFriendTeleport": "0"}
    finally:
        sm.stop()


def test_setting_off_leaves_quick_friend_teleport_alone():
    sm = _settings(disable_quick_friend_teleport=False)
    try:
        assert launch_profile._launch_config_updates() == {}
    finally:
        sm.stop()


def test_window_config_patch_carries_the_quick_teleport_tag(tmp_path, config):
    (tmp_path / "preferences.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<prefs>\n    <Resolution>800x600</Resolution>\n</prefs>\n',
        encoding="utf-8",
    )
    install = MagicMock()
    install.__truediv__ = lambda _self, _name: tmp_path

    sm = _settings(disable_quick_friend_teleport=True)
    try:
        with patch.object(launch_profile, "get_wiz_install", return_value=install):
            launch_profile._patch_window_config(1024, 768, 50, 50)
    finally:
        sm.stop()

    text = config.read_text(encoding="utf-8")
    assert '<QuickFriendTeleport TYPE="INT">0</QuickFriendTeleport>' in text
    assert '<WindowedX TYPE="INT">50</WindowedX>' in text
    assert "1024x768" in (tmp_path / "preferences.xml").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# config.xml lookup
# ---------------------------------------------------------------------------


@pytest.fixture
def install(tmp_path, config):
    """Point the launcher's config.xml lookup at the tmp file."""
    bin_dir = MagicMock()
    bin_dir.__truediv__ = lambda _self, _name: config
    root = MagicMock()
    root.__truediv__ = lambda _self, _name: bin_dir
    with patch.object(launch_profile, "get_wiz_install", return_value=root):
        yield


# ---------------------------------------------------------------------------
# UISize
# ---------------------------------------------------------------------------
#
# The game's HUD is fixed-pixel, so a launch below 800x600 has to scale it down or the
# interface overflows the client area. That clamp is the user's own setting being
# overwritten, which is why every test here is really about giving it back.

_HUGE = 4  # the game's largest UI scale, and its default
_MICRO = 5  # the smallest, and the only one 534x300 can display
_LARGE = 3  # what a 4:3 client can render at 2048x1536
_REGULAR = 2  # the cap at the stock 800x600 floor: Large renders there, but its NPC prompts cover the compass


@pytest.fixture
def ui_config(tmp_path):
    """A config.xml carrying a <UISize>, with the launcher pointed at it."""
    path = tmp_path / "config.xml"
    path.write_text(CONFIG_XML.replace("<config>\n", f'<config>\n    <UISize TYPE="INT">{_HUGE}</UISize>\n'), "utf-8")
    bin_dir = MagicMock()
    bin_dir.__truediv__ = lambda _self, _name: path
    root = MagicMock()
    root.__truediv__ = lambda _self, _name: bin_dir
    with patch.object(launch_profile, "get_wiz_install", return_value=root):
        yield path


def test_reading_the_ui_scale_finds_the_stored_value(ui_config):
    assert launch_profile.read_ui_size() == _HUGE


def test_the_ui_scale_is_none_when_the_game_never_wrote_one(install):
    """CONFIG_XML has no <UISize>: nothing to clamp, and nothing to restore."""
    assert launch_profile.read_ui_size() is None


def test_a_small_launch_clamps_the_ui_scale_and_captures_the_original(ui_config):
    assert launch_profile._ui_size_update(534, 300) == {"UISize": str(_MICRO)}
    assert SettingsManager.instance().original_ui_size == _HUGE


def test_a_launch_the_scale_fits_leaves_it_where_it_is(ui_config):
    """Widescreen, not 4:3: no 4:3 resolution carries Huge at any size.

    1280x720 is the smallest offered resolution that leaves a Huge preference untouched.
    A 4:3 client always pays a ladder step, so there is no 4:3 size that would do here --
    2048x1536 still clamps to Large.
    """
    assert launch_profile._ui_size_update(1280, 720) == {"UISize": str(_HUGE)}
    assert launch_profile._ui_size_update(2048, 1536) == {"UISize": str(_LARGE)}


def test_the_stock_floor_still_clamps_a_huge_preference_down(ui_config):
    """800x600 is capped at Regular -- the clamp is not only a sub-floor concern.

    The HUD can render Large there, but the game's own default resolution is capped
    one rung lower (``_DEFAULT_RESOLUTION_UI_SIZE_CAP``): at Large the NPC interact
    prompt sits over the teleport compass button.
    """
    assert launch_profile._ui_size_update(800, 600) == {"UISize": str(_REGULAR)}


def test_the_capture_is_taken_once_and_never_from_our_own_clamp(ui_config):
    """The bug this exists to prevent: one clamped session leaves Micro in the file.

    Re-reading config.xml would adopt that as the user's preference, and their real one
    would be gone with nothing left that remembers it.
    """
    launch_profile._ui_size_update(534, 300)
    ui_config.write_text(ui_config.read_text("utf-8").replace(f">{_HUGE}<", f">{_MICRO}<"), "utf-8")

    launch_profile._ui_size_update(534, 300)
    assert SettingsManager.instance().original_ui_size == _HUGE
    # ...and the next launch big enough to carry it puts it back, rather than staying
    # clamped forever because the file now agrees with the clamp.
    assert launch_profile._ui_size_update(1280, 720) == {"UISize": str(_HUGE)}


def test_nothing_is_written_when_the_game_has_no_ui_scale_to_clamp(install):
    assert launch_profile._ui_size_update(534, 300) == {}
    assert SettingsManager.instance().original_ui_size is None


def test_the_window_config_patch_clamps_the_scale_alongside_the_resolution(tmp_path, ui_config):
    (tmp_path / "preferences.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<prefs>\n    <Resolution>800x600</Resolution>\n</prefs>\n',
        encoding="utf-8",
    )
    install_root = MagicMock()
    install_root.__truediv__ = lambda _self, _name: tmp_path

    sm = _settings(disable_quick_friend_teleport=False)
    try:
        # The capture and the clamp both go through the real SettingsManager, which the
        # module-level patch above would otherwise replace with a MagicMock.
        with (
            patch.object(launch_profile, "get_wiz_install", return_value=install_root),
            patch.object(launch_profile, "_ui_size_update", return_value={"UISize": str(_MICRO)}),
        ):
            launch_profile._patch_window_config(534, 300, 50, 50)
    finally:
        sm.stop()

    assert "534x300" in (tmp_path / "preferences.xml").read_text(encoding="utf-8")
    # Resolution goes to preferences.xml, UISize to config.xml, and neither file holds
    # both — the split that makes this worth a test at all.
    config_text = (tmp_path / "config.xml").read_text(encoding="utf-8")
    assert f'<UISize TYPE="INT">{_MICRO}</UISize>' in config_text
    assert "534x300" not in config_text
    assert "UISize" not in (tmp_path / "preferences.xml").read_text(encoding="utf-8")


def test_restoring_puts_the_user_s_scale_back_and_forgets_it(ui_config):
    launch_profile._ui_size_update(534, 300)
    launch_profile._patch_xml_fields(ui_config, {"UISize": str(_MICRO)})

    launch_profile.restore_ui_size()

    assert launch_profile.read_ui_size() == _HUGE
    assert SettingsManager.instance().original_ui_size is None


def test_restoring_twice_is_harmless(ui_config):
    """It runs on quit and again on the next start, since the quit may never have come."""
    launch_profile._ui_size_update(534, 300)
    launch_profile.restore_ui_size()
    launch_profile.restore_ui_size()
    assert launch_profile.read_ui_size() == _HUGE


def test_restoring_with_nothing_captured_touches_nothing(ui_config):
    before = ui_config.read_text(encoding="utf-8")
    launch_profile.restore_ui_size()
    assert ui_config.read_text(encoding="utf-8") == before


def test_a_failed_restore_keeps_the_capture_for_the_next_start(ui_config):
    """Losing the capture would leave the user's game clamped with nothing to undo it."""
    launch_profile._ui_size_update(534, 300)
    with patch.object(launch_profile, "_patch_xml_fields", side_effect=OSError("locked")):
        launch_profile.restore_ui_size()
    assert SettingsManager.instance().original_ui_size == _HUGE


# ---------------------------------------------------------------------------
# Resolution and IsFullscreen
# ---------------------------------------------------------------------------
#
# Every launch writes the tiled resolution into preferences.xml, and a fullscreen game
# ignores it — so both are the user's own settings being overwritten, and both get the
# same capture-once/restore-on-quit treatment <UISize> already had.

_USER_RESOLUTION = "1920x1080"

PREFERENCES_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    "<prefs>\n"
    f"    <Resolution>{_USER_RESOLUTION}</Resolution>\n"
    '    <IsFullscreen TYPE="INT">1</IsFullscreen>\n'
    '    <GammaLevel TYPE="FLOAT">1.0</GammaLevel>\n'
    "</prefs>\n"
)


@pytest.fixture
def prefs(tmp_path):
    """A preferences.xml carrying the user's own display settings, launcher pointed at it."""
    path = tmp_path / "preferences.xml"
    path.write_text(PREFERENCES_XML, encoding="utf-8")
    bin_dir = MagicMock()
    bin_dir.__truediv__ = lambda _self, _name: path
    root = MagicMock()
    root.__truediv__ = lambda _self, _name: bin_dir
    with patch.object(launch_profile, "get_wiz_install", return_value=root):
        yield path


def test_a_launch_forces_windowed_mode_alongside_the_resolution(prefs):
    """Fullscreen makes the game ignore <Resolution>, so patching one without the other
    puts up a window the tile cannot hold, with nothing saying why."""
    assert launch_profile._prefs_update(1024, 768) == {"Resolution": "1024x768", "IsFullscreen": "0"}


def test_a_launch_captures_the_user_s_resolution_and_window_mode(prefs):
    launch_profile._prefs_update(800, 600)
    settings = SettingsManager.instance()
    assert settings.original_resolution == _USER_RESOLUTION
    assert settings.original_fullscreen == 1


def test_the_prefs_capture_is_taken_once_and_never_from_our_own_write(prefs):
    """The bug this exists to prevent: one tiled session leaves 800x600 in the file.

    Re-reading preferences.xml would adopt that as the user's own resolution, and theirs
    would be gone with nothing left that remembers it.
    """
    launch_profile._patch_xml_fields(prefs, launch_profile._prefs_update(800, 600))

    launch_profile._prefs_update(800, 600)

    settings = SettingsManager.instance()
    assert settings.original_resolution == _USER_RESOLUTION
    assert settings.original_fullscreen == 1


def test_nothing_is_captured_when_the_game_wrote_no_display_prefs(prefs):
    prefs.write_text('<?xml version="1.0" encoding="utf-8"?>\n<prefs>\n</prefs>\n', encoding="utf-8")
    launch_profile._prefs_update(800, 600)
    settings = SettingsManager.instance()
    assert settings.original_resolution is None
    assert settings.original_fullscreen is None


def test_restoring_puts_the_display_prefs_back_and_forgets_them(prefs):
    launch_profile._patch_xml_fields(prefs, launch_profile._prefs_update(534, 300))

    launch_profile.restore_game_prefs()

    assert prefs.read_text(encoding="utf-8") == PREFERENCES_XML
    settings = SettingsManager.instance()
    assert settings.original_resolution is None
    assert settings.original_fullscreen is None


def test_restoring_the_display_prefs_twice_is_harmless(prefs):
    """It runs on quit and again on the next start, since the quit may never have come."""
    launch_profile._patch_xml_fields(prefs, launch_profile._prefs_update(534, 300))
    launch_profile.restore_game_prefs()
    launch_profile.restore_game_prefs()
    assert prefs.read_text(encoding="utf-8") == PREFERENCES_XML


def test_restoring_display_prefs_with_nothing_captured_touches_nothing(prefs):
    before = prefs.read_text(encoding="utf-8")
    launch_profile.restore_game_prefs()
    assert prefs.read_text(encoding="utf-8") == before


def test_a_failed_prefs_restore_keeps_the_captures_for_the_next_start(prefs):
    """Losing them would leave the user's game tiled with nothing left to undo it."""
    launch_profile._prefs_update(534, 300)
    with patch.object(launch_profile, "_patch_xml_fields", side_effect=OSError("locked")):
        launch_profile.restore_game_prefs()
    settings = SettingsManager.instance()
    assert settings.original_resolution == _USER_RESOLUTION
    assert settings.original_fullscreen == 1
