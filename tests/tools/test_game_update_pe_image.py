"""Tests for tools/game_update/pe_image.py: reading a client exe's sections and code off disk."""

import sys

import pytest

from bartlebot.paths import project_root

sys.path.insert(0, str(project_root()))

from tests.pe_fixture import DATA_RVA, RDATA_RVA, TEXT_RVA, Code, build_pe  # noqa: E402
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


def test_functions_come_from_pdata_and_function_at_finds_the_one_holding_an_address():
    code = Code()
    code.pad(0x100)
    image = Image(
        build_pe(bytes(code.buf), functions=[(TEXT_RVA + 0x40, TEXT_RVA + 0x80), (TEXT_RVA, TEXT_RVA + 0x20)])
    )
    assert image.functions == [(TEXT_RVA, TEXT_RVA + 0x20), (TEXT_RVA + 0x40, TEXT_RVA + 0x80)]
    assert image.function_at(TEXT_RVA + 0x50) == (TEXT_RVA + 0x40, TEXT_RVA + 0x80)
    assert image.function_at(TEXT_RVA + 0x30) is None  # between the two


def test_a_string_is_found_whole_in_either_encoding():
    # A wide string is 2-aligned, as the compiler places one: hence the extra NUL before it.
    rdata = b"\0Manager::InitializeTail\0Manager::Initialize\0\0" + "Wide".encode("utf-16-le") + b"\0\0"
    image = Image(build_pe(b"\xcc", rdata=rdata))
    # Only where it starts a string: not inside "Manager::InitializeTail", nor "Initialize" alone.
    assert image.strings("Manager::Initialize") == [RDATA_RVA + 25]
    assert image.strings("Initialize") == []
    assert image.strings("Wide") == [RDATA_RVA + 46]


def test_referrers_and_callers_find_the_code_naming_an_address():
    code = Code()
    target = code.raw(b"\xc3")  # the function called
    code.pad(15)
    call_at = code.call(target)
    lea_at = code.rip(b"\x48\x8d\x0d", RDATA_RVA + 8)  # lea rcx, [rip+string]
    code.rip(b"\x48\x8d\x0d", RDATA_RVA + 16)
    image = Image(build_pe(bytes(code.buf), rdata=bytes(32)))
    assert image.callers(target) == [call_at]
    assert image.referrers(RDATA_RVA + 8) == [lea_at]


def test_a_file_that_is_not_an_exe_is_refused():
    with pytest.raises(ValueError, match="not an MZ"):
        Image(b"KIWAD" + bytes(100))
