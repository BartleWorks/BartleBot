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
