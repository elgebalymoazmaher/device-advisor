import os

from extraction.specs import parse_spec_page

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures")


def _load(name: str) -> str:
    path = os.path.join(FIXTURE_DIR, name)
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_parse_device_name():
    html = _load("apple_iphone_17e_spec.html")
    result = parse_spec_page(html)
    assert result["name"] == "Apple iPhone 17e"


def test_parse_brief_fields():
    html = _load("apple_iphone_17e_spec.html")
    result = parse_spec_page(html)
    brief = result["brief"]

    expected_keys = {
        "released", "body", "os", "storage",
        "displaysize", "displayres", "camerapixels",
        "videopixels", "ramsize", "chipset",
        "batsize", "battype",
    }
    assert set(brief.keys()) == expected_keys, f"Got keys: {set(brief.keys())}"
    assert brief["released"] == "Released 2026, March 11"
    assert brief["chipset"] == "Apple A19"
    assert brief["ramsize"] == "8"


def test_parse_detailed_categories():
    html = _load("apple_iphone_17e_spec.html")
    result = parse_spec_page(html)
    detailed = result["detailed"]

    expected_categories = {
        "Network", "Launch", "Body", "Display", "Platform",
        "Memory", "Main Camera", "Selfie camera", "Sound",
        "Comms", "Features", "Battery", "Misc",
    }
    for cat in expected_categories:
        assert cat in detailed, f"Missing category: {cat}"


def test_parse_launch():
    html = _load("apple_iphone_17e_spec.html")
    result = parse_spec_page(html)
    launch = result["detailed"]["Launch"]
    assert "Announced" in launch
    assert "Status" in launch
    assert "2026" in launch["Announced"]


def test_parse_network_includes_toggle_rows():
    html = _load("apple_iphone_17e_spec.html")
    result = parse_spec_page(html)
    network = result["detailed"]["Network"]
    assert "Technology" in network
    assert "2G bands" in network
    assert "3G bands" in network
    assert "4G bands" in network
    assert "5G bands" in network
    assert "Speed" in network


def test_parse_platform():
    html = _load("apple_iphone_17e_spec.html")
    result = parse_spec_page(html)
    platform = result["detailed"]["Platform"]
    assert platform["OS"] == "iOS 26.3, upgradable to iOS 26.5"
    assert platform["Chipset"] == "Apple A19 (3 nm)"
    assert "CPU" in platform
    assert "GPU" in platform


def test_parse_memory():
    html = _load("apple_iphone_17e_spec.html")
    result = parse_spec_page(html)
    memory = result["detailed"]["Memory"]
    assert memory["Card slot"] == "No"
    assert "Internal" in memory


def test_parse_battery():
    html = _load("apple_iphone_17e_spec.html")
    result = parse_spec_page(html)
    battery = result["detailed"]["Battery"]
    assert "4005 mAh" in battery["Type"]


def test_parse_misc():
    html = _load("apple_iphone_17e_spec.html")
    result = parse_spec_page(html)
    misc = result["detailed"]["Misc"]
    assert "Colors" in misc
    assert "Black" in misc["Colors"]
    assert "Price" in misc
