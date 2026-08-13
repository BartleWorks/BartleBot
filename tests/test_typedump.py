"""Tests for the type dump query tool.

These run against a small hand-built dump rather than the 26 MB real one, so
they stay fast and do not depend on a particular game revision being checked in.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from typedump import Prop, TypeDump, _sizeof, main  # noqa: E402


def _prop(offset: int, type_: str = "int", **kw) -> dict:
    return {
        "type": type_,
        "offset": offset,
        "container": kw.get("container", "Static"),
        "pointer": kw.get("pointer", False),
        "dynamic": False,
    }


RAW = {
    "class PropertyClass": {"bases": [], "properties": {}},
    "class Base": {
        "bases": ["PropertyClass"],
        "properties": {"m_shared": _prop(72), "m_baseOnly": _prop(80, "float")},
    },
    "class Derived": {
        "bases": ["Base"],
        "properties": {
            "m_shared": _prop(999),  # subclass redeclaration wins over the base
            "m_vec": _prop(112, "class SharedPointer<class Window>", container="Vector"),
            "m_tail": _prop(200, "unsigned int"),
        },
    },
    "class Derived*": {  # byte-identical pointer variant, as the real dump emits
        "bases": ["Base"],
        "properties": {
            "m_shared": _prop(999),
            "m_vec": _prop(112, "class SharedPointer<class Window>", container="Vector"),
            "m_tail": _prop(200, "unsigned int"),
        },
    },
}


@pytest.fixture
def dump() -> TypeDump:
    return TypeDump(RAW)


# ------------------------------------------------------------------ lookup


def test_resolve_accepts_bare_and_prefixed_names(dump):
    assert dump.resolve("Derived") is not None
    assert dump.resolve("class Derived") is not None
    assert dump.resolve("derived") is not None, "lookup should be case-insensitive"
    assert dump.resolve("NoSuchClass") is None


def test_props_merges_inherited(dump):
    names = {p.name for p in dump.props("Derived")}
    assert names == {"m_shared", "m_baseOnly", "m_vec", "m_tail"}


def test_subclass_redeclaration_overrides_base(dump):
    shared = next(p for p in dump.props("Derived") if p.name == "m_shared")
    assert shared.offset == 999, "the derived class's own offset must win"
    assert shared.owner == "Derived"


def test_props_sorted_by_offset(dump):
    offsets = [p.offset for p in dump.props("Derived")]
    assert offsets == sorted(offsets)


def test_props_of_unknown_class_is_empty(dump):
    assert dump.props("Nonexistent") == ()


# ------------------------------------------------------------------ offsets


def test_at_exact_match(dump):
    hits = dump.at("Derived", 200)
    assert [p.name for p in hits] == ["m_tail"]


def test_at_returns_nothing_for_unreflected_offset(dump):
    assert dump.at("Derived", 150) == []


def test_near_finds_bracketing_properties(dump):
    names = {p.name for p in dump.near("Derived", 150, window=64)}
    assert names == {"m_vec", "m_tail"}


def test_near_window_is_respected(dump):
    assert dump.near("Derived", 150, window=2) == []


# ------------------------------------------------------------------ search


def test_search_hides_redundant_pointer_variants(dump):
    assert dump.search("Derived") == ["class Derived"]


def test_search_can_include_pointer_variants(dump):
    assert dump.search("Derived", include_pointers=True) == ["class Derived", "class Derived*"]


def test_find_prop_matches_regex_across_classes(dump):
    hits = dump.find_prop("^m_shared$")
    assert {p.owner for p in hits} == {"class Base", "class Derived"}


def test_find_prop_skips_pointer_variants_by_default(dump):
    assert all(not p.owner.endswith("*") for p in dump.find_prop("m_vec"))


# ------------------------------------------------------------------ sizes


def test_sizeof_container_is_vector_triple(dump):
    vec = next(p for p in dump.props("Derived") if p.name == "m_vec")
    assert _sizeof(vec) == 24


def test_sizeof_unknown_type_is_zero_so_gaps_are_not_hidden():
    unknown = Prop(
        name="m_mystery",
        type="class SomeTypeWeHaveNoSizeFor",
        offset=0,
        container="Static",
        pointer=False,
        dynamic=False,
        owner="class Derived",
    )
    assert _sizeof(unknown) == 0


# ------------------------------------------------------------------ cli


def test_cli_show_and_at(tmp_path, capsys):
    path = tmp_path / "r1_WizardDev.json"
    path.write_text(json.dumps(RAW), encoding="utf-8")

    assert main(["--dump", str(path), "show", "Derived"]) == 0
    assert "m_tail" in capsys.readouterr().out

    assert main(["--dump", str(path), "at", "Derived", "200"]) == 0
    assert "m_tail" in capsys.readouterr().out


def test_cli_at_reports_failure_for_unreflected_offset(tmp_path, capsys):
    path = tmp_path / "r1_WizardDev.json"
    path.write_text(json.dumps(RAW), encoding="utf-8")

    assert main(["--dump", str(path), "at", "Derived", "150"]) == 1
    assert "nothing at exactly" in capsys.readouterr().out


def test_cli_gaps_brackets_unreflected_region(tmp_path, capsys):
    path = tmp_path / "r1_WizardDev.json"
    path.write_text(json.dumps(RAW), encoding="utf-8")

    assert main(["--dump", str(path), "gaps", "Derived", "--min", "8"]) == 0
    out = capsys.readouterr().out
    # m_vec is a 24-byte vector at 112, so it ends at 136; m_tail starts at 200.
    assert "0x88" in out and "0xc8" in out
    assert "m_vec" in out and "m_tail" in out
