import os

from extraction.listing import parse_brand_listing, parse_raw_specs

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures")


def _load(name: str) -> str:
    path = os.path.join(FIXTURE_DIR, name)
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_parse_samsung_listing():
    html = _load("samsung_listing.html")
    devices, next_url = parse_brand_listing(html)

    assert len(devices) > 0, "Expected at least one device"

    first = devices[0]
    assert first.name, "Device name is empty"
    assert first.url.startswith("https://www.gsmarena.com/"), f"Bad URL: {first.url}"
    assert first.image_url.startswith("http"), f"Bad image URL: {first.image_url}"
    assert first.raw_title, "Raw title is empty"
    assert first.slug, "Slug is empty"

    # Verify first device's raw_title is meaningful
    specs = parse_raw_specs(first.raw_title)
    assert specs["announced_raw"] is not None, f"Could not extract announced from: {first.raw_title}"
    assert specs["battery_mah"] is not None, f"Could not extract battery from: {first.raw_title}"
    assert specs["ram_gb"] is not None, f"Could not extract RAM from: {first.raw_title}"


def test_parse_samsung_listing_counts():
    html = _load("samsung_listing.html")
    devices, next_url = parse_brand_listing(html)

    assert len(devices) >= 20, f"Expected 20+ devices per page, got {len(devices)}"

    # Check pagination
    assert next_url is not None, "Expected next page URL"
    assert next_url.startswith("https://www.gsmarena.com/"), f"Bad next URL: {next_url}"


def test_parse_samsung_listing_all_fields():
    html = _load("samsung_listing.html")
    devices, _ = parse_brand_listing(html)

    for d in devices[:5]:
        assert d.name, f"Missing name for {d.url}"
        assert d.url, "Missing URL"
        assert d.image_url, f"Missing image for {d.url}"
        assert d.raw_title, f"Missing raw_title for {d.url}"
        assert "-" in d.slug, f"Unexpected slug format: {d.slug}"

        # Raw specs should all parse or be None (not crash)
        specs = parse_raw_specs(d.raw_title)
        for key in ("announced_raw", "display_size_in", "chipset", "battery_mah", "storage_gb", "ram_gb", "device_type"):
            assert key in specs, f"Missing parsed key '{key}' for {d.url}"
