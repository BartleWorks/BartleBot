"""Tests for the config.xml / preferences.xml patching done before a client launches."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bartlebot.launcher import launch_profile

# Shaped like the game's own file: a declaration it parses strictly, tags carrying
# attributes, and neighbours that must survive untouched.
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
    # Declaration, attributes and neighbours are byte-identical.
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
# LastPlayer (#112)
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


def test_last_player_reads_the_stored_gid(install):
    assert launch_profile.read_last_player() == 12345678901234567


def test_last_player_patch_replaces_only_that_gid(install, config):
    launch_profile.patch_last_player(999)
    assert launch_profile.read_last_player() == 999
    assert config.read_text(encoding="utf-8") == CONFIG_XML.replace(">12345678901234567<", ">999<")


def test_last_player_is_none_when_the_tag_is_missing(install, config):
    config.write_text(CONFIG_XML.replace('    <LastPlayer TYPE="GID">12345678901234567</LastPlayer>\n', ""), "utf-8")
    assert launch_profile.read_last_player() is None


def test_last_player_is_none_when_the_tag_is_not_a_number(install, config):
    # An empty <LastPlayer/> is what a fresh install writes, and "no character" is not 0.
    config.write_text(CONFIG_XML.replace(">12345678901234567<", "><"), encoding="utf-8")
    assert launch_profile.read_last_player() is None
