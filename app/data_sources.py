import asyncio
import json
import logging
import random
import re
from pathlib import Path

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession


logger = logging.getLogger(__name__)

URL_BASE = "https://www.numbeo.com/cost-of-living/compare_cities.jsp"
HOME_URL = "https://www.numbeo.com/cost-of-living/"

# Per-city cost index (Numbeo's "Cost of Living Index by City", NYC=100 basis),
# built locally by scripts/build_cache.py and committed. Deployed free hosts
# (Render, HF Spaces) sit on IP ranges Numbeo/Cloudflare blocks with a 503, so the
# index is how cross-country comparisons work in production at all. Numbeo's
# pairwise percentages are reciprocal *and* transitive (verified), i.e. they derive
# from one index per city — so we store ~one number per city and COMPUTE any pair
# on the fly (ratios are basis-independent), instead of caching N² pairs. Only a
# city missing from the index falls back to a (possibly blocked) live scrape.
_INDEX_PATH = Path(__file__).parent / "data" / "numbeo_index.json"


def _load_index() -> dict:
    try:
        raw = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    # Skip the "_meta" provenance block (and any other underscore-prefixed keys).
    return {k: v for k, v in raw.items() if not k.startswith("_")}


_INDEX = _load_index()


def _index_key(country: str, city: str) -> str:
    """Normalised (country, city) key so casing/whitespace don't miss."""
    return "|".join(p.strip().lower() for p in (country, city))


def _compute_pair(city1: str, city2: str, idx1: dict, idx2: dict) -> dict:
    """Build the same shape as a live scrape from two per-city indices.
    valuePct(A→B) = (index_B / index_A − 1) × 100, for cost-of-living and rent."""
    out = {"city_from": city1, "city_to": city2}
    for field, key in (("col_excl_rent", "col"), ("rent", "rent")):
        pct = round((idx2[key] / idx1[key] - 1) * 100, 1)
        out[field] = {"valuePct": pct, "direction": "higher" if pct >= 0 else "lower"}
    return out

# curl_cffi replays a real Chrome TLS/JA3 handshake *and* its full header set —
# so the request is indistinguishable from a browser down to the TLS layer, which
# plain httpx header-spoofing can't reach. This beats Cloudflare fingerprint-based
# bot-detection; whether it also beats a datacenter IP-reputation block is exactly
# what deploying this tests.
_IMPERSONATE = "chrome"

# 503/429 are Numbeo/Cloudflare soft-throttle responses — worth a short retry.
_RETRY_STATUS = {429, 503}
_MAX_ATTEMPTS = 3


def _new_session() -> AsyncSession:
    """Create the impersonating HTTP session. Isolated so tests can stub it."""
    return AsyncSession()


def extract_city_differences(text: str):
    m = re.search(r"(?:is|are)\s+([\d.]+)%\s+(higher|lower)\s+than", text, re.I)
    if not m:
        raise ValueError(f"Could not parse diff: {text}")

    pct = float(m.group(1))
    direction = m.group(2).lower()
    signed = pct if direction == "higher" else -pct

    return {"valuePct": signed, "direction": direction}


async def _fetch_compare_html(params: dict) -> str:
    """Fetch the Numbeo compare page as HTML, impersonating Chrome.

    Warms up cookies with a homepage GET, then retries the real request with
    exponential backoff + jitter on soft-throttle responses (429/503).
    """
    last_status = None
    session = _new_session()
    async with session:
        # Warm-up: collect Set-Cookie like a browser hitting the section first.
        try:
            await session.get(HOME_URL, impersonate=_IMPERSONATE, timeout=20)
        except Exception:  # noqa: BLE001 — cookies are a bonus, not required
            pass

        for attempt in range(_MAX_ATTEMPTS):
            r = await session.get(
                URL_BASE, params=params, impersonate=_IMPERSONATE, timeout=20
            )
            if r.status_code not in _RETRY_STATUS:
                if r.status_code >= 400:
                    raise RuntimeError(f"Numbeo returned HTTP {r.status_code}.")
                return r.text

            last_status = r.status_code
            if attempt < _MAX_ATTEMPTS - 1:
                backoff = 1.5 * (2 ** attempt) + random.uniform(0, 0.75)
                logger.warning(
                    "Numbeo %s (attempt %d/%d) — retrying in %.1fs",
                    r.status_code, attempt + 1, _MAX_ATTEMPTS, backoff,
                )
                await asyncio.sleep(backoff)

    # Exhausted retries on a soft throttle — surface a clean, user-facing message.
    raise RuntimeError(
        f"Numbeo is temporarily unavailable (HTTP {last_status}). "
        "Please try again in a moment."
    )


async def get_percentage_diff(country1: str, city1: str, country2: str, city2: str):
    """Cost-of-living diff for a city pair — computed from the index if both
    cities are known, otherwise a live scrape."""
    idx1 = _INDEX.get(_index_key(country1, city1))
    idx2 = _INDEX.get(_index_key(country2, city2))
    if idx1 and idx2:
        logger.info("Numbeo index hit: %s / %s", city1, city2)
        return _compute_pair(city1, city2, idx1, idx2)
    return await _scrape_live(country1, city1, country2, city2)


async def _scrape_live(country1: str, city1: str, country2: str, city2: str):
    params = dict(
        country1=country1,
        city1=city1,
        country2=country2,
        city2=city2
    )

    html = await _fetch_compare_html(params)

    soup = BeautifulSoup(html, "html.parser")

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
