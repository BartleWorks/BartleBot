"""Point BartleBot at bartleby-core: rewrite every reference to a module core now holds.

Stage 3 of #167, and each later stage that moves a module: Stage 5 added the app
skeleton, Stage 6 the cards, pills and the Log tab, Stage 8 the player cards, Stage 9 the
game view, the launch cards and the screen fit, Stage 11 settings and first run. Each module in :data:`TABLE` exists in bartleby-core, identical to
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
    **{
        f"bartlebot.gui.widgets.buttons.{m}": f"bartleby.qt.widgets.buttons.{m}"
        for m in ("action_button", "icon_button", "link_button")
    },
    **{
        f"bartlebot.gui.widgets.labels.{m}": f"bartleby.qt.widgets.labels.{m}" for m in ("elided_label", "themed_label")
    },
    **{f"bartlebot.gui.game_embed.{m}": f"bartleby.qt.game_embed.{m}" for m in ("client_tile", "win32_window")},
    **{f"bartlebot.gui.{m}": f"bartleby.qt.{m}" for m in ("global_hotkeys", "native_widgets", "streamer_mode")},
    **{f"bartlebot.clients.{m}": f"bartleby.runtime.clients.{m}" for m in _CLIENTS},
    **{f"bartlebot.game.{m}": f"bartleby.runtime.game.{m}" for m in _GAME},
    "bartlebot.launcher": "bartleby.runtime.launcher",
    "bartlebot.nodes.timing": "bartleby.runtime.timing",
    "bartlebot.nodes.paths": "bartleby.runtime.paths",
    "bartlebot.bot.run_trace": "bartleby.runtime.run_trace",
    # Stage 5: the app skeleton.
    "bartlebot.logging_setup": "bartleby.qt.app.logging_setup",
    **{f"bartlebot.gui.{m}": f"bartleby.qt.app.{m}" for m in ("single_instance", "app_exit", "ui_state")},
    "bartlebot.gui.offscreen_warmup": "bartleby.qt.offscreen_warmup",
    "bartlebot.gui.debug.qt_paint_debug": "bartleby.qt.debug.qt_paint_debug",
    # Stage 6: cards, reordering, pills and the Log tab.
    **{f"bartlebot.gui.{m}": f"bartleby.qt.{m}" for m in ("menu_utils", "art_signal")},
    **{f"bartlebot.gui.widgets.cards.{m}": f"bartleby.qt.widgets.cards.{m}" for m in ("card_frame", "add_card")},
    **{
        f"bartlebot.gui.widgets.layout.{m}": f"bartleby.qt.widgets.layout.{m}"
        for m in ("drag_reorder", "scroll_gutter", "flow_layout", "dialog_footer", "discard")
    },
    **{f"bartlebot.gui.widgets.labels.{m}": f"bartleby.qt.widgets.labels.{m}" for m in ("rule_heading", "logo_disc")},
    **{f"bartlebot.gui.sidebar.{m}": f"bartleby.qt.sidebar.{m}" for m in ("sidebar_section", "log_panel")},
    # Stage 8: player cards on view models. BartleBot's card is ``client_card.ClientCard`` now.
    "bartlebot.gui.sidebar.player_card": "bartleby.qt.sidebar.player_card",
    "bartlebot.gui.game_embed.card_columns": "bartleby.qt.game_embed.card_columns",
    # Stage 9: the game view, Quick Launch and the screen fit.
    **{f"bartlebot.gui.game_embed.{m}": f"bartleby.qt.game_embed.{m}" for m in ("game_view", "launch_cards")},
    "bartlebot.gui.screen_fit": "bartleby.qt.screen_fit",
    # Stage 11: settings and first run, and the widgets they are built from.
    **{
        f"bartlebot.gui.settings.{m}": f"bartleby.qt.settings.{m}"
        for m in ("rows", "theme_tab", "profile_editor", "game_folder_row")
    },
    "bartlebot.gui.sidebar.option_gutter": "bartleby.qt.sidebar.option_gutter",
    **{
        f"bartlebot.gui.widgets.inputs.{m}": f"bartleby.qt.widgets.inputs.{m}"
        for m in ("search_field", "game_folder_input")
    },
    "bartlebot.gui.widgets.pickers.picker_popup": "bartleby.qt.widgets.pickers.picker_popup",
    "bartlebot.clients.install_location": "bartleby.runtime.clients.install_location",
    "bartlebot.game.game_keybindings": "bartleby.runtime.game.game_keybindings",
}

#: The family pills, which left ``tag_pill`` in Stage 6; the bot-tag pills stay in it.
_PILLS = (
    "pill_height pill_style paint_pill_outline NeutralPill NeutralChip AccentPill AccountPill WarningPill ActionPill"
).split()

#: What stayed in BartleBot of ``gui/screen_fit`` in Stage 9, and what left core's ``game_sizing`` for it.
_WINDOW_PANES = "left_sidebar_floor setup_area setup_resolution FittedLayout fitted_layout".split()
_PANE_SIZING = (
    "BOTCANVAS_MIN_WIDTH BOTCANVAS_MIN_HEIGHT BOTCANVAS_DEFAULT_HEIGHT SINGLE_CLIENT_CANVAS_SHARE "
    "LEFT_SIDEBAR_MIN_WIDTH REFERENCE_FILTER_ROW_PX LEFT_SIDEBAR_CHROME_PX left_sidebar_floor "
    "canvas_height_for single_client_canvas_floor sidebar_widths_for"
).split()

#: What left BartleBot's ``accounts_tab``, ``settings_dialog`` and ``first_run_dialog`` for
#: core in Stage 11. Each keeps the rest: its ``AccountsTab`` with the character selection
#: row, its ``SettingsDialog`` listing its tabs, and its own setup pages, with its texts
#: for the Accounts page and a ``needs_asking`` over its pages.
_ACCOUNTS_TAB = (
    "ADD_ACCOUNT_TITLE EDIT_ACCOUNT_TITLE _confirm_delete _slots_in_use _Swatch _AccountColorPicker "
    "_AccountDialog add_account edit_account delete_account reorder_accounts reorder_launch_profiles "
    "add_launch_profile edit_launch_profile duplicate_launch_profile delete_launch_profile"
).split()
_SETTINGS_DIALOG = "_PREFERRED_SIZE _QT_MAX_SIZE SettingsTab _LazyTab _forms_in".split()
_FIRST_RUN = (
    "TITLE NEXT FINISH BACK STEP SAVE_FAILED RECOMMENDED_DETAIL SetupMode FirstRunPage _CardsPage "
    "MODE_HEADING MODE_CARDS ModePage COMBAT_HEADING TACTIC_CARDS SETTINGS_COMBAT COMBAT_NOTE "
    "CombatQuestionPage GAME_FOLDER_HEADING GAME_FOLDER_INTRO GAME_FOLDER_UNSET named RUNNING_GAME_POLL_MS "
    "GameFolderPage ACCOUNTS_HEADING AccountsPage LAUNCH_PROFILE_HEADING LAUNCH_PROFILE_INTRO "
    "RESOLUTION_LABEL PROFILE_WILL_MAKE PROFILE_EXISTS PROFILE_TOO_FEW PROFILE_NO_FIT LaunchProfilePage "
    "FirstRunDialog"
).split()

#: Names that moved out of a module BartleBot keeps: (module, name) to core's module, or
#: to the BartleBot module that took them.
NAMES: dict[tuple[str, str], str] = {
    ("bartlebot.settings_manager", "VALID_RESOLUTIONS"): "bartleby.runtime.game.game_sizing",
    **{("bartlebot.gui.widgets.pills.tag_pill", name): "bartleby.qt.widgets.pills.pill" for name in _PILLS},
    # Stage 7: the client manager's reorder needs it, and core cannot import a widget module.
    ("bartlebot.gui.game_embed.card_columns", "apply_order"): "bartleby.shared.ordering",
    # Stage 8: the badge left player_pills; the fit refresh waits in launch_cards for Stage 9.
    ("bartlebot.gui.widgets.pills.player_pills", "PlayerBadge"): "bartleby.qt.widgets.pills.player_badge",
    ("bartlebot.gui.game_embed.card_columns", "refresh_column_fit"): "bartleby.qt.game_embed.launch_cards",
    **{
        ("bartlebot.gui.sidebar.player_card", name): "bartlebot.gui.sidebar.client_card"
        for name in ("XYZ_PLACEHOLDER", "MOB_INTERVAL", "format_xyz", "xyz_clipboard_text")
    },
    # Stage 9: BartleBot's own part of the screen fit stays, as gui/window_panes; its pane
    # constants left core's game_sizing for game/pane_sizing.
    **{("bartlebot.gui.screen_fit", name): "bartlebot.gui.window_panes" for name in _WINDOW_PANES},
    **{("bartleby.runtime.game.game_sizing", name): "bartlebot.game.pane_sizing" for name in _PANE_SIZING},
    # Stage 11: settings and first run.
    **{("bartlebot.gui.settings.accounts_tab", name): "bartleby.qt.settings.accounts_tab" for name in _ACCOUNTS_TAB},
    **{
        ("bartlebot.gui.settings.settings_dialog", name): "bartleby.qt.settings.settings_dialog"
        for name in _SETTINGS_DIALOG
    },
    **{("bartlebot.gui.dialogs.first_run_dialog", name): "bartleby.qt.dialogs.first_run_dialog" for name in _FIRST_RUN},
    ("bartlebot.gui.dialogs.new_bot_dialog", "StartCard"): "bartleby.qt.widgets.cards.start_card",
    ("bartlebot.gui.widgets.cards.card_art_tile", "ArtIconView"): "bartleby.qt.widgets.labels.art_icon_view",
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
            # Core's own modules too, where a name left one for BartleBot (Stage 9's panes).
            if not node.module.startswith("bartlebot") and not any((node.module, a.name) in NAMES for a in node.names):
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
    # This file and its test name the old paths on purpose.
    return [p for p in out if p.resolve() != Path(__file__).resolve() and p.name != "test_codemods_adopt_core.py"]


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
