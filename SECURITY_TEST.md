# Security & Test Report — relo-calculator

_Defensive security review and test analysis of the relocation cost/savings calculator._

## 1. Scope & methodology

| Item | Detail |
|---|---|
| Target | FastAPI app (`app/`) — form-driven, server-rendered HTML, no auth, no DB |
| Attack surface | One unauthenticated `POST /compare` endpoint + static assets; outbound HTTP to Numbeo / Yahoo / Frankfurter / currency-api |
| Methods | Static review, automated dependency audit (`pip-audit`), and **automated security tests** (`tests/test_security.py`) |
| Severity scale | Qualitative **CVSS v3.1** bands (Critical / High / Medium / Low / Informational) |
| Frameworks referenced | OWASP Top 10 (2021), CWE |

### Test layers run

| Layer | File | Count (approx) | Purpose |
|---|---|---|---|
| Unit | `test_model.py`, `test_fx.py`, `test_currencies.py`, `test_data_sources.py` | ~60 | Pure logic: model math, EMA blend, parsing, lookups |
| Integration | `test_integration.py` | 5 | Full request → model → render pipeline (mocked I/O) |
| Regression | `test_regression.py` | 6 | Pins previously-fixed bugs |
| Security | `test_security.py` | ~12 | Input validation, output escaping, SSRF resistance |
| Endpoint | `test_app.py` | ~16 | Routes, validation, degradation |
| **Total** | | **111 passing** | **~92% line coverage** |

Run: `uv run pytest --cov` · Audit: `uv run pip-audit`

---

## 2. Findings summary

| # | Finding | Severity | OWASP / CWE | Status |
|---|---|---|---|---|
| F-1 | Vulnerable dependencies (14 CVEs: `python-multipart`, `starlette`, `idna`, `python-dotenv`) | **High** | A06:2021 Vulnerable Components | ✅ Fixed (upgraded) |
| F-2 | No application-level rate limiting (DoS / outbound-API abuse) | **Medium** | A05:2021 Misconfiguration / CWE-770 | ⚠️ Accepted (see notes) |
| F-3 | Missing HTTP security headers (CSP, X-Content-Type-Options, etc.) | **Low** | A05:2021 Misconfiguration | ⚠️ Recommended |
| F-4 | `json.dumps` injected into inline `<script>` without `/`/`<` escaping | **Low** | A03:2021 Injection / CWE-79 | ⚠️ Accepted (data is static) |
| F-5 | Reflected user input (XSS) | **Informational** | A03:2021 Injection | ✅ Mitigated (autoescape + validation) |
| F-6 | SSRF via city/country fields | **Informational** | A10:2021 SSRF | ✅ Mitigated (fixed host, query params) |
| F-7 | Verbose error messages | **Informational** | A04:2021 Insecure Design | ✅ Mitigated (typed handlers) |

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

`python-multipart` is **directly on the attack path** — it parses every `POST /compare` form body. **Remediation applied:** `uv lock --upgrade`; `python-multipart>=0.0.31` pinned in `pyproject.toml`; `pip-audit` added to the dev group for ongoing scanning. Re-audit: **"No known vulnerabilities found."** All 111 tests still pass on the upgraded stack.

### F-2 — No rate limiting · Medium · ⚠️ Accepted
Each `POST /compare` triggers outbound requests (Numbeo + up to 3 Yahoo attempts + cookie prime + fallbacks). An attacker could:
- exhaust the host's CPU/sockets, or
- get the deployment's egress IP throttled/blocked by Numbeo/Yahoo (amplification of a third party's rate limit).

**Why accepted:** personal/hobby tool behind a platform (Render/Koyeb) that provides basic edge protection; no data at risk. **Recommendation for public exposure:** add `slowapi` (per-IP limit, e.g. 10/min on `/compare`) and an in-process cache of `(cities)` results. The FX layer already caches spot lookups for 1h.

### F-3 — Missing security headers · Low · ⚠️ Recommended
Responses lack `Content-Security-Policy`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, and `X-Frame-Options`/`frame-ancestors`. Impact is limited (no auth, no cookies, no sensitive data), but a CSP would further harden against XSS and clickjacking. **Recommendation:** a small middleware setting `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, and a CSP allowing only inline styles/scripts this app already uses.

### F-4 — JSON in inline `<script>` · Low · ⚠️ Accepted
`index.html` injects `{{ ccy_json|safe }}` / `{{ cap_json|safe }}` into a `<script>`. `json.dumps` does not escape `<`, `>`, or `/`, so a value containing `</script>` could break out. **Why accepted:** both blobs are `json.dumps` of **static, server-controlled** dicts loaded from `app/data/*.json` — no user input flows in, and the data contains only currency codes / city names. **Recommendation (defence-in-depth):** escape `<`/`>`/`&` (or use `tojson`) if these files ever become user-editable.

### F-5 — Reflected XSS · Informational · ✅ Mitigated
Two layers:
1. **Input validation** — `_validate_place` accepts only letters (incl. accented), spaces, hyphens, apostrophes, periods, so `<`, `>`, `"`, `;`, `|`, `$` are rejected before reflection.
2. **Output encoding** — Jinja2 autoescaping (on by default for `.html`) encodes any reflected value. Verified by `test_reflected_input_is_html_escaped`: a `<script>` payload appears only as `&lt;script&gt;`.

### F-6 — SSRF · Informational · ✅ Mitigated
City/country names go to Numbeo as **URL query parameters** against a **hard-coded host** (`www.numbeo.com`) — they cannot redirect the request elsewhere. FX URLs use **currency codes derived from a fixed map**, never raw user text. `test_scraper_only_hits_numbeo_host` asserts the request host is unchanged regardless of input.

### F-7 — Error handling · Informational · ✅ Mitigated
A prior bare `except` was replaced with typed handlers (`ValueError`, `httpx.HTTPStatusError`, `httpx.RequestError`, `RuntimeError`, generic). User-facing messages are friendly; full tracebacks are logged server-side only, not returned. The generic catch-all returns "An unexpected error occurred."

---

## 4. Positive controls observed
- Input allow-listing on place fields; numeric/range validation on salary and sliders.
- FX failure degrades gracefully (cost indices still shown; savings skipped with notice) instead of erroring.
- No secrets, no database, no authentication surface, no file uploads.
- Outbound timeouts (15s) and bounded retries prevent hung requests.

## 5. Recommendations (prioritised)
1. **(Done)** Keep dependencies patched — `pip-audit` is wired into the dev group; run it in CI.
2. **Medium** — Add per-IP rate limiting (`slowapi`) + cache Numbeo results before public exposure.
3. **Low** — Add a security-headers middleware (CSP, nosniff, referrer-policy).
4. **Low** — Use `tojson`/escaping for the inline JSON blobs as defence-in-depth.

---

_Generated as part of a defensive security review. Severity ratings follow CVSS v3.1 qualitative bands; mappings reference OWASP Top 10 (2021)._
