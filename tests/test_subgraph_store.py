"""Tests for bartlebot.subgraph_store — the two-root subgraph library."""

from __future__ import annotations

from bartlebot.bot.schema import SUBGRAPH_ENTRY, StateModel, SubgraphDefinition, TransitionModel
from bartlebot.paths import project_root
from bartlebot.store_roots import Origin
from bartlebot.subgraph_store import (
    SubgraphLibrary,
    count_subgraph_uses,
    delete_subgraph,
    list_subgraphs,
    load_subgraph,
    origin_of,
    save_subgraph,
)


def _defn(sg_id: str = "sg-a", name: str = "A", version: int = 1) -> SubgraphDefinition:
    return SubgraphDefinition(
        id=sg_id,
        name=name,
        version=version,
        states=[
            StateModel(id="entry_start", node_type=SUBGRAPH_ENTRY, label="Start"),
            StateModel(id="s", node_type="relog"),
        ],
        transitions=[TransitionModel(trigger="advance", source="entry_start", dest="s")],
    )


def test_user_root_shadows_shipped(tmp_path):
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    _defn(name="Shipped").save(shipped / "subgraphs" / "sg-a.json")
    assert load_subgraph("sg-a", shipped, user).name == "Shipped"

    save_subgraph(_defn(name="Mine", version=2), shipped, user)
    assert load_subgraph("sg-a", shipped, user).name == "Mine"
    assert [d.name for d in list_subgraphs(shipped, user)] == ["Mine"]
    assert load_subgraph("sg-a", shipped).name == "Shipped", "the shipped file is untouched"


def test_origin_of_matrix(tmp_path):
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    assert origin_of("sg-a", shipped, user) is Origin.MISSING
    _defn().save(shipped / "subgraphs" / "sg-a.json")
    assert origin_of("sg-a", shipped, user) is Origin.SHIPPED
    save_subgraph(_defn(), shipped, user)
    assert origin_of("sg-a", shipped, user) is Origin.SHIPPED_OVERRIDE
    delete_subgraph("sg-a", shipped, user)
    assert origin_of("sg-a", shipped, user) is Origin.SHIPPED, "delete on an override is revert"
    save_subgraph(_defn("sg-b"), shipped, user)
    assert origin_of("sg-b", shipped, user) is Origin.USER
    # a single sandbox root is all the user's
    save_subgraph(_defn("sg-c"), tmp_path)
    assert origin_of("sg-c", tmp_path) is Origin.USER


def test_save_only_touches_user_root(tmp_path):
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    save_subgraph(_defn(), shipped, user)
    assert (user / "subgraphs" / "a.json").exists()
    assert not (shipped / "subgraphs").exists()


def test_unloadable_file_is_skipped(tmp_path):
    good = _defn("good")
    good.save(tmp_path / "subgraphs" / "good.json")
    (tmp_path / "subgraphs" / "bad.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "subgraphs" / "wrong.json").write_text(
        '{"id": "wrong", "name": "W", "states": [{"id": "a-b"}]}', encoding="utf-8"
    )

    assert [d.id for d in list_subgraphs(tmp_path)] == ["good"]
    lib = SubgraphLibrary(tmp_path)
    assert lib.ids() == ["good"]
    assert lib.get("bad") is None
    assert lib.error_for("bad")
    assert lib.error_for("wrong")
    assert lib.error_for("good") is None
    assert lib.get("nothing") is None and lib.error_for("nothing") is None


def test_library_save_refreshes_the_cache_and_origin(tmp_path):
    shipped, user = tmp_path / "shipped", tmp_path / "user"
    _defn(name="Shipped").save(shipped / "subgraphs" / "sg-a.json")
    lib = SubgraphLibrary(shipped, user)
    assert lib.get("sg-a").name == "Shipped"
    assert lib.origin("sg-a") is Origin.SHIPPED

    lib.save(_defn(name="Mine", version=2))
    assert lib.get("sg-a").version == 2
    assert lib.origin("sg-a") is Origin.SHIPPED_OVERRIDE
    lib.delete("sg-a")
    assert lib.get("sg-a").name == "Shipped"


def test_a_fixed_file_clears_its_error(tmp_path):
    path = tmp_path / "subgraphs" / "sg-a.json"
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")
    lib = SubgraphLibrary(tmp_path)
    assert lib.error_for("sg-a")
    _defn().save(path)
    lib.reload()
    assert lib.error_for("sg-a") is None
    assert lib.get("sg-a") is not None


def test_every_linked_bot_resolves_under_the_shipped_root_alone():
    """The guard against authoring into a copy the repo never sees.

    A definition saved to ``%APPDATA%`` shadows the repo's, so the app composes from
    the private copy, everything looks right, and the tracked one goes stale — or,
    as happened to ``foulgaze``, never exists at all. Resolving against the project
    root with no user root is what a clone or CI sees. Author with ``BARTLEBOT_DEV``
    set and the definition lands where this test looks.
    """
    shipped = project_root()
    library = SubgraphLibrary(shipped, shipped)
    # Template drafts and versions too: every bot made from one links what it links.
    uses = count_subgraph_uses(shipped / "bots") | count_subgraph_uses(shipped / "templates")
    assert library.ids(), "no shipped subgraph, so this guard checks nothing"
    assert not library.errors, f"shipped definitions that fail to load: {library.errors}"
    assert uses, "no shipped bot or template links a subgraph, so this guard checks nothing"

    unresolved = sorted(sid for sid in uses if library.get(sid) is None)
    assert not unresolved, f"linked from bots/ but not loadable from subgraphs/: {unresolved}"


def test_every_shipped_bot_and_template_composes_cleanly():
    """A shipped definition change that breaks a bot fails here, not in someone's run.

    Every linked region in every bot, draft and released version has to come out
    ``linked`` with nothing to report: no ``held``, ``missing``, ``invalid`` or
    ``cycle``, and no nested instance held inside it.
    """
    from bartlebot.bot.schema import BotConfig  # noqa: PLC0415
    from bartlebot.subgraphs import compose_bot  # noqa: PLC0415

    shipped = project_root()
    library = SubgraphLibrary(shipped, shipped)
    files = sorted((shipped / "bots").rglob("*.json")) + sorted((shipped / "templates").glob("*/[dv]*.json"))
    linked, broken = 0, []
    for path in files:
        _composed, report = compose_bot(BotConfig.load(path), library)
        linked += bool(report.regions)
        broken += [
            f"{path.relative_to(shipped).as_posix()} {rid}: {status.kind} {status.reasons}"
            for rid, status in report.regions.items()
            if status.kind != "linked" or status.reasons
        ]

    assert linked, "nothing shipped links a subgraph, so this checks nothing"
    assert not broken, "shipped files that don't compose cleanly:\n" + "\n".join(broken)
