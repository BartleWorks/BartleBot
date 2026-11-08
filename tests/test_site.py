"""The site (site/): the download page and the manual, and what the release and the README rely on it for.

The build itself is .github/workflows/site.yml's, with --strict, which fails on a link to
a page or an anchor that is not there. These cover what a strict build does not: images,
pages left out of the nav, links to the developer docs, the edit link, the download
button, and the install page every release's notes link. site/CLAUDE.md has the rules.
"""

import inspect
import os
import re
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse

import pytest
from bartleby.qt.app.logging_setup import prune_session_logs
from bartleby.qt.logo import logo_svg

from bartlebot.packaging.product import LOGO_BACKGROUND, product
from bartlebot.paths import project_root
from bartlebot.updates.feed import FEED_URL, REPOSITORY

sys.path.insert(0, str(project_root()))

from tools.pack_release import INSTALL_URL, SETUP_NAME  # noqa: E402

SITE = project_root() / "site"
CONFIG = tomllib.loads((SITE / "zensical.toml").read_text(encoding="utf-8"))["project"]
PAGES = SITE / CONFIG["docs_dir"]
#: The install page, which every release's notes link (``INSTALL_URL``).
INSTALL_PAGE = PAGES / "install.md"
#: The landing page's template, which draws its hero and its download button.
HOME = SITE / CONFIG["theme"]["custom_dir"] / "home.html"
WORKFLOW = project_root() / ".github" / "workflows" / "site.yml"
#: A markdown image whose target is a file beside the page, not a URL.
_LOCAL_IMAGE = re.compile(r"!\[[^\]]*\]\((?!https?://)([^)\s]+)\)")
#: A markdown link's target, an image's among them.
_LINK = re.compile(r"\]\(([^)\s]+)\)")
#: A link into the repo's developer docs, by a relative path or on GitHub.
_DEVELOPER_DOC = re.compile(r"(?:^|/)docs/")
#: A link to a source file on GitHub rather than to a page of the site.
_SOURCE_FILE = re.compile(r"github\.com/[^/]+/[^/]+/(?:blob|tree|raw)/")
#: The top of the nav, in order: the four kinds of page after the two the site began with.
NAV_SECTIONS = ["Home", "Installing", "Getting started", "Guides", "Reference", "Troubleshooting"]


def _page(name: str) -> str:
    return (PAGES / name).read_text(encoding="utf-8")


def _nav_entries(nav: list) -> Iterator[str]:
    """Every page the nav names, however deep, as a path under the pages folder."""
    for entry in nav:
        if isinstance(entry, str):
            yield entry
            continue
        for value in entry.values():
            if isinstance(value, str):
                yield value
            else:
                yield from _nav_entries(value)


def _nav_urls() -> set[str]:
    """Each nav page's URL under the site, as a template names it: ``install/``, ``guides/``."""
    urls = set()
    for page in _nav_entries(CONFIG["nav"]):
        stem = page.removesuffix(".md")
        urls.add("" if stem == "index" else stem.removesuffix("index").removesuffix("/") + "/")
    return urls


def _markdown_pages(pages: Path) -> list[Path]:
    return sorted(pages.rglob("*.md"))


def pages_outside_the_nav(pages: Path, nav: list) -> list[str]:
    """The markdown pages under *pages* that *nav* leaves out. A strict build only reports them."""
    listed = set(_nav_entries(nav))
    return [rel for page in _markdown_pages(pages) if (rel := page.relative_to(pages).as_posix()) not in listed]


def nav_entries_without_a_page(pages: Path, nav: list) -> list[str]:
    return [entry for entry in _nav_entries(nav) if not (pages / entry).is_file()]


def links_to_developer_docs(pages: Path) -> list[tuple[str, str]]:
    """Each (page, target) linking into ``docs/`` or to a source file: a user reads the site, never the code."""
    return [
        (page.relative_to(pages).as_posix(), target)
        for page in _markdown_pages(pages)
        for target in _LINK.findall(page.read_text(encoding="utf-8"))
        if _SOURCE_FILE.search(target)
        or (not target.startswith(("http://", "https://")) and _DEVELOPER_DOC.search(target))
    ]


def _shown_images(pages: Path) -> Iterator[tuple[Path, str, Path]]:
    """Each (page, target, file) a page shows, the file resolved from the page's own folder."""
    for page in _markdown_pages(pages):
        for target in _LOCAL_IMAGE.findall(page.read_text(encoding="utf-8")):
            yield page, target, (page.parent / target).resolve()


def unused_images(pages: Path) -> list[str]:
    """The images under ``images/`` that no page shows, which go stale unseen."""
    shown = {file for _page, _target, file in _shown_images(pages)}
    return [
        image.relative_to(pages).as_posix()
        for image in sorted((pages / "images").rglob("*"))
        if image.is_file() and image.resolve() not in shown
    ]


def test_the_install_url_the_release_notes_link_is_the_sites_install_page():
    assert INSTALL_PAGE.is_file()
    assert INSTALL_PAGE.relative_to(PAGES).as_posix() in set(_nav_entries(CONFIG["nav"]))
    assert INSTALL_URL == f"{CONFIG['site_url']}{INSTALL_PAGE.stem}/"


def test_the_download_button_fetches_setup_from_the_latest_published_release():
    """The same redirect the updater reads its feed through: never a draft, never a pre-release."""
    assert CONFIG["extra"]["download_url"] == f"{FEED_URL}{SETUP_NAME}"
    assert 'class="md-button md-button--primary" href="{{ config.extra.download_url }}"' in HOME.read_text(
        encoding="utf-8"
    )


def test_the_install_page_has_the_download_button_from_the_template_not_the_markdown():
    """overrides/main.html draws the button above the install page, so the markdown never carries one."""
    main = (SITE / CONFIG["theme"]["custom_dir"] / "main.html").read_text(encoding="utf-8")

    assert 'page.url == "install/"' in main
    assert 'href="{{ config.extra.download_url }}"' in main
    assert "install/" in _nav_urls()


def test_the_landing_page_is_drawn_by_the_home_template():
    """A template a page names but the theme cannot find is no error to a strict build."""
    assert re.search(r"^template: (.+)$", _page("index.md"), re.M)[1] == HOME.name


def test_the_home_templates_links_are_pages_of_the_site():
    """A strict build checks the links in markdown, never those in a template."""
    linked = re.findall(r"\{\{ '([^']+)' \| url \}\}", HOME.read_text(encoding="utf-8"))

    assert linked
    assert [link for link in linked if link not in _nav_urls()] == []


def test_the_install_page_names_the_file_the_button_downloads():
    assert f"**{SETUP_NAME}**" in INSTALL_PAGE.read_text(encoding="utf-8")


def test_every_image_a_page_shows_is_in_the_site():
    """A strict build passes a missing image, which then shows as a broken one."""
    shown = list(_shown_images(PAGES))

    assert shown
    assert [(page.name, target) for page, target, file in shown if not file.is_file()] == []


def test_the_sites_logo_is_the_apps():
    """The site can only show a file under its own pages, so the logo is core's tree on our color, written out."""
    site_logo = PAGES / CONFIG["theme"]["logo"]

    assert site_logo.read_text(encoding="utf-8") == logo_svg(LOGO_BACKGROUND)
    assert CONFIG["theme"]["favicon"] == CONFIG["theme"]["logo"]


def test_the_site_is_at_the_orgs_pages_address_and_names_the_repository():
    repository = urlparse(REPOSITORY)
    org, name = repository.path.strip("/").split("/")

    assert CONFIG["site_url"] == f"https://{org.lower()}.github.io/{name}/"
    assert CONFIG["repo_url"] == REPOSITORY


def test_the_site_loads_no_web_fonts():
    """A visit sends nothing to anyone but GitHub."""
    assert CONFIG["theme"]["font"] is False


def test_the_readme_points_to_the_site():
    assert CONFIG["site_url"] in (project_root() / "README.md").read_text(encoding="utf-8")


def test_the_workflow_builds_strictly_and_publishes_only_masters_site_from_a_public_repo():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "zensical build --strict" in workflow
    (deploy_if,) = re.findall(r"^    if: (.*)$", workflow, re.M)
    assert "github.event_name != 'pull_request'" in deploy_if
    assert "github.ref == 'refs/heads/master'" in deploy_if
    assert "!github.event.repository.private" in deploy_if


def test_the_nav_is_home_installing_then_the_four_kinds_of_page():
    """Getting started teaches, Guides walk through a task, Reference states facts, Troubleshooting starts from a symptom."""
    assert [next(iter(entry)) for entry in CONFIG["nav"]] == NAV_SECTIONS
    sections = {name: value for entry in CONFIG["nav"] for name, value in entry.items()}
    # A section opens on its index page, which lists what it holds (navigation.indexes).
    assert sections["Guides"][0] == "guides/index.md"
    assert sections["Reference"][0] == "reference/index.md"
    assert {"navigation.indexes", "navigation.sections"} <= set(CONFIG["theme"]["features"])


def test_every_page_is_in_the_nav_and_every_nav_entry_is_a_page():
    assert pages_outside_the_nav(PAGES, CONFIG["nav"]) == []
    assert nav_entries_without_a_page(PAGES, CONFIG["nav"]) == []


def test_a_page_left_out_of_the_nav_and_a_nav_entry_with_no_page_are_both_found(tmp_path):
    (tmp_path / "guides").mkdir()
    for page in ("index.md", "guides/index.md", "guides/forgotten.md"):
        (tmp_path / page).write_text("# A page\n", encoding="utf-8")
    nav = [{"Home": "index.md"}, {"Guides": ["guides/index.md", {"Running bots": ["guides/running/gone.md"]}]}]

    assert pages_outside_the_nav(tmp_path, nav) == ["guides/forgotten.md"]
    assert nav_entries_without_a_page(tmp_path, nav) == ["guides/running/gone.md"]


def test_no_page_links_to_a_developer_doc_or_a_source_file():
    assert links_to_developer_docs(PAGES) == []


def test_a_link_to_a_developer_doc_or_a_source_file_is_found(tmp_path):
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides" / "options.md").write_text(
        "See [the levels](../../../docs/BOT_OPTIONS.md), [the code](https://github.com/BartleWorks/BartleBot/blob/master/"
        "bartlebot/options/bot_options.py), [Installing](../install.md), [an issue](https://github.com/BartleWorks/"
        "BartleBot/issues/new) and [the docs on the web](https://zensical.org/docs/setup/).\n",
        encoding="utf-8",
    )

    assert links_to_developer_docs(tmp_path) == [
        ("guides/options.md", "../../../docs/BOT_OPTIONS.md"),
        ("guides/options.md", "https://github.com/BartleWorks/BartleBot/blob/master/bartlebot/options/bot_options.py"),
    ]


def test_every_image_under_images_is_shown_by_some_page():
    assert unused_images(PAGES) == []


def test_an_image_no_page_shows_is_found_and_one_shown_from_a_folder_below_is_not(tmp_path):
    images = tmp_path / "images" / "running"
    images.mkdir(parents=True)
    for name in ("log.webp", "stale.webp"):
        (images / name).write_bytes(b"")
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides" / "running.md").write_text("![The Log tab](../images/running/log.webp)\n", encoding="utf-8")

    assert unused_images(tmp_path) == ["images/running/stale.webp"]


def test_every_hand_written_page_links_to_its_source_on_master():
    """Zensical's own default names site/'s folder as if it were the repo's root, so the path is set."""
    assert CONFIG["edit_uri"] == f"edit/master/{PAGES.relative_to(project_root()).as_posix()}/"
    assert urlparse(CONFIG["repo_url"]).hostname == "github.com"
    assert "content.action.edit" in CONFIG["theme"]["features"]


def test_a_generated_page_shows_no_edit_link():
    """An edit to a page a script writes is lost at the next render, so main.html draws the page without the actions."""
    main = (SITE / CONFIG["theme"]["custom_dir"] / "main.html").read_text(encoding="utf-8")
    generated = re.search(r"\{% elif page\.meta\.generated %\}(.*?)\{% else %\}", main, re.S)

    assert generated
    assert "{{ page.content }}" in generated[1]
    assert "actions.html" not in generated[1]


def test_no_hand_written_page_is_marked_generated():
    """Marking one would hide its edit link; only a script's output carries the mark."""
    marked = [page.relative_to(PAGES).as_posix() for page in _markdown_pages(PAGES) if _front_matter_generated(page)]

    assert [page for page in marked if not page.startswith("reference/")] == []


def _front_matter_generated(page: Path) -> bool:
    match = re.match(r"---\n(.*?)\n---\n", page.read_text(encoding="utf-8"), re.S)
    return bool(match) and re.search(r"^generated: true$", match[1], re.M) is not None


def test_troubleshooting_names_the_folder_the_logs_are_written_to(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("BARTLEBOT_DEV", raising=False)
    running = product()
    folder = "%APPDATA%\\" + str(running.log_dir.relative_to(tmp_path)).replace(os.sep, "\\")
    keep = inspect.signature(prune_session_logs).parameters["keep"].default
    page = _page("troubleshooting.md")

    assert folder == r"%APPDATA%\Bartleby\BartleBot\logs"
    assert folder in page
    assert f"`{running.slug}_" in page
    assert f"keeps the {keep} most recent" in page


def test_the_landing_page_and_getting_started_send_a_reader_to_the_logs():
    for name in ("index.md", "getting-started.md"):
        assert "troubleshooting.md" in _page(name), name
    assert "](troubleshooting.md#where-the-logs-are)" in _page("index.md")


def test_getting_started_walks_from_installing_to_the_run_summary():
    page = _page("getting-started.md")
    sections = re.split(r"^## ", page, flags=re.M)[1:]

    assert "](install.md)" in sections[0]
    assert sections[-1].startswith("Read the run summary")


@pytest.fixture
def setup_headings(qapp) -> list[str]:
    """Every question setup asks, as its page shows it."""
    from bartleby.qt.dialogs.first_run_dialog import MODE_HEADING  # noqa: PLC0415 - Qt, kept to the tests that need it

    from bartlebot.gui.dialogs.first_run_dialog import SETUP_PAGE_TYPES  # noqa: PLC0415

    return [MODE_HEADING, *(page.heading for page in SETUP_PAGE_TYPES)]


def test_getting_started_quotes_every_setup_page_as_the_app_shows_it(setup_headings):
    """A page renamed in the app, or a page added, fails here until Getting started says the same."""
    from bartleby.qt.dialogs.first_run_dialog import MODE_CARDS, TACTIC_CARDS  # noqa: PLC0415

    page = _page("getting-started.md")
    choices = [name for name, _text in (*MODE_CARDS.values(), *TACTIC_CARDS.values())]

    assert [heading for heading in setup_headings if f"**{heading}**" not in page] == []
    assert [choice for choice in choices if f"**{choice}**" not in page] == []


def test_the_rule_files_say_a_change_a_user_can_see_updates_its_page_in_the_same_branch():
    root = (project_root() / "CLAUDE.md").read_text(encoding="utf-8")
    tests = (project_root() / "tests" / "CLAUDE.md").read_text(encoding="utf-8")

    assert "### A change a user can see updates the site in the same branch" in root
    assert "**A change a user can see ships with its site page.**" in tests
    assert (SITE / "CLAUDE.md").is_file()


#: The running-bots guides, in the order the nav lists them (Stage 3 of epic #25).
RUNNING_GUIDES = [
    "guides/running/library.md",
    "guides/running/running-a-bot.md",
    "guides/running/options-and-settings.md",
    "guides/running/quicksell.md",
    "guides/running/accounts-clients-and-realms.md",
    "guides/running/when-something-goes-wrong.md",
    "guides/running/safety.md",
    "guides/running/pvp.md",
]


def _source(relative: str) -> str:
    """A module's text with each string the formatter split across lines joined up again."""
    text = (project_root() / relative).read_text(encoding="utf-8")
    return re.sub(r'"\s*\n\s*f?"', "", text)


def test_the_running_bots_guides_are_a_section_under_guides_each_listed_on_its_index():
    guides = next(entry["Guides"] for entry in CONFIG["nav"] if isinstance(entry, dict) and "Guides" in entry)
    index = _page("guides/index.md")

    assert guides[0] == "guides/index.md"
    assert {"Running bots": RUNNING_GUIDES} in guides
    assert [page for page in RUNNING_GUIDES if f"]({page.removeprefix('guides/')})" not in index] == []
    assert "!!! info" not in index


def test_the_landing_page_and_getting_started_link_the_safety_page():
    assert "](guides/running/safety.md)" in _page("index.md")
    assert "](guides/running/safety.md)" in _page("getting-started.md")


def test_troubleshootings_last_two_sections_stay_where_the_logs_are_then_opening_an_issue():
    headings = re.findall(r"^## (.+?)(?: \{ #[a-z0-9-]+ \})?$", _page("troubleshooting.md"), re.M)

    assert headings[-2:] == ["Where the logs are", "Opening an issue"]


def _quoted_messages() -> list[tuple[str, str, str]]:
    """(page, the words it quotes, where the app says them): each quote a guide or Troubleshooting makes."""
    from bartlebot.clients import recovery_readiness, run_probe  # noqa: PLC0415
    from bartlebot.gui.dialogs import safety_warning_dialog as safety  # noqa: PLC0415
    from bartlebot.nodes.registry import NODE_SPECS  # noqa: PLC0415
    from bartlebot.nodes.utility import quicksell  # noqa: PLC0415
    from bartlebot.options import player_realms  # noqa: PLC0415

    controller = _source("bartlebot/app_controller.py")
    machine = _source("bartlebot/bot/machine.py")
    manager = _source("bartlebot/clients/client_manager.py")
    trouble, guide = "troubleshooting.md", "guides/running/"
    return [
        (trouble, "This bot cannot be run. It sends a client to a zone BartleBot does not automate", controller),
        (trouble, "The bot was stopped: a client entered", controller),
        (trouble, "This bot cannot be Separate", player_realms.blocked_reason(["Teleport to Friend"])),
        (trouble, "Player 1 is in a fight", run_probe.refusal_text([1])),
        (trouble, "clients cannot be recovered automatically if they crash", recovery_readiness.warning_text(["x"], 2)),
        (trouble, recovery_readiness.NO_ACCOUNT, recovery_readiness.NO_ACCOUNT),
        (trouble, "no password saved", recovery_readiness.NO_PASSWORD),
        (trouble, "No stored password for account", manager),
        (trouble, "waiting at character select", manager),
        (trouble, "The bot stopped at", controller),
        (trouble, "which returned", controller),
        (trouble, "cannot be closed and relaunched automatically", machine),
        (trouble, "The bot went back to its recovery state", machine),
        (trouble, "times in a row for the same reason", machine),
        (trouble, "The players fled", machine),
        (trouble, "fights within", machine),
        (trouble, "consecutive ticks without advancing", machine),
        (trouble, "Reached terminal state", machine),
        (trouble, "FAILURE: quicksell open button did not appear", _source("bartlebot/nodes/utility/quicksell.py")),
        (
            guide + "safety.md",
            "This bot sells drops when your backpack is full.",
            NODE_SPECS["quicksell"].safety_warning,
        ),
        (guide + "safety.md", "This bot's author has it sell", safety.declared_sales_message(["Sell Pets"])),
        (guide + "safety.md", "With quicksell off, nothing is sold.", quicksell.SAFETY_WARNING_DISABLED),
        (guide + "safety.md", "This bot teleports to collect", NODE_SPECS["collect_reagent"].safety_warning),
        (guide + "safety.md", "This bot teleports to collect", NODE_SPECS["collect_item"].safety_warning),
        (
            guide + "safety.md",
            "This bot teleports your players to another wizard",
            NODE_SPECS["teleport_to_friend"].safety_warning,
        ),
        (guide + "safety.md", "Team-Up is on with more than one player.", NODE_SPECS["enter_dungeon"].safety_warning),
        (
            guide + "safety.md",
            "A level scaling player already has an elixir active.",
            safety.level_scaling_warning([1]).message,
        ),
        (
            guide + "safety.md",
            "The game's Dblclick Go To Friend option is on",
            safety.quick_friend_teleport_warning([1]).message,
        ),
        (guide + "safety.md", "Gameplay Options 4", safety.QUICK_FRIEND_TELEPORT_SETTING),
        (guide + "quicksell.md", safety.QUICKSELL_OPT_OUT_LABEL, safety.QUICKSELL_OPT_OUT_LABEL),
    ]


def test_the_guides_and_troubleshooting_quote_each_message_as_the_app_shows_it():
    """A message reworded in the app fails here until the page that quotes it says the same."""
    quoted = _quoted_messages()

    assert [(page, words) for page, words, _app in quoted if words not in _page(page)] == []
    assert [(page, words) for page, words, app in quoted if words not in app] == []


def test_options_and_settings_names_each_level_as_an_option_row_does():
    from bartlebot.options import bot_options  # noqa: PLC0415

    page = _page("guides/running/options-and-settings.md")
    levels = [
        bot_options.SOURCE_OVERRIDE,
        bot_options.SOURCE_BOT_FILE,
        bot_options.SOURCE_APP_SETTING,
        bot_options.SOURCE_NODE_DEFAULT,
    ]

    assert [level for level in levels if f"| **{level}** |" not in page] == []


def code_names_on(pages: Path, names: set[str]) -> list[tuple[str, str]]:
    """(page, name) for each node type or setting key a hand-written page names, as a whole word."""
    found = []
    for page in _markdown_pages(pages):
        if _front_matter_generated(page):
            continue
        text = page.read_text(encoding="utf-8")
        found += [(page.relative_to(pages).as_posix(), n) for n in sorted(names) if re.search(rf"\b{n}\b", text)]
    return found


def _code_names() -> set[str]:
    """Node types and app setting keys with an underscore: words no page has a reason to use."""
    from bartlebot.nodes.registry import NODE_SPECS  # noqa: PLC0415
    from bartlebot.settings_manager import _DEFAULTS  # noqa: PLC0415

    keys = {key for section in _DEFAULTS.values() if isinstance(section, dict) for key in section}
    return {name for name in (*NODE_SPECS, *keys) if "_" in name}


def test_no_hand_written_page_names_a_node_type_or_a_setting_key():
    assert code_names_on(PAGES, _code_names()) == []


def test_a_page_naming_a_node_type_or_a_setting_key_is_found(tmp_path):
    (tmp_path / "guide.md").write_text("Set default_safety_delay, then add a teleport_to_friend.\n", encoding="utf-8")
    (tmp_path / "fine.md").write_text("Set **Default Safety Delay**, then add Teleport to Friend.\n", encoding="utf-8")

    assert code_names_on(tmp_path, {"default_safety_delay", "teleport_to_friend"}) == [
        ("guide.md", "default_safety_delay"),
        ("guide.md", "teleport_to_friend"),
    ]


#: The making-bots guides, in the order the nav lists them (Stage 4 of epic #25). Read in
#: this order, they build a first bot from a blank one.
MAKING_GUIDES = [
    "guides/making/new-bot-and-templates.md",
    "guides/making/the-canvas.md",
    "guides/making/edges-and-failure.md",
    "guides/making/variables-and-conditions.md",
    "guides/making/subgraphs.md",
    "guides/making/sharing.md",
]


def _section(page: str, heading: str) -> str:
    """The text under one heading of *page*, up to the next heading of any level."""
    text = _page(page)
    start = re.search(rf"^#+ {re.escape(heading)}\b.*$", text, re.M)
    assert start is not None, f"{page} has no heading {heading!r}"
    rest = text[start.end() :]
    end = re.search(r"^#+ ", rest, re.M)
    return rest[: end.start()] if end else rest


def test_the_making_bots_guides_are_a_section_after_running_bots_listed_under_a_heading_of_their_own():
    guides = next(entry["Guides"] for entry in CONFIG["nav"] if isinstance(entry, dict) and "Guides" in entry)
    making = _page("guides/index.md").split("\n## Making bots\n", 1)

    assert guides[1:] == [{"Running bots": RUNNING_GUIDES}, {"Making bots": MAKING_GUIDES}]
    assert len(making) == 2
    assert [page for page in MAKING_GUIDES if f"]({page.removeprefix('guides/')})" not in making[1]] == []


def test_the_first_bot_starts_where_new_bot_leaves_off_and_ends_in_a_run():
    """The walk Done when asks for: a blank bot, two nodes in a loop, then the failure edge and Run."""
    new_bot = _page("guides/making/new-bot-and-templates.md")
    build = _section("guides/making/the-canvas.md", "Build a first bot")
    finish = _section("guides/making/edges-and-failure.md", "Finish the first bot")

    assert "](the-canvas.md#build-a-first-bot)" in new_bot
    assert "](new-bot-and-templates.md#new-bot)" in build
    assert "](edges-and-failure.md#finish-the-first-bot)" in build
    assert "**FAILURE** port to [Sleep](" in finish
    assert "**Run bot**" in finish


@pytest.fixture
def blank_canvas(qapp):
    """A new blank bot open in Edit mode, as New Bot leaves it."""
    from bartlebot.content.new_bot import BotDetails, create_bot  # noqa: PLC0415
    from bartlebot.content.subgraphs.subgraph_store import SubgraphLibrary  # noqa: PLC0415
    from bartlebot.gui.canvas.bot_canvas import BotCanvas, BotMode  # noqa: PLC0415

    canvas = BotCanvas()
    canvas.set_subgraph_library(SubgraphLibrary())
    canvas.setup(create_bot(None, BotDetails(name="Wisps")), mode=BotMode.EDIT)
    yield canvas
    canvas.prepare_for_shutdown()


def test_the_first_bot_has_the_errors_and_warnings_the_guides_say_at_each_step(blank_canvas):
    """Built as the pages tell the reader to, the bot reports what they promise, and nothing once finished."""
    from PyQt6.QtCore import QPointF  # noqa: PLC0415

    from bartlebot.bot.schema import TransitionModel  # noqa: PLC0415
    from bartlebot.gui.canvas.bot_canvas import NO_NODES_ERROR  # noqa: PLC0415

    canvas = blank_canvas
    assert canvas.validation_errors() == [NO_NODES_ERROR]
    assert NO_NODES_ERROR in _page("guides/making/new-bot-and-templates.md")

    wisps = canvas.scene.create_state_at("collect_wisps", QPointF(0, 0))
    sleep = canvas.scene.create_state_at("sleep", QPointF(440, 0))
    config = canvas.config
    assert (config.initial, config.recovery_state) == (wisps, wisps)
    assert canvas.validation_warnings() == ["Sleep: Unreachable from initial state"]

    next(s for s in config.states if s.id == sleep).params["duration"] = 30
    for source, dest in ((wisps, sleep), (sleep, wisps)):
        config.transitions.append(TransitionModel(trigger="advance", source=source, dest=dest, when="SUCCESS"))
    warning = "Collect Wisps: Uncovered return value: 'FAILURE'"
    assert (canvas.validation_errors(), canvas.validation_warnings()) == ([], [warning])
    assert f'"{warning.split(": ", 1)[1]}"' in _section("guides/making/the-canvas.md", "Build a first bot")

    config.transitions.append(TransitionModel(trigger="advance", source=wisps, dest=sleep, when="FAILURE"))
    assert (canvas.validation_errors(), canvas.validation_warnings()) == ([], [])


def test_new_bot_and_templates_lists_every_official_template_by_its_cards_first_line(tmp_path):
    from bartlebot.content.new_bot import starting_points  # noqa: PLC0415

    official = starting_points(project_root(), tmp_path)
    table = _section("guides/making/new-bot-and-templates.md", "The Official templates")
    rows = dict(re.findall(r"^\| \*\*(.+?)\*\* \| (.+?) \|$", table, re.M))

    assert official, "no Official template was read"
    assert sorted(rows) == sorted(point.name for point in official)
    assert [p.name for p in official if not p.summary.startswith(rows[p.name])] == []


def test_the_canvas_lists_every_node_record_fills_and_no_other():
    from bartlebot.gui.canvas.recording import RECORDABLE_NODES  # noqa: PLC0415
    from bartlebot.nodes.registry import node_display_name  # noqa: PLC0415

    table = _section("guides/making/the-canvas.md", "Record from the game")

    assert sorted(re.findall(r"^\| \[(.+?)\]\(", table, re.M)) == sorted(map(node_display_name, RECORDABLE_NODES))


def test_the_canvas_quotes_what_record_says_it_fills():
    from bartlebot.gui.canvas.recording import recording_hint  # noqa: PLC0415
    from bartlebot.nodes.registry import NODE_PARAM_SCHEMAS  # noqa: PLC0415

    hint = recording_hint("enter_dungeon", NODE_PARAM_SCHEMAS["enter_dungeon"], {})

    assert f'"{hint}"' in _section("guides/making/the-canvas.md", "Record from the game")


def test_edges_and_failure_quotes_each_failure_edge_view_as_its_button_does():
    from bartlebot.gui.canvas.bot_canvas import _FAILURE_EDGE_TOOLTIPS  # noqa: PLC0415

    table = _section("guides/making/edges-and-failure.md", "Show or hide failure edges")

    assert [tip for tip in _FAILURE_EDGE_TOOLTIPS.values() if f'| "{tip}" |' not in table] == []


def _making_quotes() -> list[tuple[str, str, str]]:
    """(page, the words it quotes, where the app says them): each quote the making-bots guides make.

    A quote in quotation marks or bold is matched with them on the page, and without them in the app.
    """
    from bartleby.shared import share_match  # noqa: PLC0415

    from bartlebot.content import share_import  # noqa: PLC0415
    from bartlebot.content.templates import setup_steps  # noqa: PLC0415
    from bartlebot.gui.canvas import bot_canvas  # noqa: PLC0415
    from bartlebot.gui.dialogs import export_dialog, import_dialog  # noqa: PLC0415
    from bartlebot.gui.dialogs import templates_dialog as gallery  # noqa: PLC0415
    from bartlebot.gui.sidebar import setup_panel  # noqa: PLC0415

    templates, canvas, edges, sharing = (
        f"guides/making/{name}.md" for name in ("new-bot-and-templates", "the-canvas", "edges-and-failure", "sharing")
    )
    panel = _source("bartlebot/gui/canvas/param_panel.py")
    setup = _source("bartlebot/gui/sidebar/setup_panel.py")
    controller = _source("bartlebot/app_controller.py")
    exporting = _source("bartlebot/gui/dialogs/export_dialog.py")
    newer = share_match.update_message("BartleBot", "9.9.9", "BartleBot")
    review = (
        setup_panel.APPLY_TEXT,
        setup_panel.USE_TEMPLATE_TEXT,
        setup_panel.KEEP_MINE_TEXT,
        setup_panel.FINISH_REVIEW_TEXT,
        setup_panel.LATER_TEXT,
        setup_panel.ADD_STEP_TEXT,
        gallery.EDIT_TEXT,
        gallery.DUPLICATE_TEXT,
        gallery.RENAME_TEXT,
        gallery.DELETE_TEXT,
    )
    shown_once = {share_import.OUTCOME_TEXT[share_import.Outcome.COPIED]}
    return [
        (templates, '"Fill in:"', panel),
        (templates, f'"{setup_steps.ERROR_PREFIX.strip()}"', setup_steps.ERROR_PREFIX),
        (templates, f'"{bot_canvas.UPDATE_AVAILABLE_TEXT}"', bot_canvas.UPDATE_AVAILABLE_TEXT),
        (templates, '"You changed this too."', setup),
        (templates, '"Can\'t apply:"', setup),
        (templates, '"Setup step"', panel),
        *((templates, f"**{text}**", text) for text in review),
        (canvas, f"**{bot_canvas._EDIT_TIP}**", bot_canvas._EDIT_TIP),
        (canvas, '"recovery · not drawn"', panel),
        (canvas, '"Walk to sigil…"', _source("bartlebot/gui/canvas/recording.py")),
        (canvas, "**Save and finish editing**", _source("bartlebot/gui/canvas/bot_canvas.py")),
        (canvas, "**Save Without**", _source("bartlebot/gui/dialogs/rewards_tag_dialog.py")),
        (edges, '"recovery · not drawn"', panel),
        (
            edges,
            '"Uncovered return value"',
            _source("bartlebot/gui/canvas/edit_scene.py").replace("{label}", "return value"),
        ),
        (edges, '"Reached terminal state"', _source("bartlebot/bot/machine.py")),
        (edges, '"The bot stopped at"', controller),
        (edges, '"which returned FAILURE."', controller.replace("{result}", "FAILURE")),
        *((sharing, f'"{text}"', text) for text in share_import.OUTCOME_TEXT.values() if text not in shown_once),
        *((sharing, f"**{text}**", text) for text in share_import.ACTION_TEXT.values()),
        *((sharing, f'"{heading}"', heading) for heading, _kinds in export_dialog.GROUPS),
        (sharing, f'"{import_dialog.CHOICES_HEADING}"', import_dialog.CHOICES_HEADING),
        (sharing, f'"{import_dialog.REST_HEADING}"', import_dialog.REST_HEADING),
        (sharing, f"**{export_dialog.COMMIT_TEXT}**", export_dialog.COMMIT_TEXT),
        (sharing, '"Include its decks"', exporting),
        (sharing, '"Include my Bot Combat Strategy"', exporting),
        (sharing, '"Made with a newer BartleBot"', newer),
        ("troubleshooting.md", '"Made with a newer BartleBot"', newer),
    ]


def test_the_making_bots_guides_quote_each_message_as_the_app_shows_it():
    """A message reworded in the app fails here until the page that quotes it says the same."""
    quoted = _making_quotes()

    assert [(page, words) for page, words, _app in quoted if words not in _page(page)] == []
    assert [(page, words) for page, words, app in quoted if words.strip('"*') not in app] == []
