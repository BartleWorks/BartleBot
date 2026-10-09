"""Apply one stage of the #64 folder reorganization, or check that the tree is consistent.

The plan lives in the comment on issue #64; ``STAGES`` below is its move map, held
as data, and the single source of truth every stage runs. **Delete this tool, and
the ``libcst`` dev dependency that exists for it, in Stage 5.**

``uv run python tools/reorg_64.py <stage>`` applies that stage's slice of the map:

1. ``git mv`` each file (or directory), creating each new package's empty
   ``__init__.py``, and removing a package the stage empties (``utils/``,
   ``defaults/``).
2. **Import pass (libcst).** ``from bartlebot import variables`` becomes
   ``from bartlebot.bot import variables``. A statement importing modules bound for
   different packages (``from bartlebot import dialog_captions, player_realms``) is
   split into one statement per destination, which a dotted-path regex cannot do.
   It maps by full dotted name, so ``from bartlebot import subgraphs`` becomes
   ``from bartlebot.content.subgraphs import subgraphs``, never the package.
3. **Text pass (regex)** over ``*.py``, ``*.md``, ``*.toml``, ``*.json``, ``*.yml``
   in the repo, plus ``.claude/`` and the project memory directory: every dotted
   reference ``bartlebot.<old>`` and every slash reference ``bartlebot/<old>.py``
   becomes the new one. One alternation, longest first, in a single ``re.sub``, so
   nothing is rewritten twice; the lookarounds keep ``gui.theme`` from matching
   ``gui.themed``.
4. ``ruff check --select I --fix`` then ``ruff format`` on every Python file it changed.

``--check`` resolves every ``bartlebot.*``, ``tests.*`` and ``tools.*`` import in
every Python file, lazy ones inside functions included, then greps for every old
name and path of each stage already applied, and confirms each move of an
uncommitted stage shows in git as a rename. It also lists, for reading, every string
literal naming a moved module's file (``"subgraphs.py"``), since a path built from
pieces is invisible to both passes. Run it at the end of every stage.

Usage::

    uv run python tools/reorg_64.py 2a --dry-run   # print the moves, change nothing
    uv run python tools/reorg_64.py 2a
    uv run python tools/reorg_64.py --check
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import libcst as cst

ROOT = Path(__file__).resolve().parent.parent
MEMORY_DIR = Path.home() / ".claude" / "projects" / "c--GitHub-BartleBot" / "memory"
TEXT_SUFFIXES = {".py", ".md", ".toml", ".json", ".yml", ".yaml"}
SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", "worktrees"}
CHECKED_ROOTS = ("bartlebot", "tests", "tools")

# Keys are dotted paths under the stage's root (``bartlebot`` unless the stage says
# otherwise). A group maps a destination package to the module stems it receives,
# each taken from ``from[<destination>]`` (the root's top level when absent). An
# ``extra`` maps one module's old dotted path to its new one, a rename included, and
# ``dirs`` moves a directory by path.
STAGES: dict[str, dict] = {
    "2a": {  # game/
        "game": "badge_categories card_art dialog_captions entity_names game_keybindings game_sizing "
        "name_tables realms restricted_zones scales spell_names spell_schools stat_caps "
        "wiki_drops wiki_pages wizard_names worlds zone_lists zone_names zone_nav",
        "extra": {"utils.drop_detection": "game.drop_detection"},
        "dirs": {"bartlebot/data": "bartlebot/game/data"},
    },
    "2b": {  # combat/
        "combat": "card_kinds fight_keys deck_io dsl_syntax combat_profile combat_grouping combat_warnings "
        "strategy_store strategy_summary strategy_tags strategy_workset",
        "extra": {
            "defaults.combat_config": "combat.combat_config",  # removes defaults/
            "bot.strategy_models": "combat.profile_models",  # the Stage 1 rename
            "gui.deck_content": "combat.deck_content",
        },
    },
    "2c": {  # content/
        "content": "store_roots bot_roots bot_folders bot_fork bot_tags bot_summary new_bot share_export share_import",
        "content.templates": "template_store template_diff template_gallery setup_steps",
        "content.subgraphs": "subgraphs subgraph_store subgraph_signature subgraph_tags signature_tree",
    },
    "2d": {  # clients/
        "clients": "client_manager group_manager account_identity character_roster credentials launch_tracker "
        "relog recovery_readiness port_map sightings tile_slots authoring_tools",
        "extra": {
            "utils.process_resources": "clients.process_resources",  # removes utils/
            "utils.window_kill": "clients.window_kill",
        },
    },
    "2e": {
        "options": "bot_options option_groups option_values param_cards player_realms",
        "bot": "variables variable_watch",
    },
    "3": {  # gui/
        "gui": "ui_state streamer_mode",
        "gui.debug": "qt_layout_debug qt_paint_debug",
        "extra": {
            "gui.window_show_trace": "gui.debug.window_show_trace",
            "gui.canvas.repaint_trace": "gui.debug.repaint_trace",
            "gui.canvas.press_trace": "gui.debug.press_trace",
        },
        "gui.dialogs": "entity_browser export_dialog import_dialog make_copy_dialog new_bot_dialog recovery_dialog "
        "rewards_tag_dialog safety_warning_dialog templates_dialog ui_tree_browser wiki_import_dialog",
        "gui.theming": "theme themed account_accent icon_utils",
        # every name below is currently gui.widgets.<name>
        "gui.widgets.buttons": "action_button icon_button link_button record_button toggle_switch",
        "gui.widgets.labels": "elided_label themed_label rule_heading logo_disc",
        "gui.widgets.inputs": "growing_text_edit search_field list_text dsl_edit friend_name_edit ratio_spin_box "
        "realm_combo player_realms_choice option_widgets variable_widgets",
        "gui.widgets.pickers": "picker_popup player_picker player_list_widget school_picker scales_picker "
        "tag_picker zone_picker",
        "gui.widgets.pills": "tag_pill tags_field player_pills source_pill",
        "gui.widgets.cards": "card_frame add_card strategy_card card_art_tile deck_card_list archmastery_badge "
        "archmastery_editor duel_slot_gem",
        "gui.widgets.layout": "flow_layout drag_reorder scroll_gutter discard dialog_footer drop_tree",
        "from": {
            "gui.dialogs": "gui",
            "gui.theming": "gui",
            **{
                f"gui.widgets.{kind}": "gui.widgets"
                for kind in ("buttons", "labels", "inputs", "pickers", "pills", "cards", "layout")
            },
        },
    },
    "4": {  # tests/ mirrors bartlebot/; every name below is currently tests.<name>
        "root": "tests",
        "bot": "test_ambient_combat_slots test_disconnect_dialog_watcher test_edge_assignments test_machine "
        "test_machine_ambient_combat test_machine_logging test_machine_parked test_machine_preempt "
        "test_machine_variables test_network_link_watcher test_return_routing test_schema test_variable_watch "
        "test_variables test_variables_authoring test_variables_schema",
        "clients": "test_account_adoption test_account_identity test_account_identity_storage test_authoring_tools "
        "test_auto_group test_character_roster test_client_loop test_client_manager_identities "
        "test_client_reorder test_client_slots test_group_manager test_group_member_port test_hook_toggle "
        "test_launch_pending test_port_map test_process_resources test_recovery test_recovery_readiness "
        "test_relog test_relog_cycle test_roster_learning test_shutdown_release test_sightings "
        "test_slot_order test_speed_authoring test_window_kill",
        "combat": "test_card_kinds test_combat_grouping test_combat_profile test_combat_warnings test_deck_content "
        "test_deck_io test_deck_jobs test_dsl_syntax test_dsl_validation test_fight_keys "
        "test_profile_models test_strategy_store test_strategy_summary test_strategy_tags "
        "test_strategy_workset test_strategy_workset_conflicts test_strategy_workset_profile",
        "content": "test_blank_bot test_bot_folders test_bot_fork test_bot_roots test_bot_summary test_bot_tags "
        "test_new_bot test_share_export test_share_import test_store_filenames test_store_roots",
        "content.subgraphs": "test_signature_tree test_subgraph_integration test_subgraph_signature "
        "test_subgraph_store test_subgraphs",
        "content.templates": "test_setup_steps test_template_diff test_template_diff_assignments test_template_drafts "
        "test_template_roots test_template_store test_user_templates",
        "game": "test_badge_categories test_card_art test_dialog_captions test_drop_detection test_entity_names "
        "test_game_keybindings test_game_sizing test_name_tables test_realms test_restricted_zones "
        "test_scales test_spell_name_check test_spell_names test_spell_schools test_stat_caps test_wiki_drops "
        "test_wiki_pages test_wizard_names test_worlds test_zone_lists test_zone_names test_zone_nav",
        "gui": "test_app_logo test_button_guard test_global_hotkeys test_main_window_paths test_menu_anchoring "
        "test_native_widgets test_offscreen_warmup test_screen_fit test_streamer_mode test_ui_state "
        "test_window_minimum",
        "gui.canvas": "test_bot_header test_bot_library_filter test_call_page_cards test_canvas_header_visibility "
        "test_canvas_lod test_canvas_port_marks test_canvas_property_edits test_canvas_toolbar_layout "
        "test_crumb_bar test_drop_keep_list_widget test_edge_assignments_panel test_edge_label_stacking "
        "test_edge_pen test_edge_repaint_bounds test_editor_helpers test_failure_edge_mode "
        "test_focus_mode_indicator test_fold test_friend_param_form test_fsm_role_checkboxes "
        "test_graph_view_cursor_priming test_insert_at_port test_insert_on_edge test_minimap_background "
        "test_nav_zone_param test_node_browser_subgraphs test_node_browser_theme test_node_paint_bounds "
        "test_param_panel_theme test_param_rows test_port_marks test_record_labels test_recording "
        "test_region_collapse test_region_follows_members test_subgraph_call_node "
        "test_subgraph_categories test_subgraph_definition_document test_subgraph_stepping "
        "test_template_ids test_undo_manager test_variables_canvas test_variables_panel "
        "test_wiki_page_param test_xyz_field test_xyz_paste",
        "gui.combat_strategies": "test_assignment_card test_combat_strategy_dialog "
        "test_combat_strategy_dialog_profile test_deck_editor_client_verbs "
        "test_dialog_advance test_dialog_theme test_dsl_live_validation "
        "test_duel_config_editor test_strategy_filters test_strategy_picker_dialog",
        "gui.debug": "test_input_diagnostics test_qt_layout_debug test_qt_paint_debug test_repaint_trace "
        "test_window_show_trace",
        "gui.dialogs": "test_entity_browser_lifecycle test_export_ui test_import_ui test_new_bot_dialog "
        "test_rewards_prompt test_safety_warning test_ui_tree_browser test_wiki_import_parsing",
        "gui.game_embed": "test_authoring_bar test_card_actions test_card_column_reorder test_card_drag_feedback "
        "test_game_tab_cards test_game_view_theme test_launch_card_widgets test_launch_cards "
        "test_launch_column_scrolling test_pill_text test_window_restore",
        "gui.settings": "test_profile_editing_route test_profile_editor test_settings_rows test_settings_tabs",
        "gui.sidebar": "test_card_fold_stores test_combat_tab test_initial_state_warnings test_option_card "
        "test_option_value_cards test_player_card_text test_player_card_widgets test_setup_panel "
        "test_sidebar_layout",
        "gui.sidebar.sections": "test_client_refresh_interval test_client_refresh_one test_combat_order_colors "
        "test_drop_list_edit test_drop_section test_favorite_realm_option "
        "test_options_section test_options_section_derived test_possible_drops_sorting "
        "test_variable_values_section test_variables_section",
        "gui.theming": "test_account_accent test_account_color_pin test_disabled_affordance test_font_sizes "
        "test_icon_utils test_input_border_theme test_sidebar_theme test_theme_refresh_guard "
        "test_themed_mixin",
        "gui.widgets.buttons": "test_action_button test_icon_button test_inspector_button_weights test_link_button "
        "test_record_button test_surface_button_weights",
        "gui.widgets.cards": "test_add_card test_archmastery_badge test_archmastery_editor test_card_art_tile "
        "test_deck_card_list test_strategy_card",
        "gui.widgets.inputs": "test_dsl_edit test_friend_name_option test_growing_text_edit test_list_text "
        "test_ratio_spin_box test_search_field",
        "gui.widgets.labels": "test_elided_label test_rule_heading",
        "gui.widgets.layout": "test_dialog_footer test_discard_widget test_drag_reorder test_flow_layout "
        "test_scroll_gutter",
        "gui.widgets.pickers": "test_picker_popup test_player_list_widget test_record_from_player test_scales_picker "
        "test_school_picker test_tag_picker test_zone_picker",
        "gui.widgets.pills": "test_pill_outline test_player_pills",
        "launcher": "test_auto_select test_launch_batch test_launch_config_patch test_remember_realm",
        "nodes": "test_combat_drop_recording test_combat_flee test_combat_runner test_ensure_together "
        "test_hotkey_node_defaults test_housing test_join_fight test_last_player_target test_node_helpers "
        "test_param_types test_port_clients test_separate_realms test_take_turns test_wizard_city "
        "test_zone_navigation",
        "nodes.combat": "test_check_health_and_mana test_collect_wisps test_handle_combat test_handle_potions "
        "test_scaled_ratios",
        "nodes.interaction": "test_click_path test_collect_reagent test_interact test_interact_menu "
        "test_wait_for_dialogue",
        "nodes.teleportation": "test_mark_location test_sync_in_zone test_teleport_to_coordinates "
        "test_teleport_to_friend test_teleport_to_marked_location test_teleport_to_world_hub",
        "nodes.utility": "test_apply_elixir test_change_realm test_check_level_scaling test_check_zone "
        "test_handle_level_scaling test_quicksell test_quicksell_keep_list test_realm_list "
        "test_relog_node test_sleep",
        "nodes.zone_entry": "test_enter_dungeon test_enter_gauntlet test_enter_zone test_enter_zone_via_list "
        "test_enter_zone_via_object test_enter_zone_via_ride test_object_use_delay",
        "options": "test_bot_options test_derived_declarations test_node_page_cards test_option_groups "
        "test_option_values_quicksell test_param_cards test_player_realms",
        "shared": "test_account_store test_content_hash test_official_set test_shared_strategy_library "
        "test_strategy_library_filter test_strategy_models test_vendor_identity",
        "tools": "test_drop_categories_tool test_drop_default_failure_edges test_drop_icons "
        "test_drop_name_patterns_tool test_duel_slot_icons test_import_template_tool test_probe_aggro_radius "
        "test_probe_fault_delta test_probe_group_tab test_probe_housing_equip test_probe_name_behavior "
        "test_probe_same_place test_rebuild_from_template_tool test_renumber_states_tool "
        "test_scales_table_tool test_tag_icons test_typedump test_world_icons test_zone_nav_edits "
        "test_zone_nav_recorder test_zone_nav_tester_app test_zone_nav_tree",
        "wizsprinter": "test_dsl_negation test_optional_enchant test_spell_ranking test_untargetable_enemies",
    },
}

_RESERVED = {"extra", "dirs", "from", "root"}


@dataclass(frozen=True)
class Move:
    old: str  # repo-relative posix path
    new: str
    is_dir: bool = False

    @property
    def old_dotted(self) -> str:
        return _dotted(self.old)

    @property
    def new_dotted(self) -> str:
        return _dotted(self.new)


def _dotted(path: str) -> str:
    return path.removesuffix(".py").replace("/", ".")


def _path(root: str, dotted: str) -> str:
    return f"{root}/{dotted.replace('.', '/')}.py"


def stage_moves(stage: str) -> list[Move]:
    spec = STAGES[stage]
    root = spec.get("root", "bartlebot")
    sources = spec.get("from", {})
    moves = []
    for dest, stems in spec.items():
        if dest in _RESERVED:
            continue
        src = sources.get(dest, "")
        for stem in stems.split():
            old = f"{src}.{stem}" if src else stem
            moves.append(Move(_path(root, old), _path(root, f"{dest}.{stem}")))
    for old, new in spec.get("extra", {}).items():
        moves.append(Move(_path(root, old), _path(root, new)))
    for old, new in spec.get("dirs", {}).items():
        moves.append(Move(old, new, is_dir=True))
    return moves


# ---------------------------------------------------------------------------
# Files


def _walk(base: Path) -> Iterator[Path]:
    if not base.is_dir():
        return
    for p in base.iterdir():
        if p.is_dir():
            if p.name not in SKIP_DIRS:
                yield from _walk(p)
        elif p.suffix in TEXT_SUFFIXES:
            yield p


def repo_files(suffixes: set[str]) -> list[Path]:
    """Tracked and untracked-but-not-ignored files in the repo, without this tool."""
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode()
    me = Path(__file__).resolve()
    files = {ROOT / f for f in out.split("\0") if f}
    return sorted(p for p in files if p.suffix in suffixes and p.is_file() and p.resolve() != me)


def text_files() -> list[Path]:
    files = set(repo_files(TEXT_SUFFIXES))
    files.update(_walk(ROOT / ".claude"))
    files.update(_walk(MEMORY_DIR))
    me = Path(__file__).resolve()
    return sorted(p for p in files if p.resolve() != me)


def _read(p: Path) -> str | None:
    try:
        return p.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        print(f"  skipped (not UTF-8): {p}")
        return None


def _write(p: Path, text: str) -> None:
    p.write_bytes(text.encode("utf-8"))


def _rel(p: Path) -> str:
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)


# ---------------------------------------------------------------------------
# Step 1: git mv


def validate(moves: list[Move]) -> list[str]:
    problems = []
    for m in moves:
        old, new = ROOT / m.old, ROOT / m.new
        if not (old.is_dir() if m.is_dir else old.is_file()):
            problems.append(f"missing source: {m.old}")
        if new.exists():
            problems.append(f"destination exists: {m.new}")
    news = [m.new for m in moves]
    if len(set(news)) != len(news):
        problems.append("two moves share a destination")
    return problems


def _git(*args: str) -> None:
    subprocess.run(["git", *args], cwd=ROOT, check=True)


def _ensure_packages(new_file: Path, root: Path) -> None:
    """Give every directory between ``root`` and ``new_file`` an ``__init__.py``."""
    d = new_file.parent
    chain = []
    while d != root and root in d.parents:
        chain.append(d)
        d = d.parent
    for pkg in reversed(chain):
        pkg.mkdir(exist_ok=True)
        init = pkg / "__init__.py"
        if not init.exists():
            init.write_bytes(b"")
            _git("add", _rel(init))
            print(f"  created {_rel(init)}")


def move_files(moves: list[Move]) -> None:
    emptied = set()
    for m in moves:
        old, new = ROOT / m.old, ROOT / m.new
        top = ROOT / m.new.split("/", 1)[0]
        _ensure_packages(new, top)  # for a directory, its parents only: data/ is not a package
        if not m.is_dir:
            emptied.add(old.parent)
        _git("mv", m.old, m.new)
        print(f"  {m.old} -> {m.new}")
    for d in sorted(emptied):
        left = [p for p in d.iterdir() if p.name != "__pycache__"]
        init = d / "__init__.py"
        if left == [init] and init.stat().st_size == 0:
            _git("rm", "-q", _rel(init))
            shutil.rmtree(d, ignore_errors=True)
            print(f"  removed the emptied package {_rel(d)}/")


# ---------------------------------------------------------------------------
# Step 2: imports


def _dotted_expr(node: cst.BaseExpression | None) -> str:
    if node is None:
        return ""
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        return f"{_dotted_expr(node.value)}.{node.attr.value}"
    raise TypeError(node)


def _module_expr(dotted: str) -> cst.Attribute | cst.Name:
    return cst.parse_expression(dotted)  # type: ignore[return-value]


class ImportRewriter(cst.CSTTransformer):
    def __init__(self, modmap: dict[str, str]):
        self.modmap = modmap
        self.notes: list[str] = []

    def _split(self, node: cst.ImportFrom) -> list[cst.ImportFrom] | None:
        """The statements ``node`` becomes, or None when it is unchanged."""
        if node.relative:
            return None
        parent = _dotted_expr(node.module)
        if parent in self.modmap:
            return [node.with_changes(module=_module_expr(self.modmap[parent]))]
        if isinstance(node.names, cst.ImportStar):
            return None
        groups: dict[str, list[cst.ImportAlias]] = {}
        changed = False
        for alias in node.names:
            name = _dotted_expr(alias.name)
            new = self.modmap.get(f"{parent}.{name}")
            if new is None:
                groups.setdefault(parent, []).append(alias)
                continue
            changed = True
            new_parent, new_name = new.rsplit(".", 1)
            asname = alias.asname
            if new_name != name and asname is None:
                # A rename: keep the bound name so the file's uses still resolve.
                asname = cst.AsName(name=cst.Name(name))
                self.notes.append(f"renamed module bound as {name!r}; tidy by hand")
            groups.setdefault(new_parent, []).append(alias.with_changes(name=cst.Name(new_name), asname=asname))
        if not changed:
            return None
        if len(groups) > 1 and "#" in cst.Module(body=[]).code_for_node(node):
            self.notes.append("split an import that carried a comment inside it; check the comment survived")
        if len(groups) == 1:
            ((new_parent, aliases),) = groups.items()
            return [node.with_changes(module=_module_expr(new_parent), names=aliases)]
        return [
            cst.ImportFrom(
                module=_module_expr(new_parent),
                names=[a.with_changes(comma=cst.MaybeSentinel.DEFAULT) for a in aliases],
            )
            for new_parent, aliases in groups.items()
        ]

    def leave_SimpleStatementLine(self, original, updated):
        body, split = [], False
        for stmt in updated.body:
            if isinstance(stmt, cst.ImportFrom):
                new = self._split(stmt)
                if new is not None:
                    split |= len(new) > 1
                    body.extend(new)
                    continue
            elif isinstance(stmt, cst.Import):
                stmt = stmt.with_changes(
                    names=[
                        a.with_changes(name=_module_expr(self.modmap[_dotted_expr(a.name)]))
                        if _dotted_expr(a.name) in self.modmap
                        else a
                        for a in stmt.names
                    ]
                )
            body.append(stmt)
        if not split or len(updated.body) != 1:
            return updated.with_changes(body=body)
        lines = [updated.with_changes(body=[s]) for s in body]
        lines[1:] = [ln.with_changes(leading_lines=[]) for ln in lines[1:]]
        return cst.FlattenSentinel(lines)


def rewrite_imports(modmap: dict[str, str]) -> set[Path]:
    changed = set()
    for p in repo_files({".py"}):
        src = _read(p)
        if src is None or not any(old in src for old in _import_needles(modmap)):
            continue
        try:
            tree = cst.parse_module(src)
        except cst.ParserSyntaxError as e:
            print(f"  could not parse {_rel(p)}: {e}")
            continue
        rewriter = ImportRewriter(modmap)
        new = tree.visit(rewriter).code
        for note in rewriter.notes:
            print(f"  {_rel(p)}: {note}")
        if new != src:
            _write(p, new)
            changed.add(p)
    return changed


def _import_needles(modmap: dict[str, str]) -> set[str]:
    """Cheap substrings that any import needing a rewrite contains."""
    return {old.rsplit(".", 1)[-1] for old in modmap}


# ---------------------------------------------------------------------------
# Step 3: text


def _forms(moves: list[Move]) -> dict[str, str]:
    """Every old dotted and slash spelling, mapped to its new spelling."""
    forms = {}
    for m in moves:
        forms[m.old_dotted] = m.new_dotted
        forms[m.old] = m.new
    return forms


def _pattern(olds: Iterable[str]) -> re.Pattern[str]:
    alternation = "|".join(re.escape(o) for o in sorted(olds, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9_])(?:{alternation})(?![A-Za-z0-9_])")


def rewrite_text(moves: list[Move]) -> set[Path]:
    forms = _forms(moves)
    pattern = _pattern(forms)
    for new in forms.values():
        if pattern.search(new):
            raise SystemExit(f"a new name still matches an old one, so the pass is not idempotent: {new}")
    changed = set()
    for p in text_files():
        src = _read(p)
        if src is None:
            continue
        new = pattern.sub(lambda mo: forms[mo.group(0)], src)
        if new != src:
            _write(p, new)
            changed.add(p)
    return changed


# ---------------------------------------------------------------------------
# Step 4: ruff


def run_ruff(files: Iterable[Path]) -> None:
    py = sorted(_rel(p) for p in files if p.suffix == ".py" and ROOT in p.resolve().parents)
    for i in range(0, len(py), 100):
        chunk = py[i : i + 100]
        subprocess.run([sys.executable, "-m", "ruff", "check", "--select", "I", "--fix", "-q", *chunk], cwd=ROOT)
        subprocess.run([sys.executable, "-m", "ruff", "format", "-q", *chunk], cwd=ROOT)


# ---------------------------------------------------------------------------
# --check


def _module_name(p: Path) -> str:
    return _dotted(p.relative_to(ROOT).as_posix()).removesuffix(".__init__")


_spec_cache: dict[str, object] = {}


def _spec(name: str):
    if name not in _spec_cache:
        try:
            _spec_cache[name] = importlib.util.find_spec(name)
        except (ImportError, ValueError, AttributeError):
            _spec_cache[name] = None
    return _spec_cache[name]


_defined_cache: dict[str, set[str] | None] = {}


def _defined_names(module: str) -> set[str] | None:
    """Top-level names a module's source binds, or None when anything goes."""
    if module in _defined_cache:
        return _defined_cache[module]
    spec = _spec(module)
    origin = getattr(spec, "origin", None)
    names: set[str] | None = set()
    if not origin or not origin.endswith(".py"):
        names = None
    else:
        tree = ast.parse(Path(origin).read_text(encoding="utf-8"))
        stack: list[ast.stmt] = list(tree.body)
        while stack and names is not None:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
                if node.name == "__getattr__":
                    names = None
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    if a.name == "*":
                        names = None
                        break
                    names.add(a.asname or a.name.split(".")[0])
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    names.update(n.id for n in ast.walk(t) if isinstance(n, ast.Name))
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                for field in ("body", "orelse", "finalbody", "handlers"):
                    stack.extend(getattr(node, field, []))
            elif isinstance(node, ast.ExceptHandler):
                stack.extend(node.body)
    _defined_cache[module] = names
    return names


def check_imports() -> list[str]:
    problems = []
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    for p in repo_files({".py"}):
        src = _read(p)
        if src is None:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            problems.append(f"{_rel(p)}: does not parse: {e}")
            continue
        here = _module_name(p)
        is_pkg = p.name == "__init__.py"
        for node in ast.walk(tree):
            where = f"{_rel(p)}:{getattr(node, 'lineno', '?')}"
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] in CHECKED_ROOTS and _spec(a.name) is None:
                        problems.append(f"{where}: import {a.name}: no such module")
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = here.split(".") if is_pkg else here.split(".")[:-1]
                    base = base[: len(base) - (node.level - 1)]
                    parent = ".".join(base + ([node.module] if node.module else []))
                else:
                    parent = node.module or ""
                if parent.split(".")[0] not in CHECKED_ROOTS:
                    continue
                spec = _spec(parent)
                if spec is None:
                    problems.append(f"{where}: from {parent} import ...: no such module")
                    continue
                for a in node.names:
                    if a.name == "*":
                        continue
                    sub = _spec(f"{parent}.{a.name}")
                    if sub is not None:
                        locs = getattr(sub, "submodule_search_locations", None)
                        if locs and any((Path(d) / f"{a.name}.py").exists() for d in locs):
                            problems.append(
                                f"{where}: from {parent} import {a.name} binds the package, "
                                f"not the module {parent}.{a.name}.{a.name}"
                            )
                        continue
                    defined = _defined_names(parent)
                    if defined is not None and a.name not in defined:
                        problems.append(f"{where}: from {parent} import {a.name}: not defined there")
    return problems


def _applied(stage: str) -> bool:
    moves = stage_moves(stage)
    return all((ROOT / m.new).exists() and not (ROOT / m.old).exists() for m in moves)


def check_leftovers(stages: list[str]) -> list[str]:
    moves = [m for s in stages for m in stage_moves(s)]
    if not moves:
        return []
    pattern = _pattern(_forms(moves))
    problems = []
    for p in text_files():
        src = _read(p)
        if src is None:
            continue
        for i, line in enumerate(src.splitlines(), 1):
            for mo in pattern.finditer(line):
                problems.append(f"{_rel(p)}:{i}: old name {mo.group(0)}")
    return problems


def file_name_literals(stages: list[str]) -> list[str]:
    """String literals naming a moved module's file: ``"subgraphs.py"``, ``"widgets/action_button.py"``.

    No pass can tell whether the path such a literal builds is still right, so these
    are listed for reading, not counted as problems. A full repo path is left out,
    since the text pass already rewrote it.
    """
    stems = {Path(m.old).name for s in stages for m in stage_moves(s) if not m.is_dir}
    if not stems:
        return []
    roots = "|".join(CHECKED_ROOTS)
    names = "|".join(re.escape(s) for s in stems)
    pattern = re.compile(rf"""["'](?!(?:{roots})/)(?:[^"'\n]*/)?(?:{names})["']""")
    notes = []
    for p in repo_files({".py"}):
        src = _read(p) or ""
        for i, line in enumerate(src.splitlines(), 1):
            if pattern.search(line):
                notes.append(f"{_rel(p)}:{i}: {line.strip()}")
    return notes


def _in_head(path: str) -> bool:
    return subprocess.run(["git", "cat-file", "-e", f"HEAD:{path}"], cwd=ROOT, capture_output=True).returncode == 0


def check_renames(stages: list[str]) -> list[str]:
    status = subprocess.run(
        ["git", "diff", "HEAD", "-M", "--name-status"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    renames = {tuple(line.split("\t")[1:3]) for line in status.splitlines() if line.startswith("R")}
    problems = []
    for s in stages:
        for m in stage_moves(s):
            if m.is_dir:
                files = subprocess.run(
                    ["git", "ls-tree", "-r", "--name-only", "HEAD", m.old], cwd=ROOT, capture_output=True, text=True
                ).stdout.split()
                pairs = [(f, m.new + f[len(m.old) :]) for f in files]
            else:
                pairs = [(m.old, m.new)] if _in_head(m.old) else []
            problems.extend(f"not a rename in git: {o} -> {n}" for o, n in pairs if (o, n) not in renames)
    return problems


def check() -> int:
    applied = [s for s in STAGES if _applied(s)]
    print(f"stages applied: {', '.join(applied) or 'none'}")
    notes = file_name_literals(applied)
    if notes:
        print("read these: each names a moved module's file, and may build a path to its old home")
        for line in notes:
            print(f"  {line}")
    problems = check_imports() + check_leftovers(applied) + check_renames(applied)
    for line in problems:
        print(f"  {line}")
    print(f"{len(problems)} problem(s)")
    return 1 if problems else 0


# ---------------------------------------------------------------------------


def apply(stage: str, dry_run: bool) -> int:
    moves = stage_moves(stage)
    problems = validate(moves)
    for line in problems:
        print(f"  {line}")
    if problems:
        return 1
    print(f"stage {stage}: {len(moves)} move(s)")
    if dry_run:
        for m in moves:
            print(f"  {m.old} -> {m.new}")
        return 0
    modmap = {m.old_dotted: m.new_dotted for m in moves if not m.is_dir}
    move_files(moves)
    changed = rewrite_imports(modmap)
    changed |= rewrite_text(moves)
    run_ruff(changed)
    print(f"rewrote {len(changed)} file(s). Next: --check, the suite, a smoke launch.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("stage", nargs="?", choices=sorted(STAGES), help="the stage to apply")
    ap.add_argument("--dry-run", action="store_true", help="validate and print the stage's moves only")
    ap.add_argument("--check", action="store_true", help="check imports and leftovers; change nothing")
    args = ap.parse_args(argv)
    if args.check:
        return check()
    if not args.stage:
        ap.error("give a stage, or --check")
    return apply(args.stage, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
