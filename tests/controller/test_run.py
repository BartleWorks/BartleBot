"""The run's module functions. Its sequence is tested through the stand-ins in the ``test_run_*.py`` beside this one
and ``tests/clients/test_auto_group.py``, which bind ``Run``'s methods."""

from __future__ import annotations


async def test_a_run_is_traced_only_in_developer_mode(monkeypatch, tmp_path):
    from bartleby.runtime import run_trace  # noqa: PLC0415

    import bartlebot.controller.run as run_module  # noqa: PLC0415

    monkeypatch.setattr(run_module, "session_log_dir", lambda: tmp_path)
    monkeypatch.delenv("BARTLEBOT_DEV", raising=False)
    try:
        assert run_module.start_run_trace(lambda client: 1) is None
        assert not run_trace.active()

        monkeypatch.setenv("BARTLEBOT_DEV", "1")
        path = run_module.start_run_trace(lambda client: 1)
        assert run_trace.active()
        assert path.parent == tmp_path / "trace"
    finally:
        run_trace.stop()
