"""No shipped bot writes a zone pattern that can match a house it does not mean.

A zone name's first segment is its world, or ``Housing_<style>`` for a house. ``*Housing*`` matches
every house in the game, the player's own included, which is how teleport_to_friend skipped a whole
gauntlet run and called it a success (#14). Two rules keep that from coming back:

* **The first segment starts with a literal.** ``Avalon*`` names a world family; ``*Housing*`` or a
  bare ``*`` names nothing in particular.
* **A house pattern keeps its first segment whole.** ``Housing*`` is as broad as ``*Housing*``, since
  every house shares that prefix. Wildcard only below it:
  ``Housing_AV_Gauntlet/AV_Gauntlet_??/AV_Gauntlet_??_01``.

When the zone cannot be known at all -- a friend's house -- leave the param blank where the node
allows it, rather than reaching for a broader pattern.
"""

import json

from bartlebot.nodes.node_helpers import has_glob
from bartlebot.paths import project_root

BOTS = project_root() / "bots"


def _zone_values(bot: dict):
    """Every (where, key, value) for a param, in states and subgraph instances."""
    for state in bot.get("states", []):
        for key, value in (state.get("params") or {}).items():
            yield state.get("id"), key, value
    for region in bot.get("regions", []):
        source = region.get("source") or {}
        for state_id, params in (source.get("param_overrides") or {}).items():
            for key, value in params.items():
                yield f"{region.get('id')}/{state_id}", key, value
        for key, value in (source.get("inputs") or {}).items():
            yield region.get("id"), key, value


def too_broad(pattern: str) -> bool:
    """Whether a zone pattern breaks either rule in the module docstring."""
    first = pattern.split("/", 1)[0]
    if not has_glob(first):
        return False
    return has_glob(first[:1]) or first.casefold().startswith("housing")


def test_the_rules_catch_the_patterns_they_are_for():
    assert too_broad("*Housing*") and too_broad("*") and too_broad("Housing*")
    assert not too_broad("Avalon*") and not too_broad("WizardCity/*")
    assert not too_broad("Housing_AV_Gauntlet/AV_Gauntlet_??/AV_Gauntlet_??_01")


def test_no_shipped_zone_pattern_is_too_broad():
    offenders = []
    for path in sorted(BOTS.rglob("*.json")):
        bot = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(bot, dict):
            continue
        for where, key, value in _zone_values(bot):
            if "zone" in key and isinstance(value, str) and value and too_broad(value):
                offenders.append(f"{path.relative_to(BOTS).as_posix()} {where}.{key} = {value!r}")
    assert not offenders, "zone patterns too broad (see this module's docstring):\n" + "\n".join(offenders)
