"""Nothing in BartleBot writes a client's position but ``walking.hop`` (Stage 7 of #322).

The user called this critical: no hop goes farther than ``HOP_MAX``, whoever calls it, so the
one function that writes a position is the one that checks the distance. This scans all of
``bartlebot/`` for every way a position can be written, as the family guards beside it scan for
their conventions, and fails on any of them outside ``walking.hop``:

- a ``.teleport(`` call, wizwalker's write;
- wizsprinter's ``tp_to`` and every other ``tp_to_*`` and ``goto_closest*`` on ``SprintyClient``,
  read from the class so a new one is caught too;
- any import of wizsprinter's ``wiz_navigator``, which moves by teleport;
- a ``.write_position(`` or ``.write_xyz(`` call, a body's or the teleport helper's.

The free camera's own ``write_position``, which puts the detached camera over the player when
freecam is entered, is the one exception: it moves the camera, never the wizard.
"""

from __future__ import annotations

import ast

import pytest
from wizwalker.extensions.wizsprinter.sprinty_client import SprintyClient

from bartlebot.paths import project_root

PACKAGE = project_root() / "bartlebot"

#: The one function allowed to write a position, as ``<file>:<function>``.
THE_HOP = "nodes/walking.py:hop"

#: Every other write, as ``<file>:<function>:<call>``, and why it is not a wizard's position.
EXCEPTIONS = {
    "gui/sidebar/client_card.py:_toggle_freecam:write_position": (
        "The free camera's position, set over the player as freecam is entered: it moves the camera, never the wizard."
    ),
}

WRITES = frozenset(
    {"teleport", "write_position", "write_xyz"}
    | {n for n in dir(SprintyClient) if n.startswith("tp_to") or n.startswith("goto_closest")}
)


def position_writes(source: str, where: str) -> list[str]:
    """Every position write in *source*, as ``<where>:<function>:<call>:<line>``, or the import
    of ``wiz_navigator`` as ``<where>:<function>:wiz_navigator:<line>``."""
    found: list[str] = []

    def visit(node: ast.AST, function: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in WRITES:
            found.append(f"{where}:{function}:{node.func.attr}:{node.lineno}")
        if isinstance(node, ast.ImportFrom) and node.module and "wiz_navigator" in node.module:
            found.append(f"{where}:{function}:wiz_navigator:{node.lineno}")
        if isinstance(node, ast.ImportFrom) and any(a.name == "wiz_navigator" for a in node.names):
            found.append(f"{where}:{function}:wiz_navigator:{node.lineno}")
        if isinstance(node, ast.Import) and any("wiz_navigator" in a.name for a in node.names):
            found.append(f"{where}:{function}:wiz_navigator:{node.lineno}")
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(ast.parse(source), "")
    return found


def _allowed(write: str) -> bool:
    file, function, call, _ = write.split(":")
    return f"{file}:{function}" == THE_HOP or f"{file}:{function}:{call}" in EXCEPTIONS


def _all_writes() -> list[str]:
    found: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        found += position_writes(path.read_text(encoding="utf-8"), path.relative_to(PACKAGE).as_posix())
    return found


def test_nothing_writes_a_position_but_the_hop():
    assert [w for w in _all_writes() if not _allowed(w)] == []


def test_the_hop_writes_once_and_every_exception_is_still_there():
    """The hop's one write is where the guard expects it, and an exception that matches nothing
    fails, so the list cannot outlive what it excuses."""
    writes = _all_writes()
    assert [w.split(":")[2] for w in writes if w.startswith(THE_HOP + ":")] == ["teleport"]
    for exception in EXCEPTIONS:
        assert any(w.startswith(exception + ":") for w in writes), exception


@pytest.mark.parametrize(
    "planted",
    [
        "async def run(client):\n    await client.teleport(xyz)\n",
        "async def run(client):\n    await client.body.write_position(xyz)\n",
        "async def run(client):\n    await client.tp_to_closest_mob()\n",
        "async def run(client):\n    await client.goto_closest_health_wisp()\n",
        "from wizwalker.extensions.wizsprinter import wiz_navigator\n",
        "from wizwalker.extensions.wizsprinter.wiz_navigator import toZone\n",
    ],
)
def test_the_guard_fails_on_a_planted_write(planted):
    found = position_writes(planted, "nodes/some_node.py")
    assert found and not any(_allowed(w) for w in found)


def test_a_write_named_like_the_hop_elsewhere_is_still_caught():
    """Only walking.py's hop is the hop: a function of that name in another file is not."""
    found = position_writes("async def hop(client):\n    await client.teleport(xyz)\n", "nodes/other.py")
    assert found and not any(_allowed(w) for w in found)
