"""Tests for the transform step, using small made-up markets (no real data, no network)."""
import json

import pandas as pd
import pandera.errors
import pytest

from transform import normalize
from transform.schemas import markets_schema, prices_schema


def raw_market(market_id="1", outcome_prices='["1", "0"]', status="resolved", **fields):
    """A raw Polymarket market with only the fields normalize uses."""
    market = {
        "id": market_id,
        "question": "Will it happen?",
        "events": [{"id": "e1"}],
        "startDate": "2025-01-01T00:00:00Z",
        "createdAt": "2024-12-31T00:00:00Z",
        "endDate": "2025-12-31T00:00:00Z",
        "closedTime": "2025-01-10 12:00:00+00",
        "outcomePrices": outcome_prices,
        "umaResolutionStatus": status,
        "volumeNum": 2_000_000,
    }
    market.update(fields)
    return market


def test_market_columns_and_ids():
    markets = normalize.normalize_polymarket_markets([raw_market()])

    row = markets.iloc[0]
    assert row["market_id"] == "poly:1"
    assert row["event_id"] == "poly:e1"
    assert row["platform"] == "polymarket"
    assert row["close_time"] == pd.Timestamp("2025-01-10 12:00", tz="UTC")  # closedTime, not endDate
    markets_schema.validate(markets)  # has exactly the standard columns and types


@pytest.mark.parametrize("outcome_prices, expected", [
    ('["1", "0"]', 1),
    ('["0", "1"]', 0),
    ('["0.99999995", "0.00000005"]', 1),  # rounding noise still counts as a clear result
])
def test_outcome_from_final_prices(outcome_prices, expected):
    markets = normalize.normalize_polymarket_markets([raw_market(outcome_prices=outcome_prices)])
    assert markets["outcome"].tolist() == [expected]


def test_markets_without_a_clear_result_are_dropped():
    raw = [
        raw_market("cancelled", outcome_prices='["0.5", "0.5"]'),
        raw_market("not-final", status="proposed"),
        raw_market("ok"),
    ]
    markets = normalize.normalize_polymarket_markets(raw)
    assert markets["market_id"].tolist() == ["poly:ok"]


def test_open_time_falls_back_to_created_at():
    markets = normalize.normalize_polymarket_markets([raw_market(startDate=None)])
    assert markets["open_time"].iloc[0] == pd.Timestamp("2024-12-31", tz="UTC")


def test_drop_short_markets():
    raw = [
        raw_market("23h", startDate="2025-01-09T13:00:00Z"),  # closes 2025-01-10 12:00 -> 23 hours
        raw_market("24h", startDate="2025-01-09T12:00:00Z"),  # exactly 1 day is kept
    ]
    markets = normalize.drop_short_markets(normalize.normalize_polymarket_markets(raw))
    assert markets["market_id"].tolist() == ["poly:24h"]


def test_prices_combine_daily_and_hourly_and_clip(tmp_path):
    (tmp_path / "daily").mkdir()
    (tmp_path / "hourly").mkdir()
    (tmp_path / "daily" / "1.json").write_text(json.dumps({"history": [{"t": 1736467200, "p": 0.6}]}))
    (tmp_path / "hourly" / "1.json").write_text(json.dumps({"history": [{"t": 1736510400, "p": 1.005}]}))
    markets = pd.DataFrame({"market_id": ["poly:1", "poly:no-files"]})  # a market without files is skipped

    prices = normalize.normalize_polymarket_prices(markets, prices_dir=tmp_path)

    assert prices["fidelity"].tolist() == ["daily", "hourly"]
    assert prices["price"].tolist() == [0.6, 1.0]  # 1.005 was clipped to 1
    assert prices["time"].iloc[0] == pd.Timestamp("2025-01-10", tz="UTC")
    prices_schema.validate(prices)


def test_schema_rejects_impossible_values():
    markets = normalize.normalize_polymarket_markets([raw_market()])
    markets.loc[0, "outcome"] = 2
    with pytest.raises(pandera.errors.SchemaError):
        markets_schema.validate(markets)


def test_scheduled_ends_come_from_end_date_not_close_time():
    ends = normalize.normalize_polymarket_scheduled_ends([raw_market("1"), raw_market("2", endDate=None)])
    assert ends["market_id"].tolist() == ["poly:1"]  # a market without an end date is left out
    assert ends["scheduled_end"].iloc[0] == pd.Timestamp("2025-12-31", tz="UTC")  # endDate, not closedTime
