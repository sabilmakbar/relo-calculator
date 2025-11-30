import re
import httpx
from bs4 import BeautifulSoup


URL_BASE = "https://www.numbeo.com/cost-of-living/compare_cities.jsp"

# Extremely generic — looks like any browser
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/119.0 Safari/537.36"
)


def load_city_differences(text: str):
    m = re.search(r"is\s+([\d.]+)%\s+(higher|lower)\s+than", text, re.I)
    if not m:
        raise ValueError(f"Could not parse diff: {text}")

    pct = float(m.group(1))
    direction = m.group(2).lower()
    signed = pct if direction == "higher" else -pct

    return {"valuePct": signed, "direction": direction}


async def scrape_diffs(country1, city1, country2, city2, amount=11000, displayCurrency="MYR"):
    params = dict(
        country1=country1,
        city1=city1,
        country2=country2,
        city2=city2,
        amount=str(amount),
        displayCurrency=displayCurrency,
    )

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            URL_BASE,
            params=params,
            headers={"User-Agent": USER_AGENT},
        )
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    table = soup.select_one("table.table_indices_diff")
    if not table:
        raise RuntimeError("Numbeo table not found.")

    col_excl_rent = None
    rent = None

    for tr in table.select("tbody tr"):
        td = tr.find("td")
        if not td:
            continue
        text = " ".join(td.get_text(strip=True, separator=" ").split())

        if text.startswith("Cost of Living in "):
            col_excl_rent = extract_diff(text)
        elif text.startswith("Rent Prices in "):
            rent = extract_diff(text)

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
