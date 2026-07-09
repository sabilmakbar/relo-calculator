"""Build the offline Numbeo cache for every capital pair, cheaply.

Deployed free hosts (Render, HF Spaces) get 503'd by Numbeo/Cloudflare, so the
app serves cross-country comparisons from app/data/numbeo_cache.json, built here
locally from a clean residential IP.

Full pairwise coverage of N capitals is N*(N-1) comparisons — 8,000+ for the
whole dropdown, which the rate limiter (≈50 requests/burst) makes impractical.
But Numbeo's pairwise percentages are reciprocal AND transitive (verified), i.e.
they come from a single per-city cost index. So we only scrape each capital ONCE
against a fixed reference city (~91 requests), store its index relative to the
reference, then COMPUTE every ordered pair offline:

    valuePct(A→B) = (index_B / index_A − 1) × 100

Run it (repeatedly — it resumes, skipping capitals already in the index, and the
rate limiter will stop each run after ~50 new cities):

    uv run python scripts/build_cache.py

Each run persists the per-city index (numbeo_index.json) and regenerates the full
pairwise cache from whatever's collected so far. Capitals Numbeo doesn't know are
skipped. Commit both JSON files.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.currencies import COUNTRIES, country_to_capital  # noqa: E402
from app.data_sources import _CACHE_PATH, _cache_key, _scrape_live  # noqa: E402

# Reference city — must exist in Numbeo. Its index is 1.0; everything is relative.
REF_COUNTRY, REF_CITY = "Singapore", "Singapore"

INDEX_PATH = Path(__file__).resolve().parent.parent / "app" / "data" / "numbeo_index.json"
DELAY_SECONDS = 2.0


def _index_key(country: str, city: str) -> str:
    return f"{country.strip().lower()}|{city.strip().lower()}"


# Some dropdown capitals don't match Numbeo's own city naming. We scrape under
# Numbeo's name but still key the result by the display capital, so the app's
# lookup (which uses the dropdown value) resolves. Keys: (country, display_city).
NUMBEO_CITY = {
    ("United States", "New York"): "New York, NY",
    ("Ukraine", "Kyiv"): "Kiev",
    ("Israel", "Tel Aviv"): "Tel Aviv-Yafo",
    ("Kazakhstan", "Astana"): "Nur-Sultan",
    ("India", "New Delhi"): "Delhi",
}


def _load_index() -> dict:
    try:
        raw = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return {k: v for k, v in raw.items() if not k.startswith("_")}


async def scrape_indices(index: dict) -> tuple[int, int, int]:
    """Scrape REF→capital for any capital not yet in the index. Returns
    (new, already, failed)."""
    capitals = [(c, country_to_capital(c)) for c in COUNTRIES]
    capitals = [(c, city) for c, city in capitals if city]

    # The reference anchors the scale at 1.0 — no need to scrape it.
    index.setdefault(_index_key(REF_COUNTRY, REF_CITY), {"col": 1.0, "rent": 1.0})

    new = already = failed = 0
    for i, (country, city) in enumerate(capitals, 1):
        key = _index_key(country, city)
        if key in index:
            already += 1
            continue
        scrape_city = NUMBEO_CITY.get((country, city), city)
        label = f"{city}, {country}" + (f" (as '{scrape_city}')" if scrape_city != city else "")
        try:
            r = await _scrape_live(REF_COUNTRY, REF_CITY, country, scrape_city)
            index[key] = {
                "col": 1 + r["col_excl_rent"]["valuePct"] / 100,
                "rent": 1 + r["rent"]["valuePct"] / 100,
            }
            new += 1
            print(f"[{i}/{len(capitals)}] ok    {label}")
        except Exception as exc:  # noqa: BLE001 — skip & report, keep going
            failed += 1
            print(f"[{i}/{len(capitals)}] SKIP  {label}  ({exc})")
        INDEX_PATH.write_text(  # persist after every attempt → resumable
            json.dumps({"_meta": {"reference": f"{REF_CITY}, {REF_COUNTRY}"},
                        **dict(sorted(index.items()))}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        await asyncio.sleep(DELAY_SECONDS)
    return new, already, failed


def expand_to_pairs(index: dict) -> dict:
    """Compute every ordered pair from the per-city indices."""
    # key -> (country, city) recovered from index keys, plus display cities.
    cities = {}  # index_key -> (country_title, city_title)
    for c in COUNTRIES:
        city = country_to_capital(c)
        if city and _index_key(c, city) in index:
            cities[_index_key(c, city)] = (c, city)

    pairs = {}
    for ak, (a_country, a_city) in cities.items():
        for bk, (b_country, b_city) in cities.items():
            if ak == bk:
                continue
            sa, sb = index[ak], index[bk]
            entry = {"city_from": a_city, "city_to": b_city}
            for field, dest_key in (("col_excl_rent", "col"), ("rent", "rent")):
                pct = round((sb[dest_key] / sa[dest_key] - 1) * 100, 1)
                entry[field] = {
                    "valuePct": pct,
                    "direction": "higher" if pct >= 0 else "lower",
                }
            pairs[_cache_key(a_country, a_city, b_country, b_city)] = entry
    return pairs


async def main() -> int:
    index = _load_index()
    print(f"Reference: {REF_CITY}, {REF_COUNTRY}. Index has {len(index)} cities.\n")
    new, already, failed = await scrape_indices(index)

    pairs = expand_to_pairs(index)
    payload = {
        "_meta": {
            "source": "Numbeo cost-of-living compare_cities (index-derived)",
            "reference": f"{REF_CITY}, {REF_COUNTRY}",
            "note": "Per-city indices in numbeo_index.json; pairs computed. See scripts/build_cache.py",
            "cities": len(index),
            "pairs": len(pairs),
        },
        **dict(sorted(pairs.items())),
    }
    _CACHE_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"\nIndex: {new} new, {already} already, {failed} skipped ({len(index)} cities).\n"
        f"Cache: {len(pairs)} pairs → {_CACHE_PATH.relative_to(Path.cwd())}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
