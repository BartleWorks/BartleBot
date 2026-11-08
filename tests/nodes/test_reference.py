"""Tests for bartlebot.nodes.reference: the site's Reference pages, rendered from the specs and committed.

The committed pages under site/pages/reference/ must equal a fresh render, so a change
to any text they show fails here until tools/build_site_reference.py is rerun.
"""

from __future__ import annotations

import re
import shutil
import tomllib

import pytest

from bartlebot.bot.schema import SUBGRAPH_ENTRY, StateModel, SubgraphDefinition, TransitionModel
from bartlebot.content.store_roots import resolve_roots
from bartlebot.content.subgraphs.subgraph_store import list_subgraphs, save_subgraph
from bartlebot.nodes import reference
from bartlebot.nodes.param_schema import ParamField
from bartlebot.nodes.reference import (
    COMMAND,
    INDEX_PAGE,
    OPTIONS_PAGE,
    REFERENCE_DIR,
    SETTING_ROWS,
    SUBGRAPHS_PAGE,
    category_page,
    escape,
    is_generated,
    node_anchor,
    node_options,
    page_names,
    param_default,
    render_category_page,
    render_pages,
    render_subgraphs_page,
    slug,
    stale_pages,
    write_pages,
)
from bartlebot.nodes.registry import NODE_CATEGORIES, NODE_SPECS, node_display_name
from bartlebot.options.bot_options import APP_SETTING_KEYS
from bartlebot.paths import project_root

FOLDER = project_root() / REFERENCE_DIR
PAGES = project_root() / "site" / "pages"
#: A heading, with its explicit id when it has one.
_HEADING = re.compile(r"^#{1,6} (.*?)(?: \{ #([a-z0-9-]+) \})?$", re.M)
#: A link's target, with or without a page before the anchor.
_LINK = re.compile(r"\]\(([^)\s]+)\)")


@pytest.fixture(scope="module")
def pages() -> dict[str, str]:
    return render_pages()


def _ids(text: str) -> list[str]:
    """Every heading's id on a page: its explicit one, else the one its words give."""
    body = text.split("\n---\n", 1)[1]
    return [explicit or slug(words) for words, explicit in _HEADING.findall(body)]


def _section(text: str, heading_id: str) -> str:
    """The text under the heading with *heading_id*, up to the next heading of any level."""
    start = text.index(f"{{ #{heading_id} }}")
    following = _HEADING.search(text, text.index("\n", start))
    return text[start : following.start() if following else len(text)]


# ---------------------------------------------------------------------------
# The committed pages
# ---------------------------------------------------------------------------


def test_the_committed_reference_is_current(pages):
    stale = stale_pages(FOLDER, pages)

    assert stale == [], f"The Reference pages are out of date: {', '.join(stale)}. Rerun `{COMMAND}`."


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(lambda mp: mp.setattr(NODE_SPECS["sleep"], "description", "Waits."), id="description"),
        pytest.param(lambda mp: mp.setattr(NODE_SPECS["relog"], "usage_notes", "Rarely."), id="usage-notes"),
        pytest.param(lambda mp: mp.setattr(NODE_SPECS["quicksell"], "safety_warning", "Careful."), id="warning"),
        pytest.param(lambda mp: mp.setattr(NODE_SPECS["sleep"].params[0], "help", "Long."), id="param-help"),
        pytest.param(lambda mp: mp.setattr(NODE_SPECS["sleep"].returns[0], "description", "Now."), id="returns"),
        pytest.param(lambda mp: mp.setattr(NODE_SPECS["branch"], "see_also", []), id="see-also"),
        pytest.param(
            lambda mp: mp.setattr(NODE_SPECS["enter_dungeon"].required_bot_options[2], "description", "Harder."),
            id="option-description",
        ),
    ],
)
def test_changing_any_text_a_page_shows_makes_the_reference_stale(monkeypatch, change):
    change(monkeypatch)

    assert stale_pages(FOLDER, render_pages())


def test_changing_an_official_subgraphs_description_makes_the_reference_stale(tmp_path):
    shutil.copytree(project_root() / "subgraphs", tmp_path / "subgraphs")
    for path in (tmp_path / "subgraphs").glob("*.json"):
        defn = SubgraphDefinition.load(path)
        path.write_text(defn.model_copy(update={"description": "Changed."}).model_dump_json(), encoding="utf-8")

    assert stale_pages(FOLDER, render_pages(subgraph_root=tmp_path)) == [SUBGRAPHS_PAGE]


def test_every_page_is_marked_generated_and_names_the_tool(pages, tmp_path):
    write_pages(tmp_path, pages)

    for name in pages:
        front = (tmp_path / name).read_text(encoding="utf-8").split("\n---\n", 1)[0]
        assert is_generated(tmp_path / name), name
        assert "tools/build_site_reference.py" in front, name


def test_the_tool_deletes_a_generated_page_no_longer_produced_and_leaves_a_hand_written_one(pages, tmp_path):
    write_pages(tmp_path, pages)
    (tmp_path / "retired.md").write_text("---\ngenerated: true\n---\n\n# Retired\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("# Notes\n", encoding="utf-8")

    assert stale_pages(tmp_path, pages) == ["retired.md"]
    assert write_pages(tmp_path, pages) == ([], ["retired.md"])
    assert sorted(p.name for p in tmp_path.glob("*.md")) == sorted([*pages, "notes.md"])


def test_the_nav_lists_the_reference_pages_in_the_order_they_are_rendered(pages):
    config = tomllib.loads((project_root() / "site" / "zensical.toml").read_text(encoding="utf-8"))
    section = next(entry["Reference"] for entry in config["project"]["nav"] if "Reference" in entry)

    assert section == [f"reference/{name}" for name in page_names()]
    assert list(pages) == page_names()


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def test_each_category_has_a_page_and_every_node_appears_once_under_its_display_name(pages):
    entries = {}
    for name, node_types in NODE_CATEGORIES:
        text = pages[category_page(name)]
        for words, heading_id in _HEADING.findall(text):
            if heading_id:
                entries.setdefault(heading_id, []).append((name, words))
        for t in node_types:
            assert entries[node_anchor(t)] == [(name, node_display_name(t))]

    assert len(NODE_SPECS) == 30
    assert sorted(entries) == sorted(node_anchor(t) for t in NODE_SPECS)


def test_a_nodes_anchor_is_its_type_and_stays_when_its_display_name_changes(monkeypatch):
    monkeypatch.setattr(reference, "node_display_name", lambda node_type: "Renamed")

    text = render_category_page("Zone Entry", ["enter_dungeon"])

    assert "## Renamed { #enter-dungeon }" in text


def test_a_conditional_option_names_the_param_that_adds_it_by_its_label(pages):
    sleep = _section(pages[category_page("Utility")], "sleep")

    assert "[Safety Delay](options.md#safety-delay), while **Use Safety Delay** is on" in sleep


def test_every_anchor_on_a_page_is_unique(pages):
    for name, text in pages.items():
        ids = _ids(text)
        assert len(ids) == len(set(ids)), name


def test_every_link_into_the_reference_resolves_to_a_heading_on_its_page(pages):
    ids = {name: set(_ids(text)) for name, text in pages.items()}
    links = [(f"reference/{name}", target) for name, text in pages.items() for target in _LINK.findall(text)]
    # The hand-written pages link into the reference too, and a strict build is not part of the suite.
    for page in PAGES.rglob("*.md"):
        if page.parent != FOLDER:
            for target in _LINK.findall(page.read_text(encoding="utf-8")):
                if "reference/" in target:
                    links.append((page.relative_to(PAGES).as_posix(), target))

    for source, target in links:
        if target.startswith(("http://", "https://")):
            continue
        path, _, anchor = target.partition("#")
        name = path.rpartition("/")[2] or source.rpartition("/")[2]
        assert name in ids, (source, target)
        assert not anchor or anchor in ids[name], (source, target)


def test_no_entry_shows_a_raw_node_type_or_param_key(pages):
    keys = {t for t in NODE_SPECS if "_" in t}
    keys |= {f.key for spec in NODE_SPECS.values() for f in spec.params if "_" in f.key}
    for name, text in pages.items():
        shown = text.split("\n---\n", 1)[1]
        shown = re.sub(r"\{ #[^}]+ \}|\]\([^)]*\)", "", shown).replace("\\", "")
        found = sorted(k for k in keys if re.search(rf"\b{re.escape(k)}\b", shown))
        assert found == [], name


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


def test_every_node_declared_option_has_an_entry_naming_its_app_setting_and_its_nodes(pages):
    text = pages[OPTIONS_PAGE]
    options = node_options()

    assert len(options) == 14
    for option in options:
        section = _section(text, slug(option.spec.name))
        row = SETTING_ROWS.get(option.spec.name)
        assert (f"**Settings › Bot › {row}**" in section) == bool(row), option.spec.name
        for node_type, _declaration in option.declared_by:
            page = category_page(next(c for c, ts in NODE_CATEGORIES if node_type in ts))
            assert f"]({page}#{node_anchor(node_type)})" in section, (option.spec.name, node_type)


def test_every_declaration_of_one_option_agrees_on_what_its_entry_says():
    """The entry is drawn from the first declaration, so the rest must say the same."""
    for option in node_options():
        for node_type, declaration in option.declared_by:
            assert (declaration.description, declaration.default, declaration.type, declaration.depends_on) == (
                option.spec.description,
                option.spec.default,
                option.spec.type,
                option.spec.depends_on,
            ), (option.spec.name, node_type)


def test_every_app_setting_names_the_row_the_settings_dialog_draws():
    from bartlebot.gui.settings.bot_tab import FRIEND_NAME_TITLE
    from bartlebot.gui.settings.players_rows import REFILL_POTIONS_TITLE
    from bartlebot.gui.settings.quicksell_group import (
        ENABLE_TITLE,
        SELL_PETS_TITLE,
        SELL_SKELETON_KEYS_TITLE,
        SELL_SOCKET_WRENCHES_TITLE,
    )
    from bartlebot.gui.settings.realm_delay_rows import FAVORITE_REALM_TITLE, SAFETY_DELAY_TITLE

    assert SETTING_ROWS == {
        "Safety Delay": SAFETY_DELAY_TITLE,
        "Level Scaling Players": None,
        "Refill Potions": REFILL_POTIONS_TITLE,
        "Quicksell": ENABLE_TITLE,
        "Sell Pets": SELL_PETS_TITLE,
        "Sell Socket Wrenches": SELL_SOCKET_WRENCHES_TITLE,
        "Sell Skeleton Keys": SELL_SKELETON_KEYS_TITLE,
        "Friend Name": FRIEND_NAME_TITLE,
        "Favorite Realm": FAVORITE_REALM_TITLE,
    }
    assert SETTING_ROWS.keys() == APP_SETTING_KEYS.keys()


def test_an_options_default_is_its_app_settings_own_when_the_node_leaves_it_to_the_user(pages):
    text = pages[OPTIONS_PAGE]

    assert "- **Default:** Off" in _section(text, "quicksell")
    assert "- **Default:** On" in _section(text, "sell-pets")
    assert "- **Default:** 3 seconds" in _section(text, "safety-delay")


# ---------------------------------------------------------------------------
# Subgraphs
# ---------------------------------------------------------------------------


def _subgraph(sg_id: str, name: str) -> SubgraphDefinition:
    return SubgraphDefinition(
        id=sg_id,
        name=name,
        description=f"What {name} does.",
        states=[
            StateModel(id="entry_start", node_type=SUBGRAPH_ENTRY, label="Start"),
            StateModel(id="s", node_type="relog"),
        ],
        transitions=[TransitionModel(trigger="advance", source="entry_start", dest="s")],
    )


def test_a_users_own_subgraph_never_appears_on_the_subgraphs_page(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    shipped, user = resolve_roots(None, None)
    assert user != shipped
    save_subgraph(_subgraph("mine-1", "My own subgraph"), user_root=user, record=False)
    assert "My own subgraph" in [d.name for d in list_subgraphs()]

    text = render_subgraphs_page()

    assert "My own subgraph" not in text
    assert "## Sync players { #sync-players }" in text


def test_the_subgraphs_page_lists_each_official_subgraph_with_its_ports_and_nodes(tmp_path):
    save_subgraph(_subgraph("sg-b", "Beta"), root=tmp_path, record=False)
    save_subgraph(_subgraph("sg-a", "Alpha"), root=tmp_path, record=False)

    text = render_subgraphs_page(tmp_path)

    assert text.index("## Alpha { #alpha }") < text.index("## Beta { #beta }")
    alpha = _section(text, "alpha")
    assert "What Alpha does." in alpha
    assert "This subgraph has no inputs." in alpha
    assert "- **Entries:** Start\n- **Exits:** none" in alpha
    assert "**Nodes it runs:** [Relog](utility.md#relog)" in alpha


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "markdown"),
    [
        ("Use * and ? to match *Bandit*.", r"Use \* and ? to match \*Bandit\*."),
        ("WizardCity/WC_Ravenwood", r"WizardCity/WC\_Ravenwood"),
        ("a | b", r"a \| b"),
        ("<b> & [x](y) {#id} `code`", r"&lt;b&gt; &amp; \[x\](y) \{\#id\} \`code\`"),
        ("- not a list\n1. nor this", "\\- not a list\n1\\. nor this"),
    ],
)
def test_spec_text_is_escaped_so_it_renders_as_itself(text, markdown):
    assert escape(text) == markdown


@pytest.mark.parametrize(
    ("field", "shown"),
    [
        (ParamField(key="k", type="str", label="K", default="x", default_hint="every player"), "every player"),
        (ParamField(key="k", type="zone", label="K", required=True), "required"),
        (ParamField(key="k", type="bool", label="K", default=True), "on"),
        (ParamField(key="k", type="ratio", label="K", default=0.5), "50%"),
        (ParamField(key="k", type="float", label="K (s)", default=600.0), "600 seconds"),
        (ParamField(key="k", type="player_slot", label="K", default=1), "Player 1"),
        (ParamField(key="k", type="player_slot", label="K", default=0), "none"),
        (ParamField(key="k", type="str_list", label="K", default=[]), "none"),
    ],
)
def test_a_params_default_is_its_hint_or_what_it_starts_as(field, shown):
    assert param_default(field) == shown


def test_the_index_lists_every_page_of_the_section(pages):
    index = pages[INDEX_PAGE]

    for name in page_names()[1:]:
        assert f"]({name})" in index
