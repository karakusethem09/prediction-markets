"""Tests for the Polymarket extractor. HTTP is always mocked: no real API calls."""
import json

import pytest
import requests

from extract import polymarket


def make_response(status_code, body):
    """Build a real requests.Response, so the real raise_for_status() is used."""
    response = requests.Response()
    response.status_code = status_code
    response._content = json.dumps(body).encode()
    response.url = polymarket.GAMMA_URL
    return response


@pytest.fixture
def fake_http(monkeypatch):
    """Replace session.get with a fake that returns prepared responses in order.

    Usage: calls = fake_http([response1, response2]); after the test,
    calls holds the params of every request that was made.
    """
    def setup(responses):
        calls = []

        def fake_get(url, params, timeout):
            calls.append(params)
            return responses.pop(0)

        monkeypatch.setattr(polymarket.session, "get", fake_get)
        return calls

    return setup


@pytest.fixture
def sleeps(monkeypatch):
    """Record sleep times instead of actually sleeping, so tests run instantly."""
    waits = []
    monkeypatch.setattr(polymarket.time, "sleep", waits.append)
    return waits


def test_get_with_retry_returns_data_on_success(fake_http, sleeps):
    calls = fake_http([make_response(200, [{"id": "1"}])])

    data = polymarket.get_with_retry(polymarket.GAMMA_URL, params={})

    assert data == [{"id": "1"}]
    assert len(calls) == 1
    assert sleeps == []


def test_get_with_retry_retries_after_429(fake_http, sleeps):
    calls = fake_http([
        make_response(429, {"error": "rate limited"}),
        make_response(200, [{"id": "1"}]),
    ])

    data = polymarket.get_with_retry(polymarket.GAMMA_URL, params={})

    assert data == [{"id": "1"}]
    assert len(calls) == 2
    assert sleeps == [1]  # first backoff is 2**0 = 1 second


def test_get_with_retry_does_not_retry_404(fake_http, sleeps):
    calls = fake_http([make_response(404, {"error": "not found"})])

    with pytest.raises(requests.HTTPError):
        polymarket.get_with_retry(polymarket.GAMMA_URL, params={})

    assert len(calls) == 1  # a 404 is our mistake, so it must not be retried
    assert sleeps == []


def test_pagination_follows_cursor_and_stops_on_last_page(monkeypatch, tmp_path, sleeps):
    monkeypatch.setattr(polymarket, "RAW_DIR", tmp_path)  # don't touch the real data/raw
    # Three pages; the last one has no next_cursor, like the real API
    pages = [
        {"markets": [{"id": "1"}, {"id": "2"}], "next_cursor": "c1"},
        {"markets": [{"id": "3"}, {"id": "4"}], "next_cursor": "c2"},
        {"markets": [{"id": "5"}]},
    ]
    cursors_sent = []

    def fake_get_with_retry(url, params):
        cursors_sent.append(params.get("after_cursor"))
        return pages.pop(0)

    monkeypatch.setattr(polymarket, "get_with_retry", fake_get_with_retry)

    total = polymarket.fetch_resolved_markets()

    assert total == 5
    assert cursors_sent == [None, "c1", "c2"]  # each request uses the previous page's cursor
    assert len(list(tmp_path.glob("page_*.json"))) == 3  # every raw page saved


# A market as the API returns it (only the fields the price download uses)
MARKET = {
    "id": "123",
    "clobTokenIds": '["yes-token", "no-token"]',  # a JSON string, like the real API
    "closedTime": "2025-01-03 00:00:00+00",
}
CLOSE_TS = 1735862400  # 2025-01-03 00:00:00 UTC as unix seconds


@pytest.fixture
def fake_prices(monkeypatch, tmp_path, sleeps):
    """Send price files to a temp folder and record every request instead of making it."""
    monkeypatch.setattr(polymarket, "PRICES_DIR", tmp_path)
    (tmp_path / "daily").mkdir()
    (tmp_path / "hourly").mkdir()
    requests_made = []

    def fake_get_with_retry(url, params):
        requests_made.append(params)
        return {"history": [{"t": CLOSE_TS, "p": 0.5}]}

    monkeypatch.setattr(polymarket, "get_with_retry", fake_get_with_retry)
    return requests_made


def test_price_history_saves_daily_and_hourly(fake_prices, tmp_path):
    polymarket.fetch_price_history(MARKET)

    daily, hourly = fake_prices
    assert daily["market"] == "yes-token"  # the first outcome's token
    assert daily["interval"] == "max" and daily["fidelity"] == 1440
    assert hourly["fidelity"] == 60
    assert hourly["endTs"] == CLOSE_TS  # the window ends when the market closed
    assert hourly["endTs"] - hourly["startTs"] == 15 * 24 * 60 * 60  # and covers 15 days
    assert (tmp_path / "daily" / "123.json").exists()
    assert (tmp_path / "hourly" / "123.json").exists()


def test_price_history_skips_files_already_downloaded(fake_prices, tmp_path):
    (tmp_path / "daily" / "123.json").write_text("{}")
    (tmp_path / "hourly" / "123.json").write_text("{}")

    polymarket.fetch_price_history(MARKET)

    assert fake_prices == []  # nothing was requested again


def test_fetch_all_skips_markets_without_tokens_and_continues_after_a_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(polymarket, "PRICES_DIR", tmp_path)
    markets = [{"id": "no-tokens"}, {"id": "bad"}, {"id": "good"}]
    markets[1]["clobTokenIds"] = markets[2]["clobTokenIds"] = '["t"]'
    fetched = []

    def fake_fetch_price_history(market):
        if market["id"] == "bad":
            raise RuntimeError("gave up")
        fetched.append(market["id"])

    monkeypatch.setattr(polymarket, "fetch_price_history", fake_fetch_price_history)

    failed = polymarket.fetch_all_price_histories(markets)

    assert failed == ["bad"]
    assert fetched == ["good"]  # the failure didn't stop the run, and no-tokens was skipped
