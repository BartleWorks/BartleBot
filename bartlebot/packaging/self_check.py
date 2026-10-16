"""``bartlebot.py --self-check``: prove a build carries everything the app reads at run time.

A frozen build fails in ways a checkout never does. A data file left out of the bundle,
a native extension that did not come along, Qt's platform plugin, the package metadata
``importlib.metadata`` reads, or the entry points keyring finds its backends by. Each
of those works from a checkout and fails only once the app is packaged, and some fail
only when a user reaches the screen that needs them. So CI runs this against every
build and fails the build on any failure (``docs/RELEASING.md``).

Every check reads what the running app would read: the shipped root is
:func:`bartlebot.paths.project_root`, which is the install directory in a build. A test
passes another root to see a check fail. Nothing is written, and no window is shown.

Each check prints one line, ``ok`` or ``FAIL``, with its name and what it found or why it
failed, and the exit code is the number of failures.
"""

from __future__ import annotations

import importlib.metadata
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from bartlebot.paths import project_root

#: The flag ``bartlebot.py`` dispatches on.
FLAG = "--self-check"

#: What a check reads, and what it found; a failing check raises with the reason.
CheckFn = Callable[[Path], str]


class CheckFailed(Exception):
    """A check found something wrong, said in the words it prints."""


@dataclass(frozen=True)
class Result:
    name: str
    ok: bool
    detail: str
    seconds: float

    def line(self) -> str:
        return f"{'ok  ' if self.ok else 'FAIL'}  {self.name}: {self.detail}"


def _require(condition: object, reason: str) -> None:
    if not condition:
        raise CheckFailed(reason)


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _folder(root: Path, name: str) -> Path:
    folder = root / name
    _require(folder.is_dir(), f"{folder} is missing")
    return folder


# ---------------------------------------------------------------------------
# The checks, in the order they run
# ---------------------------------------------------------------------------


def check_version(_root: Path) -> str:
    """The version ``importlib.metadata`` reports, which share files and the updater read."""
    try:
        return importlib.metadata.version("bartlebot")
    except importlib.metadata.PackageNotFoundError as exc:
        raise CheckFailed("no package metadata for bartlebot, so the app cannot say its version") from exc


def check_keyring(_root: Path) -> str:
    """The backend keyring uses, and one read through it. A read of an absent entry writes nothing."""
    import keyring  # noqa: PLC0415
    from keyring.backends import fail  # noqa: PLC0415

    backend = keyring.get_keyring()
    name = f"{type(backend).__module__}.{type(backend).__name__}"
    _require(not isinstance(backend, fail.Keyring), f"keyring found no backend ({name}), so no password can be kept")
    if sys.platform == "win32":
        from keyring.backends.Windows import WinVaultKeyring  # noqa: PLC0415

        _require(isinstance(backend, WinVaultKeyring), f"keyring is using {name}, not Windows Credential Manager")
    from bartlebot.shared.vendor import keyring_service  # noqa: PLC0415

    keyring.get_password(keyring_service("self-check"), "absent")
    return name


def check_app_imports(_root: Path) -> str:
    """The app's modules, from the controller down, and the node library."""
    import bartlebot.app_controller  # noqa: F401, PLC0415
    from bartlebot.nodes.registry import NODE_REGISTRY  # noqa: PLC0415

    _require(NODE_REGISTRY, "the node library is empty")
    return _count(len(NODE_REGISTRY), "node type")


def check_bots(root: Path) -> str:
    """Every Official bot loads."""
    from bartlebot.bot.schema import BotConfig  # noqa: PLC0415
    from bartlebot.content.bot_roots import BOTS_DIRNAME  # noqa: PLC0415

    paths = sorted(_folder(root, BOTS_DIRNAME).rglob("*.json"))
    _require(paths, f"no bot in {root / BOTS_DIRNAME}")
    for path in paths:
        try:
            BotConfig.load(path)
        except Exception as exc:
            raise CheckFailed(f"{path.relative_to(root).as_posix()} does not load: {exc}") from exc
    return _count(len(paths), "bot")


def check_templates(root: Path) -> str:
    """Every Official template's draft and released versions load, and each release has its recorded bytes."""
    from bartlebot.bot.schema import BotConfig  # noqa: PLC0415
    from bartlebot.content.templates.template_store import (  # noqa: PLC0415
        TEMPLATES_DIRNAME,
        list_templates,
        sha256_matches,
    )

    _folder(root, TEMPLATES_DIRNAME)
    templates = list_templates(root, root)
    _require(templates, f"no template in {root / TEMPLATES_DIRNAME}")
    versions = 0
    for template in templates:
        _require(template.latest_version is not None, f"{template.name} has never been released")
        BotConfig.load(template.draft_path)
        for entry in template.index.versions:
            _require(sha256_matches(template, entry), f"{template.name} v{entry.version} is not the file released")
            BotConfig.load(template.version_path(entry.version))
            versions += 1
    return f"{_count(len(templates), 'template')}, {_count(versions, 'released version')}"


def check_subgraphs(root: Path) -> str:
    """Every Official subgraph definition loads."""
    from bartlebot.content.subgraphs.subgraph_store import SubgraphLibrary  # noqa: PLC0415

    _folder(root, "subgraphs")
    library = SubgraphLibrary(root, root)
    _require(not library.errors, f"definitions that do not load: {sorted(library.errors)}")
    _require(library.ids(), f"no subgraph in {root / 'subgraphs'}")
    return _count(len(library.ids()), "subgraph")


def check_shared_set(root: Path) -> str:
    """The shared Official set: every duel, player strategy and deck loads, and the shipped defaults are in it."""
    from bartlebot.shared import combat_defaults  # noqa: PLC0415
    from bartlebot.shared.strategy_library import LIBRARY_DIRNAME, MODELS, LibraryKind, kind_dir  # noqa: PLC0415

    _folder(root, LIBRARY_DIRNAME)
    ids: dict[LibraryKind, set[str]] = {}
    for kind, model in MODELS.items():
        folder = kind_dir(root, kind)
        paths = sorted(folder.glob("*.json")) if folder.is_dir() else []
        _require(paths, f"no {kind.value} in {folder}")
        ids[kind] = set()
        for path in paths:
            try:
                ids[kind].add(model.model_validate(json.loads(path.read_bytes())).id)
            except Exception as exc:
                raise CheckFailed(f"{path.relative_to(root).as_posix()} does not load: {exc}") from exc
    duels = {
        *combat_defaults.SHIPPED_DUEL_IDS.values(),
        *combat_defaults.SHIPPED_AMBIENT_DUEL_IDS.values(),
    }
    players = {
        combat_defaults.SHIPPED_DEFAULT_PLAYER_ID,
        combat_defaults.SHIPPED_TEAM_HITTER_ID,
        combat_defaults.SHIPPED_DEFAULT_SUPPORT_ID,
        combat_defaults.SHIPPED_GUARDIAN_SUPPORT_ID,
    }
    missing = sorted((duels - ids[LibraryKind.DUEL]) | (players - ids[LibraryKind.PLAYER]))
    _require(not missing, f"shipped defaults missing from the set: {missing}")
    return ", ".join(_count(len(found), kind.value) for kind, found in ids.items())


def game_data_files() -> list[Path]:
    """Every file under ``game/data/`` the app reads, by the path its reader holds."""
    from bartlebot.game import (  # noqa: PLC0415
        badge_categories,
        dialog_captions,
        entity_names,
        name_tables,
        realms,
        restricted_zones,
        scales,
        spell_names,
        spell_schools,
        stat_caps,
        worlds,
        zone_names,
        zone_nav,
    )

    return [
        badge_categories.BADGE_CATEGORIES_PATH,
        dialog_captions.CAPTIONS_PATH,
        entity_names.ENTITY_NAMES_PATH,
        entity_names.REAGENT_NODES_PATH,
        name_tables.TABLES_PATH,
        realms.REALM_NAMES_PATH,
        restricted_zones.RESTRICTED_ZONES_PATH,
        scales.SCALES_PATH,
        spell_names.NAMES_PATH,
        spell_schools.SCHOOLS_PATH,
        stat_caps.CAPS_PATH,
        worlds.WORLD_NAMES_PATH,
        zone_names.ZONE_NAMES_PATH,
        zone_nav.ZONE_NAV_PATH,
    ]


def check_game_data(_root: Path) -> str:
    """Every snapshot in ``game/data/`` is there and parses."""
    files = game_data_files()
    for path in files:
        _require(path.is_file(), f"{path} is missing")
        try:
            json.loads(path.read_bytes())
        except ValueError as exc:
            raise CheckFailed(f"{path.name} does not parse: {exc}") from exc
    return _count(len(files), "file")


def check_type_dump(_root: Path) -> str:
    """The type dump art extraction reads, opened by katsuba as a run opens it."""
    from katsuba.op import TypeList  # noqa: PLC0415

    from bartlebot.game.art_extraction import TYPE_DUMP_DIR, type_dump  # noqa: PLC0415

    path = type_dump()
    _require(path is not None, f"no r<revision>_WizardDev.json in {TYPE_DUMP_DIR}")
    TypeList.open(str(path))
    return path.name


def check_native(_root: Path) -> str:
    """The native extensions a screen loads on demand: curl_cffi (wiki import) and Pillow's DDS decoder (game art)."""
    from curl_cffi import requests  # noqa: F401, PLC0415
    from PIL import DdsImagePlugin, Image  # noqa: F401, PLC0415

    from bartlebot.nodes.combat_runner import check_dsl_uncached  # noqa: PLC0415
    from bartlebot.shared.combat_defaults import LAST_RESORT_DSL  # noqa: PLC0415

    checked = check_dsl_uncached(LAST_RESORT_DSL)
    _require(checked.error is None, f"the combat DSL grammar does not parse the last-resort DSL: {checked.error}")
    return "curl_cffi, Pillow DDS, combat DSL grammar"


def check_qt(_root: Path) -> str:
    """Qt's platform plugin and SVG support, and every icon and the logo rendering. No window is shown."""
    from PyQt6.QtSvg import QSvgRenderer  # noqa: PLC0415
    from PyQt6.QtWidgets import QApplication  # noqa: PLC0415

    from bartlebot.gui.logo import LOGO_SVG  # noqa: PLC0415
    from bartlebot.gui.theming.icon_utils import ICONS_DIR  # noqa: PLC0415

    app = QApplication.instance() or QApplication([sys.argv[0]])
    icons = sorted(ICONS_DIR.glob("*.svg"))
    _require(icons, f"no icon in {ICONS_DIR}")
    for path in [*icons, LOGO_SVG]:
        _require(QSvgRenderer(str(path)).isValid(), f"{path.name} does not render")
    return f"platform {app.platformName()}, {_count(len(icons), 'icon')} and the logo"


CHECKS: tuple[tuple[str, CheckFn], ...] = (
    ("version", check_version),
    ("keyring", check_keyring),
    ("app", check_app_imports),
    ("bots", check_bots),
    ("templates", check_templates),
    ("subgraphs", check_subgraphs),
    ("shared set", check_shared_set),
    ("game data", check_game_data),
    ("type dump", check_type_dump),
    ("native", check_native),
    ("qt", check_qt),
)


def run_checks(root: Path | None = None, checks: tuple[tuple[str, CheckFn], ...] = CHECKS) -> list[Result]:
    """Run every check against ``root`` (the shipped root by default), each on its own: one failing stops none."""
    root = root if root is not None else project_root()
    results = []
    for name, fn in checks:
        started = time.perf_counter()
        try:
            detail, ok = fn(root), True
        except CheckFailed as exc:
            detail, ok = str(exc), False
        except Exception as exc:
            detail, ok = f"{type(exc).__name__}: {exc}", False
        results.append(Result(name, ok, detail, time.perf_counter() - started))
    return results


def main(out: TextIO | None = None, root: Path | None = None, checks: tuple[tuple[str, CheckFn], ...] = CHECKS) -> int:
    """Print one line per check and return the number that failed: the process's exit code."""
    out = out if out is not None else sys.stdout
    results = run_checks(root, checks)
    for result in results:
        print(result.line(), file=out, flush=True)
    failed = sum(not r.ok for r in results)
    print(f"{len(results) - failed} of {len(results)} checks passed", file=out, flush=True)
    return failed
