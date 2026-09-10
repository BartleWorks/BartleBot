"""Tests for bartlebot.subgraph_store — the two-root subgraph library."""

from __future__ import annotations

from bartlebot.bot.schema import StateModel, SubgraphDefinition
from bartlebot.store_roots import Origin
from bartlebot.subgraph_store import (
    SubgraphLibrary,
    delete_subgraph,
    list_subgraphs,
    load_subgraph,
    origin_of,
    save_subgraph,
)


def _defn(sg_id: str = "sg-a", name: str = "A", version: int = 1) -> SubgraphDefinition:
    return SubgraphDefinition(id=sg_id, name=name, version=version, states=[StateModel(id="s", node_type="relog")])


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
    assert (user / "subgraphs" / "sg-a.json").exists()
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
