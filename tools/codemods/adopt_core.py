"""Point BartleBot at bartleby-core: rewrite every reference to a module core now holds.

Stage 3 of #167. Each module in :data:`TABLE` exists in bartleby-core, identical to
BartleBot's but for its imports, the cuts the stage lists and its docstring paths, and
BartleBot's copy is deleted in the same stage. No shim keeps an old path alive, so every
``from`` and ``import`` line, every dotted path in a string (``monkeypatch.setattr``,
``patch``) and every path in a comment or docstring is rewritten here, in one pass:

    uv run python tools/codemods/adopt_core.py [--check] [PATH ...]

With no paths it rewrites ``bartlebot/``, ``tests/``, ``tools/`` and ``bartlebot.py``.
``--check`` lists the files it would change and changes nothing. Run ``ruff check --fix``
and ``ruff format`` after it, which put the rewritten imports in order.

A ``from`` line that imports a moved module next to one that stays is split in two:
``from bartlebot.clients import client_manager, game_keys`` becomes one line from
``bartlebot.clients`` and one from ``bartleby.runtime.clients``.
:data:`NAMES` covers a name that left a module BartleBot keeps, which a
``from`` line also splits. ``bartlebot.nodes.node_helpers`` is not in the table: BartleBot keeps it, and it imports
the names core holds from ``bartleby.runtime.actions.node_helpers`` itself.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

_CLIENTS = (
    "account_identity character_roster credentials game_keys group_manager launch_tracker "
    "process_resources sightings tile_slots window_kill wizwalker_cache"
).split()
_GAME = "dialog_captions game_sizing key_bindings name_tables realms wizard_names".split()

#: Each BartleBot module path, or package path, and core's. A package's submodules follow it.
TABLE: dict[str, str] = {
    "bartlebot.shared": "bartleby.shared",
    "bartlebot.gui.theming": "bartleby.qt.theming",
    "bartlebot.gui.title_bar": "bartleby.qt.title_bar",
    **{f"bartlebot.gui.widgets.buttons.{m}": f"bartleby.qt.widgets.buttons.{m}" for m in ("action_button", "icon_button", "link_button")},
    **{f"bartlebot.gui.widgets.labels.{m}": f"bartleby.qt.widgets.labels.{m}" for m in ("elided_label", "themed_label")},
    **{f"bartlebot.gui.game_embed.{m}": f"bartleby.qt.game_embed.{m}" for m in ("client_tile", "win32_window")},
    **{f"bartlebot.gui.{m}": f"bartleby.qt.{m}" for m in ("global_hotkeys", "native_widgets", "streamer_mode")},
    **{f"bartlebot.clients.{m}": f"bartleby.runtime.clients.{m}" for m in _CLIENTS},
    **{f"bartlebot.game.{m}": f"bartleby.runtime.game.{m}" for m in _GAME},
    "bartlebot.launcher": "bartleby.runtime.launcher",
    "bartlebot.nodes.timing": "bartleby.runtime.timing",
    "bartlebot.nodes.paths": "bartleby.runtime.paths",
    "bartlebot.bot.run_trace": "bartleby.runtime.run_trace",
}

#: Names that moved out of a module BartleBot keeps: (module, name) to core's module.
NAMES: dict[tuple[str, str], str] = {
    ("bartlebot.settings_manager", "VALID_RESOLUTIONS"): "bartleby.runtime.game.game_sizing",
}

DEFAULT_PATHS = ("bartlebot", "tests", "tools", "bartlebot.py")


def moved(module: str) -> str | None:
    """Core's path for *module*, or None when BartleBot keeps it."""
    for old in sorted(TABLE, key=len, reverse=True):
        if module == old or module.startswith(old + "."):
            return TABLE[old] + module[len(old) :]
    return None


def _dotted(old: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w.]){re.escape(old)}(?![\w])")


def _slashed(old: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w/.-]){re.escape(old.replace('.', '/'))}(?![\w])")


#: Longest first, so a module's own entry wins over its package's.
_PATTERNS = [
    (pattern, new_form)
    for old in sorted(TABLE, key=len, reverse=True)
    for pattern, new_form in ((_dotted(old), TABLE[old]), (_slashed(old), TABLE[old].replace(".", "/")))
]


#: A comment on an import line; an import holds no string for a ``#`` to hide in.
_COMMENT = re.compile(r"(#.*?)\s*$")


def _import_lines(node: ast.ImportFrom, indent: str, suffix: str) -> list[str] | None:
    """*node* rewritten, one line per target module, or None if nothing in it moved."""
    groups: dict[str, list[str]] = {}
    changed = False
    for alias in node.names:
        target = moved(f"{node.module}.{alias.name}")
        if (node.module, alias.name) in NAMES:
            module = NAMES[(node.module, alias.name)]
        elif target is not None:
            module = target.rsplit(".", 1)[0]
        else:
            module = moved(node.module) or node.module
        changed |= module != node.module
        groups.setdefault(module, []).append(alias.name if alias.asname is None else f"{alias.name} as {alias.asname}")
    if not changed:
        return None
    lines = []
    for i, (module, names) in enumerate(groups.items()):
        tail = suffix if i == 0 else ""
        lines.append(f"{indent}from {module} import {', '.join(names)}{tail}")
    return lines


def rewrite(source: str) -> str:
    """*source* with every reference to a moved module pointed at core."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        tree = None
    lines = source.splitlines(keepends=True)
    if tree is not None:
        edits = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ImportFrom) and node.level == 0 and node.module):
                continue
            if not node.module.startswith("bartlebot"):
                continue
            first = lines[node.lineno - 1]
            last = lines[node.end_lineno - 1]
            indent = first[: len(first) - len(first.lstrip())]
            # One comment (a noqa) stays, on the first line written; two can't be placed.
            comments = [m.group(1) for line in lines[node.lineno - 1 : node.end_lineno] if (m := _COMMENT.search(line))]
            suffix = f"  {comments[0]}" if comments else ""
            new = _import_lines(node, indent, suffix)
            if new is not None:
                if len(comments) > 1:
                    raise ValueError(f"more than one comment in the import at line {node.lineno}; rewrite it by hand")
                eol = "\r\n" if last.endswith("\r\n") else "\n"
                edits.append((node.lineno, node.end_lineno, [line + eol for line in new]))
        for start, end, new in sorted(edits, reverse=True):
            lines[start - 1 : end] = new
    text = "".join(lines)
    for pattern, new_form in _PATTERNS:
        text = pattern.sub(new_form, text)
    return text


def files(paths: list[Path]) -> list[Path]:
    out = []
    for path in paths:
        if path.is_file():
            out.append(path)
        else:
            out += sorted(p for p in path.rglob("*.py") if "__pycache__" not in p.parts)
    return [p for p in out if p.resolve() != Path(__file__).resolve()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="list the files that would change; change none")
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args(argv)
    paths = args.paths or [REPO / p for p in DEFAULT_PATHS]
    changed = 0
    for path in files(paths):
        source = path.read_bytes().decode("utf-8")
        try:
            new = rewrite(source)
        except ValueError as exc:
            raise SystemExit(f"{path}: {exc}") from exc
        if new != source:
            changed += 1
            print(path.relative_to(REPO) if path.is_relative_to(REPO) else path)
            if not args.check:
                path.write_bytes(new.encode("utf-8"))
    print(f"{changed} file(s) {'to change' if args.check else 'changed'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
