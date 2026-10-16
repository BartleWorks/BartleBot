"""The log header says whether this is a packaged build or a checkout, and never where it is."""

from __future__ import annotations

import sys

from bartlebot.packaging import build_kind as bk


def test_a_checkout_says_so(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)

    assert not bk.is_packaged()
    assert bk.build_kind() == bk.CHECKOUT


def test_a_packaged_build_says_so(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert bk.is_packaged()
    assert bk.build_kind() == bk.PACKAGED
    assert "\\" not in bk.build_kind() and "/" not in bk.build_kind()
