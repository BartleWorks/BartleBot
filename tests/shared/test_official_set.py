"""Which install directory's copy of the shared Official set is read (#168, #27).

The rule: the higher set version is used whole, the running product's own copy on a
tie, and a product whose key is gone (uninstalled) or whose folder is missing is
passed over, so the other falls back to its own set. Each set's version is read from
the ``official_set.json`` beside it, never from the registry.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bartleby.shared.official_set import SET_FILE, set_version

from bartlebot.paths import project_root

#: The version of the running product's own set, in these tests.
SHARED_SET_VERSION = 8


def _ship_set(root: Path, version: object) -> Path:
    """Give ``root`` a shared set that says ``version``."""
    path = root / SET_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": version}), encoding="utf-8")
    return root


@pytest.fixture
def own(tmp_path) -> Path:
    return _ship_set(tmp_path / "BartleBot", SHARED_SET_VERSION)


def test_the_shipped_set_says_its_version():
    assert set_version(project_root()) == json.loads((project_root() / SET_FILE).read_bytes())["version"]
    assert set_version(project_root()) >= 8
