import re
import httpx
from bs4 import BeautifulSoup


URL_BASE = "https://www.numbeo.com/cost-of-living/compare_cities.jsp"

# Extremely generic — looks like any browser
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/119.0 Safari/537.36"
)


def extract_city_differences(text: str):
    m = re.search(r"(?:is|are)\s+([\d.]+)%\s+(higher|lower)\s+than", text, re.I)
    if not m:
        raise ValueError(f"Could not parse diff: {text}")

    pct = float(m.group(1))
    direction = m.group(2).lower()
    signed = pct if direction == "higher" else -pct

    return {"valuePct": signed, "direction": direction}


async def get_percentage_diff(country1: str, city1: str, country2: str, city2: str):
    params = dict(
        country1=country1,
        city1=city1,
        country2=country2,
        city2=city2
    )

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            URL_BASE,
            params=params,
            headers={"User-Agent": USER_AGENT},
        )
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")

    # Numbeo renders "Our system cannot find city X, Y" when a city is unknown.
    page_text = soup.get_text(" ", strip=True)
    if "cannot find city" in page_text.lower():
        # Extract Numbeo's own message for a user-friendly error.
        import re as _re
        m = _re.search(r"cannot find city ([^.]+)", page_text, _re.I)
        detail = m.group(1).strip() if m else f"{city1}, {country1} or {city2}, {country2}"
        raise ValueError(f"Numbeo doesn't recognise: {detail}. Check spelling and try again.")

    table = soup.select_one("table.table_indices_diff")
    if not table:
        raise RuntimeError("Numbeo table not found — the page layout may have changed.")

    col_excl_rent = None
    rent = None

    idx_iter = 0
    for tr in table.find_all("tr"):
        td = tr.find("td")
        if not td:
            continue
        idx_iter += 1
        text = " ".join(td.get_text(strip=True, separator=" ").split())

        if text.startswith("Cost of Living in "):
            col_excl_rent = extract_city_differences(text)
        elif text.startswith("Rent Prices in "):
            rent = extract_city_differences(text)

    if not col_excl_rent or not rent:
        raise RuntimeError("Unable to extract COL or rent differences.")

    city_from = table.select_one("span.city2").get_text(strip=True) if table.select_one("span.city2") else city1
    city_to = table.select_one("span.city1").get_text(strip=True) if table.select_one("span.city1") else city2

    return {
        "city_from": city_from,
        "city_to": city_to,
        "col_excl_rent": col_excl_rent,
        "rent": rent,
    }
