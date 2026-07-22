# Security & Test Report — relo-calculator

_Defensive security review and test analysis of the relocation cost/savings calculator._

_Last re-reviewed against the per-city-index architecture (offline cost index, `curl_cffi` scrape fallback, `/tax` endpoint, Hugging Face sync workflow)._

## 1. Scope & methodology

| Item | Detail |
|---|---|
| Target | FastAPI app (`app/`) — form-driven, server-rendered HTML, no auth, no DB |
| Endpoints | `GET /` (landing), `GET /relo`, `POST /compare`, `GET/POST /tax`, `HEAD /` (health) |
| Attack surface | Two unauthenticated POST forms (`/compare`, `/tax`) + static assets. Outbound HTTP only on a **cache miss** — cost data is served from a committed offline index, so most requests make no external calls; live scrape (Numbeo via `curl_cffi`) and FX (Yahoo → Frankfurter → currency-api) fire only when needed |
| Methods | Static review, automated dependency audit (`pip-audit`), and **automated security tests** (`tests/test_security.py`) |
| Severity scale | Qualitative **CVSS v3.1** bands (Critical / High / Medium / Low / Informational) |
| Frameworks referenced | OWASP Top 10 (2021), CWE |

### Test layers run

| Layer | File | Purpose |
|---|---|---|
| Unit | `test_model/fx/currencies/data_sources/tax.py` | Model math, EMA blend, index compute, parsing, lookups, tax rates |
| Integration | `test_integration.py` | Full request → model → render pipeline (mocked I/O) |
| Regression | `test_regression.py` | Pins previously-fixed bugs |
| Security | `test_security.py` | Input validation, output escaping, SSRF resistance |
| Endpoint | `test_app.py`, `test_tax.py` | Routes, validation, degradation |
| **Total** | | **152 passing · 100% line coverage** |

Run: `uv run pytest --cov` · Audit: `uv run pip-audit`

---

## 2. Findings summary

| # | Finding | Severity | OWASP / CWE | Status |
|---|---|---|---|---|
| F-1 | Vulnerable dependencies (14 CVEs: `python-multipart`, `starlette`, `idna`, `python-dotenv`) | **High** | A06:2021 Vulnerable Components | ✅ Fixed (upgraded; re-audit clean) |
| F-2 | No application-level rate limiting (DoS / outbound-API abuse) | **Medium** | A05:2021 Misconfiguration / CWE-770 | ⚠️ Accepted — surface reduced by the offline index |
| F-3 | Missing HTTP security headers (CSP, X-Content-Type-Options, etc.) | **Low** | A05:2021 Misconfiguration | ⚠️ Recommended |
| F-4 | `json.dumps` injected into inline `<script>` without `/`/`<` escaping | **Low** | A03:2021 Injection / CWE-79 | ⚠️ Accepted (data is static) |
| F-5 | Reflected user input (XSS) on `/compare` and `/tax` | **Informational** | A03:2021 Injection | ✅ Mitigated (autoescape + validation) |
| F-6 | SSRF via city/country fields | **Informational** | A10:2021 SSRF | ✅ Mitigated (fixed host, query params) |
| F-7 | Verbose error messages | **Informational** | A04:2021 Insecure Design | ✅ Mitigated (typed handlers) |
| F-8 | CI/CD secret handling (`HF_TOKEN` in the HF sync workflow) | **Informational** | A05:2021 Misconfiguration / CWE-798 | ✅ Mitigated (encrypted secret, masked, not hardcoded) |

---

## 3. Detailed findings

### F-1 — Vulnerable dependencies · High · ✅ Fixed
`pip-audit` initially reported **14 known vulnerabilities** across 4 packages:

| Package | Was | CVEs | Fixed at |
|---|---|---|---|
| `python-multipart` | 0.0.20 | 6 (multipart DoS / parsing) | **0.0.31+** |
| `starlette` | 0.50.0 | 6 (DoS, multipart) | **1.3.1+** |
| `idna` | 3.11 | 1 | **3.15+** |
| `python-dotenv` | 1.2.1 | 1 | **1.2.2+** |

`python-multipart` is **directly on the attack path** — it parses every POST form body. **Remediation applied:** `uv lock --upgrade`; `python-multipart>=0.0.31` pinned; `pip-audit` in the dev group for ongoing scanning. The scraper later moved to `curl_cffi` (which bundles `libcurl-impersonate`); a fresh `pip-audit` on the current lockfile still reports **"No known vulnerabilities found."**

### F-2 — No rate limiting · Medium · ⚠️ Accepted (surface reduced)
`POST /compare` can trigger outbound requests (Numbeo scrape + FX with cookie prime, retries, and fallbacks), so in principle an attacker could exhaust host resources or get the egress IP throttled by a third party (amplification).

**What changed:** cost-of-living comparisons are now served from a **committed offline index** (`app/data/numbeo_index.json`). For the ~360 covered cities, `/compare` performs **pure local computation with zero outbound calls** — a live scrape only fires for an *un-cached* city. FX still makes outbound calls for cross-currency pairs (mitigated by a 1-hour spot cache). Net effect: the Numbeo-amplification vector is largely eliminated for normal use; only FX and the rare uncached scrape remain.

**Why accepted:** personal/hobby tool on a platform (Render / Hugging Face Spaces) with basic edge protection; no data at risk. **Recommendation for heavy public exposure:** add `slowapi` (per-IP limit on the POST endpoints). Now lower priority than before given the index.

### F-3 — Missing security headers · Low · ⚠️ Recommended
Responses lack `Content-Security-Policy`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, and `X-Frame-Options`/`frame-ancestors`. Impact is limited (no auth, no cookies, no sensitive data), but a CSP would further harden against XSS and clickjacking. **Recommendation:** a small middleware setting `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, and a CSP allowing only the inline styles/scripts this app already uses.

### F-4 — JSON in inline `<script>` · Low · ⚠️ Accepted
`index.html` and `tax.html` inject `{{ ccy_json|safe }}` / `{{ cap_json|safe }}` into a `<script>`. `json.dumps` does not escape `<`, `>`, or `/`, so a value containing `</script>` could break out. **Why accepted:** these blobs are `json.dumps` of **static, server-controlled** dicts loaded from `app/data/*.json` (currency codes, capital names) — no user input flows in. **Recommendation (defence-in-depth):** switch to `tojson` (which escapes `<`/`>`/`&`) if those files ever become user-editable.

### F-5 — Reflected XSS · Informational · ✅ Mitigated
Both POST endpoints (`/compare`, `/tax`) apply the same two layers:
1. **Input validation** — `_validate_place` accepts only letters (incl. accented), spaces, hyphens, apostrophes, periods, so `<`, `>`, `"`, `;`, `|`, `$` are rejected before reflection; salary/percentage fields are numeric-and-range validated.
2. **Output encoding** — Jinja2 autoescaping (on by default for `.html`) encodes any reflected value, including error strings that echo user input. Verified by `test_reflected_input_is_html_escaped`: a `<script>` payload appears only as `&lt;script&gt;`.

### F-6 — SSRF · Informational · ✅ Mitigated
City/country names reach Numbeo as **URL query parameters** against a **hard-coded host** (`www.numbeo.com`) — including the `curl_cffi` warm-up GET and the compare request — so user input cannot redirect the request elsewhere. FX URLs use **currency codes derived from a fixed map**, never raw user text. `test_scraper_only_hits_numbeo_host` asserts the request host is unchanged regardless of input (even a URL-shaped city value stays a query param).

### F-7 — Error handling · Informational · ✅ Mitigated
`/compare` uses typed handlers (`ValueError`, `httpx.HTTPStatusError`, `httpx.RequestError`, `RuntimeError`, generic); `/tax` catches `ValueError`. User-facing messages are friendly; full tracebacks are logged server-side only, never returned. The generic catch-all returns "An unexpected error occurred."

### F-8 — CI/CD secret handling · Informational · ✅ Mitigated
The `sync-to-huggingface` workflow authenticates to the HF Space with an `HF_TOKEN`. **Controls:** the token is a GitHub **encrypted repo secret** (not committed, not a repo variable); it's referenced via `${{ secrets.HF_TOKEN }}` and GitHub masks secret values in logs; the workflow does not enable shell tracing (`set -x`) that could echo the token-in-URL; non-sensitive config (space, username) lives in repo **variables**, not secrets. **Recommendation (defence-in-depth):** scope the token to write-access on that Space only, and rotate periodically.

---

## 4. Positive controls observed
- Input allow-listing on place fields; numeric/range validation on salary, gross, and sliders.
- **Cost data served from a committed offline index — most requests make no outbound calls at all**, shrinking the third-party-amplification surface.
- FX failure degrades gracefully (cost indices still shown; savings skipped with a notice) instead of erroring.
- No secrets in the repo, no database, no authentication surface, no file uploads.
- CI secrets held as GitHub encrypted secrets; deploy is a single Docker image with no env secrets required at runtime.
- Outbound timeouts (15–20s) and bounded retries prevent hung requests.

## 5. Recommendations (prioritised)
1. **(Done)** Keep dependencies patched — `pip-audit` is wired into the dev group; run it in CI.
2. **Low–Medium** — Add per-IP rate limiting (`slowapi`) on the POST endpoints before heavy public exposure (lower priority now that the index removes most outbound calls).
3. **Low** — Add a security-headers middleware (CSP, nosniff, referrer-policy).
4. **Low** — Use `tojson`/escaping for the inline JSON blobs as defence-in-depth.
5. **Low** — Scope/rotate the HF write token.

---

_Generated as part of a defensive security review. Severity ratings follow CVSS v3.1 qualitative bands; mappings reference OWASP Top 10 (2021)._
