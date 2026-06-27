"""Tests for src.scraper.parsing.listings."""

from __future__ import annotations

from src.scraper.parsing.listings import (
    DeviceListing,
    parse_brand_listing,
    parse_raw_specs,
)

LISTING_HTML = """
<html><body>
<div class="makers">
<ul>
<li>
<a href="samsung_galaxy_s24-12773.php">
<img src="//example.com/s24.jpg" title="Android 14, up to 12GB RAM, 6.2in display." />
<strong>Galaxy S24<span> 5G</span></strong>
</a>
</li>
<li>
<a href="nokia_3310-737.php" title="A legendary classic phone.">
<strong>Nokia 3310</strong>
</a>
</li>
</ul>
</div>
<div class="review-nav-v2">
<div class="nav-pages">
<a class="prevnextbutton" href="#" title="prev page">&laquo;</a>
<a class="prevnextbutton" href="samsung-phones-9-p2.php" title="Next page">&raquo;</a>
</div>
</div>
</body></html>
"""


def test_parse_brand_listing_extracts_devices() -> None:
    devices, _next_url = parse_brand_listing(LISTING_HTML)
    assert len(devices) == 2

    s24 = devices[0]
    assert s24.slug == "samsung_galaxy_s24-12773"
    assert s24.url == "https://www.gsmarena.com/samsung_galaxy_s24-12773.php"
    assert s24.image_url == "//example.com/s24.jpg"
    assert "Android 14" in s24.raw_title
    # Current (preserved) behaviour: when <strong> has a nested <span>, the
    # span's text wins over the strong's own text -- here that's "5G" rather
    # than "Galaxy S24". Documented here so a future change to real-world
    # markup handling shows up as a deliberate, visible diff.
    assert s24.name == "5G"

    nokia = devices[1]
    assert nokia.name == "Nokia 3310"
    assert nokia.raw_title == "A legendary classic phone."


def test_parse_brand_listing_next_page_url() -> None:
    _, next_url = parse_brand_listing(LISTING_HTML)
    assert next_url == "https://www.gsmarena.com/samsung-phones-9-p2.php"


def test_parse_brand_listing_no_next_page() -> None:
    html = """
    <div class="makers"><ul>
    <li><a href="x-1234.php"><strong>X</strong></a></li>
    </ul></div>
    <div class="review-nav-v2"><div class="nav-pages">
    <a class="prevnextbutton" href="#" title="prev">&laquo;</a>
    </div></div>
    """
    _, next_url = parse_brand_listing(html)
    assert next_url is None


def test_parse_brand_listing_next_detected_by_arrow_glyph_without_title() -> None:
    html = """
    <div class="makers"><ul>
    <li><a href="x-1234.php"><strong>X</strong></a></li>
    </ul></div>
    <div class="review-nav-v2"><div class="nav-pages">
    <a class="prevnextbutton" href="x-p2.php">&rsaquo;</a>
    </div></div>
    """
    _, next_url = parse_brand_listing(html)
    assert next_url == "https://www.gsmarena.com/x-p2.php"


def test_parse_brand_listing_missing_makers_div_returns_empty() -> None:
    devices, next_url = parse_brand_listing("<html><body>nothing</body></html>")
    assert devices == []
    assert next_url is None


def test_parse_brand_listing_skips_items_without_anchor_or_php_href() -> None:
    html = """
    <div class="makers"><ul>
    <li><span>no anchor here</span></li>
    <li><a href="/not-a-device">also skipped</a></li>
    <li><a href="ok-1234.php"><strong>OK</strong></a></li>
    </ul></div>
    """
    devices, _ = parse_brand_listing(html)
    assert len(devices) == 1
    assert devices[0].name == "OK"


def test_device_listing_to_dict_round_trip() -> None:
    listing = DeviceListing(
        name="Phone",
        slug="phone-1",
        url="https://example.test/phone-1.php",
        image_url="https://example.test/phone-1.jpg",
        raw_title="Android 14, 5000 mAh battery.",
    )
    as_dict = listing.to_dict()
    assert as_dict == {
        "name": "Phone",
        "slug": "phone-1",
        "url": "https://example.test/phone-1.php",
        "image_url": "https://example.test/phone-1.jpg",
        "raw_title": "Android 14, 5000 mAh battery.",
    }


# --- parse_raw_specs ---------------------------------------------------------


def test_parse_raw_specs_extracts_known_fields() -> None:
    raw = (
        "Announced 2024, January 17. Released 2024, January 24. "
        'Features 6.2" AMOLED display, Snapdragon 8 Gen 3 chipset, '
        "4000 mAh battery, 256 GB storage. Android 14, up to 12 GB RAM."
    )
    specs = parse_raw_specs(raw)
    assert specs["announced_raw"] == "2024, January 17"
    assert specs["display_size_in"] == "6.2"
    assert specs["chipset"] == "Snapdragon 8 Gen 3"
    assert specs["battery_mah"] == "4000"
    assert specs["storage_gb"] == "256"
    assert specs["ram_gb"] == "12"
    assert specs["device_type"] == "14, up to 12 GB RAM"


def test_parse_raw_specs_missing_fields_are_none() -> None:
    specs = parse_raw_specs("Just a short blurb with nothing recognizable")
    assert specs == {
        "device_type": None,
        "announced_raw": None,
        "display_size_in": None,
        "chipset": None,
        "battery_mah": None,
        "storage_gb": None,
        "ram_gb": None,
    }


def test_parse_raw_specs_empty_string() -> None:
    specs = parse_raw_specs("")
    assert all(value is None for value in specs.values())
