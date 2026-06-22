import re

from bs4 import BeautifulSoup

from models.device import DeviceListing

GSMA_URL = "https://www.gsmarena.com/"

RE_ANNOUNCED = re.compile(r"Announced ([^.]+)\.")
RE_DISPLAY_SIZE = re.compile(r"Features ([\d.]+)[\"″]")
RE_CHIPSET = re.compile(r"display[,:] (.+?) chipset", re.IGNORECASE)
RE_BATTERY = re.compile(r"(\d+) mAh battery", re.IGNORECASE)
RE_STORAGE = re.compile(r"(\d+) GB storage", re.IGNORECASE)
RE_RAM = re.compile(r"(\d+) GB RAM", re.IGNORECASE)

OS_FAMILIES = [
    "Android", "iOS", "iPadOS", "Windows", "Tizen",
    "KaiOS", "HarmonyOS", "Fire OS", "Sailfish OS",
]


def _extract_device_type(raw_title: str) -> str | None:
    pattern = "|".join(re.escape(os) for os in OS_FAMILIES)
    m = re.search(rf"(?:{pattern})\s*(.+?)\.", raw_title)
    if m:
        val = m.group(1).strip()
        return val if val else None
    return None


def _extract(pattern: re.Pattern, text: str) -> str | None:
    m = pattern.search(text)
    return m.group(1) if m else None


def parse_raw_specs(raw_title: str) -> dict[str, str | None]:
    return {
        "device_type": _extract_device_type(raw_title),
        "announced_raw": _extract(RE_ANNOUNCED, raw_title),
        "display_size_in": _extract(RE_DISPLAY_SIZE, raw_title),
        "chipset": _extract(RE_CHIPSET, raw_title),
        "battery_mah": _extract(RE_BATTERY, raw_title),
        "storage_gb": _extract(RE_STORAGE, raw_title),
        "ram_gb": _extract(RE_RAM, raw_title),
    }


def parse_brand_listing(html: str) -> tuple[list[DeviceListing], str | None]:
    soup = BeautifulSoup(html, "html.parser")
    devices: list[DeviceListing] = []

    makers = soup.select_one("div.makers")
    if not makers:
        return [], None

    ul = makers.find("ul")
    if not ul:
        return [], None

    for li in ul.find_all("li", recursive=False):
        a = li.find("a")
        if not a:
            continue
        href = a.get("href", "")
        if not href or not href.endswith(".php"):
            continue

        device_url = GSMA_URL + href
        slug = href.replace(".php", "")

        img = a.find("img")
        image_url = img.get("src", "").strip() if img else ""

        title_attr = img.get("title", "").strip() if img else ""
        raw_title = title_attr if title_attr else a.get("title", "").strip()

        strong = a.find("strong")
        if strong:
            span = strong.find("span")
            device_name = (span or strong).get_text(strip=True)
        else:
            device_name = a.get_text(strip=True)

        devices.append(DeviceListing(
            name=device_name,
            slug=slug,
            url=device_url,
            image_url=image_url,
            raw_title=raw_title,
        ))

    next_url = _next_page_url(soup)
    return devices, next_url


def _next_page_url(soup: BeautifulSoup) -> str | None:
    nav = soup.select_one("div.review-nav-v2")
    if not nav:
        return None
    pages = nav.select_one("div.nav-pages")
    if not pages:
        return None
    next_link = pages.find("a", class_="prevnextbutton", title="Next page")
    if next_link:
        href = next_link.get("href", "")
        if href and href != "#":
            return GSMA_URL + href
    return None
