"""The shared strategy library: one copy at the vendor root, and two products writing it (#168).

What these hold: a file another program changed between load and save is never
overwritten, an older build never saves over a newer format, and the library's default
roots are the vendor directory and the install directory with the newest shipped set.
"""

from __future__ import annotations

from bartleby.shared import official_set
from bartleby.shared.official_set import InstallKey
from bartleby.shared.strategy_library import (
    LibraryKind,
)
from bartleby.shared.vendor import product_dir, vendor_dir

from bartlebot.content.store_roots import DEV_ENV_VAR, resolve_shared_roots
from bartlebot.packaging.product import NAME as PRODUCT
from bartlebot.paths import project_root

PLAYER = LibraryKind.PLAYER


# ---------------------------------------------------------------------------
# Saving checks before it writes
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Format versions
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Where the library is
# ---------------------------------------------------------------------------


def test_the_library_is_written_at_the_vendor_root_not_the_products():
    shipped, user = resolve_shared_roots(None, None)
    assert user == vendor_dir()
    assert user != product_dir(PRODUCT)
    assert shipped == project_root()


def test_developer_mode_keeps_the_library_in_the_repo(monkeypatch):
    monkeypatch.setenv(DEV_ENV_VAR, "1")
    official_set.set_registry(_Keys(InstallKey("BartleBuddy", project_root().parent, 99)))
    assert resolve_shared_roots(None, None) == (project_root(), project_root())


class _Keys:
    def __init__(self, *keys: InstallKey) -> None:
        self.keys = list(keys)

    def install_keys(self) -> list[InstallKey]:
        return list(self.keys)


def test_the_default_shipped_root_is_the_newest_installed_set(monkeypatch, tmp_path):
    buddy = tmp_path / "BartleBuddy"
    buddy.mkdir()
    official_set.set_registry(_Keys(InstallKey("BartleBuddy", buddy, official_set.set_version(project_root()) + 1)))
    assert resolve_shared_roots(None, None) == (buddy, vendor_dir())
