# Relocation Finance Tools

> 🌍 **[Try it live →](https://relo-calculator.onrender.com/)** — no install needed. (Free tier: the first visit after a quiet spell may take ~30s to wake.)

Estimate the money side of moving abroad. A landing page (`/`) offers two tools:

- **Relocation calculator** (`/relo`) — compares cost of living, exchange rates, and projected monthly savings between two cities.
- **Take-home estimator** (`/tax`) — rough gross → net (after-tax) estimate by country.

FastAPI + server-rendered HTML. No database, no secrets, no API keys — cost data is scraped from Numbeo and FX is pulled from free public sources with automatic fallback.

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) (it manages the Python 3.11+ toolchain).

```bash
uv sync
uv run uvicorn app.main:app --reload      # or: ./run.sh
```

Then open <http://localhost:8000>.

### Docker

```bash
docker build -t relo-calculator .
docker run -p 8000:8000 relo-calculator
```

## Docs

| Doc | What's in it |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | App flow, the savings model, FX prediction (EMA blend + fallback chain), budget weights, the tax tool, data files, limitations |
| [docs/TESTING.md](docs/TESTING.md) | Test layers, coverage, how to run + `pip-audit` |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Free-tier hosting options, `$PORT` / health-check notes |
| [docs/SECURITY_TEST.md](docs/SECURITY_TEST.md) | Security review — findings by CVSS band, mapped to OWASP Top 10 |

## Tests

```bash
uv run pytest --cov     # 152 tests, 100% coverage
```

## License

See [LICENSE](LICENSE).
