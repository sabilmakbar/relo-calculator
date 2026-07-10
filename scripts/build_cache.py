"""Build the per-city cost index from Numbeo's "Cost of Living Index by City".

ONE request to the rankings page yields ~550 cities with their Cost of Living
Index and Rent Index (NYC = 100 basis) — no per-pair scraping, no rate-limit
dance. We keep up to MAX_PER_COUNTRY cities per dropdown country (always including
that country's capital, aliased if Numbeo names it differently), and the app
computes any pair from these indices on the fly (ratios are basis-independent).

    uv run python scripts/build_cache.py

Commit the regenerated app/data/numbeo_index.json. Cost-of-living data moves
slowly, so a snapshot stays valid for months; re-run to refresh.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bs4 import BeautifulSoup  # noqa: E402
from curl_cffi.requests import Session  # noqa: E402

from app.currencies import COUNTRIES, country_to_capital  # noqa: E402
from app.data_sources import _INDEX_PATH, _index_key  # noqa: E402

RANKINGS_URL = "https://www.numbeo.com/cost-of-living/rankings_current.jsp"
MAX_PER_COUNTRY = 10

# Rankings country label (lowercased) → our dropdown country name, when they differ.
COUNTRY_ALIAS = {
    "czechia": "Czech Republic",
    "hong kong (china)": "Hong Kong",
}

# Capitals Numbeo's ranking omits (too little data to be ranked). NYC=100 basis,
# taken once from the compare page; low-data cities that rarely move, so a static
# snapshot is fine. Without these, selecting the country would 503 in production.
FALLBACK_CITIES = {
    ("Laos", "Vientiane"): {"col": 35.2, "rent": 13.7},
    ("Brunei", "Bandar Seri Begawan"): {"col": 42.8, "rent": 15.9},
    ("Myanmar", "Yangon"): {"col": 38.6, "rent": 10.1},
}

_OUR = {c.lower(): c for c in COUNTRIES}


def fetch_rankings() -> list[tuple[str, str, float, float]]:
    """Return (country_raw, city, col_index, rent_index) for every ranked city."""
    s = Session()
    r = s.get(RANKINGS_URL, impersonate="chrome", timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    table = soup.select_one("table#t2") or soup.select_one("table")
    rows = []
    for tr in table.find_all("tr")[1:]:
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        label = tds[1].get_text(strip=True)  # e.g. "New York, NY, United States"
        if ", " not in label:
            continue
        parts = [p.strip() for p in label.split(",")]
        country_raw, city = parts[-1], ", ".join(parts[:-1])
        try:
            col = float(tds[2].get_text(strip=True))
            rent = float(tds[3].get_text(strip=True))
        except ValueError:
            continue
        rows.append((country_raw, city, col, rent))
    return rows


def build_index(rows):
    by_country = defaultdict(list)  # our_country -> [(city, col, rent)] in rank order
    unmatched = set()
    for country_raw, city, col, rent in rows:
        our = COUNTRY_ALIAS.get(country_raw.lower()) or _OUR.get(country_raw.lower())
        if not our:
            unmatched.add(country_raw)
            continue
        by_country[our].append((city, col, rent))

    index = {}
    for our, cities in by_country.items():
        for city, col, rent in cities[:MAX_PER_COUNTRY]:
            index[_index_key(our, city)] = {"col": col, "rent": rent}
        # Ensure the dropdown capital resolves even if Numbeo names it differently
        # (e.g. "New York" vs "New York, NY") by aliasing it to the best match.
        cap = country_to_capital(our)
        if cap and _index_key(our, cap) not in index:
            match = next(
                (t for t in cities
                 if cap.lower() in t[0].lower() or t[0].lower() in cap.lower()),
                None,
            )
            if match:
                index[_index_key(our, cap)] = {"col": match[1], "rent": match[2]}

    # Backfill capitals the ranking doesn't carry.
    for (country, city), vals in FALLBACK_CITIES.items():
        index.setdefault(_index_key(country, city), dict(vals))
    return index, unmatched


def main() -> int:
    rows = fetch_rankings()
    print(f"rankings rows parsed: {len(rows)}")
    index, unmatched = build_index(rows)

    caps = [(c, country_to_capital(c)) for c in COUNTRIES]
    covered = [c for c, cap in caps if cap and _index_key(c, cap) in index]
    missing = [c for c, cap in caps if cap and _index_key(c, cap) not in index]
    print(f"cities indexed: {len(index)}")
    print(f"capitals covered: {len(covered)}/{len(COUNTRIES)}")
    if missing:
        print("capitals NOT covered (live-scrape fallback):", ", ".join(missing))

    payload = {
        "_meta": {
            "source": "Numbeo Cost of Living Index by City (rankings_current.jsp)",
            "basis": "New York City = 100",
            "max_per_country": MAX_PER_COUNTRY,
            "cities": len(index),
            "note": "Pairs computed on the fly; see app/data_sources.py + scripts/build_cache.py",
        },
        **dict(sorted(index.items())),
    }
    _INDEX_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {_INDEX_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
