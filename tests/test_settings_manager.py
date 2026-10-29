def test_recent_bot_files_in_dev_mode_list_only_repo_files(tmp_path, monkeypatch):
    from bartlebot.paths import project_root
    from bartlebot.settings_manager import SettingsManager

    mine = tmp_path / "mine.json"
    mine.write_text("{}")
    official = next(project_root().joinpath("bots").rglob("*.json"))
    sm = SettingsManager.instance()
    sm.set("recent_bot_files", value=[str(mine), str(official)])
    monkeypatch.setenv("BARTLEBOT_DEV", "1")
    assert sm.recent_bot_files == [str(official.resolve())]
    sm.add_recent_bot_file(str(official))
    monkeypatch.delenv("BARTLEBOT_DEV")
    assert str(mine.resolve()) in sm.recent_bot_files


# -- the layout setup fits ------------------------------------------------------------


def test_setup_may_fit_the_shipped_pane_sizes_and_resolution():
    from bartlebot.settings_manager import FITTED_LAYOUT_KEYS, SettingsManager

    sm = SettingsManager.instance()

    assert all(sm.layout_follows_setup(key) for key in FITTED_LAYOUT_KEYS)
    assert sm.resolution_follows_setup


def test_setup_may_fit_a_pane_size_it_fitted_before():
    from bartlebot.settings_manager import SettingsManager

    sm = SettingsManager.instance()
    sm.set_fitted_layout({"bottom_section_height": 321, "right_sidebar_width": 500})

    assert sm.get_general("bottom_section_height") == 321
    assert sm.layout_follows_setup("bottom_section_height")
    assert sm.layout_follows_setup("right_sidebar_width")


def test_a_pane_size_chosen_in_settings_is_the_users():
    from bartlebot.settings_manager import SettingsManager

    sm = SettingsManager.instance()
    sm.set_fitted_layout({"bottom_section_height": 321})
    sm.set_general("bottom_section_height", 400)

    assert not sm.layout_follows_setup("bottom_section_height")
    assert sm.layout_follows_setup("left_sidebar_width")


def test_setup_may_fit_a_resolution_it_fitted_before_but_not_one_chosen_in_settings():
    from bartlebot.settings_manager import SettingsManager

    sm = SettingsManager.instance()
    sm.set_fitted_resolution((960, 540))
    assert sm.game_resolution == (960, 540)
    assert sm.resolution_follows_setup

    sm.game_resolution = (1024, 768)

    assert not sm.resolution_follows_setup


def test_layout_budget_can_judge_another_canvas_height():
    from bartlebot.settings_manager import SettingsManager

    sm = SettingsManager.instance()
    sm.set_general("bottom_section_height", 500)

    assert sm.layout_budget((1920, 1032), bottom_section=220)[1] == 1032 - 220 - 60
    assert sm.layout_budget((1920, 1032))[1] == 1032 - 500 - 60


# -- the file itself (#154) -----------------------------------------------------------


def test_a_save_that_fails_part_way_leaves_the_last_file_whole(monkeypatch):
    from bartleby.shared import strategy_models

    from bartlebot.settings_manager import SettingsManager

    sm = SettingsManager()
    sm.save()
    before = sm.settings_path.read_bytes()

    def _dies_part_way(data, fh, **kwargs):
        fh.write("{")
        raise OSError("disk full")

    monkeypatch.setattr(strategy_models.json, "dump", _dies_part_way)
    sm.set("general", "theme", value="dark")
    try:
        sm.save()
    except OSError:
        pass

    assert sm.settings_path.read_bytes() == before


def test_an_unreadable_file_is_kept_and_reported(caplog):
    from bartlebot.settings_manager import SettingsManager

    path = SettingsManager().settings_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"general": {"theme": "da', encoding="utf-8")

    SettingsManager()

    kept = path.with_name("settings.json.unreadable")
    assert kept.read_text(encoding="utf-8") == '{"general": {"theme": "da'
    assert "could not be read" in caplog.text
