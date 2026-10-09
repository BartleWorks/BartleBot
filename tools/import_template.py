"""Turn an old ``bots/templates/<name>_template.json`` into a draft in the template store.

A one-off for Stage 10.1 (see ``bartlebot/content/templates/template_store.py``). Before it, a template
was an ordinary bot file that users opened and saved under a new name. After it, each
is a folder under ``templates/`` holding ``draft.json`` and an index, and nothing is
released until the maintainer releases it.

For each file it:

- renumbers every node in the template scheme (``tools/renumber_states.py --prefix t_``):
  nodes become ``t_1..N`` and each subgraph instance becomes ``t_<slug>_<n>``;
- keeps the template's id, which becomes the template's own id;
- drops a trailing " Template" from the name, since the store already says what it is;
- moves the description into ``template_meta.blurb``. It holds the old step-by-step
  instructions for now; Stage 10.4 replaces those with setup steps.

Usage::

    uv run python tools/import_template.py bots/templates/*.json
    uv run python tools/import_template.py bots/templates/*.json --write

Without ``--write`` it prints what it would do and writes nothing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bartlebot.bot.schema import BotConfig, TemplateMeta  # noqa: E402
from bartlebot.content.templates.template_store import TEMPLATE_ID_PREFIX, find_template, save_draft  # noqa: E402
from tools.renumber_states import renumber  # noqa: E402

_SUFFIX = " Template"


def to_draft(config: BotConfig) -> dict[str, str]:
    """Make ``config`` a template draft, in place. Returns the id map the renumber applied."""
    if config.is_template:
        raise ValueError(f"{config.name!r} is already a template draft")
    id_map = renumber(config, TEMPLATE_ID_PREFIX)
    if config.name.endswith(_SUFFIX):
        config.name = config.name[: -len(_SUFFIX)]
    config.template_meta = TemplateMeta(blurb=config.description or "")
    config.description = None
    return id_map


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("templates", nargs="+", type=Path, help="old template bot files")
    ap.add_argument("--write", action="store_true", help="write the drafts into templates/")
    args = ap.parse_args(argv)

    for path in args.templates:
        config = BotConfig.load(path)
        if find_template(config.id, PROJECT_ROOT) is not None:
            print(f"{path}: template {config.id} is already in the store; skipped")
            continue
        id_map = to_draft(config)
        print(f"{path} -> {config.name!r}, {len(id_map)} ids renamed")
        if args.write:
            print(f"  wrote {save_draft(config, PROJECT_ROOT).relative_to(PROJECT_ROOT).as_posix()}")

    if not args.write:
        print("\nDry run. Pass --write to write the drafts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
