"""No coroutine opens a modal itself (#154).

A coroutine runs as an asyncio task, and a modal opened inside one -- ``exec()``, or a
``QMessageBox``/``QFileDialog``/``QInputDialog`` static -- re-enters Qt's event loop with the
task still on the stack. Every other task that wakes while it is up is wedged for good
("qasync + Python 3.13 task re-entrancy rule" in bartlebot/gui/CLAUDE.md). A coroutine
defers the dialog instead: ``QTimer.singleShot(0, lambda: ...)``, a queued signal, or
``QMenu.popup``.

Read from the source, so a new coroutine is covered the day it is written. A call inside
a lambda or a nested function is not the coroutine's own, and is allowed: that is what a
deferral looks like.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "bartlebot"

#: The statics that open a modal.
MODAL_CLASSES = {"QMessageBox", "QFileDialog", "QInputDialog", "QColorDialog", "QFontDialog"}


def _own_nodes(fn: ast.AsyncFunctionDef):
    """Every node in *fn*'s body, less what a lambda or nested function inside it holds."""
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _is_modal(call: ast.Call) -> bool:
    func = call.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr in ("exec", "exec_"):
        return True
    return isinstance(func.value, ast.Name) and func.value.id in MODAL_CLASSES


def _modals_in_coroutines() -> list[str]:
    found = []
    for path in sorted(ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.AsyncFunctionDef):
                continue
            for node in _own_nodes(fn):
                if isinstance(node, ast.Call) and _is_modal(node):
                    where = path.relative_to(ROOT.parent).as_posix()
                    found.append(f"{where}:{node.lineno} {fn.name}: {ast.unparse(node.func)}")
    return found


def test_no_coroutine_opens_a_modal_itself():
    assert _modals_in_coroutines() == []


def test_the_guard_sees_a_modal_and_lets_a_deferral_through():
    tree = ast.parse(
        "async def f(self):\n"
        "    QMessageBox.critical(self, 't', 'x')\n"
        "    menu.exec(point)\n"
        "    QTimer.singleShot(0, lambda: QMessageBox.critical(self, 't', 'x'))\n"
    )
    fn = tree.body[0]
    calls = [n for n in _own_nodes(fn) if isinstance(n, ast.Call) and _is_modal(n)]
    assert sorted(ast.unparse(c.func) for c in calls) == ["QMessageBox.critical", "menu.exec"]
