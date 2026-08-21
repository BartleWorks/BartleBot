"""Tests for the HTML parsing logic in wiki_import_dialog.py.

Only the three module-level functions and the _validate_url static method are tested here —
all are pure string/BeautifulSoup logic with no Qt or network dependencies.
"""

import pytest
from bs4 import BeautifulSoup

from bartlebot.gui.wiki_import_dialog import (
    WikiImportDialog,
    _is_item_link,
    _parse_drops,
    _text_before_first_link,
    wiki_page_path,
)


def _tag(html: str):
    """Parse a single HTML snippet and return the first tag found."""
    return BeautifulSoup(html, "html.parser").find()


# ---------------------------------------------------------------------------
# _is_item_link
# ---------------------------------------------------------------------------


def test_relative_item_path_is_accepted():
    assert _is_item_link("/wiki/Item:Great Spyre Ring") is True


def test_relative_hat_path_is_accepted():
    assert _is_item_link("/wiki/Hat:Exalted Hat") is True


def test_relative_robe_path_is_accepted():
    assert _is_item_link("/wiki/Robe:Exalted Robe") is True


def test_relative_pet_path_is_accepted():
    assert _is_item_link("/wiki/Pet:Snow Serpent") is True


def test_relative_jewel_path_is_accepted():
    assert _is_item_link("/wiki/Jewel:Opal") is True


def test_relative_treasurecard_path_is_accepted():
    assert _is_item_link("/wiki/TreasureCard:Storm Shark") is True


def test_relative_tc_alias_is_accepted():
    assert _is_item_link("/wiki/TC:Sprite") is True


def test_relative_reagent_path_is_accepted():
    assert _is_item_link("/wiki/Reagent:Black Lotus") is True


def test_full_url_with_valid_namespace_is_accepted():
    assert _is_item_link("https://wiki.wizard101central.com/wiki/Hat:Exalted Hat") is True


def test_external_url_is_rejected():
    assert _is_item_link("https://example.com/wiki/Item:Foo") is False


def test_creature_namespace_is_rejected():
    assert _is_item_link("/wiki/Creature:Gurtok Piercer") is False


def test_no_namespace_page_is_rejected():
    assert _is_item_link("/wiki/SomePage") is False


def test_path_without_wiki_segment_is_rejected():
    assert _is_item_link("/Item:Foo") is False


def test_bare_path_lacking_wiki_prefix_is_rejected():
    # After lstrip("/") this becomes "Item:Foo" which doesn't start with "wiki/"
    assert _is_item_link("Item:Foo") is False


# ---------------------------------------------------------------------------
# _text_before_first_link
# ---------------------------------------------------------------------------


def test_none_returns_empty_string():
    assert _text_before_first_link(None) == ""


def test_tag_with_no_children_returns_empty_string():
    assert _text_before_first_link(_tag("<span></span>")) == ""


def test_tag_with_only_text_returns_that_text():
    assert _text_before_first_link(_tag("<span>Fire Drops</span>")) == "Fire Drops"


def test_text_before_link_is_returned():
    tag = _tag('<span>Ice Drops <a href="/wiki/Item:Foo">link</a></span>')
    assert _text_before_first_link(tag) == "Ice Drops"


def test_link_as_first_child_returns_empty_string():
    tag = _tag('<span><a href="/wiki/Item:Foo">link</a> trailing text</span>')
    assert _text_before_first_link(tag) == ""


def test_nested_element_before_link_contributes_its_text():
    tag = _tag('<span><b>Storm</b> Drops<a href="/wiki/Item:Foo">link</a></span>')
    assert _text_before_first_link(tag) == "Storm Drops"


# ---------------------------------------------------------------------------
# _parse_drops — primary (drop-category) format
# ---------------------------------------------------------------------------

_DROP_CATEGORY_HTML = """
<html><body>
<div id="mw-content-text">
  <p>Fire Elemental</p>
  <div class="drop-category">
    <a href="/wiki/Item:Fire Ring">Fire Ring</a>
    <a href="/wiki/Creature:Gurtok">Gurtok</a>
  </div>
  <p>Ice Elemental</p>
  <div class="drop-category">
    <a href="/wiki/Pet:Snow Serpent">Snow Serpent</a>
  </div>
</div>
</body></html>
"""


def test_drop_category_two_categories_bucketed_correctly():
    result = _parse_drops(_DROP_CATEGORY_HTML)
    assert "Fire Elemental" in result
    assert "Ice Elemental" in result
    assert result["Fire Elemental"] == ["Fire Ring"]
    assert result["Ice Elemental"] == ["Snow Serpent"]


def test_drop_category_non_item_link_is_filtered():
    result = _parse_drops(_DROP_CATEGORY_HTML)
    all_items = [item for items in result.values() for item in items]
    assert "Gurtok" not in all_items


def test_drop_category_deduplicates_items():
    html = """
    <div id="mw-content-text">
      <p>Loot</p>
      <div class="drop-category">
        <a href="/wiki/Item:Ring">Ring</a>
        <a href="/wiki/Item:Ring">Ring</a>
      </div>
    </div>
    """
    result = _parse_drops(html)
    assert result["Loot"].count("Ring") == 1


def test_drop_category_empty_div_excluded_from_result():
    html = """
    <div id="mw-content-text">
      <p>Nothing</p>
      <div class="drop-category">
        <a href="/wiki/Creature:Foo">Foo</a>
      </div>
    </div>
    """
    result = _parse_drops(html)
    assert "Nothing" not in result
    assert result == {}


def test_drop_category_without_mw_content_text_wrapper_still_parses():
    html = """
    <html><body>
      <p>Fire Elemental</p>
      <div class="drop-category">
        <a href="/wiki/Item:Fire Ring">Fire Ring</a>
      </div>
    </body></html>
    """
    result = _parse_drops(html)
    assert "Fire Ring" in result.get("Fire Elemental", [])


def test_drop_category_no_previous_sibling_falls_back_to_other():
    html = """
    <div id="mw-content-text">
      <div class="drop-category">
        <a href="/wiki/Item:Mystery Item">Mystery Item</a>
      </div>
    </div>
    """
    result = _parse_drops(html)
    assert "Mystery Item" in result.get("Other", [])


# ---------------------------------------------------------------------------
# _parse_drops — fallback (heading-based) format
# ---------------------------------------------------------------------------

_HEADING_HTML = """
<html><body>
<div id="mw-content-text">
  <a href="/wiki/Reagent:Black Lotus">Black Lotus</a>
  <h2>Common Drops<span>[edit]</span></h2>
  <a href="/wiki/Item:Common Ring">Common Ring</a>
  <h3>Rare Drops</h3>
  <a href="/wiki/Jewel:Diamond">Diamond</a>
  <a href="/wiki/Creature:Ignore Me">Ignore Me</a>
</div>
</body></html>
"""


def test_fallback_items_before_first_heading_go_to_other():
    result = _parse_drops(_HEADING_HTML)
    assert "Black Lotus" in result.get("Other", [])


def test_fallback_items_grouped_under_h2_heading():
    result = _parse_drops(_HEADING_HTML)
    assert "Common Ring" in result.get("Common Drops", [])


def test_fallback_items_grouped_under_h3_heading():
    result = _parse_drops(_HEADING_HTML)
    assert "Diamond" in result.get("Rare Drops", [])


def test_fallback_edit_marker_stripped_from_heading():
    result = _parse_drops(_HEADING_HTML)
    # Key must not include "[edit]"
    assert all("[edit]" not in k for k in result)
    assert "Common Drops" in result


def test_fallback_edit_source_marker_stripped_from_heading():
    html = """
    <div id="mw-content-text">
      <h2>Drops<span>[edit source]</span></h2>
      <a href="/wiki/Item:Rare Ring">Rare Ring</a>
    </div>
    """
    result = _parse_drops(html)
    assert "Drops" in result
    assert all("[edit source]" not in k for k in result)


def test_fallback_non_item_links_filtered():
    result = _parse_drops(_HEADING_HTML)
    all_items = [item for items in result.values() for item in items]
    assert "Ignore Me" not in all_items


def test_fallback_deduplicates_items():
    html = """
    <div id="mw-content-text">
      <h2>Loot</h2>
      <a href="/wiki/Item:Ring">Ring</a>
      <a href="/wiki/Item:Ring">Ring</a>
    </div>
    """
    result = _parse_drops(html)
    assert result["Loot"].count("Ring") == 1


# ---------------------------------------------------------------------------
# _parse_drops — edge cases
# ---------------------------------------------------------------------------


def test_empty_string_returns_empty_dict():
    assert _parse_drops("") == {}


def test_html_with_no_links_returns_empty_dict():
    assert _parse_drops("<p>Nothing here</p>") == {}


def test_html_with_only_non_item_links_returns_empty_dict():
    html = """
    <div id="mw-content-text">
      <a href="/wiki/Creature:Foo">Foo</a>
      <a href="/wiki/Spell:Bar">Bar</a>
    </div>
    """
    assert _parse_drops(html) == {}


# ---------------------------------------------------------------------------
# WikiImportDialog._validate_url (static method — no Qt instantiation needed)
# ---------------------------------------------------------------------------


def test_validate_url_accepts_valid_wiki_url():
    assert WikiImportDialog._validate_url("https://wiki.wizard101central.com/wiki/Creature:Foo") is None


def test_validate_url_rejects_http():
    err = WikiImportDialog._validate_url("http://wiki.wizard101central.com/wiki/Foo")
    assert err is not None
    assert isinstance(err, str)


def test_validate_url_rejects_wrong_host():
    err = WikiImportDialog._validate_url("https://example.com/wiki/Foo")
    assert err is not None
    assert isinstance(err, str)


def test_validate_url_rejects_missing_wiki_path():
    err = WikiImportDialog._validate_url("https://wiki.wizard101central.com/Foo")
    assert err is not None
    assert isinstance(err, str)


# ---------------------------------------------------------------------------
# Host validation
#
# The URL reaching _FetchWorker is fetched with curl_cffi impersonating Chrome,
# so "is this the wiki?" has to be an exact hostname comparison. The substring
# test this replaced accepted every lookalike below.
# ---------------------------------------------------------------------------

_LOOKALIKES = [
    "https://wiki.wizard101central.com.evil.co/wiki/Creature:Jade Oni",
    "https://evil.co/wiki/?ref=wiki.wizard101central.com",
    "https://evil.co/wiki.wizard101central.com/wiki/page",
    "https://wiki.wizard101central.com@evil.co/wiki/page",
    "https://WIKI.WIZARD101CENTRAL.COM.evil.co/wiki/page",
]


@pytest.mark.parametrize("url", _LOOKALIKES)
def test_a_lookalike_host_is_rejected(url):
    assert wiki_page_path(url) is None
    assert WikiImportDialog._validate_url(url) is not None


def test_the_real_wiki_is_accepted():
    url = "https://wiki.wizard101central.com/wiki/Creature:Jade Oni"
    assert wiki_page_path(url) == "/wiki/Creature:Jade Oni"
    assert WikiImportDialog._validate_url(url) is None


def test_the_host_comparison_is_case_insensitive():
    assert wiki_page_path("https://WIKI.Wizard101Central.COM/wiki/Item:X") == "/wiki/Item:X"


def test_http_is_rejected():
    assert wiki_page_path("http://wiki.wizard101central.com/wiki/Item:X") is None


def test_a_url_off_the_wiki_path_is_rejected():
    assert wiki_page_path("https://wiki.wizard101central.com/not-wiki/X") is None


def test_userinfo_is_rejected_even_on_the_real_host():
    # Resolves to the real wiki, but a URL that reads as one host and resolves to
    # another has no legitimate use here.
    assert wiki_page_path("https://evil.co@wiki.wizard101central.com/wiki/X") is None


def test_a_relative_href_is_same_origin_by_construction():
    assert wiki_page_path("/wiki/Item:Amber") == "/wiki/Item:Amber"


def test_a_relative_href_off_the_wiki_path_is_rejected():
    assert wiki_page_path("/images/foo.png") is None


def test_blank_input_is_rejected():
    assert wiki_page_path("") is None
    assert wiki_page_path("   ") is None


@pytest.mark.parametrize("url", _LOOKALIKES)
def test_an_item_link_on_a_lookalike_host_is_not_an_item_link(url):
    # The href is never followed, but a crafted page should not be able to inject
    # names into the drop list through an off-site link either.
    assert not _is_item_link(url.replace("/wiki/page", "/wiki/Item:Evil"))
