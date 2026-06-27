# relo-calculator

A personal tool for estimating whether a relocation offer is financially worth it — comparing cost of living and projected monthly savings between two cities.

## How it works

### App flow

```
User fills form
    │
    ▼
POST /compare
    │
    ├─► data_sources.py
    │       Hits Numbeo's compare_cities page for the two cities,
    │       scrapes the HTML diff table to extract two signed percentages:
    │         • Cost of Living (excl. rent): how much more/less expensive daily life is
    │         • Rent Prices: how much more/less rent is
    │
    └─► model.py
            Applies a fixed budget model to your salary:
              30% → rent
              50% → other living costs
              20% → savings (remainder)
            Scales each bucket by the Numbeo percentages to estimate
            what you'd actually save per month in the new city.
            Also computes the break-even salary: the minimum you'd need
            in the new city to match your current savings rate.
```

### What you input

| Field | Required | Notes |
|---|---|---|
| Country 1 / City 1 | Yes | Your current location |
| Country 2 / City 2 | Yes | Where you're considering moving |
| Current net salary | Yes | Monthly take-home, any currency |
| New net salary | One of these | Direct offer amount |
| Expected increase % | One of these | Applied to current salary |

### What you get

- How much more/less expensive daily life and rent are in the destination (sourced from Numbeo)
- Estimated monthly savings at home vs. destination
- The break-even salary needed in the destination to maintain your current savings

### Model assumptions

The savings model uses fixed spending weights (in `app/model.py`):

- **30%** of net income → rent
- **50%** of net income → other living costs (food, transport, utilities, etc.)
- **20%** → savings

These are intentionally simple. Adjust `w_rent` and `w_non_rent` in `model.py` to match your actual spending.

---

## Running locally

### With UV (recommended)

```bash
# Install dependencies
uv sync

# Start dev server
./run.sh
# or: uv run uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000).

### With Docker

```bash
docker build -t relo-calculator .
docker run -p 8000:8000 relo-calculator
```

---

## Deploying

The app is a single Docker container with no external dependencies (no database, no secrets). Any platform that runs containers works:

| Platform | Command |
|---|---|
| Railway / Render / Fly.io | Point to repo or push image — they auto-detect the Dockerfile |
| Docker Compose | `docker compose up` |
| Manual VPS | `docker run -d -p 8000:8000 --restart=unless-stopped relo-calculator` |

No environment variables are required.

---

## Limitations

- **Numbeo data quality** — indices are crowd-sourced and may lag reality, especially for smaller cities. Missing or mis-named cities will return a parsing error.
- **Fixed budget weights** — the 30/50/20 split is a rough proxy. Your mileage will vary based on actual lifestyle.
- **Currency agnostic** — the tool doesn't convert currencies. Use the same currency unit for both salaries.
- **Scraping dependency** — if Numbeo changes their HTML structure, the scraper breaks. Check the error message if results stop appearing.
