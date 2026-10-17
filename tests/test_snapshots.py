"""Tests for the snapshot rule, using small hand-made tables."""
import pandas as pd

from transform.schemas import snapshots_schema
from transform.snapshots import take_snapshots


def utc(text):
    return pd.Timestamp(text, tz="UTC").as_unit("us")


def market(market_id="poly:1", open_time="2025-01-01", close_time="2025-01-10 12:00"):
    return {"market_id": market_id, "open_time": utc(open_time), "close_time": utc(close_time)}


def tables(markets, prices):
    """Build markets and prices DataFrames with the same column types as the real ones."""
    prices = pd.DataFrame(prices, columns=["market_id", "time", "price", "fidelity"])
    prices["time"] = prices["time"].map(utc)
    return pd.DataFrame(markets), prices


def test_uses_latest_price_before_target_never_after():
    # 1-day target is 2025-01-09 12:00
    markets, prices = tables([market()], [
        ("poly:1", "2025-01-09 10:00", 0.40, "hourly"),
        ("poly:1", "2025-01-09 11:00", 0.45, "hourly"),  # latest at or before target: use this
        ("poly:1", "2025-01-09 12:30", 0.90, "hourly"),  # closer, but after the target: never use
    ])

    snapshots = take_snapshots(markets, prices, horizons_days=[1])

    assert snapshots["price"].tolist() == [0.45]
    assert snapshots["price_time"].iloc[0] == utc("2025-01-09 11:00")
    snapshots_schema.validate(snapshots)


def test_price_exactly_at_target_is_used():
    markets, prices = tables([market()], [("poly:1", "2025-01-09 12:00", 0.3, "hourly")])
    assert take_snapshots(markets, prices, horizons_days=[1])["price"].tolist() == [0.3]


def test_hourly_wins_a_tie_with_daily():
    markets, prices = tables([market(close_time="2025-01-10 00:00")], [
        ("poly:1", "2025-01-09 00:00", 0.50, "hourly"),
        ("poly:1", "2025-01-09 00:00", 0.51, "daily"),
    ])
    assert take_snapshots(markets, prices, horizons_days=[1])["price"].tolist() == [0.50]


def test_skips_horizon_from_before_the_market_opened():
    # Open for 3 days: a 1-day snapshot exists, a 7-day one doesn't
    markets, prices = tables([market(open_time="2025-01-07 12:00")], [
        ("poly:1", "2025-01-07 12:00", 0.2, "daily"),
        ("poly:1", "2025-01-09 12:00", 0.3, "hourly"),
    ])
    snapshots = take_snapshots(markets, prices, horizons_days=[1, 7])
    assert snapshots["horizon_days"].tolist() == [1]


def test_skips_price_older_than_one_day():
    markets, prices = tables([market()], [("poly:1", "2025-01-08 11:00", 0.4, "daily")])  # 25h before target
    assert take_snapshots(markets, prices, horizons_days=[1]).empty


def test_markets_never_use_each_others_prices():
    markets, prices = tables([market("poly:1"), market("poly:2")], [
        ("poly:1", "2025-01-09 11:00", 0.1, "hourly"),
        ("poly:2", "2025-01-09 11:30", 0.9, "hourly"),
    ])
    snapshots = take_snapshots(markets, prices, horizons_days=[1])
    assert dict(zip(snapshots["market_id"], snapshots["price"])) == {"poly:1": 0.1, "poly:2": 0.9}
