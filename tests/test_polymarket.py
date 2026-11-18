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

    def fake_fetch_price_history(market, refresh=False):
        if market["id"] == "bad":
            raise RuntimeError("gave up")
        fetched.append(market["id"])

    monkeypatch.setattr(polymarket, "fetch_price_history", fake_fetch_price_history)

    failed = polymarket.fetch_all_price_histories(markets)

    assert failed == ["bad"]
    assert fetched == ["good"]  # the failure didn't stop the run, and no-tokens was skipped


def test_price_history_refresh_downloads_again(fake_prices, tmp_path):
    (tmp_path / "daily" / "123.json").write_text('{"history": []}')  # an earlier, empty download
    (tmp_path / "hourly" / "123.json").write_text('{"history": []}')

    polymarket.fetch_price_history(MARKET, refresh=True)

    assert len(fake_prices) == 2  # both files were downloaded again
    assert json.loads((tmp_path / "daily" / "123.json").read_text())["history"]  # and overwritten


def not_json_response(status_code=200, text=b"<html>Bad gateway</html>"):
    """A response whose body isn't JSON (e.g. a proxy's error page sent with status 200)."""
    response = make_response(status_code, None)
    response._content = text
    return response


def test_get_with_retry_retries_a_body_that_isnt_json(fake_http, sleeps):
    calls = fake_http([not_json_response(), make_response(200, [{"id": "1"}])])

    assert polymarket.get_with_retry(polymarket.GAMMA_URL, params={}) == [{"id": "1"}]
    assert len(calls) == 2 and sleeps == [1]  # the same backoff as a 5xx


def test_get_with_retry_retries_a_server_error(fake_http, sleeps):
    calls = fake_http([make_response(500, {"error": "oops"}), make_response(200, [{"id": "1"}])])

    assert polymarket.get_with_retry(polymarket.GAMMA_URL, params={}) == [{"id": "1"}]
    assert len(calls) == 2 and sleeps == [1]


def test_get_with_retry_gives_up_with_runtime_error_when_no_answer_is_json(fake_http, sleeps):
    # The error the callers handle (e.g. fetch_all_price_histories), never a JSON decoding error
    calls = fake_http([not_json_response() for _ in range(5)])

    with pytest.raises(RuntimeError, match="after 5 attempts"):
        polymarket.get_with_retry(polymarket.GAMMA_URL, params={})

    assert len(calls) == 5
    assert sleeps == [1, 2, 4, 8]  # no wait after the last attempt


@pytest.fixture
def new_york_clock(monkeypatch):
    """Run the test with the machine's local time zone set to New York (UTC-5 in January), then restore it."""
    monkeypatch.setenv("TZ", "America/New_York")
    polymarket.time.tzset()
    yield
    monkeypatch.undo()
    polymarket.time.tzset()


def test_a_close_time_without_an_offset_is_read_as_utc(fake_prices, new_york_clock):
    polymarket.fetch_price_history({**MARKET, "closedTime": "2025-01-03 00:00:00"})

    _, hourly = fake_prices
    assert hourly["endTs"] == CLOSE_TS  # as with "+00", never five hours later (the machine's time zone)


@pytest.mark.parametrize("error", [requests.exceptions.ChunkedEncodingError, requests.exceptions.ContentDecodingError])
def test_get_with_retry_retries_a_response_cut_off_mid_body(monkeypatch, sleeps, error):
    answers = [error("connection broken"), make_response(200, [{"id": "1"}])]

    def fake_get(url, params, timeout):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(polymarket.session, "get", fake_get)
    assert polymarket.get_with_retry(polymarket.GAMMA_URL, params={}) == [{"id": "1"}]
    assert sleeps == [1]
