"""BartleBot's render sheet: its surfaces, with sample data, offscreen in both themes.

The visual gate of the shared library plan (#167): rendered on ``master`` before a stage
starts and on the stage's branch before it merges, every surface here is pixel-identical
unless the stage names the change. ``bartleby.qt.testing.render_sheet`` (bartleby-core) draws
and compares; this file is the list.

    uv run python tools/render_sheet.py OUT_DIR
    uv run python tools/render_sheet.py --compare BEFORE_DIR AFTER_DIR

Sample data only: four accounts aliased ``P1`` to ``P4`` on ``example.com`` addresses, one
launch profile, no wizard names and no game art. The settings and the art cache are a
throwaway ``%APPDATA%``, never the developer's, and the screen the launch fit is judged
against is pinned, so the pixels depend on this tree alone, and the core it pins.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: The sample accounts, in slot order.
ACCOUNTS = [{"username": f"p{n}@example.com", "alias": f"P{n}"} for n in range(1, 5)]
PROFILES = [{"name": "Farm Team", "accounts": [a["username"] for a in ACCOUNTS], "resolution": [1280, 720]}]

#: A 4K screen at 100%: the launch cards are judged against it, so none is refused by the
#: machine the sheet happens to be rendered on.
SCREEN = ((3840, 2160), 1.0)

#: Log lines as the Log tab shows them, at the level it shows by default.
LOG_LINES = [
    ("12:00:01 [WARNING] bartlebot.node.quicksell: P2's backpack is full; quicksell skipped", logging.WARNING),
    ("12:00:04 [ERROR] bartlebot.bot.machine: enter_dungeon failed for P3: no prompt at the sigil", logging.ERROR),
]


def core_render_sheet():
    """bartleby-core's render sheet module, imported when the sheet is drawn rather than with this one."""
    from bartleby.qt.testing import render_sheet  # noqa: PLC0415

    return render_sheet


def themes():
    """Core's light and dark themes, which BartleBot's widgets read."""
    from bartleby.qt.theming import theme  # noqa: PLC0415

    return core_render_sheet().Themes(
        {"light": theme.LIGHT, "dark": theme.DARK}, theme.active_theme, theme.set_theme_instance
    )


@contextlib.contextmanager
def sample_data() -> Iterator[None]:
    """The sample accounts and profile in the settings, and the screen pinned; both put back after."""
    from bartleby.qt import screen_fit  # noqa: PLC0415

    from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415

    sm = SettingsManager.instance()
    saved = (sm.accounts, sm.launch_profiles, screen_fit.screen_metrics)
    sm.accounts = [dict(a) for a in ACCOUNTS]
    sm.launch_profiles = [dict(p) for p in PROFILES]
    screen_fit.screen_metrics = lambda _widget=None: SCREEN
    try:
        yield
    finally:
        sm.accounts, sm.launch_profiles, screen_fit.screen_metrics = saved


class _SampleClient:
    """A hooked client as the Players tab keys it: by its window handle, nothing more."""

    process_id = 1234
    title = "Wizard101"

    def __init__(self, handle: int) -> None:
        self.window_handle = handle


def _main_window():
    from bartlebot.gui.main_window import MainWindow  # noqa: PLC0415

    return MainWindow()


def _quick_launch():
    from bartlebot.gui.game_embed.game_view_panel import new_game_view  # noqa: PLC0415

    view = new_game_view()
    view.refresh_launch_lists()
    return view


def _players_tab():
    from bartlebot.gui.sidebar.sections.client_section import ClientSection  # noqa: PLC0415

    section = ClientSection()
    section.set_all_clients([_SampleClient(h) for h in range(1, 5)], [])
    return section


def _bar_a():
    from bartlebot.gui.game_embed.game_view_panel import AuthoringBar  # noqa: PLC0415

    return AuthoringBar()


def _log_tab():
    from bartleby.qt.app.logging_setup import LogEntry  # noqa: PLC0415
    from bartleby.qt.sidebar.log_panel import LogPanel  # noqa: PLC0415

    from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415

    panel = LogPanel(SettingsManager.instance())
    for text, level in LOG_LINES:
        panel.append_entry(LogEntry(time=0.0, level=level, logger="", message=text, slot=None, text=text))
    return panel


def _title_bar():
    from bartleby.qt.logo import logo_icon  # noqa: PLC0415
    from bartleby.qt.title_bar.title_bar import TitleBar  # noqa: PLC0415
    from PyQt6.QtWidgets import QMenuBar  # noqa: PLC0415

    menu_bar = QMenuBar()
    for name in ("&File", "&Edit", "&View", "&Help"):
        menu_bar.addMenu(name)
    bar = TitleBar(menu_bar, logo_icon())
    bar.set_title("BartleBot")
    return bar


def _accounts_tab():
    from bartlebot.gui.settings.accounts_tab import AccountsTab  # noqa: PLC0415

    return AccountsTab()


def _settings(tab: str):
    """The Settings dialog, opened on *tab*."""

    def build():
        from bartlebot.gui.settings.settings_dialog import SettingsDialog  # noqa: PLC0415

        dialog = SettingsDialog()
        dialog.open_tab(tab)
        return dialog

    return build


def _setup(page: str | None):
    """Setup, rerun so every page is in the walk, turned to the page whose class is *page*; None for the first."""

    def build():
        from bartleby.qt.dialogs.first_run_dialog import FirstRunDialog  # noqa: PLC0415
        from bartleby.shared.preferences import PreferenceStore  # noqa: PLC0415

        from bartlebot.gui.dialogs.first_run_dialog import setup_pages

        dialog = FirstRunDialog(setup_pages(PreferenceStore(), rerun=True), rerun=True)
        if page is not None:
            dialog._show(next(p for p in dialog.pages if type(p).__name__ == page))
        return dialog

    return build


def surfaces() -> list:
    """BartleBot's sheet, in the order the plan names it."""
    Surface = core_render_sheet().Surface
    return [
        Surface("main_window", _main_window, (1600, 1000)),
        Surface("quick_launch", _quick_launch, (1280, 800)),
        Surface("players_tab", _players_tab, (320, 760)),
        Surface("bar_a", _bar_a, (360, 32)),
        Surface("log_tab", _log_tab, (420, 300)),
        Surface("title_bar", _title_bar, (1280, 40)),
        Surface("settings_accounts", _accounts_tab, (900, 700)),
        Surface("settings_general", _settings("general"), (900, 860)),
        Surface("settings_theme", _settings("theme"), (900, 860)),
        Surface("setup_mode", _setup(None), (600, 640)),
        Surface("setup_accounts", _setup("AccountsPage"), (600, 640)),
        Surface("setup_launch_profile", _setup("LaunchProfilePage"), (600, 640)),
    ]


def render(out_dir: Path) -> list[Path]:
    """Render the sheet into *out_dir*. Needs a ``QApplication`` and BartleBot's settings."""
    with sample_data():
        return core_render_sheet().render(surfaces(), out_dir, themes())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out_dir", nargs="?", type=Path, help="where to write <surface>.<theme>.png")
    parser.add_argument("--compare", nargs=2, type=Path, metavar=("BEFORE", "AFTER"))
    args = parser.parse_args(argv)
    if args.compare:
        changed = core_render_sheet().compare(*args.compare)
        print("\n".join(changed) if changed else "identical")
        return 1 if changed else 0
    if args.out_dir is None:
        parser.error("give an output folder, or --compare BEFORE AFTER")

    # A throwaway %APPDATA% before anything reads settings, and no window manager.
    scratch = Path(tempfile.mkdtemp(prefix="render_sheet_"))
    os.environ["APPDATA"] = str(scratch / "roaming")
    os.environ["LOCALAPPDATA"] = str(scratch / "local")
    os.environ.pop("BARTLEBOT_DEV", None)
    core_render_sheet().use_offscreen_platform()
    sys.path.insert(0, str(REPO))

    from bartleby.qt.native_widgets import keep_siblings_alien  # noqa: PLC0415
    from bartleby.qt.theming.theme import THEME  # noqa: PLC0415
    from PyQt6.QtWidgets import QApplication  # noqa: PLC0415

    keep_siblings_alien()
    app = QApplication(sys.argv[:1])
    app.setFont(THEME.ui_font())
    for path in render(args.out_dir):
        print(path)
    return 0


if __name__ == "__main__":
    # Which app this is, for the parts of bartleby-core that say so, as bartlebot.py does first.
    from bartleby.shared.product import use

    from bartlebot.packaging.product import product

    use(product())
    sys.exit(main())
