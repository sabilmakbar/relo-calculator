import asyncio
import logging
import random
import re

import httpx
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)

URL_BASE = "https://www.numbeo.com/cost-of-living/compare_cities.jsp"
HOME_URL = "https://www.numbeo.com/cost-of-living/"

# Full Chrome header set. The point is to be indistinguishable from a real tab:
# the sec-ch-ua / Sec-Fetch-* headers are what a browser auto-attaches and a
# naive scraper omits. Keep the UA major version and the sec-ch-ua version in
# sync (both Chrome 126) — a mismatch is itself a flag.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": HOME_URL,
    "Upgrade-Insecure-Requests": "1",
    "sec-ch-ua": '"Chromium";v="126", "Not)A;Brand";v="99", "Google Chrome";v="126"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-User": "?1",
    "Sec-Fetch-Dest": "document",
}

# 503/429 are Numbeo/Cloudflare soft-throttle responses — worth a short retry.
_RETRY_STATUS = {429, 503}
_MAX_ATTEMPTS = 3


def extract_city_differences(text: str):
    m = re.search(r"(?:is|are)\s+([\d.]+)%\s+(higher|lower)\s+than", text, re.I)
    if not m:
        raise ValueError(f"Could not parse diff: {text}")

    pct = float(m.group(1))
    direction = m.group(2).lower()
    signed = pct if direction == "higher" else -pct

    return {"valuePct": signed, "direction": direction}


async def _fetch_compare_page(params: dict) -> httpx.Response:
    """Fetch the Numbeo compare page like a browser would.

    Primes Cloudflare/consent cookies with a warm-up GET to the homepage on a
    persistent client, then retries the real request with exponential backoff +
    jitter on soft-throttle responses (429/503).
    """
    last_exc: httpx.HTTPStatusError | None = None
    async with httpx.AsyncClient(
        timeout=15, follow_redirects=True, http2=True, headers=BROWSER_HEADERS
    ) as client:
        # Warm-up: collect Set-Cookie like a browser hitting the section first.
        try:
            await client.get(HOME_URL)
        except httpx.RequestError:
            pass  # cookies are a bonus, not required — proceed to the real request

        for attempt in range(_MAX_ATTEMPTS):
            r = await client.get(URL_BASE, params=params)
            if r.status_code not in _RETRY_STATUS:
                r.raise_for_status()
                return r

            last_exc = httpx.HTTPStatusError(
                f"Numbeo returned {r.status_code}", request=r.request, response=r
            )
            if attempt < _MAX_ATTEMPTS - 1:
                backoff = 1.5 * (2 ** attempt) + random.uniform(0, 0.75)
                logger.warning(
                    "Numbeo %s (attempt %d/%d) — retrying in %.1fs",
                    r.status_code, attempt + 1, _MAX_ATTEMPTS, backoff,
                )
                await asyncio.sleep(backoff)

    # Exhausted retries on a soft throttle — surface a clean, user-facing message.
    raise RuntimeError(
        "Numbeo is temporarily unavailable (rate-limited). Please try again in a moment."
    ) from last_exc


async def get_percentage_diff(country1: str, city1: str, country2: str, city2: str):
    params = dict(
        country1=country1,
        city1=city1,
        country2=country2,
        city2=city2
    )

    r = await _fetch_compare_page(params)

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
