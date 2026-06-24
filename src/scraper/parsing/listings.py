"""Turns one brand's device-listing page into DeviceListing rows, plus a regex pass that pulls a few quick specs straight out of each listing's title text."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from bs4 import BeautifulSoup, Tag

from src.shared.settings import GSMA_URL

RE_ANNOUNCED = re.compile(r"Announced ([^.]+)\.")
RE_DISPLAY_SIZE = re.compile(r'Features ([\d.]+)["\u2033]')
RE_CHIPSET = re.compile(r"display[,:] (.+?) chipset", re.IGNORECASE)
RE_BATTERY = re.compile(r"(\d+) mAh battery", re.IGNORECASE)
RE_STORAGE = re.compile(r"(\d+) GB storage", re.IGNORECASE)
RE_RAM = re.compile(r"(\d+) GB RAM", re.IGNORECASE)

OS_FAMILIES = [
    "Android",
    "iOS",
    "iPadOS",
    "Windows",
    "Tizen",
    "KaiOS",
    "HarmonyOS",
    "Fire OS",
    "Sailfish OS",
]

_NEXT_ARROW_CODEPOINTS = {0x25BA, 0x25B6, 0x2192, 0x203A, 0x276F, 0x00BB}


@dataclass(frozen=True, slots=True)
class DeviceListing:
    name: str
    slug: str
    url: str
    image_url: str
    raw_title: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def parse_brand_listing(html: str) -> tuple[list[DeviceListing], str | None]:
    soup = BeautifulSoup(html, "html.parser")
    makers = soup.select_one("div.makers")
    if not makers:
        return [], None

    devices: list[DeviceListing] = []
    for li in makers.select("ul > li"):
        listing = _parse_listing_item(li)
        if listing is not None:
            devices.append(listing)

    return devices, _next_page_url(soup)


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


def _parse_listing_item(li: Tag) -> DeviceListing | None:
    anchor = li.find("a")
    if not anchor:
        return None

    href = anchor.get("href", "")
    if not href or not href.endswith(".php"):
        return None

    image = anchor.find("img")
    title_attr = image.get("title", "").strip() if image else ""
    raw_title = title_attr or anchor.get("title", "").strip()

    strong = anchor.find("strong")
    if strong:
        span = strong.find("span")
        name = (span or strong).get_text(strip=True)
    else:
        name = anchor.get_text(strip=True)

    return DeviceListing(
        name=name,
        slug=href.removesuffix(".php"),
        url=GSMA_URL + href,
        image_url=image.get("src", "").strip() if image else "",
        raw_title=raw_title,
    )


def _extract_device_type(raw_title: str) -> str | None:
    os_pattern = "|".join(re.escape(os_name) for os_name in OS_FAMILIES)
    match = re.search(rf"(?:{os_pattern})\s*(.+?)\.", raw_title)
    if match:
        value = match.group(1).strip()
        return value or None
    return None


def _extract(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1) if match else None


def _next_page_url(soup: BeautifulSoup) -> str | None:
    pages = soup.select_one("div.review-nav-v2 div.nav-pages")
    if not pages:
        return None

    for candidate in pages.find_all("a", class_="prevnextbutton"):
        href = candidate.get("href", "")
        if not href or href == "#":
            continue

        title = candidate.get("title", "").strip().lower()
        text = candidate.get_text(strip=True)
        if "next" in title or "next" in text.lower() or _looks_like_next_arrow(text):
            return GSMA_URL + href

    return None


def _looks_like_next_arrow(text: str) -> bool:
    return any(ord(char) in _NEXT_ARROW_CODEPOINTS for char in text)
