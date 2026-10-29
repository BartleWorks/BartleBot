"""Velopack's part of startup: the install, update and uninstall hooks, run before anything else.

An installed BartleBot is launched by Velopack itself at four moments, with an argument
naming the moment: after install, before and after an update, and before uninstall.
``velopack.App().run()`` sees that argument, calls the matching hook below and exits the
process, so the app never starts. Velopack kills a hook that runs too long
(:data:`INSTALL_LIMIT_SECONDS`, :data:`UPDATE_LIMIT_SECONDS`), so ``bartlebot.py`` calls
:func:`run_velopack` first, before Qt, logging or anything else is loaded, and a hook may
never show a window. On every other launch ``run()`` returns and the app starts as usual.

Install and update record the install and register the share file types; uninstall
takes both away again (``docs/INSTALLER.md``). Update and uninstall also remove the
update the app staged for Velopack, which is spent by then:

- **The install key**, ``HKCU\\Software\\Bartleby\\BartleBot``, with the install directory
  and the exe (:mod:`bartleby.shared.install_key`). An update writes the same values
  again, which mends a key that went missing and changes nothing otherwise.
- **The six share file types** (:mod:`bartleby.shared.file_types`): BartleBot's own
  three, :func:`own_types`, and the three it shares with every product by the vendor.
  Uninstall removes its own three, and hands the shared three to another installed
  product, or removes them when there is none.

A checkout never calls Velopack: there is nothing installed for it to find, and it would
say so on stderr at every launch. :func:`run_velopack` returns at once unless this is a
packaged build. So a checkout never writes the registry either.
"""

from __future__ import annotations

import functools
import sys
import traceback
from collections.abc import Callable, Sequence
from pathlib import Path

from bartleby.shared import file_types, install_key
from bartleby.shared.file_types import SHARED_TYPES, FileType
from bartleby.shared.vendor import PRODUCT

from bartlebot.packaging.build_kind import is_packaged

#: How long Velopack lets the install and uninstall hooks run before it kills them.
INSTALL_LIMIT_SECONDS = 30
#: How long Velopack lets the before- and after-update hooks run.
UPDATE_LIMIT_SECONDS = 15


#: The folder Velopack keeps the installed app in, and swaps whole on an update.
CURRENT_DIRNAME = "current"


def own_types() -> tuple[FileType, ...]:
    """BartleBot's own share types: ``.bbot``, ``.bbcombat`` and ``.bbsubgraph``, ProgIDs under the product."""
    from bartleby.shared.share_file import SHARED_EXTENSIONS  # noqa: PLC0415

    from bartlebot.content.share_export import EXTENSIONS, FILE_TYPES  # noqa: PLC0415 - only a hook needs it

    own = {kind: ext for kind, ext in EXTENSIONS.items() if kind not in SHARED_EXTENSIONS}
    return file_types.file_types(PRODUCT, own, FILE_TYPES)


def installed_executable(executable: Path | None = None) -> Path:
    """The exe anything outside the install should name: Velopack's stub, beside ``current\\``.

    The stub's path stays the same across updates, and it starts ``current\\BartleBot.exe``
    with the arguments it was given. A build not under ``current\\`` names its own exe.
    """
    exe = Path(executable or sys.executable)
    if exe.parent.name.lower() == CURRENT_DIRNAME:
        return exe.parent.parent / exe.name
    return exe


def install_dir() -> Path:
    """Where this build's shipped content sits: the folder holding ``strategies/``."""
    from bartlebot.paths import project_root  # noqa: PLC0415

    return project_root()


def record_install() -> None:
    """Write the install key and register all six share types to this install's exe."""
    exe = installed_executable()
    install_key.write(PRODUCT, install_dir(), exe)
    file_types.register(own_types() + SHARED_TYPES, exe)
    file_types.notify()


def remove_install() -> None:
    """Delete the install key and BartleBot's own types, and hand the shared ones on or remove them."""
    install_key.delete(PRODUCT)
    file_types.unregister(own_types())
    other = next(
        (r for r in install_key.read_all() if r.product != PRODUCT and r.executable.is_file()),
        None,
    )
    if other is not None:
        file_types.register(SHARED_TYPES, other.executable)
    else:
        file_types.unregister(SHARED_TYPES)
    file_types.notify()


def _never_raises(hook: Callable[[str], None]) -> Callable[[str], None]:
    """*hook*, made to print what it raises instead of raising it (#154).

    Everything a hook does is a registry entry or a spent download, none of which the app
    needs to start, and a hook that raised would fail the install, update or uninstall
    around it. Logging is not set up this early, so stderr is all there is to tell.
    """

    @functools.wraps(hook)
    def guarded(version: str) -> None:
        try:
            hook(version)
        except Exception:
            traceback.print_exc()

    return guarded


@_never_raises
def after_install(version: str) -> None:
    """The new install's first launch, before Velopack opens the app: record it, and register the types."""
    record_install()


@_never_raises
def before_update(version: str) -> None:
    """The installed version, just before Velopack replaces its folder. Nothing to do."""


@_never_raises
def after_update(version: str) -> None:
    """The new version, just after Velopack swapped its folder in: record it and register the types again.

    Both are the same values as before. Writing them again mends a key or a type that
    something removed, and picks up a share type a new release added. The update the app
    staged for Velopack is spent, so it goes (``docs/INSTALLER.md``, *The update check*).
    """
    record_install()
    _clear_staged_update()


@_never_raises
def before_uninstall(version: str) -> None:
    """The installed version, just before Velopack deletes its folder: take the key, the types and any staged update away."""
    remove_install()
    _clear_staged_update()


def _clear_staged_update() -> None:
    """The update check's download folder, which can hold a whole package and is never the user's data."""
    from bartlebot.updates.feed import clear_staging  # noqa: PLC0415 - only a hook needs it

    clear_staging()


#: Each ``velopack.App`` registration method, and the hook it is given.
HOOKS: tuple[tuple[str, Callable[[str], None]], ...] = (
    ("on_after_install_fast_callback", after_install),
    ("on_before_update_fast_callback", before_update),
    ("on_after_update_fast_callback", after_update),
    ("on_before_uninstall_fast_callback", before_uninstall),
)


def run_velopack(args: Sequence[str] | None = None) -> None:
    """In a packaged build, hand the launch to Velopack, which exits the process when it is a hook's.

    ``args`` replaces the command line Velopack reads, for a test. In a checkout this
    returns without importing Velopack.
    """
    if not is_packaged():
        return
    import velopack  # noqa: PLC0415

    app = velopack.App()
    for method, hook in HOOKS:
        getattr(app, method)(hook)
    if args is not None:
        app.set_args(list(args))
    app.run()
