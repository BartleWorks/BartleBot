"""Tests for tools/build_site_reference.py, the command line over bartlebot.nodes.reference."""

from __future__ import annotations

import os
import sys

from bartlebot.paths import project_root

sys.path.insert(0, str(project_root()))

from tools import build_site_reference  # noqa: E402


def test_check_passes_on_the_committed_reference_and_reads_only_what_every_install_ships(monkeypatch, capsys):
    monkeypatch.setenv("BARTLEBOT_DEV", "1")

    assert build_site_reference.main(["--check"]) == 0
    assert "BARTLEBOT_DEV" not in os.environ
    assert capsys.readouterr().out == ""
