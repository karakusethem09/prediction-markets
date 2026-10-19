"""Tests for the incremental pipeline. Downloads are faked (no network); the database tests
use the throwaway schema from conftest.py and are skipped if PostgreSQL isn't running."""
import json

import pandas as pd
import psycopg
import pytest

import pipeline
from extract import polymarket
from transform import normalize


def raw_market(market_id, close="2025-01-10 12:00:00+00", outcome_prices='["1", "0"]'):
    return {
        "id": market_id, "question": f"Market {market_id}?", "events": [{"id": "e1"}],
        "startDate": "2025-01-01T00:00:00Z", "createdAt": "2025-01-01T00:00:00Z",
        "closedTime": close, "outcomePrices": outcome_prices, "umaResolutionStatus": "resolved",
        "volumeNum": 2_000_000, "clobTokenIds": f'["token-{market_id}", "other"]',
    }


def prices_for(market_ids, price=0.4):
    """A small prices table: one hourly price just before each market's 1-day horizon."""
    return pd.DataFrame({
        "market_id": pd.Series([f"poly:{m}" for m in market_ids], dtype=str),  # text even when empty
        "time": pd.Series(pd.to_datetime(["2025-01-09 11:00"] * len(market_ids), utc=True)).dt.as_unit("us"),
        "price": [price] * len(market_ids),
        "fidelity": ["hourly"] * len(market_ids),
    })


@pytest.fixture
def fake_platform(monkeypatch):
    """Replace collect_updates with a fake 'platform' whose download we control.

    Set fake["downloaded"] to the raw market ids the next run should see. Like the real one,
    it only returns markets that aren't in the database yet."""
    fake = {"downloaded": [], "eligible_override": None}

    def fake_collect_updates(existing_market_ids):
        markets = normalize.normalize_polymarket_markets([raw_market(m) for m in fake["downloaded"]])
        new = markets[~markets["market_id"].isin(existing_market_ids)].reset_index(drop=True)
        new_raw_ids = [m.removeprefix("poly:") for m in new["market_id"]]
        eligible = fake["eligible_override"] if fake["eligible_override"] is not None else len(markets)
        return normalize.Updates(eligible, new, prices_for(new_raw_ids), [])

    monkeypatch.setattr(pipeline, "collect_updates", fake_collect_updates)
    return fake


def query(db_url, sql):
    with psycopg.connect(db_url) as conn:
        return conn.execute(sql).fetchall()


def test_first_run_loads_markets_and_snapshots_and_logs_checks(db_url, fake_platform):
    fake_platform["downloaded"] = ["1", "2"]

    row_counts = pipeline.run_pipeline(db_url)

    assert row_counts == {"markets": 2, "snapshots": 2}
    assert query(db_url, "SELECT count(*) FROM markets WHERE category IS NULL") == [(2,)]  # NULL, not 'nan'
    status, checks = query(db_url, "SELECT status, checks FROM pipeline_runs")[0]
    assert status == "success"
    assert [c["passed"] for c in checks] == [True, True, True, True]


def test_rerun_only_adds_new_markets(db_url, fake_platform):
    fake_platform["downloaded"] = ["1", "2"]
    pipeline.run_pipeline(db_url)

    fake_platform["downloaded"] = ["1", "2", "3"]  # one market resolved since the last run
    row_counts = pipeline.run_pipeline(db_url)

    assert row_counts == {"markets": 1, "snapshots": 1}
    assert query(db_url, "SELECT count(*) FROM markets") == [(3,)]


def test_quiet_day_is_a_successful_run_with_nothing_to_load(db_url, fake_platform):
    fake_platform["downloaded"] = ["1"]
    pipeline.run_pipeline(db_url)

    row_counts = pipeline.run_pipeline(db_url)  # same download, nothing new

    assert row_counts == {"markets": 0, "snapshots": 0}
    assert query(db_url, "SELECT status FROM pipeline_runs ORDER BY run_id") == [("success",), ("success",)]


def test_failed_check_loads_nothing_and_records_why(db_url, fake_platform):
    fake_platform["downloaded"] = ["1", "2"]
    pipeline.run_pipeline(db_url)

    fake_platform["downloaded"] = ["1", "2", "3"]
    fake_platform["eligible_override"] = 0  # the download looks broken: far fewer markets than stored
    with pytest.raises(pipeline.DataQualityError):
        pipeline.run_pipeline(db_url)

    assert query(db_url, "SELECT count(*) FROM markets") == [(2,)]  # market 3 was not loaded
    status, checks, error = query(db_url, "SELECT status, checks, error FROM pipeline_runs ORDER BY run_id")[-1]
    assert status == "failed"
    assert "DataQualityError" in error
    assert {c["name"]: c["passed"] for c in checks}["download_size"] is False


def test_updates_download_prices_only_for_new_markets(monkeypatch, tmp_path):
    """The real normalize_polymarket_updates, with the API replaced by files in tmp_path."""
    raw_dir, prices_dir = tmp_path / "markets", tmp_path / "prices"
    raw_dir.mkdir()
    page = {"markets": [raw_market("1"), raw_market("2"), raw_market("3"),
                        raw_market("cancelled", outcome_prices='["0.5", "0.5"]')]}
    (raw_dir / "page_0000.json").write_text(json.dumps(page))
    monkeypatch.setattr(polymarket, "RAW_DIR", raw_dir)
    monkeypatch.setattr(polymarket, "PRICES_DIR", prices_dir)
    monkeypatch.setattr(polymarket, "fetch_resolved_markets", lambda: None)

    asked_for = []

    def fake_fetch_all_price_histories(markets):
        asked_for.extend(m["id"] for m in markets)
        return ["3"]  # market 3's download fails

    monkeypatch.setattr(polymarket, "fetch_all_price_histories", fake_fetch_all_price_histories)

    updates = normalize.normalize_polymarket_updates(existing_market_ids={"poly:1"})

    assert updates.eligible_markets == 3  # 1, 2, 3 (the cancelled market isn't eligible)
    assert asked_for == ["2", "3"]  # never re-downloads market 1, never the cancelled one
    assert updates.markets["market_id"].tolist() == ["poly:2"]  # 3 failed, so it waits for the next run
    assert updates.failed_downloads == ["poly:3"]
    assert updates.prices.empty  # no price files were written in this test
