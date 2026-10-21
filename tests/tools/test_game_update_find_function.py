"""Tests for tools/game_update/find_function.py: finding a function on each build by what it names."""

import sys

from bartlebot.paths import project_root

sys.path.insert(0, str(project_root()))

from tests.pe_fixture import RDATA_RVA, TEXT_RVA, Code, build_pe  # noqa: E402
from tools.game_update import find_function as finder  # noqa: E402
from tools.game_update.pe_image import Image  # noqa: E402

_NAME = b"Manager::Initialize\0"


def _image() -> Image:
    """Two functions; the second names the string with a lea."""
    code = Code()
    code.pad(0x20)
    begin = code.rva
    code.raw(b"\x55")
    code.rip(b"\x48\x8d\x15", RDATA_RVA + 5)  # lea rdx, [rip+name]
    code.raw(b"\x5d\xc3")
    end = code.rva
    rdata = b"\0abc\0" + _NAME + b"\0" + "Wide one".encode("utf-16-le") + b"\0\0"
    return Image(build_pe(bytes(code.buf), rdata=rdata, functions=[(TEXT_RVA, TEXT_RVA + 0x20), (begin, end)]))


def test_read_string_takes_ascii_or_wide_and_nothing_shorter_than_four():
    image = _image()
    assert finder.read_string(image, RDATA_RVA + 5) == "Manager::Initialize"
    assert finder.read_string(image, RDATA_RVA + 6 + len(_NAME)) == "Wide one"  # 2-aligned
    assert finder.read_string(image, RDATA_RVA + 1) is None  # "abc"
    assert finder.read_string(image, TEXT_RVA) is None  # not .rdata


def test_by_string_describes_the_function_naming_it(capsys):
    finder.by_string(_image(), "new", "Manager::Initialize", None, show=5)
    out = capsys.readouterr().out
    assert f"function {TEXT_RVA + 0x20:#x}..{TEXT_RVA + 0x2A:#x}, 10 bytes" in out
    assert out.count("function ") == 1
