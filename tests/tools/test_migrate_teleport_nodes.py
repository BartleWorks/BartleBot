"""migrate_teleport_nodes.py: Teleport to Coordinates becomes Walk to XYZ, and nothing else in a file changes."""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import migrate_teleport_nodes as mtn  # noqa: E402

from bartlebot.bot.schema import BotConfig, StateModel  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def _old_bot(path: Path) -> Path:
    """A bot as BartleBot saved it before Stage 7 of #322: saved now with a Walk to XYZ, then
    given the old node's type, which is the one thing that differs."""
    BotConfig(
        id="b1",
        name="Old",
        initial="a",
        recovery_state="a",
        states=[
            StateModel(id="a", node_type="walk_to_xyz", params={"zone": "Z/One", "xyz": {"x": 1.0, "y": 2.0, "z": 3.0}})
        ],
        transitions=[],
    ).save(path)
    old = path.read_text(encoding="utf-8").replace('"walk_to_xyz"', '"teleport_to_coordinates"')
    path.write_text(old, encoding="utf-8", newline="")
    return path


def test_a_users_bot_is_rewritten_byte_for_byte_but_the_node_type(tmp_path, capsys):
    path = _old_bot(tmp_path / "mine.json")
    before = path.read_text(encoding="utf-8")

    assert mtn.main([str(tmp_path)]) == 0

    after = path.read_text(encoding="utf-8")
    assert after == before.replace('"teleport_to_coordinates"', '"walk_to_xyz"')
    assert BotConfig.load(path).states[0].node_type == "walk_to_xyz"
    assert "1 file(s) rewritten" in capsys.readouterr().out


def test_check_lists_and_writes_nothing(tmp_path, capsys):
    path = _old_bot(tmp_path / "mine.json")
    before = path.read_bytes()

    assert mtn.main(["--check", str(path)]) == 0

    assert path.read_bytes() == before
    assert "1 file(s) would change" in capsys.readouterr().out


def test_a_released_template_version_keeps_its_index_in_step(tmp_path):
    folder = tmp_path / "farmer"
    folder.mkdir()
    version = _old_bot(folder / "v1.json")
    old_sha = hashlib.sha256(version.read_bytes()).hexdigest()
    index = folder / "template.json"
    index.write_text(
        json.dumps({"id": "t", "versions": [{"version": 1, "file": "v1.json", "sha256": old_sha}]}), encoding="utf-8"
    )

    assert mtn.main([str(folder)]) == 0

    listed = json.loads(index.read_text(encoding="utf-8"))["versions"][0]["sha256"]
    assert listed == hashlib.sha256(version.read_bytes()).hexdigest() != old_sha


def test_a_file_without_the_node_is_left_alone(tmp_path):
    path = tmp_path / "fine.json"
    path.write_text('{"states": [{"id": "a", "node_type": "sleep"}]}', encoding="utf-8")
    before = path.read_bytes()

    assert mtn.main([str(tmp_path)]) == 0
    assert path.read_bytes() == before


def test_no_shipped_bot_or_template_names_teleport_to_coordinates():
    """The shipped content was rewritten in Stage 7 of #322, and the node is gone."""
    files = mtn.files_under([ROOT / d for d in mtn.SHIPPED])
    assert files and not [f for f in files if mtn.OLD in f.read_text(encoding="utf-8")]


def test_every_shipped_template_version_matches_its_listed_sha256():
    for index in (ROOT / "templates").rglob("template.json"):
        for entry in json.loads(index.read_text(encoding="utf-8"))["versions"]:
            data = (index.parent / entry["file"]).read_bytes()
            assert hashlib.sha256(data).hexdigest() == entry["sha256"], index.parent.name
