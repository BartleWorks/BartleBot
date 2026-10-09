"""Wiki page references: what a bot file is allowed to say about where drops came from.

A bot records a page *title*, never a URL, and the host comes from this module's
constant. That is what makes a reference out of a file someone else wrote unable to
name a server of its own. These tests pin that property from both directions —
what resolves, and what must not.
"""

from __future__ import annotations

import pytest

from bartlebot.game.wiki_pages import (
    WIKI_HOST,
    is_valid_title,
    page_title,
    page_url,
    resolve_wiki_page,
    wiki_page_path,
)

# ---------------------------------------------------------------------------
# wiki_page_path — exact host, not a substring
#
# The substring test this replaced accepted every lookalike below, and the URL
# went straight to a curl_cffi fetch impersonating Chrome.
# ---------------------------------------------------------------------------

_LOOKALIKES = [
    "https://wiki.wizard101central.com.evil.co/wiki/Creature:X",
    "https://evil.co/wiki/?ref=wiki.wizard101central.com",
    "https://evil.co/wiki.wizard101central.com/wiki/X",
    "https://wiki.wizard101central.com@evil.co/wiki/X",
    "https://WIKI.WIZARD101CENTRAL.COM.evil.co/wiki/X",
]


@pytest.mark.parametrize("url", _LOOKALIKES)
def test_a_lookalike_host_is_rejected(url):
    assert wiki_page_path(url) is None


def test_the_real_wiki_is_accepted():
    assert wiki_page_path(f"https://{WIKI_HOST}/wiki/Creature:Jade Oni") == "/wiki/Creature:Jade Oni"


def test_the_host_comparison_is_case_insensitive():
    assert wiki_page_path("https://WIKI.Wizard101Central.COM/wiki/Item:X") == "/wiki/Item:X"


def test_http_is_rejected():
    assert wiki_page_path(f"http://{WIKI_HOST}/wiki/Item:X") is None


def test_a_url_off_the_wiki_path_is_rejected():
    assert wiki_page_path(f"https://{WIKI_HOST}/not-wiki/X") is None


def test_userinfo_is_rejected_even_on_the_real_host():
    # Resolves to the real wiki, but a URL that reads as one host and resolves to
    # another has no legitimate use here.
    assert wiki_page_path(f"https://evil.co@{WIKI_HOST}/wiki/X") is None


def test_a_relative_href_is_same_origin_by_construction():
    assert wiki_page_path("/wiki/Item:Amber") == "/wiki/Item:Amber"


def test_a_relative_href_off_the_wiki_path_is_rejected():
    assert wiki_page_path("/images/foo.png") is None


def test_blank_input_is_rejected():
    assert wiki_page_path("") is None
    assert wiki_page_path("   ") is None


# ---------------------------------------------------------------------------
# is_valid_title — a title must not escape the /wiki/ path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    ["Creature:Lost Soul", "Item:Amber", "Creature:Lord Nightshade", "NPC:O'Leary", "Item:Boots (Level 90+)"],
)
def test_real_titles_are_valid(title):
    assert is_valid_title(title)


@pytest.mark.parametrize(
    "title",
    [
        "",
        "   ",
        "../../etc/passwd",
        "Creature:X/../../evil",
        "Creature:X/sub",
        "Creature:X?action=raw",
        "Creature:X#frag",
        r"Creature:X\evil",
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "data:text/html,x",
        "file:///etc/passwd",
        "http:evil",
        "Creature:\x00X",
    ],
)
def test_unusable_titles_are_refused(title):
    assert not is_valid_title(title)


# ---------------------------------------------------------------------------
# page_title / page_url
# ---------------------------------------------------------------------------


def test_a_title_passes_through():
    assert page_title("Creature:Lost Soul") == "Creature:Lost Soul"


def test_a_pasted_url_becomes_a_title():
    url = f"https://{WIKI_HOST}/wiki/Creature:Lost_Soul"
    assert page_title(url) == "Creature:Lost Soul"


def test_a_percent_encoded_url_becomes_a_title():
    url = f"https://{WIKI_HOST}/wiki/Creature:Lost%20Soul"
    assert page_title(url) == "Creature:Lost Soul"


def test_a_pasted_offsite_url_yields_nothing():
    assert page_title("https://evil.co/wiki/Creature:X") is None


def test_page_url_uses_the_constant_host():
    url = page_url("Creature:Lost Soul")
    assert url.startswith(f"https://{WIKI_HOST}/wiki/")


def test_page_url_refuses_an_unusable_title():
    assert page_url("../../etc/passwd") is None


def test_title_and_url_round_trip():
    title = "Creature:Lord Nightshade"
    assert page_title(page_url(title)) == title


# ---------------------------------------------------------------------------
# resolve_wiki_page — the chokepoint
# ---------------------------------------------------------------------------


def test_a_recorded_title_resolves_to_a_url():
    params = {"wiki_page": "Creature:Lost Soul"}
    assert resolve_wiki_page(params) == f"https://{WIKI_HOST}/wiki/Creature:Lost%20Soul"


def test_no_page_recorded_resolves_to_nothing():
    assert resolve_wiki_page({}) is None
    assert resolve_wiki_page({"wiki_page": ""}) is None
    assert resolve_wiki_page({"wiki_page": "   "}) is None


def test_a_non_string_value_resolves_to_nothing():
    assert resolve_wiki_page({"wiki_page": 42}) is None
    assert resolve_wiki_page({"wiki_page": ["Creature:X"]}) is None


@pytest.mark.parametrize("hostile", [*_LOOKALIKES, "../../etc/passwd", "javascript:alert(1)"])
def test_a_hostile_value_from_a_bot_file_resolves_to_nothing(hostile):
    assert resolve_wiki_page({"wiki_page": hostile}) is None


def test_an_unusable_value_is_logged(caplog):
    # Silently returning None would hide a bot file that has been tampered with.
    with caplog.at_level("WARNING"):
        resolve_wiki_page({"wiki_page": "https://evil.co/wiki/X"})
    assert "wiki_page" in caplog.text


def test_every_resolved_url_is_on_the_wiki_host():
    for value in [*_LOOKALIKES, "Creature:X", "../x", "javascript:x", f"https://{WIKI_HOST}/wiki/Item:Y"]:
        url = resolve_wiki_page({"wiki_page": value})
        assert url is None or url.startswith(f"https://{WIKI_HOST}/wiki/")
