"""Audit all bot JSON files for grid alignment, missing 'when' conditions, thin drops, and structural issues.

Any file whose node positions are off the current grid (x not a multiple of GRID_W) is
automatically reformatted via auto_layout and written back to disk.

Usage:
    poetry run python scripts/audit_bots.py
"""

from __future__ import annotations

import json
import sys
from collections import deque
from pathlib import Path

from pydantic import ValidationError

from bartlebot.bot.schema import BotConfig
from bartlebot.gui.canvas.layout import auto_layout
from bartlebot.gui.canvas.node_item import GRID_W

BOTS_DIR = Path(__file__).parent.parent / "bots"
MIN_DROPS = 10
GRID_TOL = 0.5  # px tolerance for floating-point x positions


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _is_on_grid(config: BotConfig) -> bool:
    for state in config.states:
        if state.position is None:
            continue
        x = state.position.get("x", 0)
        if abs(round(x / GRID_W) * GRID_W - x) > GRID_TOL:
            return False
    return True


def _count_off_grid(config: BotConfig) -> int:
    count = 0
    for state in config.states:
        if state.position is None:
            continue
        x = state.position.get("x", 0)
        if abs(round(x / GRID_W) * GRID_W - x) > GRID_TOL:
            count += 1
    return count


def _missing_when(config: BotConfig) -> list[str]:
    """Transitions with no 'when' and no flags — fires unconditionally (may be accidental)."""
    lines = []
    for t in config.transitions:
        if t.when is None and not t.flags:
            suffix = f'  (label: "{t.label}")' if t.label else ""
            lines.append(f'  "{t.source}" -> "{t.dest}"{suffix}')
    return lines


def _unreachable_states(config: BotConfig) -> list[str]:
    adj: dict[str, list[str]] = {s.id: [] for s in config.states}
    for t in config.transitions:
        if t.source in adj:
            adj[t.source].append(t.dest)
    visited: set[str] = set()
    queue: deque[str] = deque([config.initial])
    while queue:
        node = queue.popleft()
        if node in visited:
            continue
        visited.add(node)
        for dest in adj.get(node, []):
            if dest not in visited:
                queue.append(dest)
    return sorted({s.id for s in config.states} - visited)


def _dead_ends(config: BotConfig) -> list[str]:
    """States with no outgoing transitions — will halt the FSM when reached."""
    has_outgoing = {t.source for t in config.transitions}
    return sorted(s.id for s in config.states if s.id not in has_outgoing)


def _dead_transitions(config: BotConfig) -> list[str]:
    state_ids = {s.id for s in config.states}
    lines = []
    for t in config.transitions:
        if t.source not in state_ids:
            lines.append(f'  source "{t.source}" does not exist (-> "{t.dest}")')
        if t.dest not in state_ids:
            lines.append(f'  dest "{t.dest}" does not exist ("{t.source}" →)')
    return lines


def _duplicate_ids(config: BotConfig) -> list[str]:
    seen: set[str] = set()
    dupes = []
    for s in config.states:
        if s.id in seen:
            dupes.append(s.id)
        seen.add(s.id)
    return dupes


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    files = sorted(BOTS_DIR.rglob("*.json"))
    if not files:
        print("No bot JSON files found.")
        sys.exit(0)

    auto_formatted: list[tuple[Path, int]] = []
    missing_when: list[tuple[Path, list[str]]] = []
    thin_drops: list[tuple[Path, int]] = []
    structural: list[tuple[Path, list[str]]] = []
    clean: list[Path] = []
    errors: list[tuple[Path, str]] = []

    for bot_path in files:
        rel = bot_path.relative_to(BOTS_DIR.parent)
        try:
            config = BotConfig.load(bot_path)
        except (ValidationError, Exception) as e:
            errors.append((rel, str(e).splitlines()[0]))
            continue

        reformatted = False
        file_issues: list[str] = []

        # Grid check — auto-format if any x is off-grid
        if not _is_on_grid(config):
            off_count = _count_off_grid(config)
            auto_layout(config)
            pos_map = {s.id: s.position for s in config.states}
            raw = json.loads(bot_path.read_text(encoding="utf-8"))
            for state in raw.get("states", []):
                new_pos = pos_map.get(state["id"])
                if new_pos is not None:
                    state["position"] = new_pos
            bot_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            auto_formatted.append((rel, off_count))
            reformatted = True

        # Missing when
        when_issues = _missing_when(config)
        if when_issues:
            missing_when.append((rel, when_issues))

        # Thin drops
        drop_count = len(config.drops)
        if drop_count < MIN_DROPS:
            thin_drops.append((rel, drop_count))

        # Structural
        for d in _duplicate_ids(config):
            file_issues.append(f'  [DUPLICATE ID] "{d}"')
        for line in _dead_transitions(config):
            file_issues.append(f"  [DEAD TRANSITION] {line}")
        for u in _unreachable_states(config):
            file_issues.append(f'  [UNREACHABLE STATE] "{u}"')
        for d in _dead_ends(config):
            file_issues.append(f'  [DEAD END] "{d}"')
        if file_issues:
            structural.append((rel, file_issues))

        if not reformatted and not when_issues and drop_count >= MIN_DROPS and not file_issues:
            clean.append(rel)

    # -----------------------------------------------------------------------
    # Report
    # -----------------------------------------------------------------------

    def section(title: str) -> None:
        print(f"\n{'=' * 64}")
        print(f"  {title}")
        print("=" * 64)

    section(f"AUTO-FORMATTED - grid realigned ({len(auto_formatted)} files)")
    if auto_formatted:
        for rel, count in auto_formatted:
            noun = "node" if count == 1 else "nodes"
            print(f"  {rel}  ({count} {noun} repositioned)")
    else:
        print("  (none)")

    section(f"MISSING WHEN FIELDS ({len(missing_when)} files)")
    if missing_when:
        for rel, issues in missing_when:
            print(f"  {rel}")
            for line in issues:
                print(line)
    else:
        print("  (none)")

    section(f"THIN DROPS - fewer than {MIN_DROPS} ({len(thin_drops)} files)")
    if thin_drops:
        for rel, count in thin_drops:
            noun = "drop" if count == 1 else "drops"
            print(f"  {rel}  ({count} {noun})")
    else:
        print("  (none)")

    section(f"STRUCTURAL ISSUES ({len(structural)} files)")
    if structural:
        for rel, issues in structural:
            print(f"  {rel}")
            for line in issues:
                print(line)
    else:
        print("  (none)")

    if errors:
        section(f"PARSE ERRORS ({len(errors)} files)")
        for rel, msg in errors:
            print(f"  {rel}  — {msg}")

    section(f"CLEAN ({len(clean)} files)")
    if clean:
        for rel in clean:
            print(f"  {rel}")
    else:
        print("  (none)")

    print()
    total = len(files)
    flagged = len(auto_formatted) + len(missing_when) + len(thin_drops) + len(structural)
    print(f"Scanned {total} files: {len(clean)} clean, {flagged} flagged, {len(errors)} unreadable.")
    print("  (a file may appear in multiple sections; flagged count reflects unique sections hit)")


if __name__ == "__main__":
    main()
