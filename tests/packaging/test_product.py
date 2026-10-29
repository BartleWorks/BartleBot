"""BartleBot as bartleby-core's ``Product``: its name, version, look and folders."""

from __future__ import annotations

import importlib.metadata

from bartleby.shared.vendor import product_dir

from bartlebot.gui import logo
from bartlebot.packaging import product as bartlebot_product
from bartlebot.packaging.build_kind import CHECKOUT
from bartlebot.paths import project_root


def test_bartlebot_is_named_and_versioned_as_its_package_is():
    built = bartlebot_product.product()

    assert built.name == "BartleBot"
    assert built.slug == "bartlebot", "the import package, and the session logs' prefix"
    assert built.version == importlib.metadata.version("bartlebot")
    assert built.build_kind == CHECKOUT


def test_its_logo_is_the_one_the_rasters_are_drawn_from():
    assert bartlebot_product.product().logo == logo.LOGO_SVG
    assert logo.LOGO_SVG.is_file()


def test_its_data_and_logs_are_under_its_own_appdata_folder():
    built = bartlebot_product.product()

    assert built.data_dir == product_dir("BartleBot")
    assert built.log_dir == product_dir("BartleBot") / "logs"


def test_in_developer_mode_its_logs_stay_in_the_checkout(monkeypatch):
    monkeypatch.setenv("BARTLEBOT_DEV", "1")

    built = bartlebot_product.product()

    assert built.dev_mode is True
    assert built.log_dir == project_root() / "logs"
