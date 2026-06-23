"""Turns one phone's spec page into name + brief + detailed spec tables."""

from __future__ import annotations

from bs4 import BeautifulSoup, Tag


def parse_spec_page(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    return {
        "name": _extract_name(soup),
        "brief": _extract_brief(soup),
        "detailed": _extract_detailed(soup),
    }


def _extract_name(soup: BeautifulSoup) -> str:
    h1 = soup.select_one("h1[data-spec=modelname]")
    return h1.get_text(strip=True) if h1 else ""


def _extract_brief(soup: BeautifulSoup) -> dict[str, str]:
    brief: dict[str, str] = {}
    summary = soup.select_one("ul.specs-spotlight-features")
    if not summary:
        return brief

    for element in summary.find_all(lambda tag: tag.get("data-spec", "").endswith("-hl")):
        key = element["data-spec"].removesuffix("-hl")
        brief[key] = element.get_text(" ", strip=True)

    return brief


def _extract_detailed(soup: BeautifulSoup) -> dict[str, dict[str, str | None]]:
    detailed: dict[str, dict[str, str | None]] = {}

    for table in soup.select("#specs-list table"):
        heading = table.find("th", scope="row")
        if not heading:
            continue

        category = heading.get_text(strip=True)
        if not category:
            continue

        rows: dict[str, str | None] = {}
        for tr in table.find_all("tr", recursive=False):
            label_cell = tr.find("td", class_="ttl")
            value_cell = tr.find("td", class_="nfo")
            if not label_cell or not value_cell:
                continue

            label = label_cell.get_text(strip=True)
            if label:
                rows[label] = _extract_nfo(value_cell)

        if rows:
            detailed[category] = rows

    return detailed


def _extract_nfo(nfo: Tag) -> str | None:
    parts: list[str] = []
    for child in nfo.children:
        if isinstance(child, Tag):
            text = child.get_text(strip=True)
            if text:
                parts.append(text)
        elif isinstance(child, str) and child.strip():
            parts.append(child.strip())

    joined = " ".join(parts)
    return joined or None
