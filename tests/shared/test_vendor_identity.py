"""No keyring service is named after the product, anywhere in BartleBot.

The vendor and its keyring services are core's (``bartleby.shared.vendor``, and core's
``tests/shared/test_vendor.py``). What stays here reads BartleBot's own source: the prefix is
defined once and imported, so a module cannot drift by copying a literal.
"""

from __future__ import annotations

import ast
from pathlib import Path

from bartleby.shared.vendor import PRODUCT

from bartlebot.paths import project_root


def test_no_keyring_service_is_spelled_out_at_its_point_of_use():
    """The whole point of the module: three literals cannot drift if there are none.

    Read out of the source rather than asserted on the values, because the values are
    already right by construction — what can regress is a fourth module, or a fifth
    product, writing its own string.
    """
    offenders = []
    for path in (project_root() / "bartlebot").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                if any("SERVICE" in n for n in names):
                    offenders.append(f"{path.name}: {names[0]} = {node.value.value!r}")

    assert offenders == [], "keyring service names come from shared.vendor.keyring_service()"


def test_the_repo_holds_no_product_named_keyring_literal():
    """No module still writes ``keyring.get_password("BartleBot", ...)`` by hand."""
    hits = []
    for path in (project_root() / "bartlebot").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for line in source.splitlines():
            if "keyring." in line and f'"{PRODUCT}' in line:
                hits.append(f"{path.name}: {line.strip()}")

    assert hits == []


def test_project_root_is_where_this_file_thinks_it_is():
    """The guards above walk the tree; a wrong root would make every one of them vacuous."""
    assert (Path(project_root()) / "bartlebot" / "app_controller.py").exists()
