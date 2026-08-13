"""Tests for the ClientWizPlayerNameBehavior probe's pure logic.

The memory reads themselves need a live client, but the parts that decide *what* the
probe concludes -- pet vs wizard disambiguation, and how a failed read is rendered --
are testable with fakes and are exactly the parts worth pinning down.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from probe_name_behavior import (  # noqa: E402
    NAME_FIELDS,
    _safe,
    _show,
    describe_name_keys,
    find_name_behaviors,
    format_snapshot,
    read_name_fields,
    record_sample,
    split_bits,
)

# The runtime behavior template names, as observed on a live client -- deliberately not
# the dump's class names (ClientWizPlayerNameBehavior / ClientPetNameBehavior), since the
# whole point of the suffix match is that the two differ unpredictably.
WIZ_NAME_BEHAVIOR = "WizPlayerNameBehavior"
PET_NAME_BEHAVIOR = "PetNameBehavior"


class FakeBehavior:
    """A behavior instance whose string fields are canned per offset."""

    def __init__(self, address: int, narrow: dict[int, object], wide: dict[int, object]) -> None:
        self._address = address
        self._narrow = narrow
        self._wide = wide

    async def read_base_address(self) -> int:
        return self._address

    async def read_string_from_offset(self, offset: int):
        value = self._narrow.get(offset, "")
        if isinstance(value, Exception):
            raise value
        return value

    async def read_wide_string_from_offset(self, offset: int):
        value = self._wide.get(offset, "")
        if isinstance(value, Exception):
            raise value
        return value


class NamedBehavior(FakeBehavior):
    """A behavior that reports a template name, as the live behavior list does."""

    def __init__(self, name: str, address: int, narrow=None, wide=None) -> None:
        super().__init__(address, narrow or {}, wide or {})
        self._name = name

    async def behavior_name(self) -> str:
        return self._name


class FakeClientObject:
    """A client object exposing a behavior list, matching wizwalker's CoreObject."""

    def __init__(self, behaviors: list[NamedBehavior]) -> None:
        self._behaviors = behaviors

    async def inactive_behaviors(self) -> list[NamedBehavior]:
        return self._behaviors


# ------------------------------------------------------------------ _safe / _show


async def test_safe_returns_value_on_success():
    async def ok():
        return "Wizard Name"

    assert await _safe(ok()) == "Wizard Name"


async def test_safe_captures_exception_rather_than_raising():
    async def boom():
        raise ValueError("bad length prefix")

    result = await _safe(boom())
    assert isinstance(result, ValueError), "the exception type is the diagnostic, so it is kept"


def test_show_renders_exception_as_its_type():
    assert _show(ValueError("x")) == "<ValueError>"


def test_show_renders_empty_string_distinguishably():
    # An empty field and a failed read mean different things: empty means the field exists
    # but is unpopulated, which is the expected outcome for m_publicPlayerName locally.
    assert _show("") == "''"


# ------------------------------------------------------------------ behavior lookup


async def test_finds_wizard_behavior_by_suffix_not_class_name():
    """The live template name is WizPlayerNameBehavior, not the dump's class name."""
    obj = FakeClientObject([NamedBehavior(WIZ_NAME_BEHAVIOR, 0x1000)])
    assert set(await find_name_behaviors(obj)) == {WIZ_NAME_BEHAVIOR}


async def test_ignores_unrelated_behaviors():
    # A real client lists ~38 behaviors; only the name ones should come back.
    obj = FakeClientObject(
        [
            NamedBehavior("AnimationBehavior", 0x1000),
            NamedBehavior("PetOwnerBehavior", 0x2000),
            NamedBehavior(WIZ_NAME_BEHAVIOR, 0x3000),
        ]
    )
    assert set(await find_name_behaviors(obj)) == {WIZ_NAME_BEHAVIOR}


async def test_pet_behavior_reported_separately_not_as_the_wizard():
    """A pet inherits m_publicPlayerName at the same offset, so it must stay distinguishable."""
    obj = FakeClientObject(
        [
            NamedBehavior(WIZ_NAME_BEHAVIOR, 0x1000, narrow={0x1F8: "Wizard Name"}),
            NamedBehavior(PET_NAME_BEHAVIOR, 0x2000, narrow={0x1F8: "Pet Name"}),
        ]
    )
    found = await find_name_behaviors(obj)
    assert set(found) == {WIZ_NAME_BEHAVIOR, PET_NAME_BEHAVIOR}
    assert await found[WIZ_NAME_BEHAVIOR].read_base_address() != await found[PET_NAME_BEHAVIOR].read_base_address()


async def test_missing_behavior_is_omitted():
    assert await find_name_behaviors(FakeClientObject([])) == {}


async def test_behavior_list_failure_is_not_fatal():
    class Broken:
        async def inactive_behaviors(self):
            raise RuntimeError("AddressOutOfRange")

    assert await find_name_behaviors(Broken()) == {}


# ------------------------------------------------------------------ field reads


async def test_read_name_fields_uses_wide_reader_for_wstring():
    # m_wsNameOverride (0x078) is a std::wstring and must not be read narrowly.
    behavior = FakeBehavior(0x1000, {0x078: "WRONG-narrow"}, {0x078: "Right Wide"})
    fields = await read_name_fields(behavior)
    assert fields["m_wsNameOverride"] == "Right Wide"


async def test_read_name_fields_covers_every_declared_field():
    fields = await read_name_fields(FakeBehavior(0x1000, {}, {}))
    assert set(fields) == {label for label, _o, _w in NAME_FIELDS}


async def test_failed_read_is_captured_per_field():
    behavior = FakeBehavior(0x1000, {0x1F8: RuntimeError("AddressOutOfRange")}, {})
    fields = await read_name_fields(behavior)
    assert isinstance(fields["m_publicPlayerName"], RuntimeError)
    assert fields["m_gamerTag"] == "", "one bad field must not abort the others"


# ------------------------------------------------------------------ rendering


@pytest.fixture
def snap_with_wizard() -> dict:
    return {
        "zone": "WizardCity/WC_Hub",
        "character_id": 12345,
        "behaviors": {
            WIZ_NAME_BEHAVIOR: {
                "address": 0x1234,
                "fields": {label: "" for label, _o, _w in NAME_FIELDS} | {"m_wsNameOverride": "Wizard Name"},
            }
        },
    }


def test_format_snapshot_shows_name_and_control(snap_with_wizard):
    out = "\n".join(format_snapshot("title", snap_with_wizard))
    assert "Wizard Name" in out
    assert "12345" in out and "0x1C0" in out


def test_format_snapshot_flags_missing_behavior_as_dead():
    snap = {"zone": "z", "character_id": 1, "behaviors": {}}
    assert "route is dead" in "\n".join(format_snapshot("title", snap))


# ------------------------------------------------------------------ name key decoding


def test_split_bits_msb_first():
    # 0xAABBCCDD split into four bytes, most significant first.
    assert split_bits(0xAABBCCDD, (8, 8, 8, 8)) == [0xAA, 0xBB, 0xCC, 0xDD]


def test_split_bits_uneven_widths():
    assert split_bits(0b1111_000000_111111, (4, 6, 6)) == [0b1111, 0b000000, 0b111111]


def test_split_bits_masks_each_field():
    assert all(part < 1024 for part in split_bits(0xFFFFFFFF, (10, 10, 10)))


def test_describe_name_keys_shows_every_candidate_split():
    lines = "\n".join(describe_name_keys(0x00123456))
    assert "0x00123456" in lines
    for label in ("10/10/10", "8/12/12", "8/8/8/8", "16/16"):
        assert label in lines


def test_describe_name_keys_handles_unreadable_value():
    assert "unreadable" in "\n".join(describe_name_keys(ValueError("nope")))


# ------------------------------------------------------------------ sample recording


def _snap_with_keys(keys: int) -> dict:
    return {
        "zone": "z",
        "character_id": 100000000000000001,
        "behaviors": {
            WIZ_NAME_BEHAVIOR: {
                "address": 0x1000,
                "fields": {label: "" for label, _o, _w in NAME_FIELDS},
                "ints": {"m_nameKeys": keys, "m_eGender": 1, "m_eRace": 2},
            }
        },
    }


def test_record_sample_writes_known_name(tmp_path):
    path = tmp_path / "samples.json"
    sample = record_sample(path, "Fred Dragonrider", _snap_with_keys(12345))
    assert sample["m_nameKeys"] == 12345
    assert json.loads(path.read_text())[0]["wizard_name"] == "Fred Dragonrider"


def test_record_sample_appends_across_characters(tmp_path):
    path = tmp_path / "samples.json"
    record_sample(path, "Fred Dragonrider", _snap_with_keys(111))
    record_sample(path, "Autumn Dragonrider", _snap_with_keys(222))
    assert len(json.loads(path.read_text())) == 2


def test_record_sample_replaces_rather_than_duplicates(tmp_path):
    """Re-recording a character should correct its entry, not add a second one."""
    path = tmp_path / "samples.json"
    record_sample(path, "Fred Dragonrider", _snap_with_keys(111))
    record_sample(path, "Fred Dragonrider", _snap_with_keys(999))
    saved = json.loads(path.read_text())
    assert len(saved) == 1 and saved[0]["m_nameKeys"] == 999


def test_record_sample_returns_none_when_keys_unreadable(tmp_path):
    snap = _snap_with_keys(0)
    snap["behaviors"][WIZ_NAME_BEHAVIOR]["ints"]["m_nameKeys"] = RuntimeError("bad read")
    assert record_sample(tmp_path / "samples.json", "Fred Dragonrider", snap) is None


def test_record_sample_ignores_pet_behavior(tmp_path):
    """The pet carries the same fields; a sample must come from the wizard."""
    snap = _snap_with_keys(111)
    snap["behaviors"] = {
        PET_NAME_BEHAVIOR: {"address": 0x2000, "fields": {}, "ints": {"m_nameKeys": 999}},
        **snap["behaviors"],
    }
    sample = record_sample(tmp_path / "samples.json", "Fred Dragonrider", snap)
    assert sample["m_nameKeys"] == 111


def test_record_sample_survives_corrupt_existing_file(tmp_path):
    path = tmp_path / "samples.json"
    path.write_text("{not json", encoding="utf-8")
    assert record_sample(path, "Fred Dragonrider", _snap_with_keys(111)) is not None


def test_format_snapshot_marks_pet_behavior():
    snap = {
        "zone": "z",
        "character_id": 1,
        "behaviors": {
            PET_NAME_BEHAVIOR: {
                "address": 0x2000,
                "fields": {label: "Pet Name" for label, _o, _w in NAME_FIELDS},
            }
        },
    }
    assert "PET, not the wizard" in "\n".join(format_snapshot("title", snap))
