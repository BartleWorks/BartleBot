"""Tests for tools/game_update/pe_image.py: reading a client exe's sections and code off disk."""

import sys

import pytest

from bartlebot.paths import project_root

sys.path.insert(0, str(project_root()))

from tests.pe_fixture import DATA_RVA, TEXT_RVA, Code, build_pe  # noqa: E402
from tools.game_update.pe_image import Image  # noqa: E402


def _image(code: Code) -> Image:
    return Image(build_pe(bytes(code.buf)), label="fixture")


def test_a_file_offset_and_its_rva_map_both_ways():
    code = Code()
    code.pad(0x40)
    image = _image(code)
    offset = image.text.file_offset + 0x10
    assert image.rva(offset) == TEXT_RVA + 0x10
    assert image.offset(TEXT_RVA + 0x10) == offset
    assert image.rva(image.offset(DATA_RVA + 8)) == DATA_RVA + 8


def test_find_searches_the_code_section_only():
    code = Code()
    code.pad(8)
    at = code.raw(b"\x48\x8b\x05\x11\x22\x33\x44")
    image = _image(code)
    assert [image.rva(o) for o in image.find(rb"\x48\x8B\x05....")] == [at]
    # The section table holds ".text"; a scan of the whole file would find it there.
    assert image.find(rb"\.text") == []


def test_a_call_and_a_rip_operand_resolve_to_what_they_name():
    code = Code()
    call_at = code.call(TEXT_RVA + 0x80)
    load_at = code.rip(b"\x48\x8b\x05", DATA_RVA + 0x18)
    code.pad(0x100)
    image = _image(code)
    assert image.rva(image.call_target(image.offset(call_at))) == TEXT_RVA + 0x80
    assert image.rip_target(image.offset(load_at), 3, 7) == DATA_RVA + 0x18


def test_a_file_that_is_not_an_exe_is_refused():
    with pytest.raises(ValueError, match="not an MZ"):
        Image(b"KIWAD" + bytes(100))
