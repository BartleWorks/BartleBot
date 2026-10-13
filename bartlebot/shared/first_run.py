"""The first-run question, asked once for every product by this vendor (epic #201, Stage 6).

A user who never opens the Combat tab never learns that combat strategies exist, or that
the team default can be changed. A page that only explains gets skipped, but a question
that writes a real choice gets answered, so first run asks the one combat question a user
who writes no DSL can answer: how their team fights bosses.

=================================  =====================================================
answer                             what it stores
=================================  =====================================================
One hitter, the others support     no Team default: the shipped Default Team Duel
Everyone attacks                   :data:`SHIPPED_EVERYONE_ATTACKS_DUEL_ID` as the Team default
Skip                               nothing beyond the record that it was asked
=================================  =====================================================

Every answer, Skip included, sets :data:`ANSWERED_KEY` in ``preferences.json``, which every
product reads, so BartleBuddy does not ask again after BartleBot has, or the other way round.

"Solo or team?" is not asked: both defaults exist, and which one a fight uses comes from
the clients connected (``combat_defaults.team_size``).
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from bartlebot.shared.combat_defaults import (
    SHIPPED_EVERYONE_ATTACKS_DUEL_ID,
    TeamSize,
    chosen_default,
    set_default_choice,
)

if TYPE_CHECKING:
    from bartlebot.shared.preferences import PreferenceStore

#: Set once the combat question has been answered or skipped, by either product.
ANSWERED_KEY = "setup.combat_defaults_answered"


class BossTactic(Enum):
    """The two answers to "How does your team fight bosses?"."""

    ONE_HITTER = "one_hitter"
    EVERYONE_ATTACKS = "everyone_attacks"


#: The Team default each answer stores. None is "recommended": no key, so a later fix to
#: the shipped duel reaches everyone who took it.
TEAM_DUELS: dict[BossTactic, str | None] = {
    BossTactic.ONE_HITTER: None,
    BossTactic.EVERYONE_ATTACKS: SHIPPED_EVERYONE_ATTACKS_DUEL_ID,
}

#: The answer offered first, and selected when nothing else is.
RECOMMENDED = BossTactic.ONE_HITTER


def current_tactic(prefs: PreferenceStore) -> BossTactic | None:
    """The answer the stored Team default already is, or None for a duel neither answer names."""
    stored = chosen_default(prefs, TeamSize.TEAM)
    return next((tactic for tactic, duel in TEAM_DUELS.items() if duel == stored), None)


def combat_question_answered(prefs: PreferenceStore) -> bool:
    """Whether the combat question needs no asking.

    True once either product has recorded an answer, or Skip. True as well while the Team
    default is a duel neither answer names: the user chose it in Settings › Combat, or in
    the other product, and the question exists to make that choice. Nothing is written.
    """
    return bool(prefs.get(ANSWERED_KEY)) or current_tactic(prefs) is None


def record_answer(prefs: PreferenceStore, tactic: BossTactic | None) -> bool:
    """Store *tactic*'s Team default, or nothing for None (Skip), and record the question answered.

    Returns whether both writes reached disk. The answer is recorded only once its default
    is, so a failed write asks again next time rather than losing the choice.
    """
    if tactic is not None and not set_default_choice(prefs, TeamSize.TEAM, TEAM_DUELS[tactic]):
        return False
    return prefs.set(ANSWERED_KEY, True)
