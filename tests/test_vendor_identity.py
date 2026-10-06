"""One vendor name, defined once, everywhere it reaches the user's machine.

Three keyring services and one ``%APPDATA%`` directory used to be named after this
product, while the data in all four is about the *account* — the password, the characters
on it, its identity fingerprint, the launch profiles that bring it. A second product by
the same publisher needs every one of them, and a user cannot be asked to enter the same
accounts twice.

The renaming itself is a string change with no migration, which is only true before
1.0.0. What these tests hold is the part that stays true afterwards: the prefix is
defined in one module and imported, so a future product cannot drift by copying a
literal, and the shared layer stays extractable.
"""

from __future__ import annotations

import ast
from pathlib import Path

from bartlebot import account_identity, character_roster, credentials
from bartlebot.paths import project_root
from bartlebot.shared.vendor import PRODUCT, VENDOR, keyring_service, product_dir, vendor_dir

SHARED = project_root() / "bartlebot" / "shared"


def test_the_vendor_is_not_the_product():
    """Two names answering two questions — see shared/vendor.py.

    The organization (``The-World-Tree``) is developer-facing and lives in URLs; the
    vendor is user-facing and lives on the user's disk. Collapsing them back into one
    string is what this guards against.
    """
    assert VENDOR == "Bartleby"
    assert PRODUCT == "BartleBot"


def test_every_keyring_service_carries_the_vendor_prefix():
    services = [credentials._SERVICE, character_roster._SERVICE, account_identity._SERVICE]

    assert services == [VENDOR, f"{VENDOR}-roster", f"{VENDOR}-identity"]
    assert len(set(services)) == 3, "a shared namespace would put a roster where a password is expected"


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


def test_the_product_directory_sits_under_the_vendor_one(monkeypatch, tmp_path):
    """Per-product data beneath the vendor root; shared data at the root itself."""
    monkeypatch.setenv("APPDATA", str(tmp_path))

    assert vendor_dir() == tmp_path / "Bartleby"
    assert product_dir() == tmp_path / "Bartleby" / "BartleBot"
    assert product_dir("BartleBuddy").parent == vendor_dir()


def test_keyring_service_builds_a_namespace_from_a_suffix():
    assert keyring_service() == VENDOR
    assert keyring_service("roster") == f"{VENDOR}-roster"


def test_shared_layer_imports_nothing_product_specific():
    """The rule that keeps the shared layer extractable, in ``bartlebot/shared``.

    A fourth repo consumed as a pinned dependency, or an installable package, is only
    possible while nothing here reaches into the rest of BartleBot. One import of
    ``gui.theme`` is all it would take — which is exactly how ``account_slots`` came to
    be split out of ``gui/account_accent`` in the first place. Not only the GUI and the
    FSM: a helper like ``store_roots`` or ``paths`` would make extracting the layer a
    rewrite instead of a move (#168).
    """
    forbidden: list[str] = []
    for path in SHARED.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            for name in names:
                product = name.startswith("bartlebot") and not (
                    name == "bartlebot.shared" or name.startswith("bartlebot.shared.")
                )
                if product or name.startswith("PyQt6"):
                    forbidden.append(f"{path.name} imports {name}")

    assert forbidden == []


def test_the_shared_layer_is_reachable_without_qt():
    """Imported by a module list rather than by a running app, so a missing Qt is fatal.

    ``bartlebot.shared`` is what a live-play tool would depend on, and it must not pull a
    widget toolkit in behind it. The import guard above is the static half of this; this
    is the half that catches a transitive one.
    """
    modules = sorted(p.stem for p in SHARED.glob("*.py") if p.stem != "__init__")

    assert modules, "the shared layer has modules"
    for name in modules:
        __import__(f"bartlebot.shared.{name}")


def test_the_shared_layer_has_no_stray_files():
    """Every module here is one a second product would want. A reminder, not a rule."""
    assert {p.name for p in SHARED.glob("*.py")} == {
        "__init__.py",
        "account_slots.py",
        "account_store.py",
        "official_set.py",
        "store_files.py",
        "strategy_library.py",
        "strategy_models.py",
        "vendor.py",
    }


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
    assert (Path(project_root()) / "bartlebot" / "shared" / "vendor.py").exists()
