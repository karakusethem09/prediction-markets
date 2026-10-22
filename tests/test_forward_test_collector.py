"""Tests for the forward-test collector. HTTP is always mocked: no real API calls."""
import pandas as pd
import pytest

from forward_test import collector

RUN = pd.Timestamp("2026-10-21 09:00", tz="UTC")


def raw_market(market_id="1", **fields):
    market = {
        "id": market_id, "question": "Will it happen?", "events": [{"id": "e1"}],
        "outcomes": '["Yes", "No"]', "clobTokenIds": '["yes-token", "no-token"]', "enableOrderBook": True,
        "endDate": "2026-10-28T12:00:00Z", "startDate": "2026-09-01T00:00:00Z", "volumeNum": 250_000,
        "liquidityNum": 20_000,
    }
    market.update(fields)
    return market


@pytest.fixture
def fake_api(monkeypatch, tmp_path):
    """Replace the retry helper with canned responses by URL, and keep raw files in tmp_path."""
    monkeypatch.setattr(collector, "RAW_DIR", tmp_path)
    responses, calls = {}, []

    def fake_get_with_retry(url, params):
        calls.append((url, params))
        response = responses[url]
        return response.pop(0) if isinstance(response, list) else response

    monkeypatch.setattr(collector, "get_with_retry", fake_get_with_retry)
    return responses, calls


def test_to_candidate_keeps_binary_markets_with_an_order_book():
    candidate = collector.to_candidate(raw_market())
    assert candidate["market_id"] == "poly:1" and candidate["event_id"] == "poly:e1"
    assert candidate["token_id"] == "yes-token"
    assert candidate["scheduled_end"] == pd.Timestamp("2026-10-28 12:00", tz="UTC")
    assert collector.to_candidate(raw_market(enableOrderBook=False)) is None
    assert collector.to_candidate(raw_market(outcomes='["A", "B", "C"]', clobTokenIds='["a", "b", "c"]')) is None


def test_open_markets_follows_cursors_and_saves_every_page(fake_api, tmp_path):
    responses, calls = fake_api
    responses[collector.GAMMA_URL] = [{"markets": [raw_market("1")], "next_cursor": "c1"},
                                      {"markets": [raw_market("2")]}]

    markets = collector.open_markets_near_entry(RUN, RUN)

    assert [m["id"] for m in markets] == ["1", "2"]
    assert calls[1][1]["after_cursor"] == "c1"
    assert calls[0][1]["volume_num_min"] == 100_000 and calls[0][1]["closed"] == "false"
    assert len(list(tmp_path.rglob("markets_page_*.json"))) == 2  # raw responses kept


def test_order_book_turns_text_into_numbers(fake_api):
    responses, _ = fake_api
    responses[collector.BOOK_URL] = {"bids": [{"price": "0.22", "size": "30"}], "asks": [{"price": "0.25", "size": "10.5"}],
                                     "tick_size": "0.01", "min_order_size": "5", "last_trade_price": "0.24"}
    book = collector.order_book("yes-token", RUN)
    assert book["bids"] == [(0.22, 30.0)] and book["asks"] == [(0.25, 10.5)]
    assert (book["tick_size"], book["min_order_size"], book["last_trade_price"]) == (0.01, 5.0, 0.24)


@pytest.mark.parametrize("market, expected", [
    ({"closed": False, "umaResolutionStatus": None, "outcomePrices": '["0.3", "0.7"]', "volumeNum": 1},
     {"closed": False, "final": False, "outcome": None}),
    ({"closed": True, "umaResolutionStatus": "resolved", "outcomePrices": '["1", "0"]', "volumeNum": 1},
     {"closed": True, "final": True, "outcome": 1}),
    ({"closed": True, "umaResolutionStatus": "resolved", "outcomePrices": '["0.5", "0.5"]', "volumeNum": 1},
     {"closed": True, "final": True, "outcome": None}),
])
def test_market_result(fake_api, market, expected):
    responses, _ = fake_api
    responses[collector.MARKET_URL.format(id="7")] = market
    result = collector.market_result("poly:7", RUN)
    assert {k: result[k] for k in expected} == expected
