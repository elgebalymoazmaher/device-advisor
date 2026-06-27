"""Tests for src.scraper.parsing.brands."""

from __future__ import annotations

from bs4 import BeautifulSoup

from src.scraper.parsing.brands import _brand_name, _device_count, parse_brand_index

MAKERS_HTML = """
<html><body>
<div class="st-text">
<table>
<tr>
<td><a href="acer-phones-59.php">Acer<span> 117 devices</span></a></td>
<td><a href="samsung-phones-9.php">Samsung<span> 1184 devices</span></a></td>
<td><a href="at&amp;t-phones-57.php">AT&amp;T<span> 4 devices</span></a></td>
</tr>
</table>
</div>
</body></html>
"""


def test_parse_brand_index_basic_rows() -> None:
    rows = parse_brand_index(MAKERS_HTML)
    assert len(rows) == 3

    acer, samsung, att = rows
    assert acer == {
        "name": "Acer",
        "url": "https://www.gsmarena.com/acer-phones-59.php",
        "slug": "acer-phones-59",
        "device_count": 117,
    }
    assert samsung["name"] == "Samsung"
    assert samsung["device_count"] == 1184
    assert att["name"] == "AT&T"
    assert att["device_count"] == 4


def test_parse_brand_index_skips_links_without_php_suffix() -> None:
    html = """
    <div class="st-text"><table><tr>
    <td><a href="acer-phones-59.php">Acer<span> 1 devices</span></a></td>
    <td><a href="/news/some-article">Not a brand</a></td>
    <td><span>No anchor at all</span></td>
    </tr></table></div>
    """
    rows = parse_brand_index(html)
    assert len(rows) == 1
    assert rows[0]["name"] == "Acer"


def test_parse_brand_index_empty_html_returns_empty_list() -> None:
    assert parse_brand_index("<html><body>nothing here</body></html>") == []


def test_parse_brand_index_falls_back_to_whole_document_without_st_text() -> None:
    html = (
        '<table><tr><td><a href="acer-phones-59.php">'
        "Acer<span> 5 devices</span></a></td></tr></table>"
    )
    rows = parse_brand_index(html)
    assert len(rows) == 1
    assert rows[0]["device_count"] == 5


# --- _brand_name / _device_count direct unit tests --------------------------


def _anchor(html: str):
    return BeautifulSoup(html, "html.parser").find("a")


def test_brand_name_direct_text_node_before_span() -> None:
    anchor = _anchor('<a href="x.php">Acer<span> 117 devices</span></a>')
    assert _brand_name(anchor) == "Acer"


def test_brand_name_multi_word_brand() -> None:
    anchor = _anchor('<a href="x.php">Sony Ericsson<span> 80 devices</span></a>')
    assert _brand_name(anchor) == "Sony Ericsson"


def test_brand_name_no_span_falls_back_to_full_text() -> None:
    anchor = _anchor('<a href="x.php">JustABrand</a>')
    assert _brand_name(anchor) == "JustABrand"


def test_device_count_parses_number() -> None:
    span = BeautifulSoup("<span> 1184 devices</span>", "html.parser").find("span")
    assert _device_count(span) == 1184


def test_device_count_none_span_returns_zero() -> None:
    assert _device_count(None) == 0


def test_device_count_unparseable_text_returns_zero() -> None:
    span = BeautifulSoup("<span>no number here</span>", "html.parser").find("span")
    assert _device_count(span) == 0
