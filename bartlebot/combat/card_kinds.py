"""What broad category a spell belongs to — blade, trap, ward, charm, aura.

Saved decks name exact spells, which is what makes them hard to share: a deck
asking for ``Inferno Blade`` fails on a wizard who blades with ``Storm Blade``,
even though either would do.  Recording what a card *is* alongside what it is
called lets a failed apply say "a blade — you have these instead" rather than
just naming a card the user has never heard of.

Classification has to happen when a deck is *imported*, not when it is applied.
The applying wizard does not own the missing card, so there is no
``SpellTemplate`` to inspect; the authoring wizard does, and ``_entry_name``
already reads that template on the way past.  See :mod:`bartlebot.combat.deck_io`.

The predicates mirror ``SprintyCombat``'s — a card this module calls a blade is
one the combat DSL would also pick for ``<blade>``.  The effect-type and target
sets are imported from there rather than copied, so a fork bump moves both at
once; only the four small predicates are restated, because wizsprinter's live
inside a larger per-effect requirement check that needs a live ``CombatCard``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

BLADE = "blade"
CHARM = "charm"
WARD = "ward"
TRAP = "trap"
AURA = "aura"

#: User-facing wording, for messages like "Inferno Blade — a blade".
KIND_LABELS: dict[str, str] = {
    BLADE: "a blade",
    CHARM: "a charm",
    WARD: "a ward",
    TRAP: "a trap",
    AURA: "an aura",
}


def _sets():
    """Load wizsprinter's category sets, or None if they cannot be imported.

    Deferred and guarded: this module is imported by ``deck_io``, which is
    imported by the GUI at startup, and a classification that stops working
    after a fork bump should cost the labels in one dialog rather than the
    ability to launch.
    """
    try:
        from wizwalker.extensions.wizsprinter.sprinty_combat import (  # noqa: PLC0415
            ally_targets,
            aoe_targets,
            charm_effects,
            enemy_targets,
            ward_effects,
        )
    except Exception:
        logger.debug("wizsprinter category sets unavailable — card kinds disabled", exc_info=True)
        return None
    return charm_effects, ward_effects, ally_targets, enemy_targets, aoe_targets


def classify_effect(effect_type, target, param: int, rounds: int) -> str | None:
    """Name the category one spell effect falls into, or None if it is none of them.

    Mirrors ``sprinty_combat.is_blade`` and friends: the distinctions are all
    made from the same four fields, and the sign of ``param`` plus who is
    targeted is what separates a blade from a charm, or a ward from a trap.
    """
    sets = _sets()
    if sets is None:
        return None
    charm_effects, ward_effects, ally_targets, enemy_targets, aoe_targets = sets

    single_ally = target in ally_targets - aoe_targets
    single_enemy = target in enemy_targets - aoe_targets

    if rounds > 0 and effect_type in charm_effects | ward_effects and target in ally_targets:
        # An aura outlives the round it is cast in; that is the whole difference
        # between it and a blade sitting on the same effect type.
        return AURA
    if rounds != 0:
        return None
    if effect_type in charm_effects:
        if single_ally and param > 0:
            return BLADE
        if single_enemy and param < 0:
            return CHARM
    if effect_type in ward_effects:
        if single_ally and param < 0:
            return WARD
        if single_enemy and param > 0:
            return TRAP
    return None


def classify_effects(effects: Iterable[tuple]) -> str | None:
    """Category for a whole spell, from its ``(effect_type, target, param, rounds)`` tuples.

    First match wins.  A spell with several effects is named for the first one
    that lands in a category — good enough to say "this was a blade", which is
    all the failure message needs, and it avoids inventing a precedence order
    the combat DSL itself does not have.
    """
    for effect_type, target, param, rounds in effects:
        kind = classify_effect(effect_type, target, param, rounds)
        if kind is not None:
            return kind
    return None


def describe_kind(kind: str | None) -> str:
    """ "a blade" / "a trap" / "" — the phrase used in a skipped-card message."""
    return KIND_LABELS.get(kind or "", "")
