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


# -- the canvas height setup fits -----------------------------------------------------


def test_setup_may_fit_the_shipped_canvas_height():
    from bartlebot.settings_manager import SettingsManager

    assert SettingsManager.instance().canvas_height_follows_setup


def test_setup_may_fit_a_canvas_height_it_fitted_before():
    from bartlebot.settings_manager import SettingsManager

    sm = SettingsManager.instance()
    sm.set_fitted_canvas_height(321)

    assert sm.get_general("bottom_section_height") == 321
    assert sm.canvas_height_follows_setup


def test_a_canvas_height_chosen_in_settings_is_the_users():
    from bartlebot.settings_manager import SettingsManager

    sm = SettingsManager.instance()
    sm.set_fitted_canvas_height(321)
    sm.set_general("bottom_section_height", 400)

    assert not sm.canvas_height_follows_setup


def test_layout_budget_can_judge_another_canvas_height():
    from bartlebot.settings_manager import SettingsManager

    sm = SettingsManager.instance()
    sm.set_general("bottom_section_height", 500)

    assert sm.layout_budget((1920, 1032), bottom_section=220)[1] == 1032 - 220 - 60
    assert sm.layout_budget((1920, 1032))[1] == 1032 - 500 - 60
