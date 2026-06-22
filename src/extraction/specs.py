from bs4 import BeautifulSoup, Tag


def parse_spec_page(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    name = _extract_name(soup)
    brief = _extract_brief(soup)
    detailed = _extract_detailed(soup)
    return {"name": name, "brief": brief, "detailed": detailed}


def _extract_name(soup: BeautifulSoup) -> str:
    h1 = soup.select_one("h1[data-spec=modelname]")
    return h1.get_text(strip=True) if h1 else ""


def _extract_brief(soup: BeautifulSoup) -> dict[str, str]:
    brief: dict[str, str] = {}
    ul = soup.select_one("ul.specs-spotlight-features")
    if not ul:
        return brief
    for el in ul.find_all(lambda tag: tag.get("data-spec", "").endswith("-hl")):
        key = el["data-spec"].removesuffix("-hl")
        brief[key] = el.get_text(" ", strip=True)
    return brief


def _extract_detailed(soup: BeautifulSoup) -> dict[str, dict[str, str | None]]:
    detailed: dict[str, dict[str, str | None]] = {}

    for table in soup.select("#specs-list table"):
        th = table.find("th", scope="row")
        if not th:
            continue
        category = th.get_text(strip=True)
        if not category:
            continue

        rows: dict[str, str | None] = {}
        for tr in table.find_all("tr", recursive=False):
            ttl_td = tr.find("td", class_="ttl")
            nfo_td = tr.find("td", class_="nfo")
            if not ttl_td or not nfo_td:
                continue
            label = ttl_td.get_text(strip=True)
            if not label:
                continue
            value = _extract_nfo(nfo_td)
            rows[label] = value

        if rows:
            detailed[category] = rows

    return detailed


def _extract_nfo(nfo: Tag) -> str | None:
    parts: list[str] = []
    for child in nfo.children:
        if isinstance(child, Tag):
            text = child.get_text(strip=True)
            if child.name in ("br", "hr"):
                if parts and not parts[-1]:
                    continue
            if text:
                parts.append(text)
        elif isinstance(child, str) and child.strip():
            parts.append(child.strip())
    joined = " ".join(parts)
    return joined if joined else None
