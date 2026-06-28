# relo-calculator

**Relocation Finance Tools** — estimate the money side of moving abroad. A landing page (`/`) offers two tools:

- **Relocation calculator** (`/relo`) — compares cost of living, exchange rates, and projected monthly savings between two cities.
- **Take-home estimator** (`/tax`) — rough gross → net (after-tax) estimate by country.

## How it works

### App flow

```
User fills form (countries, cities, salary)
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
    │       If cross-currency, fetches ~200d of daily rates from
    │       Yahoo Finance and computes EMA-30/60/180 as a
    │       next-month rate forecast (+ a sensitivity band).
    │
    └─► model.py
            Applies a fixed budget model to your salary:
              30% → rent · 50% → other living costs · 20% → savings
            Scales costs by the Numbeo percentages (FX-adjusted for
            cross-country moves) to estimate monthly savings, the
            savings delta vs. home, and the break-even salary.
```

### What you input

| Field | Required | Notes |
|---|---|---|
| From Country / City | Yes | Country is a **dropdown**; selecting one auto-fills the city with its capital (editable). City is otherwise free text |
| To Country / City | Yes | Where you're considering moving |
| Current net salary | Yes | Monthly take-home, in home currency (label shows the inferred code) |
| New net salary | One of these | Offer amount, **in destination currency** |
| Expected increase % of savings | One of these | Target savings growth vs. now; the app back-solves the salary needed |
| Savings rate (slider + number) | No | % of income saved (default 20%) |
| Rent share (slider + number) | No | Rent's % of the **remaining** spend; the complement is other living costs (default 25%) |

> Country is a dropdown because the supported set is finite (it drives currency inference). The country→currency and country→capital data live in [`app/data/`](app/data/) as JSON (`currency_by_country.json`, `capital_city.json`). Cities stay free text — there's no reliable offline list of Numbeo cities to populate a dropdown from. Submitted form values persist across submits, and all monetary figures (inputs and results) render with thousand separators.

Currencies are **inferred automatically** from the country names — no need to type currency codes. Cross-country comparisons trigger an FX lookup; same-country (or same-currency) comparisons skip it.

The two budget sliders show a **live breakdown** (savings / rent / other-living amounts) as you drag, based on the current salary — no submit needed.

### Take-home pay estimator (`/tax`)

A separate page at `/tax` estimates **gross → net** take-home from a flat per-country effective rate ([`app/data/tax_rates.json`](app/data/tax_rates.json)). It's deliberately decoupled from the relocation calculator (which stays net-only and precise). The rates are **approximate, indicative figures with cited provenance** (a `_meta` block in the JSON): OECD members align with the OECD *net personal average tax rate* (Taxing Wages), zero-income-tax jurisdictions per PwC, others rounded from public tax summaries. The page shows the metric, sources, and links to PwC / Numbeo to verify. Real take-home varies with income, deductions, and household, so the relocation calculator still prefers your actual net figure.

### What you get

- How much more/less expensive daily life and rent are in the destination (Numbeo)
- **FX prediction** for next month (EMA-30) with a sensitivity band, shown both directions
- Estimated monthly savings at home vs. destination — in both currencies, plus a % delta
- The break-even salary needed in the destination to maintain your current savings
- If you gave a savings target, the destination salary required to hit it

### Model assumptions

The budget split is **controlled by two sliders** (defaults match the classic 30/50/20):

- **Savings rate** — % of net income saved (default 20%)
- **Rent share** — rent's % of the remaining spend (default 25% → 20% of income); the complement goes to other living costs (food, transport, utilities, etc.)

So `savings + rent + other = 100%` of income, always. The weights flow into the model per request; `W_RENT` / `W_NON_RENT` in `model.py` are just the fallback defaults when the sliders aren't sent.

The model assumes you **replicate your home lifestyle** in the new city — each spending bucket is scaled by its own Numbeo index (rent by the Rent index, other costs by the Cost-of-Living-excl-rent index), independently.

### FX prediction

The next-month exchange-rate forecast is a **weighted blend of three EMAs** over the last ~200 trading days:

```
forecast = 0.3·EMA-30 + 0.3·EMA-90 + 0.4·EMA-180
```

(renormalised over whatever horizons the history supports). EMA-30 adds recent responsiveness; EMA-180 anchors the long-term trend. The spread across the EMAs forms a sensitivity band, re-run through the model to give a savings range. A green ▲ / red ▼ arrow shows whether the forecast sits above or below the EMA-180 anchor. The whole prediction renders as a single compact line in the UI.

**Data sources** (tried in order, automatic fallback):

1. **Yahoo Finance** — daily history, broadest currency coverage; primary source. The client seeds Yahoo's consent cookies (via `fc.yahoo.com`) and retries across both API hosts to reduce 429s — though IP-based throttling on shared/free hosts can't be fully avoided, which is exactly why the fallbacks exist.
2. **Frankfurter / ECB** — free, no API key, ~31 major currencies (incl. MYR, SGD); used if Yahoo is unavailable or rate-limited (HTTP 429).
3. **currency-api (spot)** — free, keyless, ~150 currencies (incl. Gulf currencies like SAR that ECB omits). No daily history, so it yields a **spot rate only** — the card shows the current rate with no EMA forecast. Spot lookups are cached in-process for an hour and are reversible (a cached A→B also answers B→A as 1/rate).

The blend renormalises when history is short: if only some of EMA-30/90/180 are available, their weights (0.3/0.3/0.4) are rescaled proportionally over what's present.

If **all** sources fail, the cost-of-living comparison still renders — only the savings estimate (which needs currency conversion) is skipped, with a clear notice. The active source is shown in the FX card.

> Not TradingView / XE / Wise: none offer a free, keyless public API for rates — TradingView means scraping (against ToS), and XE/Wise require paid or authenticated business accounts. The sources above are the robust free alternatives.

> EMA is a smoothing/trend tool, not a precise forecaster — FX is famously hard to predict and a random walk beats most models. Treat the numbers as a plausible range, not a guarantee. The wider the EMA-30 ↔ EMA-180 gap, the less certain the estimate.

---

## Running locally

### With UV (recommended)

```bash
uv sync            # install dependencies
./run.sh           # start dev server (or: uv run uvicorn app.main:app --reload)
```

Open [http://localhost:8000](http://localhost:8000).

### With Docker

```bash
docker build -t relo-calculator .
docker run -p 8000:8000 relo-calculator
```

---

## Testing

The suite uses `pytest` with `pytest-asyncio`; network calls (Numbeo, Yahoo, Frankfurter, currency-api) are mocked via `respx` and monkeypatching — no live requests.

```bash
uv sync --group dev          # install test + audit dependencies
uv run pytest                # run the suite
uv run pytest --cov          # run with a coverage report
uv run pytest --cov --cov-report=html   # HTML report in htmlcov/
uv run pip-audit             # scan dependencies for known CVEs
```

### Layers & coverage

Latest run — **129 tests, 93% line coverage**, spanning four layers:

| Layer | File | Focus |
|---|---|---|
| Unit | `test_model/fx/currencies/data_sources.py` | Model math, EMA blend, lookups, parsing |
| Integration | `test_integration.py` | Full request → model → render pipeline |
| Regression | `test_regression.py` | Pins previously-fixed bugs |
| Security | `test_security.py` | Validation, XSS escaping, SSRF resistance |
| Endpoint | `test_app.py` | Routes, validation, degradation |

| Module | Coverage |
|---|---|
| `app/currencies.py` | 100% |
| `app/fx.py` | 96% |
| `app/model.py` | 96% |
| `app/data_sources.py` | 96% |
| `app/main.py` | ~86% (uncovered: rare HTTP error branches) |
| **Total** | **93%** |

See [SECURITY_TEST.md](SECURITY_TEST.md) for the security review — findings rated by CVSS band and mapped to the OWASP Top 10. The dependency audit is clean after upgrading `python-multipart` / `starlette` / `idna` / `python-dotenv` (14 CVEs resolved).

---

## Deploying

A single Docker container with no external dependencies (no database, no secrets). Any container platform works:

| Platform | Command |
|---|---|
| Railway / Render / Fly.io | Point to repo or push image — they auto-detect the Dockerfile |
| Docker Compose | `docker compose up` |
| Manual VPS | `docker run -d -p 8000:8000 --restart=unless-stopped relo-calculator` |

No environment variables required.

---

## Limitations

- **Numbeo data quality** — indices are crowd-sourced and may lag reality, especially for smaller cities. Missing or mis-named cities return a clear error.
- **Fixed budget weights** — the 30/50/20 split is a rough proxy; real spending varies.
- **FX is a forecast, not a guarantee** — see the FX prediction note above.
- **Currency coverage** — `currencies.py` maps common countries; an unknown country falls back to no FX conversion (with a warning) rather than failing.
- **Scraping dependency** — if Numbeo or Yahoo change their structure, the relevant fetch breaks. Errors are surfaced to the user rather than swallowed.
