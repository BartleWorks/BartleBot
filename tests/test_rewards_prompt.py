"""The save-time Rewards prompt: the dialog, and the controller's once-per-open rule.

Which bots are asked at all is ``wants_rewards_prompt``'s, tested in ``test_bot_tags``.
This is what happens once one is: the three answers, and that Save & Done only leaves
Edit mode behind a save that went through.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QDialog

import bartlebot.app_controller as app_controller
from bartlebot.app_controller import AppController
from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.gui.rewards_tag_dialog import RewardsChoice, RewardsTagDialog
from bartlebot.gui.widgets.tag_pill import SuggestedTagPill


def _config(tags: list[str] | None = None) -> BotConfig:
    return BotConfig(
        id="bot-1",
        name="Test Bot",
        initial="s0",
        recovery_state="s0",
        states=[StateModel(id="s0", node_type="collect_reagent", params={})],
        transitions=[],
        tags=tags or ["Boss"],
    )


# ---------------------------------------------------------------------------
# The dialog
# ---------------------------------------------------------------------------


def _pill(dialog: RewardsTagDialog, tag: str) -> SuggestedTagPill:
    return next(p for p in dialog.findChildren(SuggestedTagPill) if p.tag == tag)


def test_the_dialog_offers_every_reward_and_starts_with_the_suggested_ones_checked(qapp):
    dialog = RewardsTagDialog(["Reagents", "Collecting"])

    assert len(dialog.findChildren(SuggestedTagPill)) == 12
    # Collecting is an activity: suggested, but not the dialog's to offer.
    assert dialog.selected_tags() == ["Reagents"]


def test_save_waits_for_a_tag(qapp):
    dialog = RewardsTagDialog([])
    assert not dialog._commit.isEnabled()

    _pill(dialog, "Gear").setChecked(True)

    assert dialog._commit.isEnabled()
    assert dialog.selected_tags() == ["Gear"]


def test_save_and_save_without_say_which_they_were(qapp):
    tagged = RewardsTagDialog(["Reagents"])
    tagged._commit.click()
    assert tagged.result() == QDialog.DialogCode.Accepted
    assert tagged.choice is RewardsChoice.TAGGED

    without = RewardsTagDialog([])
    without._save_without()
    assert without.result() == QDialog.DialogCode.Accepted
    assert without.choice is RewardsChoice.WITHOUT


# ---------------------------------------------------------------------------
# The controller
# ---------------------------------------------------------------------------


class _FakeDialog:
    """Stands in for the modal: answers with whatever the test queued, and counts the asks."""

    answers: list[tuple[QDialog.DialogCode, RewardsChoice | None, list[str]]] = []
    asked = 0

    def __init__(self, suggested, parent) -> None:
        type(self).asked += 1
        self._code, self.choice, self._tags = type(self).answers.pop(0)

    def exec(self):
        return self._code

    def selected_tags(self) -> list[str]:
        return self._tags


@pytest.fixture
def fake_dialog(monkeypatch):
    _FakeDialog.answers = []
    _FakeDialog.asked = 0
    monkeypatch.setattr(app_controller, "RewardsTagDialog", _FakeDialog)
    return _FakeDialog


def _controller(config: BotConfig, path) -> SimpleNamespace:
    """A stand-in ``self`` with the real save methods bound to it; the window is inert."""
    exits: list[bool] = []
    ns = SimpleNamespace(
        _loaded_config=config,
        _last_save_path=str(path),
        _has_unsaved_changes=True,
        _rewards_declined_for=None,
        _delete_autosave=lambda *a: None,
        exits=exits,
        _window=SimpleNamespace(
            bot_canvas=SimpleNamespace(
                refresh_tags=lambda: None,
                exit_edit_mode=lambda saved=False: exits.append(saved),
            ),
        ),
    )
    for name in ("_save_bot", "_confirm_rewards_tags", "_save_and_done"):
        setattr(ns, name, getattr(AppController, name).__get__(ns))
    return ns


def test_cancel_writes_nothing_and_stays_in_edit_mode(tmp_path, fake_dialog):
    path = tmp_path / "bot.json"
    fake_dialog.answers = [(QDialog.DialogCode.Rejected, None, [])]
    controller = _controller(_config(), path)

    controller._save_and_done()

    assert not path.exists()
    assert controller._has_unsaved_changes
    assert controller.exits == []


def test_picking_a_reward_saves_it_into_the_file(tmp_path, fake_dialog):
    path = tmp_path / "bot.json"
    fake_dialog.answers = [(QDialog.DialogCode.Accepted, RewardsChoice.TAGGED, ["Pets"])]
    controller = _controller(_config(), path)

    controller._save_and_done()

    assert BotConfig.load(path).tags == ["Pets", "Boss"]
    assert controller.exits == [True]


def test_save_without_is_not_asked_again_this_open(tmp_path, fake_dialog):
    path = tmp_path / "bot.json"
    fake_dialog.answers = [(QDialog.DialogCode.Accepted, RewardsChoice.WITHOUT, [])]
    controller = _controller(_config(), path)

    assert controller._save_bot()
    assert controller._save_bot()

    assert fake_dialog.asked == 1
    assert BotConfig.load(path).tags == ["Boss"]


def test_a_bot_with_a_reward_is_never_asked(tmp_path, fake_dialog):
    controller = _controller(_config(["Gear", "Boss"]), tmp_path / "bot.json")

    assert controller._save_bot()

    assert fake_dialog.asked == 0
