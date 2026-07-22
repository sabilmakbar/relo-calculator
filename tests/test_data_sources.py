"""Tests for Numbeo scraping and parsing."""
import pytest

import app.data_sources as ds
from app.data_sources import (
    HOME_URL,
    _index_key,
    extract_city_differences,
    get_percentage_diff,
)


# ── Fake curl_cffi session ────────────────────────────────────────
# curl_cffi uses libcurl, not httpx, so respx can't intercept it. Instead we
# stub _new_session() with a fake that replays canned (status, html) responses.

class _FakeResp:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class _FakeSession:
    def __init__(self, compare_responses):
        self._compare = list(compare_responses)
        self.compare_calls = 0
        self.requests = []  # (url, params) for every GET

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, **kwargs):
        self.requests.append((url, params))
        if url == HOME_URL:
            return _FakeResp(200, "")  # warm-up
        self.compare_calls += 1
        return self._compare.pop(0)


def _install(monkeypatch, *compare_responses):
    """Point _new_session at a fake replaying the given compare responses."""
    session = _FakeSession(compare_responses)
    monkeypatch.setattr(ds, "_new_session", lambda: session)
    return session


async def _noop_sleep(_seconds):
    """Stand-in for asyncio.sleep so retry tests don't wait on real backoff."""
    return None


# ── extract_city_differences (pure) ───────────────────────────────

def test_extract_higher():
    out = extract_city_differences("Cost of Living in X is 134.5% higher than in Y")
    assert out == {"valuePct": 134.5, "direction": "higher"}


def test_extract_lower_is_negative():
    out = extract_city_differences("Rent Prices in X are 25.5% lower than in Y")
    assert out["valuePct"] == -25.5
    assert out["direction"] == "lower"


def test_extract_unparseable_raises():
    with pytest.raises(ValueError, match="Could not parse"):
        extract_city_differences("totally unrelated text")


# ── get_percentage_diff (stubbed session) ─────────────────────────

async def test_get_percentage_diff_success(monkeypatch, numbeo_html):
    _install(monkeypatch, _FakeResp(200, numbeo_html))
    out = await get_percentage_diff("Malaysia", "Kuala Lumpur", "Singapore", "Singapore")
    assert out["col_excl_rent"] == {"valuePct": 134.5, "direction": "higher"}
    assert out["rent"] == {"valuePct": 409.4, "direction": "higher"}
    assert out["city_from"] == "Kuala Lumpur"
    assert out["city_to"] == "Singapore"


async def test_get_percentage_diff_lower(monkeypatch, numbeo_html_lower):
    _install(monkeypatch, _FakeResp(200, numbeo_html_lower))
    out = await get_percentage_diff("Malaysia", "Kuala Lumpur", "Malaysia", "Penang")
    assert out["col_excl_rent"]["valuePct"] == -12.0
    assert out["rent"]["valuePct"] == -25.5


async def test_unknown_city_raises_valueerror(monkeypatch, numbeo_html_unknown_city):
    _install(monkeypatch, _FakeResp(200, numbeo_html_unknown_city))
    with pytest.raises(ValueError, match="doesn't recognise"):
        await get_percentage_diff("Malaysia", "Atlantis", "Singapore", "Singapore")


async def test_missing_table_raises_runtimeerror(monkeypatch, numbeo_html_no_table):
    _install(monkeypatch, _FakeResp(200, numbeo_html_no_table))
    with pytest.raises(RuntimeError, match="table not found"):
        await get_percentage_diff("Malaysia", "Kuala Lumpur", "Singapore", "Singapore")


async def test_non_retryable_http_error_raises(monkeypatch):
    _install(monkeypatch, _FakeResp(404))
    with pytest.raises(RuntimeError, match="HTTP 404"):
        await get_percentage_diff("Malaysia", "Kuala Lumpur", "Singapore", "Singapore")


async def test_persistent_503_retries_then_raises(monkeypatch):
    # Don't actually sleep through the backoff during tests.
    monkeypatch.setattr("app.data_sources.asyncio.sleep", _noop_sleep)
    session = _install(
        monkeypatch, _FakeResp(503), _FakeResp(503), _FakeResp(503)
    )
    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        await get_percentage_diff("Malaysia", "Kuala Lumpur", "Singapore", "Singapore")
    assert session.compare_calls == 3  # retried up to _MAX_ATTEMPTS


async def test_transient_503_then_success(monkeypatch, numbeo_html):
    monkeypatch.setattr("app.data_sources.asyncio.sleep", _noop_sleep)
    session = _install(
        monkeypatch, _FakeResp(503), _FakeResp(200, numbeo_html)
    )
    out = await get_percentage_diff("Malaysia", "Kuala Lumpur", "Singapore", "Singapore")
    assert out["col_excl_rent"] == {"valuePct": 134.5, "direction": "higher"}
    assert session.compare_calls == 2


# ── index layer ───────────────────────────────────────────────────

def test_index_key_normalises_case_and_whitespace():
    assert _index_key("Malaysia", " Kuala Lumpur ") == "malaysia|kuala lumpur"


async def test_index_hit_computes_without_network(monkeypatch):
    ds._INDEX[_index_key("Malaysia", "Kuala Lumpur")] = {"col": 40.0, "rent": 12.0}
    ds._INDEX[_index_key("Singapore", "Singapore")] = {"col": 92.0, "rent": 36.0}
    # If it hit the network the fake would raise (no responses queued).
    session = _install(monkeypatch)
    out = await get_percentage_diff("malaysia", "KUALA LUMPUR", "singapore", "Singapore")
    # ratio idx_B/idx_A: col 92/40 → +130%, rent 36/12 → +200%
    assert out["col_excl_rent"] == {"valuePct": 130.0, "direction": "higher"}
    assert out["rent"] == {"valuePct": 200.0, "direction": "higher"}
    assert out["city_from"] == "KUALA LUMPUR" and out["city_to"] == "Singapore"
    assert session.compare_calls == 0


async def test_index_miss_falls_through_to_scrape(monkeypatch, numbeo_html):
    # KL is indexed but Penang is not → the pair can't be computed → live scrape.
    ds._INDEX[_index_key("Malaysia", "Kuala Lumpur")] = {"col": 40.0, "rent": 12.0}
    session = _install(monkeypatch, _FakeResp(200, numbeo_html))
    out = await get_percentage_diff("Malaysia", "Penang", "Singapore", "Singapore")
    assert out["col_excl_rent"] == {"valuePct": 134.5, "direction": "higher"}
    assert session.compare_calls == 1


# ── coverage: fallback / error branches ───────────────────────────

def test_load_index_missing_file_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(ds, "_INDEX_PATH", tmp_path / "does_not_exist.json")
    assert ds._load_index() == {}


def test_new_session_returns_impersonating_session():
    from curl_cffi.requests import AsyncSession
    assert isinstance(ds._new_session(), AsyncSession)


async def test_warmup_failure_is_ignored(monkeypatch, numbeo_html):
    """A failed cookie warm-up must not abort the real request."""
    class _WarmupFails:
        compare_calls = 0
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False
        async def get(self, url, params=None, **kwargs):
            if url == HOME_URL:
                raise RuntimeError("warm-up boom")
            type(self).compare_calls += 1
            return _FakeResp(200, numbeo_html)
    monkeypatch.setattr(ds, "_new_session", lambda: _WarmupFails())
    out = await get_percentage_diff("Malaysia", "Kuala Lumpur", "Singapore", "Singapore")
    assert out["col_excl_rent"] == {"valuePct": 134.5, "direction": "higher"}


async def test_row_without_td_is_skipped(monkeypatch):
    html = """<table class="table_indices_diff">
      <tr><th>Index</th><th>Difference</th></tr>
      <tr><td>Cost of Living in B is 10.0% higher than in A</td></tr>
      <tr><td>Rent Prices in B are 20.0% higher than in A</td></tr>
    </table>"""
    _install(monkeypatch, _FakeResp(200, html))
    out = await get_percentage_diff("A", "A", "B", "B")
    assert out["col_excl_rent"]["valuePct"] == 10.0
    assert out["rent"]["valuePct"] == 20.0


async def test_table_without_col_or_rent_rows_raises(monkeypatch):
    html = """<table class="table_indices_diff">
      <tr><td>Some unrelated row</td></tr>
    </table>"""
    _install(monkeypatch, _FakeResp(200, html))
    with pytest.raises(RuntimeError, match="Unable to extract"):
        await get_percentage_diff("A", "A", "B", "B")
