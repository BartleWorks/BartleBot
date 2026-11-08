"""Every page the app opens for help exists, with the heading it names (epic #25, Stage 5)."""

from __future__ import annotations

import ast
import re
import tomllib

import pytest

from bartlebot.nodes.registry import NODE_SPECS
from bartlebot.packaging.help_topics import (
    GUIDE_HOME,
    HELP_TOPICS,
    REPORT_PROBLEM_URL,
    SITE_URL,
    TROUBLESHOOTING,
    help_url,
    node_help_url,
    node_target,
    page_url,
)
from bartlebot.paths import project_root

SITE = project_root() / "site"
PAGES = SITE / "pages"
#: A heading's explicit id: `## Words { #the-id }`.
_EXPLICIT_ID = re.compile(r"^#{1,6} .*\{ #([a-z0-9-]+) \}\s*$", re.M)


def _explicit_ids(page: str) -> set[str]:
    return set(_EXPLICIT_ID.findall((PAGES / page).read_text(encoding="utf-8")))


def _resolves(target: str) -> None:
    page, _, anchor = target.partition("#")
    assert (PAGES / page).is_file(), f"{target}: no page {page} under site/pages/"
    if anchor:
        assert anchor in _explicit_ids(page), f"{target}: no heading on {page} has the explicit id {anchor!r}"


@pytest.mark.parametrize("topic", sorted(HELP_TOPICS))
def test_every_topic_names_a_page_and_a_heading_with_that_explicit_id(topic):
    _resolves(HELP_TOPICS[topic])


def test_the_help_menus_pages_exist():
    _resolves(GUIDE_HOME)
    _resolves(TROUBLESHOOTING)


@pytest.mark.parametrize("node_type", sorted(NODE_SPECS))
def test_every_nodes_help_is_its_reference_entry(node_type):
    _resolves(node_target(node_type))
    assert node_help_url(node_type) == page_url(node_target(node_type))


def test_a_mistyped_anchor_fails_the_check():
    with pytest.raises(AssertionError, match="explicit id"):
        _resolves("troubleshooting.md#where-the-log-is")


def test_the_site_url_is_the_one_the_site_is_built_for():
    config = tomllib.loads((SITE / "zensical.toml").read_text(encoding="utf-8"))

    assert config["project"]["site_url"] == SITE_URL


@pytest.mark.parametrize(
    ("target", "url"),
    [
        ("index.md", SITE_URL),
        ("guides/index.md", SITE_URL + "guides/"),
        ("troubleshooting.md", SITE_URL + "troubleshooting/"),
        ("guides/making/sharing.md#import", SITE_URL + "guides/making/sharing/#import"),
    ],
)
def test_a_page_is_served_as_a_folder(target, url):
    assert page_url(target) == url


def test_an_unknown_topic_opens_the_guides_home():
    assert help_url("no_such_surface") == help_url(None) == SITE_URL + "guides/"


def test_report_a_problem_opens_the_new_issue_page_troubleshooting_links():
    assert f"]({REPORT_PROBLEM_URL})" in (PAGES / TROUBLESHOOTING).read_text(encoding="utf-8")


def _topics_named_in_code() -> list[tuple[str, str]]:
    """Every literal topic handed to ``set_help_topic`` or ``help_url`` in the app's code."""
    found = []
    for path in (project_root() / "bartlebot").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in ("set_help_topic", "help_url"):
                continue
            for arg in node.args:
                # An f-string's pieces aren't topics: Settings' tabs are checked by building it.
                pieces = {id(c) for f in ast.walk(arg) if isinstance(f, ast.JoinedStr) for c in ast.walk(f)}
                found += [
                    (path.name, c.value)
                    for c in ast.walk(arg)
                    if isinstance(c, ast.Constant) and isinstance(c.value, str) and id(c) not in pieces
                ]
    return found


def test_every_topic_the_app_names_is_in_the_map():
    named = _topics_named_in_code()

    assert named, "the scan found no topics at all"
    assert [(file, topic) for file, topic in named if topic not in HELP_TOPICS] == []
