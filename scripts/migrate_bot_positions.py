"""Apply auto-layout to all existing bot files so positions fit the current node sizes.

Run once after node sizes or grid constants change to give every bot a clean layout.

Usage:
    uv run python scripts/migrate_bot_positions.py
"""

import json
import sys
from pathlib import Path

# These imports require the BartleBot package to be on sys.path (uv run handles this).
from bartlebot.bot.schema import BotConfig
from bartlebot.gui.canvas.layout import auto_layout

BOTS_DIR = Path(__file__).parent.parent / "bots"


def migrate_file(path: Path) -> bool:
    """Apply auto-layout to *path* and write updated positions. Returns True if changed."""
    config = BotConfig.load(path)
    auto_layout(config)

    pos_map = {s.id: s.position for s in config.states}

    data = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    for state in data.get("states", []):
        new_pos = pos_map.get(state["id"])
        if new_pos is not None and state.get("position") != new_pos:
            state["position"] = new_pos
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
