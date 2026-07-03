# Architecture & how it works

## Tools

A landing page (`/`) offers two independent tools:

- **Relocation calculator** (`/relo`, computes via `POST /compare`) — cost of living, FX, and projected savings between two cities.
- **Take-home estimator** (`/tax`) — rough gross → net (after-tax) by country.

## Relocation calculator flow

```
User opens /  (landing) → picks "Relocation calculator" (/relo)
    │
    ▼
POST /compare
    │
    ├─► currencies.py
    │       Infers the ISO 4217 currency for each country
    │       (Malaysia → MYR, Singapore → SGD). No manual input.
    │
    ├─► data_sources.py   ┐
    │       Scrapes Numbeo │ run concurrently
    │       cost/rent diffs│ (asyncio.gather)
    │                      │
    └─► fx.py              ┘
    │       If cross-currency, fetches ~200d of daily rates and
    │       computes a blended next-month rate forecast (+ band).
    │
    └─► model.py
            Splits income by the budget sliders, scales costs by the
            Numbeo percentages (FX-adjusted cross-country), and returns
            monthly savings, the savings delta vs. home, and the
            break-even salary.
```

## What you input

| Field | Required | Notes |
|---|---|---|
| From Country / City | Yes | Country is a **dropdown**; selecting one auto-fills the city with its capital (editable). City is otherwise free text |
| To Country / City | Yes | Where you're considering moving |
| Current net salary | Yes | Monthly take-home, in home currency (label shows the inferred code) |
| New net salary | One of these | Offer amount, **in destination currency** |
| Expected increase % of savings | One of these | Target savings growth vs. now; the app back-solves the salary needed |
| Savings rate (slider + number) | No | % of income saved (default 20%) |
| Rent share (slider + number) | No | Rent's % of the **remaining** spend (default 25%); the complement is other living costs |

Currencies are **inferred automatically** from the country names. Cross-country comparisons trigger an FX lookup; same-country (or same-currency) comparisons skip it. Submitted form values persist across submits, and all monetary figures (inputs and results) render with thousand separators. The two budget sliders show a **live breakdown** as you drag.

The country→currency and country→capital data live in [`../app/data/`](../app/data/) as JSON (`currency_by_country.json`, `capital_city.json`). Cities stay free text — there's no reliable offline list of Numbeo cities to populate a dropdown from.

## What you get

- How much more/less expensive daily life and rent are in the destination (Numbeo)
- **FX prediction** for next month with a sensitivity band, shown both directions
- Estimated monthly savings at home vs. destination — in both currencies, plus a % delta
- The break-even salary needed in the destination to maintain your current savings
- If you gave a savings target, the destination salary required to hit it

## The savings model

The budget split is **controlled by two sliders**:

- **Savings rate** — % of net income saved (default **20%**)
- **Rent share** — rent's % of the remaining spend (default **25%**)

With the defaults this works out to **20% rent · 60% other living · 20% savings** of income (`savings + rent + other = 100%` always). The weights flow into the model per request; `W_RENT` / `W_NON_RENT` in `model.py` are only the fallback defaults when the sliders aren't sent.

The model assumes you **replicate your home lifestyle** in the new city — each spending bucket is scaled by its own Numbeo index (rent by the Rent index, other costs by the Cost-of-Living-excl-rent index), independently. For cross-country moves the home spending baseline is converted to the destination currency before the Numbeo percentages are applied (the percentages are already FX-adjusted), which avoids mixing currency scales.

## FX prediction

The next-month exchange-rate forecast is a **weighted blend of three EMAs** over the last ~200 trading days:

```
forecast = 0.3·EMA-30 + 0.3·EMA-90 + 0.4·EMA-180
```

(renormalised over whatever horizons the history supports). EMA-30 adds recent responsiveness; EMA-180 anchors the long-term trend. The spread across the EMAs forms a sensitivity band, re-run through the model to give a savings range. A green ▲ / red ▼ arrow shows whether the forecast sits above or below the EMA-180 anchor. Very small rates are shown scaled (e.g. `1,000,000 IDR = 49 EUR`).

**Data sources** (tried in order, automatic fallback):

1. **Yahoo Finance** — daily history, broadest currency coverage; primary source. The client seeds Yahoo's consent cookies (via `fc.yahoo.com`) and retries across both API hosts to reduce 429s — though IP-based throttling on shared/free hosts can't be fully avoided, which is why the fallbacks exist.
2. **Frankfurter / ECB** — free, no API key, ~31 major currencies (incl. MYR, SGD); used if Yahoo is unavailable or rate-limited (HTTP 429).
3. **currency-api (spot)** — free, keyless, ~150 currencies (incl. Gulf currencies like SAR that ECB omits). No daily history → **spot rate only** (no EMA forecast). Spot lookups are cached in-process for an hour and are reversible (a cached A→B also answers B→A as 1/rate).

The blend renormalises when history is short. If **all** sources fail, the cost-of-living comparison still renders — only the savings estimate (which needs currency conversion) is skipped, with a clear notice.

> Not TradingView / XE / Wise: none offer a free, keyless public API for rates — TradingView means scraping (against ToS), and XE/Wise require paid or authenticated business accounts.

> EMA is a smoothing/trend tool, not a precise forecaster — FX is famously hard to predict and a random walk beats most models. Treat the numbers as a plausible range, not a guarantee.

## Take-home pay estimator (`/tax`)

A separate page estimates **gross → net** take-home from a flat per-country effective rate ([`../app/data/tax_rates.json`](../app/data/tax_rates.json)). It's deliberately decoupled from the relocation calculator (which stays net-only and precise). The rates are **approximate, indicative figures with cited provenance** (a `_meta` block in the JSON): OECD members align with the OECD *net personal average tax rate* (Taxing Wages), zero-income-tax jurisdictions per PwC, others rounded from public tax summaries. The page shows the metric, sources, and links to PwC / Numbeo to verify.

## Limitations

- **Numbeo data quality** — indices are crowd-sourced and may lag reality, especially for smaller cities. Missing or mis-named cities return a clear error.
- **Budget weights are a proxy** — the split is configurable but still a simplification of real spending.
- **FX is a forecast, not a guarantee** — see above.
- **Currency coverage** — an unknown country falls back to no FX conversion (with a warning) rather than failing.
- **Tax estimates are indicative** — flat effective rates, not payroll-grade.
- **Scraping dependency** — if Numbeo/Yahoo change structure, the relevant fetch breaks; errors are surfaced, not swallowed. The Numbeo fetch uses `curl_cffi` to impersonate a real Chrome TLS/JA3 fingerprint (plus its header set), primes cookies with a warm-up request, and retries 429/503 with backoff. This beats Cloudflare *fingerprint*-based bot-detection; a hard IP-reputation block (common from datacenter/free-tier hosts) can still return 503, in which case it degrades to a "temporarily unavailable" notice rather than a raw error.
