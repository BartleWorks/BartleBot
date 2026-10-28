"""Node help text: the shared wordings, and the rules bartleby-core/docs/UI_TEXT.md sets for every node."""

from __future__ import annotations

import re

import pytest

from bartlebot.nodes import help_text
from bartlebot.nodes.registry import NODE_SPECS


def _texts(spec) -> list[tuple[str, str]]:
    """Every string a node's help page shows, each with where it is shown."""
    texts = [
        ("description", spec.description),
        ("usage notes", spec.usage_notes),
        ("safety", spec.safety_warning or ""),
    ]
    for param in spec.params:
        texts += [(f"{param.key} label", param.label), (f"{param.key} help", param.help)]
        texts.append((f"{param.key} hint", param.default_hint))
    texts += [(f"{ret.name} return", ret.description) for ret in spec.returns]
    texts += [(f"{option.name} option", option.description or "") for option in spec.required_bot_options]
    return texts


_CODE_WORDS = {param.key for spec in NODE_SPECS.values() for param in spec.params if "_" in param.key} | {
    node_type for node_type in NODE_SPECS if "_" in node_type
}


@pytest.mark.parametrize("node_type", sorted(NODE_SPECS))
def test_node_help_names_no_code(node_type):
    """N1 and N2: a param by its label and a node by its name, never a key or a file."""
    for where, text in _texts(NODE_SPECS[node_type]):
        for word in _CODE_WORDS:
            assert not re.search(rf"\b{word}\b", text), f"{node_type} {where} names {word!r}"
        assert "bartlebot/" not in text, f"{node_type} {where} names a file"
        assert "→" not in text, f"{node_type} {where} uses → where a path takes ›"


@pytest.mark.parametrize("node_type", sorted(NODE_SPECS))
def test_a_hint_is_a_lowercase_fragment(node_type):
    """N7: what the default does, as a fragment, not a sentence."""
    for param in NODE_SPECS[node_type].params:
        hint = param.default_hint
        if hint:
            assert not hint.endswith("."), f"{node_type} {param.key}: {hint!r}"
            assert not hint[0].isupper(), f"{node_type} {param.key}: {hint!r}"


_SHARED = {
    "interact_xyz": help_text.INTERACT_XYZ,
    "reset_xyz": help_text.RESET_XYZ,
    "interact_clients": help_text.INTERACT_PLAYERS,
    "sequential": help_text.SEQUENTIAL_INTERACTION,
    "object_use_delay": help_text.OBJECT_USE_DELAY_HELP,
    "from_zone": help_text.ZONE_AT_START,
    "to_zone": help_text.ZONE_AT_END,
    "dungeon_zone": help_text.ZONE_AT_END,
}
#: A shared key that means something else on this node, so its help is its own.
_OWN_MEANING = {("enter_zone", "reset_xyz"), ("teleport_to_friend", "to_zone")}


def test_a_shared_param_reads_the_same_on_every_node():
    """N9: one text for one param, so it never reads differently on two nodes."""
    for node_type, spec in NODE_SPECS.items():
        for param in spec.params:
            if param.key in _SHARED and (node_type, param.key) not in _OWN_MEANING:
                assert param.help == _SHARED[param.key], f"{node_type} {param.key}"


def test_a_node_that_recovers_stragglers_says_so_in_one_wording():
    users = [node_type for node_type, spec in NODE_SPECS.items() if help_text.ARRIVAL_NOTE in spec.usage_notes]
    assert sorted(users) == [
        "enter_dungeon",
        "enter_zone",
        "enter_zone_via_object",
        "teleport_to_marked_location",
        "teleport_to_world_hub",
    ]


def test_a_duration_hint_counts_its_seconds():
    assert help_text.seconds(1.0) == "1 second"
    assert help_text.seconds(10.0) == "10 seconds"
    assert help_text.seconds(2.5) == "2.5 seconds"


def test_a_node_name_keeps_its_short_words_lowercase():
    """N3: title case, as Unreal names its nodes, with the first word always capitalized."""
    from bartlebot.nodes.registry import node_display_name

    assert node_display_name("teleport_to_world_hub") == "Teleport to World Hub"
    assert node_display_name("enter_zone_via_object") == "Enter Zone via Object"
    assert node_display_name("check_health_and_mana") == "Check Health and Mana"
    assert node_display_name("handle_combat") == "Handle Combat"
