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
