"""Write the site's Reference pages from the node specs, the node-declared options and the Official subgraphs.

    uv run python tools/build_site_reference.py           # rewrite site/pages/reference/
    uv run python tools/build_site_reference.py --check   # exit 1 if a page is out of date

Rerun it after changing any text a page shows: a ``NodeSpec``'s description, params,
returns, usage notes, safety warning or see also, a node-declared ``BotOptionSpec``, a
node's category or display name, or an Official subgraph. ``tests/nodes/test_reference.py``
fails until it is. It also deletes a generated page that is no longer produced, and
leaves alone any page not marked ``generated: true``.

The renderer is ``bartlebot.nodes.reference``. The output is committed, because the site
is built on Ubuntu, where wizwalker, which ``NODE_SPECS`` imports, doesn't run.
``BARTLEBOT_DEV`` is cleared first, so only what every install ships is read.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="report out-of-date pages and write nothing")
    args = parser.parse_args(argv)

    # Before anything reads a store root: developer mode would point the user root at the repo.
    os.environ.pop("BARTLEBOT_DEV", None)
    from bartlebot.nodes.reference import REFERENCE_DIR, render_pages, stale_pages, write_pages

    folder = ROOT / REFERENCE_DIR
    pages = render_pages()
    if args.check:
        stale = stale_pages(folder, pages)
        for name in stale:
            print(f"out of date: {name}")
        return 1 if stale else 0
    written, deleted = write_pages(folder, pages)
    for name in written:
        print(f"wrote {name}")
    for name in deleted:
        print(f"deleted {name}")
    if not written and not deleted:
        print("the reference is current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
