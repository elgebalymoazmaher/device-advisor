import re

from bs4 import BeautifulSoup

RE_DEVICES = re.compile(r"(\d+)\s*devices")

GSMArenaURL = "https://www.gsmarena.com/"


def parse_brand_index(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("div", class_="st-text")
    if not table:
        table = soup
    rows = []
    for td in table.find_all("td"):
        a = td.find("a")
        if not a:
            continue
        href = a.get("href", "")
        if not href or not href.endswith(".php"):
            continue
        brand_name = a.contents[0].strip() if a.contents else ""
        span = a.find("span")
        device_count = 0
        if span:
            m = RE_DEVICES.search(span.get_text())
            if m:
                device_count = int(m.group(1))
        rows.append({
            "name": brand_name,
            "url": GSMArenaURL + href,
            "slug": href.replace(".php", ""),
            "device_count": device_count,
        })
    return rows
