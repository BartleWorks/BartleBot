"""Snap all existing bot node positions to the nearest GRID_W x GRID_H grid point.

Run once after introducing snap-to-grid so existing bots don't jump on first drag.

Usage:
    poetry run python scripts/migrate_bot_positions.py
"""

import json
import sys
from pathlib import Path

# Grid constants must match node_item.py
NODE_W = 220
NODE_H = 70
NODE_GRID_PAD_W = 80
NODE_GRID_PAD_H = 20
GRID_W = NODE_W + NODE_GRID_PAD_W  # = 300
GRID_H = NODE_H + NODE_GRID_PAD_H  # = 90

BOTS_DIR = Path(__file__).parent.parent / "bots"


def snap(value: float, grid: int) -> float:
    return round(value / grid) * grid


def migrate_file(path: Path) -> bool:
    """Snap all state positions in *path* to the grid. Returns True if the file changed."""
    data = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    for state in data.get("states", []):
        pos = state.get("position")
        if pos is None:
            continue
        # Positions are stored as node center coordinates.
        # Convert to top-left, snap, convert back.
        top_left_x = pos["x"] - NODE_W / 2
        top_left_y = pos["y"] - NODE_H / 2
        snapped_x = snap(top_left_x, GRID_W)
        snapped_y = snap(top_left_y, GRID_H)
        new_center_x = snapped_x + NODE_W / 2
        new_center_y = snapped_y + NODE_H / 2
        if new_center_x != pos["x"] or new_center_y != pos["y"]:
            pos["x"] = new_center_x
            pos["y"] = new_center_y
            changed = True
    if changed:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return changed


def main() -> None:
    files = sorted(BOTS_DIR.rglob("*.json"))
    if not files:
        print("No bot JSON files found.")
        sys.exit(0)
    updated = 0
    for path in files:
        try:
            if migrate_file(path):
                print(f"  updated: {path.relative_to(BOTS_DIR.parent)}")
                updated += 1
            else:
                print(f"  ok:      {path.relative_to(BOTS_DIR.parent)}")
        except Exception as exc:
            print(f"  ERROR:   {path.relative_to(BOTS_DIR.parent)} — {exc}", file=sys.stderr)
    print(f"\n{updated}/{len(files)} files updated.")


if __name__ == "__main__":
    main()
