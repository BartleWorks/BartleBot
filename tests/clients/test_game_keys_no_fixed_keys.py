"""No key is pressed as a fixed letter: every game action goes through game_keys.press."""

from __future__ import annotations

import re

from bartlebot.paths import project_root

# Keys that are not an action the player can rebind: Space advances dialogue whatever jump is
# on, and Escape closes a page as CloseUI, which the game does not let a player rebind.
FIXED = {"ESC", "SPACEBAR"}


def test_no_game_action_is_pressed_by_a_fixed_key():
    """A Keycode named in bartlebot/ presses that key whatever the account has it bound to."""
    found = []
    for path in sorted((project_root() / "bartlebot").rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for name in re.findall(r"\bKeycode\.(\w+)", line):
                if name not in FIXED:
                    found.append(f"{path.relative_to(project_root())}:{number}: Keycode.{name}")
    listing = "\n".join(found)
    assert found == [], f"press these with game_keys.press(client, GameAction...):\n{listing}"
