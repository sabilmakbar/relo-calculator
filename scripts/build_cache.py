"""Build the offline Numbeo cache from a clean (residential) IP.

Deployed free hosts (Render, HF Spaces) sit on IP ranges Numbeo/Cloudflare
block with a 503, so the app can't scrape in production. Run this locally to
pre-scrape common city pairs into app/data/numbeo_cache.json, commit the result,
and the deployed app serves those pairs from cache (never hitting Numbeo).

    uv run python scripts/build_cache.py

Edit CITIES below to change coverage, then re-run. The scrape is directional
(A→B differs from B→A), so every ordered pair is fetched. Existing cached pairs
are preserved (merge), and failures are skipped so a partial run still helps.
"""
import asyncio
import json
import sys
from itertools import permutations
from pathlib import Path

# Make `app` importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data_sources import _CACHE_PATH, _cache_key, _load_cache, _scrape_live  # noqa: E402

# (country, city) — edit this list to change what's cached, then re-run.
CITIES = [
    ("Malaysia", "Kuala Lumpur"),
    ("Singapore", "Singapore"),
    ("Indonesia", "Jakarta"),
    ("Thailand", "Bangkok"),
    ("United Kingdom", "London"),
    ("Netherlands", "Amsterdam"),
    ("United Arab Emirates", "Dubai"),
    ("Australia", "Sydney"),
]

DELAY_SECONDS = 2.0  # be polite to Numbeo between requests


async def main() -> int:
    cache = _load_cache()  # merge into whatever's already there
    pairs = list(permutations(CITIES, 2))
    print(f"Scraping {len(pairs)} directional pairs from {len(CITIES)} cities...\n")

    ok = failed = cached_already = 0
    for i, ((c1, city1), (c2, city2)) in enumerate(pairs, 1):
        label = f"{city1}, {c1} → {city2}, {c2}"
        key = _cache_key(c1, city1, c2, city2)
        if key in cache:
            cached_already += 1
            print(f"[{i}/{len(pairs)}] have  {label}")
            continue
        try:
            cache[key] = await _scrape_live(c1, city1, c2, city2)
            ok += 1
            print(f"[{i}/{len(pairs)}] ok    {label}")
        except Exception as exc:  # noqa: BLE001 — skip & report, keep going
            failed += 1
            print(f"[{i}/{len(pairs)}] SKIP  {label}  ({exc})")
        await asyncio.sleep(DELAY_SECONDS)

    payload = {
        "_meta": {
            "source": "Numbeo cost-of-living compare_cities",
            "note": "Pre-scraped locally; see scripts/build_cache.py",
            "pairs": len(cache),
        },
        **dict(sorted(cache.items())),
    }
    _CACHE_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"\nDone: {ok} new, {cached_already} already cached, {failed} skipped "
        f"({len(cache)} total) → {_CACHE_PATH.relative_to(Path.cwd())}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
