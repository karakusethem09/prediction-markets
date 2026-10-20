"""Tests for the calibration math, using small made-up data."""
import pandas as pd
import pytest

from analysis.calibration import (
    calibration_table, describe_age, filter_snapshots, price_bin, same_markets_only, scores, wilson_interval,
)


def snapshots(prices, outcomes, horizon=1, event_ids=None):
    n = len(prices)
    return pd.DataFrame({
        "market_id": [f"m{i}" for i in range(n)],
        "horizon_days": horizon,
        "price": prices,
        "outcome": outcomes,
        "event_id": event_ids or [f"e{i}" for i in range(n)],
    })


def test_price_bins_include_edges():
    # 0.1 starts the second bucket; 1.0 goes in the top bucket, not an 11th one
    assert price_bin([0.0, 0.05, 0.1, 0.35, 0.99, 1.0]).tolist() == [0, 0, 1, 3, 9, 9]


def test_wilson_interval_known_value():
    low, high = wilson_interval(5, 10)
    assert low == pytest.approx(0.2366, abs=1e-4)
    assert high == pytest.approx(0.7634, abs=1e-4)


def test_wilson_interval_stays_between_0_and_1():
    low, high = wilson_interval([0, 3], [3, 3])  # never happened / always happened
    assert (low >= 0).all() and (high <= 1).all()


def test_calibration_table_counts_and_rates():
    data = snapshots(prices=[0.12, 0.18, 0.15, 0.85], outcomes=[1, 0, 0, 1])

    table = calibration_table(data).set_index("bin_label")

    assert table.loc["10–20%", "markets"] == 3
    assert table.loc["10–20%", "happened_rate"] == pytest.approx(1 / 3)
    assert table.loc["10–20%", "avg_price"] == pytest.approx(0.15)
    assert table.loc["80–90%", "markets"] == 1


def test_scores_perfect_forecaster():
    data = snapshots(prices=[1.0, 0.0, 1.0, 0.0], outcomes=[1, 0, 1, 0])
    result = scores(data).iloc[0]
    assert result["brier"] == 0
    assert result["brier_skill"] == 1
    assert result["calibration_error"] == 0


def test_scores_naive_forecaster_has_no_skill():
    # Always saying the base rate (50%) is exactly the naive guess: skill 0
    data = snapshots(prices=[0.5] * 4, outcomes=[1, 0, 1, 0], event_ids=["e1", "e1", "e2", "e2"])
    result = scores(data).iloc[0]
    assert result["brier"] == pytest.approx(0.25)
    assert result["brier_skill"] == pytest.approx(0)
    assert result["events"] == 2


def test_same_markets_only_keeps_markets_with_every_horizon():
    data = pd.concat([
        snapshots([0.5, 0.5], [1, 0], horizon=1),  # m0 and m1
        snapshots([0.5], [1], horizon=7),           # only m0
    ])
    assert set(same_markets_only(data)["market_id"]) == {"m0"}


def test_filter_snapshots():
    data = pd.DataFrame({
        "market_id": ["a", "b", "c"],
        "platform": ["polymarket", "polymarket", "other"],
        "volume_usd": [1_000_000.0, 5_000_000.0, 5_000_000.0],
        "close_time": pd.to_datetime(["2024-06-01", "2025-06-01", "2025-06-01"], utc=True),
    })

    assert filter_snapshots(data, min_volume=2_000_000)["market_id"].tolist() == ["b", "c"]
    assert filter_snapshots(data, platforms=["polymarket"])["market_id"].tolist() == ["a", "b"]
    assert filter_snapshots(data, close_years=(2025, 2025))["market_id"].tolist() == ["b", "c"]
    assert len(filter_snapshots(data)) == 3  # no filters: keep everything


@pytest.mark.parametrize("seconds_ago, expected", [
    (20, "just now"),
    (60, "1 minute ago"),
    (5 * 3600 + 59, "5 hours ago"),
    (86_400, "1 day ago"),
    (3 * 86_400, "3 days ago"),
])
def test_describe_age(seconds_ago, expected):
    now = pd.Timestamp("2026-10-18 12:00", tz="UTC")
    assert describe_age(now - pd.Timedelta(seconds=seconds_ago), now) == expected
