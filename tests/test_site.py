"""The download site (site/): what the release and the README rely on it for.

The build itself is .github/workflows/site.yml's, with --strict. These cover what a
strict build does not: images, the download button, and the install page every release's
notes link.
"""

import re
import sys
import tomllib
from urllib.parse import urlparse

from bartlebot.gui.logo import LOGO_SVG
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


def _page(name: str) -> str:
    return (PAGES / name).read_text(encoding="utf-8")


def test_the_install_url_the_release_notes_link_is_the_sites_install_page():
    nav = [page for entry in CONFIG["nav"] for page in entry.values()]

    assert INSTALL_PAGE.is_file()
    assert INSTALL_PAGE.relative_to(PAGES).as_posix() in nav
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
    nav = {page.removesuffix(".md") + "/" for entry in CONFIG["nav"] for page in entry.values()}

    assert 'page.url == "install/"' in main
    assert 'href="{{ config.extra.download_url }}"' in main
    assert "install/" in nav


def test_the_landing_page_is_drawn_by_the_home_template():
    """A template a page names but the theme cannot find is no error to a strict build."""
    assert re.search(r"^template: (.+)$", _page("index.md"), re.M)[1] == HOME.name


def test_the_home_templates_links_are_pages_of_the_site():
    """A strict build checks the links in markdown, never those in a template."""
    nav = {page.removesuffix(".md") + "/" for entry in CONFIG["nav"] for page in entry.values()}
    linked = re.findall(r"\{\{ '([^']+)' \| url \}\}", HOME.read_text(encoding="utf-8"))

    assert linked
    assert [link for link in linked if link not in nav] == []


def test_the_install_page_names_the_file_the_button_downloads():
    assert f"**{SETUP_NAME}**" in INSTALL_PAGE.read_text(encoding="utf-8")


def test_every_image_a_page_shows_is_in_the_site():
    """A strict build passes a missing image, which then shows as a broken one."""
    shown = [
        (page.name, target)
        for page in PAGES.rglob("*.md")
        for target in _LOCAL_IMAGE.findall(page.read_text(encoding="utf-8"))
    ]

    assert shown
    assert [(name, target) for name, target in shown if not (PAGES / target).is_file()] == []


def test_the_sites_logo_is_the_apps():
    """The site can only show a file under its own pages, so the logo is a copy."""
    site_logo = PAGES / CONFIG["theme"]["logo"]

    assert site_logo.read_bytes() == LOGO_SVG.read_bytes()
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
