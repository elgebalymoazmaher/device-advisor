"""Turns the GSMArena maker/brand index page into a list of brand rows.

Note: see the docstring in parsing/specs.py for why mypy's union-attr
complaints about `.get("href", ...)` here are stub noise (href is never a
multi-valued attribute in practice), not real bugs.
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, NavigableString, Tag

from src.shared.settings import GSMA_URL

RE_DEVICES = re.compile(r"(\d+)\s*devices")


def parse_brand_index(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("div", class_="st-text") or soup

    rows: list[dict[str, Any]] = []
    for td in table.find_all("td"):
        anchor = td.find("a")
        if not anchor:
            continue

        href = anchor.get("href", "")
        if not href or not href.endswith(".php"):
            continue

        span = anchor.find("span")
        rows.append(
            {
                "name": _brand_name(anchor),
                "url": GSMA_URL + href,
                "slug": href.removesuffix(".php"),
                "device_count": _device_count(span),
            }
        )

    return rows


def _brand_name(anchor: Tag) -> str:
    direct_text = [
        str(node).strip()
        for node in anchor.children
        if isinstance(node, NavigableString) and str(node).strip()
    ]
    if direct_text:
        return " ".join(direct_text)

    span = anchor.find("span")
    label = anchor.get_text(" ", strip=True)
    if span:
        return label.replace(span.get_text(" ", strip=True), "").strip()
    return label


def _device_count(span: Tag | None) -> int:
    if not span:
        return 0
    match = RE_DEVICES.search(span.get_text(" ", strip=True))
    return int(match.group(1)) if match else 0
