"""Tests for tools/game_update/check_hook_patterns.py: which of wizwalker's scans a new client build breaks."""

import sys

from bartlebot.paths import project_root

sys.path.insert(0, str(project_root()))

from tests.pe_fixture import Code, build_pe  # noqa: E402
from tools.game_update import check_hook_patterns as hooks  # noqa: E402
from tools.game_update.pe_image import Image  # noqa: E402


def _image(*chunks: bytes) -> Image:
    code = Code()
    for chunk in chunks:
        code.raw(chunk)
        code.pad(16)
    return Image(build_pe(bytes(code.buf)))


def test_literals_finds_every_pattern_and_skips_the_tests_and_the_file_readers(tmp_path):
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "hooks.py").write_text(
        'class Hook:\n    pattern = (\n        rb"\\x48\\x8B"\n        rb"\\x05...."\n    )\n    short = b"\\x90"\n',
        encoding="utf-8",
    )
    for skipped in ("tests", "file_readers"):
        (tmp_path / skipped).mkdir()
        (tmp_path / skipped / "x.py").write_text('MAGIC = b"KIWAD"\n', encoding="utf-8")
    found = hooks.literals(tmp_path)
    # Implicit concatenation is one literal; a literal under four bytes is not a pattern.
    assert [(lit.source, lit.line, lit.value) for lit in found] == [("memory/hooks.py", 3, rb"\x48\x8B\x05....")]


def test_each_literal_gets_the_verdict_its_counts_imply():
    old = _image(b"\xaa\xbb\xcc\xdd", b"\x11\x22\x33\x44", b"\x55\x66\x77\x88", b"\x55\x66\x77\x88")
    new = _image(b"\x11\x22\x33\x44", b"\x55\x66\x77\x88")
    found = [
        hooks.Literal("a.py", 1, rb"\xAA\xBB\xCC\xDD"),
        hooks.Literal("a.py", 2, rb"\x11\x22\x33\x44"),
        hooks.Literal("a.py", 3, rb"\x55\x66\x77\x88"),
        hooks.Literal("a.py", 4, b"\x48\x81\xec(\x00\x00\x00"),  # code wizwalker writes: not a regex
        hooks.Literal("a.py", 5, rb"\xFE\xED\xFA\xCE"),
    ]
    assert [r.verdict for r in hooks.check(found, old, new)] == ["broken", "holds", "changed", "neither", "neither"]


def test_a_broken_pattern_fails_the_run(capsys):
    broken = hooks.Result(hooks.Literal("a.py", 1, b"\x01\x02\x03\x04"), old=1, new=0)
    holds = hooks.Result(hooks.Literal("a.py", 2, b"\x05\x06\x07\x08"), old=1, new=1)
    assert hooks.report([holds]) == 0
    assert hooks.report([broken, holds]) == 1
    assert "broken: 1" in capsys.readouterr().out
