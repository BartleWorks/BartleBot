"""One vocabulary for combat (#204): "profile" means a launch profile, and only that.

A Bot Combat Strategy was called a "combat profile" on some surfaces and in most of the
code, while "profile" meant a launch profile everywhere else. These keep the word out of
the combat surfaces, their code and their docs, so the two meanings cannot drift back
together. ``docs/COMBAT_CONFIG.md``'s "Naming" section has the writing rule.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from bartlebot.combat.bot_strategy_models import BotCombatStrategy, FightRef, StateAssignment
from bartlebot.shared.strategy_models import DeckPreset, DuelCombatStrategy, PlayerAssignment, PlayerCombatStrategy

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "bartlebot"

#: The combat surfaces' modules: the two combat packages whole, and the modules outside
#: them that draw or run a combat strategy.
COMBAT_MODULES = sorted(
    [
        *(PACKAGE / "combat").glob("*.py"),
        *(PACKAGE / "gui" / "combat_strategies").glob("*.py"),
        PACKAGE / "gui" / "sidebar" / "combat_tab.py",
        PACKAGE / "gui" / "sidebar" / "strategy_panel.py",
        PACKAGE / "gui" / "widgets" / "cards" / "strategy_card.py",
        PACKAGE / "gui" / "widgets" / "pills" / "tag_pill.py",
        PACKAGE / "nodes" / "combat_runner.py",
        PACKAGE / "content" / "share_export.py",
        PACKAGE / "content" / "share_import.py",
        PACKAGE / "content" / "bot_fork.py",
    ]
)

#: What may still say the word. A launch profile is the meaning it keeps, and
#: ``use_default_duel_profile`` is the pre-v3 spelling of a bot file's param, which old
#: bot files still carry and ``fight_keys`` still reads.
ALLOWED = re.compile(r"launch profiles?|use_default_duel_profile", re.IGNORECASE)

#: The docs that talk about combat.
COMBAT_DOCS = [
    ROOT / "CLAUDE.md",
    ROOT / "bartlebot" / "gui" / "CLAUDE.md",
    ROOT / "docs" / "COMBAT_CONFIG.md",
    ROOT / "docs" / "EPICS.md",
    ROOT / "docs" / "SHARING.md",
    ROOT / "docs" / "TEMPLATES.md",
]


def _strays(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if "profile" in ALLOWED.sub("", line).lower()]


@pytest.mark.parametrize("path", COMBAT_MODULES, ids=lambda p: p.relative_to(PACKAGE).as_posix())
def test_no_combat_module_says_profile(path: Path):
    """Strings, comments and names alike: a string literal is what the user reads, and
    the rest is what the next string is written from."""
    assert _strays(path.read_text(encoding="utf-8")) == []


def test_the_scan_would_catch_one():
    assert _strays("label = \"In profile 'Solo' only\"") != []
    assert _strays("# fills the launch profile's row") == []


@pytest.mark.parametrize("path", COMBAT_DOCS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_no_combat_doc_says_combat_profile(path: Path):
    assert "combat profile" not in path.read_text(encoding="utf-8").lower()


def test_no_combat_model_field_says_profile():
    """The rename is names only: no file format changed, so no field carries the word."""
    models = (BotCombatStrategy, StateAssignment, FightRef, DuelCombatStrategy, PlayerAssignment, PlayerCombatStrategy)
    for model in (*models, DeckPreset):
        assert not [name for name in model.model_fields if "profile" in name], model.__name__


@pytest.mark.parametrize(
    ("folder", "model"),
    [("duel", DuelCombatStrategy), ("player", PlayerCombatStrategy), ("decks", DeckPreset)],
)
def test_every_shipped_strategy_file_still_loads(folder: str, model):
    files = sorted((ROOT / "strategies" / folder).glob("*.json"))
    assert files
    for path in files:
        model.model_validate(json.loads(path.read_text(encoding="utf-8")))
