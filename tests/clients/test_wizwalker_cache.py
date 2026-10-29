"""wizwalker's cache lands under the vendor root, never under StarrFox (#234)."""

from types import SimpleNamespace

import pytest
from bartleby.runtime.clients.wizwalker_cache import apply_cache_dir, cache_dir
from bartleby.shared.vendor import local_vendor_dir
from wizwalker import utils as ww_utils

from bartlebot import app_controller
from bartlebot.app_controller import AppController


@pytest.fixture(autouse=True)
def _default_cache_folder_after():
    """The override is a module global in wizwalker; no other test should inherit it."""
    yield
    ww_utils.override_cache_folder(None)


def test_the_cache_is_under_the_local_vendor_root_not_the_product():
    """Local, not roaming: wad_cache.data describes this machine's game files."""
    assert cache_dir().parent.parent == local_vendor_dir()


def test_applied_wizwalker_puts_its_cache_there():
    apply_cache_dir()

    folder = ww_utils.get_cache_folder()

    assert folder == cache_dir().absolute()
    assert folder.is_dir()
    assert "StarrFox" not in folder.parts


def test_a_second_run_finds_what_the_first_wrote():
    apply_cache_dir()
    (ww_utils.get_cache_folder() / "template_ids.json").write_text("{}", encoding="utf-8")

    ww_utils.override_cache_folder(None)
    apply_cache_dir()

    assert (ww_utils.get_cache_folder() / "template_ids.json").read_text(encoding="utf-8") == "{}"


def test_unapplied_wizwalker_keeps_its_own_default(tmp_path, monkeypatch):
    """The library knows nothing of the vendor; its default is unchanged for other tools."""
    default = tmp_path / "StarrFox" / "WizWalker" / "Cache"
    monkeypatch.setattr(ww_utils.appdirs, "user_cache_dir", lambda name, author: str(default))

    assert ww_utils.get_cache_folder() == default


class _Stop(Exception):
    """Raised by the stand-in for the first thing after the cache folder is applied."""


def test_the_app_applies_it_at_startup_before_the_handler_exists(monkeypatch):
    """The handler's CacheHandler keeps the folder it first finds, so the override comes first."""
    calls: list[str] = []
    monkeypatch.setattr(app_controller, "apply_install", lambda: None)
    monkeypatch.setattr(app_controller, "apply_cache_dir", lambda: calls.append("cache"))
    monkeypatch.setattr(app_controller, "install_app_icon", lambda _app: None)
    monkeypatch.setattr(AppController, "_apply_theme", lambda _self: None)

    def handler():
        calls.append("handler")
        raise _Stop

    monkeypatch.setattr(app_controller, "WizSprinter", handler)
    app = SimpleNamespace(
        setOrganizationName=lambda _n: None, setApplicationName=lambda _n: None, setStyle=lambda _s: None
    )

    with pytest.raises(_Stop):
        AppController(app)

    assert calls == ["cache", "handler"]
