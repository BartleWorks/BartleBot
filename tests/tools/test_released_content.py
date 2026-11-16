"""Tests for tools/released_content.py: a release may retire content, never remove it (#166)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from bartlebot.paths import project_root

sys.path.insert(0, str(project_root()))

from tools import released_content  # noqa: E402
from tools.released_content import (  # noqa: E402
    main,
    previous_release,
    removed,
    shipped,
    tag_files,
    working_tree_files,
)

RELEASE_YML = project_root() / ".github" / "workflows" / "release.yml"
CI_YML = project_root() / ".github" / "workflows" / "ci.yml"


def _doc(item_id: str, name: str = "Thing") -> bytes:
    return json.dumps({"id": item_id, "name": name}).encode("utf-8")


def _index(template_id: str, *files: str) -> bytes:
    versions = [{"version": n, "file": file} for n, file in enumerate(files, 1)]
    return json.dumps({"id": template_id, "versions": versions}).encode("utf-8")


#: A release with one of everything the check keeps.
RELEASE = {
    "bots/01_wizard_city/foulgaze.json": _doc("bot-1", "Foulgaze"),
    "subgraphs/sync-players.json": _doc("sub-1", "Sync players"),
    "strategies/duel/solo.json": _doc("duel-1", "Default Solo Duel"),
    "strategies/player/default.json": _doc("pcs-1", "Default"),
    "strategies/decks/fire.json": _doc("deck-1", "Default Fire Solo Deck"),
    "strategies/official_set.json": b'{"version": 9}',
    "templates/boss-farmer/template.json": _index("tpl-1", "v1.json", "v2.json"),
    "templates/boss-farmer/v1.json": b"{}",
    "templates/boss-farmer/v2.json": b"{}",
    "templates/boss-farmer/draft.json": b"{}",
}


def _changed(gone: tuple[str, ...] = (), new: dict[str, bytes] | None = None) -> dict[str, bytes]:
    """The release with the files in ``gone`` removed, then ``new`` written."""
    return {path: data for path, data in RELEASE.items() if path not in gone} | (new or {})


def test_the_same_content_removes_nothing():
    assert removed(shipped(RELEASE), shipped(dict(RELEASE))) == []


def test_every_kind_of_item_is_kept_by_id():
    found = shipped(RELEASE)

    assert sorted(found.items) == [
        ("bot", "bot-1"),
        ("deck", "deck-1"),
        ("duel strategy", "duel-1"),
        ("player strategy", "pcs-1"),
        ("subgraph", "sub-1"),
    ]
    assert sorted(found.template_versions) == [("tpl-1", "v1.json"), ("tpl-1", "v2.json")]


@pytest.mark.parametrize(
    ("path", "kind", "item_id"),
    [
        ("bots/01_wizard_city/foulgaze.json", "bot", "bot-1"),
        ("subgraphs/sync-players.json", "subgraph", "sub-1"),
        ("strategies/duel/solo.json", "duel strategy", "duel-1"),
        ("strategies/player/default.json", "player strategy", "pcs-1"),
        ("strategies/decks/fire.json", "deck", "deck-1"),
    ],
)
def test_a_removed_item_is_named(path, kind, item_id):
    (problem,) = removed(shipped(RELEASE), shipped(_changed((path,))))

    assert problem.startswith(f"{kind} ") and item_id in problem and path in problem


def test_a_removed_template_version_file_is_named():
    (problem,) = removed(shipped(RELEASE), shipped(_changed(("templates/boss-farmer/v1.json",))))

    assert "boss-farmer v1" in problem and "tpl-1" in problem


def test_a_template_version_no_longer_listed_is_named():
    current = _changed(new={"templates/boss-farmer/template.json": _index("tpl-1", "v1.json")})

    (problem,) = removed(shipped(RELEASE), shipped(current))

    assert "boss-farmer v2" in problem


def test_a_template_whose_id_changed_has_lost_every_version():
    current = _changed(new={"templates/boss-farmer/template.json": _index("tpl-2", "v1.json", "v2.json")})

    assert len(removed(shipped(RELEASE), shipped(current))) == 2


def test_a_renamed_or_moved_item_passes():
    """Matching is by id: where the file sits, and what it is called, can change."""
    current = _changed(
        (
            "bots/01_wizard_city/foulgaze.json",
            "templates/boss-farmer/template.json",
            "templates/boss-farmer/v1.json",
            "templates/boss-farmer/v2.json",
        ),
        {
            "bots/02_krokotopia/foulgaze-farm.json": _doc("bot-1", "Foulgaze farm"),
            "templates/boss-farmers/template.json": _index("tpl-1", "v1.json", "v2.json"),
            "templates/boss-farmers/v1.json": b"{}",
            "templates/boss-farmers/v2.json": b"{}",
        },
    )

    assert removed(shipped(RELEASE), shipped(current)) == []


def test_a_draft_and_the_set_version_are_not_compared():
    current = _changed(("templates/boss-farmer/draft.json", "strategies/official_set.json"))

    assert removed(shipped(RELEASE), shipped(current)) == []


def test_new_content_passes():
    current = _changed(new={"strategies/decks/ice.json": _doc("deck-2")})

    assert removed(shipped(RELEASE), shipped(current)) == []


def test_an_item_moved_to_another_kind_is_gone_from_its_own():
    """A deck's id turning up as a player strategy leaves every link to the deck dangling."""
    current = _changed(("strategies/decks/fire.json",), {"strategies/player/fire.json": _doc("deck-1")})

    (problem,) = removed(shipped(RELEASE), shipped(current))

    assert problem.startswith("deck ")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path):
    """A repo whose v1.0.0 shipped one bot and one template version, and whose v1.1.0 is HEAD."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "bots").mkdir()
    (tmp_path / "bots" / "a bot.json").write_bytes(_doc("bot-1"))
    (tmp_path / "templates" / "t").mkdir(parents=True)
    (tmp_path / "templates" / "t" / "template.json").write_bytes(_index("tpl-1", "v1.json"))
    (tmp_path / "templates" / "t" / "v1.json").write_bytes(b'{"note": "\xe2\x80\x94"}')
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "one")
    _git(tmp_path, "tag", "v1.0.0")
    (tmp_path / "README").write_text("two", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "two")
    _git(tmp_path, "tag", "v1.1.0")
    return tmp_path


def test_a_tag_is_read_as_it_was(repo):
    (repo / "bots" / "a bot.json").unlink()

    files = tag_files("v1.0.0", repo)

    assert files == {
        "bots/a bot.json": _doc("bot-1"),
        "templates/t/template.json": _index("tpl-1", "v1.json"),
        "templates/t/v1.json": b'{"note": "\xe2\x80\x94"}',
    }


def test_the_working_tree_is_read_as_it_is(repo):
    (repo / "bots" / "a bot.json").unlink()

    assert sorted(working_tree_files(repo)) == ["templates/t/template.json", "templates/t/v1.json"]


def test_the_previous_release_leaves_out_the_tag_being_released(repo):
    assert previous_release("v1.1.0", repo) == "v1.0.0"
    assert previous_release(None, repo) == "v1.1.0"


def test_with_no_earlier_tag_there_is_no_previous_release(repo):
    _git(repo, "tag", "-d", "v1.0.0")

    assert previous_release("v1.1.0", repo) is None


def test_main_fails_on_a_removal_and_names_it(repo, monkeypatch, capsys):
    monkeypatch.setattr(released_content, "previous_release", lambda current: previous_release(current, repo))
    monkeypatch.setattr(released_content, "tag_files", lambda tag: tag_files(tag, repo))
    monkeypatch.setattr(released_content, "working_tree_files", lambda: working_tree_files(repo))
    (repo / "bots" / "a bot.json").unlink()

    assert main(["v1.1.0"]) == 1
    err = capsys.readouterr().err
    assert "bot-1" in err and "since v1.0.0" in err


def test_main_passes_when_nothing_is_gone(repo, monkeypatch, capsys):
    monkeypatch.setattr(released_content, "tag_files", lambda tag: tag_files(tag, repo))
    monkeypatch.setattr(released_content, "working_tree_files", lambda: working_tree_files(repo))

    assert main(["--since", "v1.0.0"]) == 0
    assert "v1.0.0" in capsys.readouterr().out


def test_main_fails_with_no_release_to_compare_with(monkeypatch, capsys):
    monkeypatch.setattr(released_content, "previous_release", lambda current: None)

    assert main(["v1.0.0"]) == 1
    assert "fetch-depth" in capsys.readouterr().err


def test_the_checkout_removes_nothing_the_latest_release_shipped():
    """The release check, run at every merge rather than first at a tag.

    A clone without tags has nothing to compare with; CI checks out with every tag.
    """
    latest = previous_release(None)
    if latest is None:
        pytest.skip("no v* tag in this clone to compare with")

    assert removed(shipped(tag_files(latest)), shipped(working_tree_files())) == []


def _steps() -> list[str]:
    return RELEASE_YML.read_text(encoding="utf-8").split("\n      - ")[1:]


def test_the_release_checks_content_after_the_tag_and_before_the_build():
    steps = _steps()
    (check,) = [i for i, step in enumerate(steps) if "released_content.py" in step]

    assert 'released_content.py "$GITHUB_REF_NAME"' in steps[check]
    assert [i for i, step in enumerate(steps) if "release_version.py" in step] == [check - 1]
    assert "build_package.py" in steps[check + 1]


@pytest.mark.parametrize("workflow", [RELEASE_YML, CI_YML], ids=["release", "ci"])
def test_the_checkout_fetches_the_tags(workflow):
    """A shallow clone has no tags, so the check would have nothing to compare with."""
    steps = workflow.read_text(encoding="utf-8").split("\n      - ")
    checkouts = [step for step in steps if "actions/checkout" in step]

    assert "fetch-depth: 0" in checkouts[0]
