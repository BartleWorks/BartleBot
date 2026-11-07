"""The template store's roots as the controller's flows share them, and the two questions they answer.

Opening a draft through the whole flow is ``tests/content/templates/test_template_drafts.py``.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QMessageBox

from bartlebot.bot.schema import BotConfig, StateModel, TemplateMeta
from bartlebot.controller.templates import DRAFT_FILE_LABEL, TemplateRoots


def _config(*, template: bool = False) -> BotConfig:
    return BotConfig(
        id="t-1",
        name="Farm",
        initial="s0",
        recovery_state="s0",
        states=[StateModel(id="s0", node_type="sleep", params={})],
        transitions=[],
        template_meta=TemplateMeta(blurb="Farms one boss.") if template else None,
    )


def test_a_bot_is_labelled_by_its_file_and_a_new_one_untitled():
    assert TemplateRoots.file_label(_config(), Path("bots/Wizard City/farm.json")) == "farm.json"
    assert TemplateRoots.file_label(_config(), None) == "Untitled"


def test_a_draft_is_labelled_as_one_since_every_draft_file_has_one_name():
    assert TemplateRoots.file_label(_config(template=True), Path("templates/farm/draft.json")) == DRAFT_FILE_LABEL


def test_the_roots_go_to_the_store_as_one_pair(tmp_path):
    roots = TemplateRoots(tmp_path / "shipped", tmp_path / "user")

    assert roots.kwargs() == {"root": tmp_path / "shipped", "user_root": tmp_path / "user"}


def test_a_released_version_is_never_opened_for_editing(qapp, monkeypatch, tmp_path):
    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: told.append(a[2]))

    assert not TemplateRoots().may_edit_template(_config(template=True), str(tmp_path / "v1.json"), None)
    assert "released version" in told[0]
