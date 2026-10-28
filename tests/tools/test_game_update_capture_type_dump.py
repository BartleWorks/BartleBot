"""Tests for tools/game_update/capture_type_dump.py: finding the type registry's global in a client exe.

Only the static half is tested. Reading the tree out of a running client is wiztype's own
code, run under wiztype's environment, and needs a client.
"""

import sys

import pytest

from bartlebot.paths import project_root

sys.path.insert(0, str(project_root()))

from tests.pe_fixture import DATA_RVA, Code, build_pe  # noqa: E402
from tools.game_update import capture_type_dump as capture  # noqa: E402
from tools.game_update.pe_image import Image  # noqa: E402

_ALLOC = 0x1800  # where the allocator would be; nothing runs, so nothing need be there
_HEAD = b"\x48\x89\x00\x48\x89\x40\x08\x48\x89\x40\x10\x66\xc7\x40\x18\x01\x01"


def _registry_init(code: Code, global_rva: int, holder: bool = True) -> None:
    """A getter's init path as the client's are built: store the holder, allocate it, build its head node."""
    code.rip(b"\x48\x89\x1d", global_rva)  # mov [rip+global], rbx
    code.pad(10, b"\x90")
    if holder:
        code.raw(b"\xb9\x10\x00\x00\x00")
        code.call(_ALLOC)
    code.pad(20, b"\x90")
    code.raw(b"\xb9\x30\x00\x00\x00")
    code.call(_ALLOC)
    code.raw(_HEAD)
    code.pad(40)


def _wiztype_site(code: Code, getter_load_target: int) -> None:
    """wiztype's call site, and the getter it calls, which loads `getter_load_target` at +41."""
    site = code.rva
    code.call(site + 64)
    code.raw(b"\x48\xbd" + b"\x55" * 7 + b"\x05" + b"\x48\x3b\x38\x74\x12")
    code.pad(site + 64 - code.rva)
    code.pad(capture.GETTER_LOAD_AT, b"\x90")
    code.rip(b"\x48\x8b\x05", getter_load_target)
    code.pad(40)


def _image(globals_: list[int], *, wiztype_names: int | None = None, lead: int = 0) -> Image:
    code = Code()
    code.pad(lead)
    if wiztype_names is not None:
        _wiztype_site(code, wiztype_names)
    for g in globals_:
        _registry_init(code, g)
    return Image(build_pe(bytes(code.buf)), label="fixture")


def test_wiztypes_scan_names_the_global_when_it_still_matches():
    image = _image([DATA_RVA + 0x10, DATA_RVA + 0x30], wiztype_names=DATA_RVA + 0x30)
    assert capture.wiztype_global(image) == DATA_RVA + 0x30
    assert capture.resolve_global(image, None) == DATA_RVA + 0x30


def test_registry_globals_lists_each_map_init_in_code_order():
    image = _image([DATA_RVA + 0x10, DATA_RVA + 0x30, DATA_RVA + 0x50])
    assert capture.registry_globals(image) == [DATA_RVA + 0x10, DATA_RVA + 0x30, DATA_RVA + 0x50]


def test_a_head_node_built_without_a_holder_is_not_a_registry():
    code = Code()
    _registry_init(code, DATA_RVA + 0x10)
    _registry_init(code, DATA_RVA + 0x30, holder=False)
    image = Image(build_pe(bytes(code.buf)))
    assert capture.registry_globals(image) == [DATA_RVA + 0x10]


def test_a_client_wiztype_no_longer_matches_takes_the_references_registry_by_position():
    reference = _image([DATA_RVA + 0x10, DATA_RVA + 0x30, DATA_RVA + 0x50], wiztype_names=DATA_RVA + 0x30)
    # The patched build moved its code and its globals, and its call site no longer matches.
    client = _image([DATA_RVA + 0x110, DATA_RVA + 0x130, DATA_RVA + 0x150], lead=0x37)
    assert capture.wiztype_global(client) is None
    assert capture.resolve_global(client, reference) == DATA_RVA + 0x130


def test_a_different_number_of_map_inits_is_refused_rather_than_guessed():
    reference = _image([DATA_RVA + 0x10, DATA_RVA + 0x30], wiztype_names=DATA_RVA + 0x30)
    client = _image([DATA_RVA + 0x110, DATA_RVA + 0x130, DATA_RVA + 0x150])
    with pytest.raises(LookupError, match="2 map inits"):
        capture.resolve_global(client, reference)


def test_a_client_wiztype_no_longer_matches_needs_a_reference():
    with pytest.raises(LookupError, match="no reference"):
        capture.resolve_global(_image([DATA_RVA + 0x10]), None)
