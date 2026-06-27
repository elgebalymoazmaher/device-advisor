"""Tests for src.scraper.parsing.specs."""

from __future__ import annotations

from bs4 import BeautifulSoup

from src.scraper.parsing.specs import _extract_nfo, parse_spec_page

SPEC_HTML = """
<html><body>
<h1 data-spec="modelname">Samsung Galaxy S24</h1>
<ul class="specs-spotlight-features">
<li data-spec="camerapixels-hl">50 MP</li>
<li data-spec="ramsize-hl">8 GB RAM</li>
<li data-spec="batsize-hl">4000 mAh</li>
</ul>
<div id="specs-list">
<table>
<tr><th scope="row">Network</th></tr>
<tr><td class="ttl">Technology</td><td class="nfo">GSM / HSPA / LTE</td></tr>
</table>
<table>
<tr><th scope="row">Display</th></tr>
<tr><td class="ttl">Type</td><td class="nfo"><a>AMOLED</a> <a>120Hz</a></td></tr>
<tr><td class="ttl">Size</td><td class="nfo">6.2 inches</td></tr>
</table>
</div>
</body></html>
"""


def test_parse_spec_page_name() -> None:
    assert parse_spec_page(SPEC_HTML)["name"] == "Samsung Galaxy S24"


def test_parse_spec_page_missing_name_returns_empty_string() -> None:
    assert parse_spec_page("<html><body>no h1 here</body></html>")["name"] == ""


def test_parse_spec_page_brief_spotlight_features() -> None:
    brief = parse_spec_page(SPEC_HTML)["brief"]
    assert brief == {
        "camerapixels": "50 MP",
        "ramsize": "8 GB RAM",
        "batsize": "4000 mAh",
    }


def test_parse_spec_page_brief_missing_section_is_empty_dict() -> None:
    html = '<h1 data-spec="modelname">Phone</h1>'
    assert parse_spec_page(html)["brief"] == {}


def test_parse_spec_page_detailed_tables_grouped_by_category() -> None:
    detailed = parse_spec_page(SPEC_HTML)["detailed"]
    assert detailed["Network"] == {"Technology": "GSM / HSPA / LTE"}
    assert detailed["Display"]["Size"] == "6.2 inches"


def test_parse_spec_page_detailed_joins_multiple_child_tags_in_value_cell() -> None:
    detailed = parse_spec_page(SPEC_HTML)["detailed"]
    assert detailed["Display"]["Type"] == "AMOLED 120Hz"


def test_parse_spec_page_detailed_skips_table_without_row_heading() -> None:
    html = """
    <div id="specs-list">
    <table><tr><td class="ttl">Orphan</td><td class="nfo">Value</td></tr></table>
    </div>
    """
    assert parse_spec_page(html)["detailed"] == {}


def test_parse_spec_page_detailed_skips_rows_missing_label_or_value_cell() -> None:
    html = """
    <div id="specs-list">
    <table>
    <tr><th scope="row">Category</th></tr>
    <tr><td class="ttl">HasValue</td><td class="nfo">Yes</td></tr>
    <tr><td class="ttl">NoValueCell</td></tr>
    </table>
    </div>
    """
    detailed = parse_spec_page(html)["detailed"]
    assert detailed == {"Category": {"HasValue": "Yes"}}


def test_extract_nfo_returns_none_for_empty_cell() -> None:
    cell = BeautifulSoup('<td class="nfo"></td>', "html.parser").find("td")
    assert cell is not None
    assert _extract_nfo(cell) is None


def test_extract_nfo_handles_plain_text_value() -> None:
    cell = BeautifulSoup(
        '<td class="nfo">  Plain text value  </td>', "html.parser"
    ).find("td")
    assert cell is not None
    assert _extract_nfo(cell) == "Plain text value"
