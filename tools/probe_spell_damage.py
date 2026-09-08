"""Diagnostic: what does a damage card's effect data actually say?

WHY THIS EXISTS
    Issue #6 wants WizSprinter to rank spells *within* a category rather than
    treating every match as equal, so that `any<aoe>` picks Storm Lord over
    Bunyip's Rage over Tempest without the strategy naming them.  Ranking
    damage means scoring a card from its effect data, and three things about
    that data are not established anywhere we can read offline:

    1. What `effect_param` means on a damage-over-time effect.  The type dump
       gives `m_effectParam` at 0x4c and `m_paramPerRound` at 0x94 but no
       semantics, and nothing in wizwalker, wizsprinter or Deimos ever *reads*
       `param_per_round` -- `combat_lifting.py` only prints it.  If param is
       the per-round tick rather than the total, every DoT scores low against
       its real output and Fire Dragon loses to spells it beats.

    2. Whether `effect_param` on an enchanted card already includes the
       enchant.  If it does, sorting by damage subsumes the enchanted-first
       ordering in `get_cards()` and does it better: an enchanted Bunyip's
       Rage correctly outranks a plain Storm Lord.  If it does not, enchanted
       cards need to stay a separate outer sort key.

    3. Whether `any<damage>` really does match AoEs on a live client.  Reading
       the code says yes -- `hits_enemy()` is `target in enemy_targets`, and
       `enemy_targets` contains `enemy_team` and `enemy_team_all_at_once`,
       with no `.difference(_aoe_targets)` of the kind `is_blade`, `is_charm`,
       `is_ward` and `is_trap` all apply.  Section B runs the real matcher so
       that reading is confirmed rather than assumed.

    Section D also previews the ordering the proposed ranking would produce,
    so the design can be checked against a real hand before it is built.

HOW TO RUN
    1. Get a wizard into a battle and wait for the planning phase (the card
       hand visible, timer running).  Do not cast anything.
    2. uv run python tools/probe_spell_damage.py

    WHAT TO HAVE IN HAND, to get an answer to each question above:

    * For (1): a damage-over-time card whose tooltip states both halves --
      Fire Dragon reads "440 + 330 over 3 rounds".  If the probe prints
      param=330 the field is the total; param=110 means it is the per-round
      tick and the score needs `param * num_rounds`.
    * For (2): two copies of the same damage card with one of them enchanted
      (cast a Gargantuan on one in an earlier round).  Section C pairs them up
      and prints the delta.
    * For (3): any AoE -- Storm Lord, Tempest, a Blizzard, anything.

    A hand missing one of these still answers the others; run it again later
    with a better hand rather than trying to set all three up at once.

Read-only: never clicks, casts, or writes, so it cannot disturb your battle.
"""

from __future__ import annotations

import asyncio
import sys
from collections import defaultdict

from wizwalker.combat import CombatHandler
from wizwalker.extensions.wizsprinter import WizSprinter
from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import (
    SpellType,
    TemplateSpell,
)
from wizwalker.extensions.wizsprinter.combat_backends.spell_ranking import (
    expected_value,
    rank_facts,
    ranker_for,
)
from wizwalker.extensions.wizsprinter.sprinty_combat import (
    aoe_targets,
    card_facts,
    damage_effects,
    does_card_contain_reqs,
    enemy_targets,
    get_inner_card_effects,
)
from wizwalker.memory import SpellEffects

# Effects whose param scales with pips consumed rather than being a fixed
# amount. Tempest is the archetype: 80 *per pip*, not 80 damage.
PER_PIP_EFFECTS = {SpellEffects.damage_per_total_pip_power}


async def _safe(coro, default=None):
    try:
        return await coro
    except Exception:
        return default


class CardInfo:
    """Everything read off one card, gathered once so the sections below can
    re-read it without hitting memory again."""

    def __init__(self) -> None:
        self.name: str = "?"
        self.castable = None
        self.enchanted = None
        self.is_xpip = None
        self.rank = None
        self.effects: list[dict] = []
        self.matches_damage = None
        self.matches_aoe = None

    @property
    def damage_effects(self) -> list[dict]:
        """Leaf effects the `<damage>` matcher would count: a damage effect
        aimed at an enemy. Mirrors `is_damage()` in sprinty_combat."""
        return [
            e for e in self.effects if e["type"] in damage_effects and e["target"] in enemy_targets and e["param"] > 0
        ]

    @property
    def aoe_damage_effects(self) -> list[dict]:
        """Damage effects aimed at a whole enemy team."""
        return [e for e in self.damage_effects if e["target"] in aoe_targets]

    @property
    def per_pip(self) -> bool:
        """True if this card's damage scales with pips spent, which makes its
        param incomparable with a fixed-damage card's."""
        if self.is_xpip:
            return True
        return any(e["type"] in PER_PIP_EFFECTS for e in self.effects)


async def read_card(card) -> CardInfo:
    info = CardInfo()
    info.name = await _safe(card.name(), "?") or "?"
    info.castable = await _safe(card.is_castable())
    info.enchanted = await _safe(card.is_enchanted())

    spell = await _safe(card.get_graphical_spell())
    if spell is not None:
        rank = await _safe(spell.pip_cost())
        if rank is not None:
            info.is_xpip = await _safe(rank.is_xpip_spell())
            info.rank = await _safe(rank.spell_rank())

    for effect in await _safe(get_inner_card_effects(card), []) or []:
        info.effects.append(
            {
                "type": await _safe(effect.effect_type()),
                "target": await _safe(effect.effect_target()),
                "param": await _safe(effect.effect_param(), 0) or 0,
                "rounds": await _safe(effect.num_rounds(), 0) or 0,
                "per_round": await _safe(effect.param_per_round(), 0) or 0,
                "disposition": await _safe(effect.disposition()),
            }
        )

    info.matches_damage = await _safe(does_card_contain_reqs(card, TemplateSpell([SpellType.type_damage])))
    info.matches_aoe = await _safe(does_card_contain_reqs(card, TemplateSpell([SpellType.type_aoe])))
    return info


def _enum_name(value) -> str:
    return getattr(value, "name", str(value))


def section_a(cards: list[CardInfo]) -> None:
    print("A. HAND CONTENTS + RAW EFFECT DATA")
    print()
    print("   Question (1) is answered here. Find your damage-over-time card and")
    print("   compare its param against the tooltip's stated total.")
    print()
    for idx, info in enumerate(cards):
        flags = []
        if info.enchanted:
            flags.append("ENCHANTED")
        if info.is_xpip:
            flags.append("X-PIP")
        if info.castable is False:
            flags.append("not castable")
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        print(f"   {idx:>2}. {info.name}{suffix}")
        print(f"       rank={info.rank}  is_xpip_spell={info.is_xpip}")
        if not info.effects:
            print("       (no effects read)")
            print()
            continue
        print(f"       {'effect_type':<32} {'target':<26} {'param':>7} {'rounds':>7} {'per_round':>10}")
        for e in info.effects:
            marker = ""
            if e["type"] is SpellEffects.damage_over_time and e["rounds"]:
                quotient = e["param"] / e["rounds"]
                marker = f"   <-- param/rounds = {quotient:.1f}"
            print(
                f"       {_enum_name(e['type'])[:32]:<32} {_enum_name(e['target'])[:26]:<26}"
                f" {e['param']:>7} {e['rounds']:>7} {e['per_round']:>10}{marker}"
            )
        print()


async def print_effect_tree(card, indent: int = 7) -> None:
    """Print the card's effects with their container classes intact.

    The flat dump in section A cannot tell a container holding five
    alternatives apart from five effects that all land, and that difference is
    exactly what scoring turns on: a random-damage spell stores one effect per
    possible roll. Ranking maps RandomSpellEffect / VariableSpellEffect onto
    "one of these happens" and everything else onto "all of these happen", so
    this is the check that the mapping matches reality.
    """

    async def walk(effect, depth: int) -> None:
        pad = " " * (indent + depth * 4)
        cls = type(effect).__name__
        children = None
        for accessor in ("effects_list", "output_effect"):
            if hasattr(effect, accessor):
                children = await _safe(getattr(effect, accessor)())
                if children:
                    break
        if hasattr(effect, "elements"):
            elements = await _safe(effect.elements())
            if elements:
                children = [await _safe(el.effect()) for el in elements]

        if children:
            print(f"{pad}{cls}  ({len(children)} children)")
            for child in children:
                if child is not None:
                    await walk(child, depth + 1)
            return

        eff_type = _enum_name(await _safe(effect.effect_type()))
        target = _enum_name(await _safe(effect.effect_target()))
        param = await _safe(effect.effect_param(), 0)
        print(f"{pad}{cls}: {eff_type} @ {target} param={param}")

    for effect in await _safe(card.get_spell_effects(), []) or []:
        await walk(effect, 0)


async def section_tree(raw_cards, cards: list[CardInfo]) -> None:
    print("A2. EFFECT CONTAINER STRUCTURE")
    print()
    print("   A spell that rolls a random amount stores one effect per possible")
    print("   value. Section A cannot tell those apart from effects that all")
    print("   land, but the container class can: RandomSpellEffect and")
    print("   VariableSpellEffect hold alternatives and are scored as a mean,")
    print("   everything else is summed. Check a random-damage spell here --")
    print("   Humongofrog is the clearest -- and confirm its damage values sit")
    print("   under a Random/Variable node rather than side by side at the top.")
    print()
    for raw, info in zip(raw_cards, cards):
        print(f"   {info.name}")
        await print_effect_tree(raw)
        print()


def section_b(cards: list[CardInfo]) -> None:
    print("B. WHAT THE REAL MATCHER SAYS")
    print()
    print("   Question (3) is answered here, using does_card_contain_reqs itself.")
    print("   If any card is marked AoE and ALSO matches <damage>, then <damage>")
    print("   is AoE-inclusive on this build, as the code reading predicts.")
    print()
    print(f"   {'card':<34} {'<damage>':>10} {'<aoe>':>8}")
    print("   " + "-" * 54)
    both = []
    for info in cards:
        if info.matches_damage and info.matches_aoe:
            both.append(info.name)
        print(f"   {info.name[:34]:<34} {str(info.matches_damage):>10} {str(info.matches_aoe):>8}")
    print()
    if both:
        print(f"   VERDICT: <damage> matched {len(both)} AoE card(s): {', '.join(sorted(set(both)))}")
        print("   -> <damage> is AoE-inclusive, matching the code reading.")
    else:
        aoes = [i.name for i in cards if i.matches_aoe]
        if aoes:
            print("   VERDICT: no AoE also matched <damage> -- the code reading is WRONG,")
            print("   and the ranking design needs revisiting.")
        else:
            print("   VERDICT: inconclusive -- no AoE in hand. Re-run holding one.")
    print()


def section_c(cards: list[CardInfo]) -> None:
    print("C. ENCHANT DELTA")
    print()
    print("   Question (2) is answered here. Cards sharing a name are paired by")
    print("   enchanted state; a nonzero delta means effect_param already")
    print("   includes the enchant.")
    print()
    by_name: dict[str, list[CardInfo]] = defaultdict(list)
    for info in cards:
        by_name[info.name].append(info)

    reported = False
    for name, group in sorted(by_name.items()):
        plain = [i for i in group if not i.enchanted]
        ench = [i for i in group if i.enchanted]
        if not plain or not ench:
            continue
        reported = True
        plain_total = sum(e["param"] for e in plain[0].damage_effects)
        ench_total = sum(e["param"] for e in ench[0].damage_effects)
        print(f"   {name}")
        print(f"       plain      total damage param = {plain_total}")
        print(f"       enchanted  total damage param = {ench_total}")
        print(f"       delta                          = {ench_total - plain_total}")
        print()

    if not reported:
        enchanted = [i.name for i in cards if i.enchanted]
        if enchanted:
            print(f"   Enchanted cards in hand ({', '.join(sorted(set(enchanted)))}) have no plain")
            print("   counterpart to compare against. Re-run holding two copies, one enchanted.")
        else:
            print("   No enchanted cards in hand. Cast an enchant on a duplicate and re-run.")
        print()


# Every category `spell_ranking.CATEGORY_RANKERS` knows about, so the probe
# reports whatever the hand happens to hold rather than only damage.
RANKED_CATEGORIES = (
    SpellType.type_damage,
    SpellType.type_aoe,
    SpellType.type_blade,
    SpellType.type_trap,
    SpellType.type_charm,
    SpellType.type_ward,
    SpellType.type_mod_damage,
    SpellType.type_mod_heal,
    SpellType.type_mod_pierce,
    SpellType.type_inc_damage,
    SpellType.type_out_damage,
    SpellType.type_inc_heal,
    SpellType.type_out_heal,
    SpellType.type_pierce,
)


async def section_d(raw_cards, cards: list[CardInfo]) -> None:
    print("D. RANKING, AS THE REAL CODE COMPUTES IT")
    print()
    print("   Scored with sprinty_combat.card_facts and spell_ranking, not a")
    print("   copy of them, so this is the order combat would actually pick.")
    print("   Castable-only, since that is the pool selection sorts. Categories")
    print("   nothing in hand matches are skipped.")
    print()
    for spell_type in RANKED_CATEGORIES:
        label = f"any<{spell_type.name.removeprefix('type_')}>"
        template = TemplateSpell([spell_type])
        ranker = ranker_for(template)
        matched = [
            (raw, info)
            for raw, info in zip(raw_cards, cards)
            if info.castable and await _safe(does_card_contain_reqs(raw, template), False)
        ]
        if not matched:
            continue
        print(f"   {label}")
        facts = [await card_facts(raw, ranker, template) for raw, _ in matched]
        for pos, idx in enumerate(rank_facts(facts, ranker), start=1):
            info = matched[idx][1]
            f = facts[idx]
            tier = "per-pip" if f.is_per_pip else ("flat" if f.is_flat else "normal")
            print(
                f"       {pos}. {info.name[:30]:<30} tier={tier:<7} value={expected_value(f.score):>7.1f}"
                f"  pips={f.pip_cost:<2} src={f.source.name:<8} ench={'y' if f.is_enchanted else 'n'}"
            )
        print()


async def probe(client) -> None:
    if not await _safe(client.in_battle(), False):
        print("   This client is not in a battle. Start a fight and re-run.")
        return

    handler = CombatHandler(client)
    raw_cards = await _safe(handler.get_cards(), []) or []
    if not raw_cards:
        print("   No cards read. Wait for the planning phase (hand visible) and re-run.")
        return

    cards = [await read_card(c) for c in raw_cards]

    section_a(cards)
    await section_tree(raw_cards, cards)
    section_b(cards)
    section_c(cards)
    await section_d(raw_cards, cards)


async def main() -> int:
    sprinter = WizSprinter()
    clients = sprinter.get_new_clients()
    if not clients:
        print("No Wizard101 clients found. Start the game and log a wizard in.")
        return 1

    print(f"Found {len(clients)} client(s); activating hooks...")
    try:
        await asyncio.gather(*[c.activate_hooks() for c in clients])
    except Exception as exc:
        print(f"Hook activation failed: {exc!r}")
        return 1

    try:
        for i, client in enumerate(clients, start=1):
            print()
            print("=" * 78)
            print(f"CLIENT {i}")
            print("=" * 78)
            print()
            try:
                await probe(client)
            except Exception as exc:
                print(f"  probe failed: {exc!r}")
    finally:
        for client in clients:
            try:
                await client.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
