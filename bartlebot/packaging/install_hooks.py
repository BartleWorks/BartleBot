"""Velopack's part of startup: the install, update and uninstall hooks, run before anything else.

An installed BartleBot is launched by Velopack itself at four moments, with an argument
naming the moment: after install, before and after an update, and before uninstall.
``velopack.App().run()`` sees that argument, calls the matching hook below and exits the
process, so the app never starts. Velopack kills a hook that runs too long
(:data:`INSTALL_LIMIT_SECONDS`, :data:`UPDATE_LIMIT_SECONDS`), so ``bartlebot.py`` calls
:func:`run_velopack` first, before Qt, logging or anything else is loaded, and a hook may
never show a window. On every other launch ``run()`` returns and the app starts as usual.

A checkout never calls Velopack: there is nothing installed for it to find, and it would
say so on stderr at every launch. :func:`run_velopack` returns at once unless this is a
packaged build (``docs/INSTALLER.md``).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from bartlebot.packaging.build_kind import is_packaged

#: How long Velopack lets the install and uninstall hooks run before it kills them.
INSTALL_LIMIT_SECONDS = 30
#: How long Velopack lets the before- and after-update hooks run.
UPDATE_LIMIT_SECONDS = 15


def after_install(version: str) -> None:
    """The new install's first launch, before Velopack opens the app. Nothing to do yet."""


def before_update(version: str) -> None:
    """The installed version, just before Velopack replaces its folder. Nothing to do yet."""


def after_update(version: str) -> None:
    """The new version, just after Velopack swapped its folder in. Nothing to do yet."""


def before_uninstall(version: str) -> None:
    """The installed version, just before Velopack deletes its folder. Nothing to do yet."""


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
