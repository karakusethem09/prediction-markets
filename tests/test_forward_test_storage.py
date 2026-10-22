"""End-to-end tests of a forward-test collection: a fake collector (no network) and the throwaway
test schema from conftest.py (skipped if PostgreSQL isn't running, failing in CI instead)."""
from datetime import timedelta

import pandas as pd
import psycopg
import pytest

import forward_test.__main__ as forward_test
from forward_test.storage import load_entries

NOW = pd.Timestamp("2026-10-21 09:00", tz="UTC")


def candidate(market_id, days_left, market_start=pd.Timestamp("2026-09-01", tz="UTC")):
    return {"market_id": market_id, "platform": "polymarket", "event_id": f"e-{market_id}",
            "question": f"{market_id}?", "token_id": f"token-{market_id}",
            "scheduled_end": NOW + timedelta(days=days_left), "market_start": market_start,
            "volume_at_entry": 250_000.0, "liquidity_at_entry": 10_000.0}


@pytest.fixture
def fake_collector(monkeypatch):
    """A collector we control: which markets are open, their asks, and their results."""
    fake = {"markets": [], "asks": {}, "results": {}}
    monkeypatch.setattr(forward_test.collector, "open_markets_near_entry", lambda now, run: fake["markets"])
    monkeypatch.setattr(forward_test.collector, "to_candidate", lambda raw: raw)
    monkeypatch.setattr(forward_test.collector, "order_book", lambda token, run: {
        "bids": [(fake["asks"][token] - 0.02, 100.0)], "asks": [(fake["asks"][token], 1000.0)],
        "tick_size": 0.01, "min_order_size": 5.0, "last_trade_price": fake["asks"][token]})
    monkeypatch.setattr(forward_test.collector, "market_result",
                        lambda market_id, run: fake["results"].get(market_id, {"closed": False, "final": False,
                                                                               "outcome": None, "final_volume": 0}))
    return fake


def query(db_url, sql):
    with psycopg.connect(db_url) as conn:
        return conn.execute(sql).fetchall()


def test_markets_are_recorded_at_their_seven_day_mark_and_only_once(db_url, fake_collector):
    fake_collector["markets"] = [candidate("a", 6.9), candidate("b", 6.9), candidate("later", 9)]
    fake_collector["asks"] = {"token-a": 0.25, "token-b": 0.55}

    assert forward_test.run_collection(db_url, now=NOW) == (2, 0, 0)
    assert forward_test.run_collection(db_url, now=NOW) == (0, 0, 0)  # rerunning never duplicates

    rows = dict(query(db_url, "SELECT market_id, in_group_a FROM forward_test_entries"))
    assert rows == {"a": True, "b": False}  # "later" isn't at its 7-day mark yet
    assert query(db_url, "SELECT best_ask, fill_price_100 FROM forward_test_entries WHERE market_id = 'a'") == [(0.25, 0.25)]


def test_a_market_reached_too_late_is_missed_not_entered(db_url, fake_collector):
    fake_collector["markets"] = [candidate("a", 6.9)]
    fake_collector["asks"] = {"token-a": 0.25}
    forward_test.run_collection(db_url, now=NOW - timedelta(days=3))  # the test has been running for 3 days

    fake_collector["markets"] = [candidate("late", 5)]  # crossed its 7-day mark 2 days ago, while we were running
    assert forward_test.run_collection(db_url, now=NOW) == (0, 1, 0)
    assert query(db_url, "SELECT status, best_ask FROM forward_test_entries WHERE market_id = 'late'") == [("missed", None)]


def test_recorded_markets_are_settled_once_final(db_url, fake_collector):
    fake_collector["markets"] = [candidate("a", 6.9), candidate("v", 6.9)]
    fake_collector["asks"] = {"token-a": 0.25, "token-v": 0.28}
    forward_test.run_collection(db_url, now=NOW)

    fake_collector["markets"] = []
    fake_collector["results"] = {"a": {"closed": True, "final": True, "outcome": 1, "final_volume": 900_000.0},
                                 "v": {"closed": True, "final": True, "outcome": None, "final_volume": 1.0}}
    assert forward_test.run_collection(db_url, now=NOW + timedelta(days=8)) == (0, 0, 2)

    assert dict(query(db_url, "SELECT market_id, status FROM forward_test_entries")) == {"a": "settled", "v": "void"}
    assert query(db_url, "SELECT outcome FROM forward_test_entries WHERE market_id = 'a'") == [(1,)]


def test_a_failed_run_is_logged_and_saves_nothing(db_url, fake_collector, monkeypatch):
    fake_collector["markets"] = [candidate("a", 6.9)]
    fake_collector["asks"] = {"token-a": 0.25}

    def broken(market_id, run):
        raise RuntimeError("API down")

    monkeypatch.setattr(forward_test.collector, "market_result", broken)
    forward_test.run_collection(db_url, now=NOW)  # no open markets yet, so settlement isn't called
    fake_collector["markets"] = [candidate("b", 6.9)]
    fake_collector["asks"]["token-b"] = 0.25
    with pytest.raises(RuntimeError):
        forward_test.run_collection(db_url, now=NOW)  # settling "a" fails

    assert query(db_url, "SELECT status, error FROM forward_test_runs ORDER BY run_id")[-1] == ("failed", "RuntimeError: API down")
    assert query(db_url, "SELECT count(*) FROM forward_test_entries WHERE market_id = 'b'") == [(0,)]


def test_report_waits_for_enough_trades_before_a_verdict(db_url, fake_collector):
    fake_collector["markets"] = [candidate("a", 6.9)]
    fake_collector["asks"] = {"token-a": 0.25}
    forward_test.run_collection(db_url, now=NOW)
    fake_collector["markets"] = []
    fake_collector["results"] = {"a": {"closed": True, "final": True, "outcome": 1, "final_volume": 1.0}}
    forward_test.run_collection(db_url, now=NOW + timedelta(days=8))

    text = forward_test.report(load_entries(db_url))

    assert "Group A (best ask 20-30 cents): 1 entered, 1 settled" in text
    assert "best ask was 1.0 cents above the midpoint" in text
    assert "Verdict: not yet (1 of 100 settled Group A trades needed)" in text
