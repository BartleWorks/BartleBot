"""The controller's own module functions. Its flows are tested beside their modules, under ``tests/controller/``."""

from __future__ import annotations


async def test_a_run_is_traced_only_in_developer_mode(monkeypatch, tmp_path):
    from bartleby.runtime import run_trace  # noqa: PLC0415

    import bartlebot.app_controller as ac  # noqa: PLC0415

    monkeypatch.setattr(ac, "session_log_dir", lambda: tmp_path)
    monkeypatch.delenv("BARTLEBOT_DEV", raising=False)
    try:
        assert ac.start_run_trace(lambda client: 1) is None
        assert not run_trace.active()

        monkeypatch.setenv("BARTLEBOT_DEV", "1")
        path = ac.start_run_trace(lambda client: 1)
        assert run_trace.active()
        assert path.parent == tmp_path / "trace"
    finally:
        run_trace.stop()
