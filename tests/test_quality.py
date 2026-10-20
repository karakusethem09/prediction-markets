"""Tests for the data quality checks."""
import pandas as pd

from transform.quality import (
    check_download_size, check_schema, check_snapshot_coverage, check_snapshots_match_markets, run_checks,
)
from transform.schemas import snapshots_schema


def snapshot(market_id="poly:1", price=0.4, price_time="2025-01-08 23:00"):
    return {
        "market_id": market_id, "horizon_days": 1,
        "target_time": pd.Timestamp("2025-01-09", tz="UTC").as_unit("us"),
        "price_time": pd.Timestamp(price_time, tz="UTC").as_unit("us"),
        "price": price,
    }


def test_download_size_passes_when_nothing_disappeared():
    assert check_download_size(10_800, 10_792)["passed"]


def test_download_size_fails_when_the_download_shrinks():
    result = check_download_size(2_000, 10_792)  # e.g. pagination stopped early
    assert not result["passed"]
    assert "need at least 10252" in result["detail"]


def test_download_size_passes_on_an_empty_database():
    assert check_download_size(5, 0)["passed"]


def test_schema_check_reports_problems_instead_of_crashing():
    bad = pd.DataFrame([snapshot(price=1.5), snapshot("poly:2", price_time="2025-01-09 05:00")])

    result = check_schema("snapshots_schema", snapshots_schema, bad)

    assert not result["passed"]
    assert "problems" in result["detail"]  # price out of range + a price from the future


def test_orphan_snapshots_are_caught():
    markets = pd.DataFrame({"market_id": ["poly:1"]})
    snapshots = pd.DataFrame([snapshot("poly:1"), snapshot("poly:999")])
    assert not check_snapshots_match_markets(markets, snapshots)["passed"]


def test_run_checks_returns_every_check_by_name():
    no_markets = pd.DataFrame(columns=["market_id"])
    no_snapshots = pd.DataFrame([snapshot()]).iloc[0:0]  # empty, but with the real columns
    no_statuses = pd.DataFrame(columns=["status", "possible_horizons", "found_horizons"])
    names = [c["name"] for c in run_checks(0, 0, no_markets, no_markets, no_snapshots, no_statuses)]
    assert names == ["download_size", "markets_schema", "snapshots_schema", "snapshots_match_markets",
                     "snapshot_coverage"]


def test_snapshot_coverage_counts_only_possible_snapshots_and_never_blocks():
    statuses = pd.DataFrame({
        "status": ["complete", "retry_needed"],
        "possible_horizons": [[1], [1, 7, 30]],
        "found_horizons": [[1], [1]],
    })
    result = check_snapshot_coverage(statuses)
    assert not result["passed"] and result["blocking"] is False
    assert result["detail"].startswith("2 of 4 possible snapshots (50%); 1 markets will be retried")
