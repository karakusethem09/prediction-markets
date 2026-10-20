"""Tests for the incremental pipeline. Downloads are faked (no network); the database tests
use the throwaway schema from conftest.py and are skipped if PostgreSQL isn't running."""
import json

import pandas as pd
import psycopg
import pytest

import pipeline
from extract import polymarket
from load.postgres import run_load
from transform import normalize
from transform.completeness import MAX_ATTEMPTS

CLOSE = "2025-01-10 12:00:00+00"
NOW = pd.Timestamp("2025-01-11 12:00", tz="UTC")  # the "today" of these tests: one day after close


def raw_market(market_id, open_="2025-01-01T00:00:00Z", outcome_prices='["1", "0"]'):
    """A raw market open from `open_` until CLOSE (9.5 days by default: the 1- and 7-day horizons are possible)."""
    return {
        "id": market_id, "question": f"Market {market_id}?", "events": [{"id": "e1"}],
        "startDate": open_, "createdAt": open_, "closedTime": CLOSE, "outcomePrices": outcome_prices,
        "umaResolutionStatus": "resolved", "volumeNum": 2_000_000,
        "clobTokenIds": f'["token-{market_id}", "other"]',
    }


def prices_for(market_ids, horizons=(1, 7)):
    """One hourly price just before each requested horizon, for each market."""
    close = pd.Timestamp(CLOSE)
    rows = [(f"poly:{m}", close - pd.Timedelta(days=h, hours=1)) for m in market_ids for h in horizons]
    return pd.DataFrame({
        "market_id": pd.Series([r[0] for r in rows], dtype=str),
        "time": pd.Series(pd.to_datetime([r[1] for r in rows], utc=True)).dt.as_unit("us"),
        "price": [0.4] * len(rows),
        "fidelity": pd.Series(["hourly"] * len(rows), dtype=str),
    })


@pytest.fixture
def fake_platform(monkeypatch):
    """Replace collect_updates with a fake platform we control:

    fake["downloaded"]: raw markets the next run sees
    fake["no_prices"]: ids whose price history comes back empty (not published yet)
    fake["eligible_override"]: pretend the download had this many eligible markets
    """
    fake = {"downloaded": [], "no_prices": set(), "eligible_override": None, "retried_ids": []}

    def fake_collect_updates(existing_market_ids, retry_market_ids=()):
        markets = normalize.normalize_polymarket_markets(fake["downloaded"])
        new = markets[~markets["market_id"].isin(existing_market_ids)].reset_index(drop=True)
        retried = markets[markets["market_id"].isin(retry_market_ids)].reset_index(drop=True)
        fake["retried_ids"] = retried["market_id"].tolist()
        with_prices = [m.removeprefix("poly:") for m in pd.concat([new, retried])["market_id"]
                       if m.removeprefix("poly:") not in fake["no_prices"]]
        eligible = fake["eligible_override"] if fake["eligible_override"] is not None else len(markets)
        return normalize.Updates(eligible, new, retried, prices_for(with_prices), [])

    monkeypatch.setattr(pipeline, "collect_updates", fake_collect_updates)
    return fake


def query(db_url, sql):
    with psycopg.connect(db_url) as conn:
        return conn.execute(sql).fetchall()


def statuses(db_url):
    return dict(query(db_url, "SELECT market_id, status FROM processing_status"))


def test_first_run_loads_markets_snapshots_and_statuses(db_url, fake_platform):
    fake_platform["downloaded"] = [raw_market("1"), raw_market("2")]

    row_counts = pipeline.run_pipeline(db_url, now=NOW)

    assert row_counts == {"markets": 2, "snapshots": 4, "processing_status": 2}
    assert statuses(db_url) == {"poly:1": "complete", "poly:2": "complete"}
    assert query(db_url, "SELECT count(*) FROM markets WHERE category IS NULL") == [(2,)]  # NULL, not 'nan'
    status, checks = query(db_url, "SELECT status, checks FROM pipeline_runs")[0]
    assert status == "success"
    assert all(c["passed"] for c in checks)


def test_market_without_prices_is_retried_until_its_snapshots_arrive(db_url, fake_platform):
    """The bug this fixes: a market saved without prices used to be skipped forever."""
    fake_platform["downloaded"] = [raw_market("1"), raw_market("late")]
    fake_platform["no_prices"] = {"late"}  # its prices aren't published yet
    pipeline.run_pipeline(db_url, now=NOW)
    assert statuses(db_url)["poly:late"] == "retry_needed"

    fake_platform["no_prices"] = set()  # the next day they are
    row_counts = pipeline.run_pipeline(db_url, now=NOW + pd.Timedelta(days=1))

    assert fake_platform["retried_ids"] == ["poly:late"]  # only the incomplete market was retried
    assert row_counts["markets"] == 0  # nothing new, but...
    assert statuses(db_url)["poly:late"] == "complete"  # ...its snapshots were added
    assert query(db_url, "SELECT count(*) FROM snapshots WHERE market_id = 'poly:late'") == [(2,)]
    assert query(db_url, "SELECT attempts FROM processing_status WHERE market_id = 'poly:late'") == [(2,)]


def test_short_market_with_its_only_possible_snapshot_is_complete(db_url, fake_platform):
    fake_platform["downloaded"] = [raw_market("short", open_="2025-01-07T12:00:00Z")]  # open 3 days

    pipeline.run_pipeline(db_url, now=NOW)

    assert statuses(db_url) == {"poly:short": "complete"}  # no 7-day price, and none was possible
    assert query(db_url, "SELECT possible_horizons, found_horizons FROM processing_status") == [([1], [1])]


def test_gives_up_after_max_attempts(db_url, fake_platform):
    fake_platform["downloaded"] = [raw_market("never")]
    fake_platform["no_prices"] = {"never"}

    for day in range(MAX_ATTEMPTS):
        pipeline.run_pipeline(db_url, now=NOW + pd.Timedelta(days=day / 10))  # stay inside the retry window

    assert statuses(db_url) == {"poly:never": "unavailable"}
    pipeline.run_pipeline(db_url, now=NOW + pd.Timedelta(days=1))
    assert fake_platform["retried_ids"] == []  # unavailable markets aren't retried any more


def test_markets_saved_before_statuses_existed_get_one(db_url, fake_platform):
    # A market loaded the old way: in markets, with a snapshot, but no processing_status row
    old = normalize.normalize_polymarket_markets([raw_market("old")])
    snapshot = pd.DataFrame({
        "market_id": ["poly:old"], "horizon_days": [1],
        "target_time": [pd.Timestamp("2025-01-09 12:00", tz="UTC")],
        "price_time": [pd.Timestamp("2025-01-09 11:00", tz="UTC")], "price": [0.4],
    })
    run_load({"markets": old, "snapshots": snapshot}, db_url)
    fake_platform["downloaded"] = [raw_market("old")]

    pipeline.run_pipeline(db_url, now=NOW)

    # Open 9.5 days, so a 7-day snapshot was possible. It had prices (it has a 1-day snapshot),
    # so the missing one is a gap in the data, not something a retry would fix
    assert statuses(db_url) == {"poly:old": "unavailable"}


def test_prices_that_dont_reach_a_horizon_are_not_retried(db_url, fake_platform, monkeypatch):
    """Prices came back, but only from after the 7-day moment (e.g. trading began late): give up at once."""
    fake_platform["downloaded"] = [raw_market("late-start")]
    real_collect = pipeline.collect_updates

    def only_one_day_prices(existing, retry=()):
        updates = real_collect(existing, retry)
        updates.prices = prices_for(["late-start"], horizons=(1,))  # no price near the 7-day moment
        return updates

    monkeypatch.setattr(pipeline, "collect_updates", only_one_day_prices)
    pipeline.run_pipeline(db_url, now=NOW)

    reason = query(db_url, "SELECT status, reason FROM processing_status")[0]
    assert reason[0] == "unavailable" and "doesn't cover that moment" in reason[1]
    pipeline.run_pipeline(db_url, now=NOW)
    assert fake_platform["retried_ids"] == []  # and it is not downloaded again


def test_quiet_day_is_a_successful_run_with_nothing_to_load(db_url, fake_platform):
    fake_platform["downloaded"] = [raw_market("1")]
    pipeline.run_pipeline(db_url, now=NOW)

    row_counts = pipeline.run_pipeline(db_url, now=NOW)  # same download, nothing new or to retry

    assert row_counts == {"markets": 0, "snapshots": 0, "processing_status": 0}
    assert query(db_url, "SELECT status FROM pipeline_runs ORDER BY run_id") == [("success",), ("success",)]


def test_failed_blocking_check_loads_nothing_and_records_why(db_url, fake_platform):
    fake_platform["downloaded"] = [raw_market("1"), raw_market("2")]
    pipeline.run_pipeline(db_url, now=NOW)

    fake_platform["downloaded"] = [raw_market("1"), raw_market("2"), raw_market("3")]
    fake_platform["eligible_override"] = 0  # the download looks broken: far fewer markets than stored
    with pytest.raises(pipeline.DataQualityError):
        pipeline.run_pipeline(db_url, now=NOW)

    assert query(db_url, "SELECT count(*) FROM markets") == [(2,)]  # market 3 was not loaded
    status, checks, error = query(db_url, "SELECT status, checks, error FROM pipeline_runs ORDER BY run_id")[-1]
    assert status == "failed"
    assert "DataQualityError" in error
    assert {c["name"]: c["passed"] for c in checks}["download_size"] is False


def test_low_coverage_is_recorded_but_does_not_block(db_url, fake_platform):
    fake_platform["downloaded"] = [raw_market("1")]
    fake_platform["no_prices"] = {"1"}

    pipeline.run_pipeline(db_url, now=NOW)  # doesn't raise

    checks = query(db_url, "SELECT checks FROM pipeline_runs")[0][0]
    coverage = next(c for c in checks if c["name"] == "snapshot_coverage")
    assert coverage["passed"] is False and coverage["blocking"] is False
    assert query(db_url, "SELECT count(*) FROM markets") == [(1,)]


def test_updates_download_new_markets_and_refresh_retried_ones(monkeypatch, tmp_path):
    """The real normalize_polymarket_updates, with the API replaced by files in tmp_path."""
    raw_dir, prices_dir = tmp_path / "markets", tmp_path / "prices"
    raw_dir.mkdir()
    page = {"markets": [raw_market("1"), raw_market("2"), raw_market("3"), raw_market("4"),
                        raw_market("cancelled", outcome_prices='["0.5", "0.5"]')]}
    (raw_dir / "page_0000.json").write_text(json.dumps(page))
    monkeypatch.setattr(polymarket, "RAW_DIR", raw_dir)
    monkeypatch.setattr(polymarket, "PRICES_DIR", prices_dir)
    monkeypatch.setattr(polymarket, "fetch_resolved_markets", lambda: None)

    calls = []

    def fake_fetch_all_price_histories(markets, refresh=False):
        calls.append(([m["id"] for m in markets], refresh))
        return ["3"]  # market 3's download fails

    monkeypatch.setattr(polymarket, "fetch_all_price_histories", fake_fetch_all_price_histories)

    updates = normalize.normalize_polymarket_updates(
        existing_market_ids={"poly:1", "poly:4"}, retry_market_ids={"poly:4"})

    assert updates.eligible_markets == 4  # the cancelled market isn't eligible
    assert calls == [(["2", "3"], False), (["4"], True)]  # new ones, then the retry downloaded afresh
    assert updates.markets["market_id"].tolist() == ["poly:2"]  # 3 failed, so it waits for the next run
    assert updates.retried["market_id"].tolist() == ["poly:4"]
    assert updates.failed_downloads == ["poly:3"]
