"""End-to-end tests of forward-test collections: a fake collector (no network) and the throwaway test
schema from conftest.py, with the migration applied first (skipped without PostgreSQL; failing in CI)."""
from datetime import timedelta

import pandas as pd
import psycopg
import pytest

import forward_test.__main__ as lab
from forward_test.storage import SchemaNotReady, health, load_entries, migrate, save

NOW = pd.Timestamp("2026-10-28 10:00", tz="UTC")
META = {"requested_at": NOW, "received_at": NOW, "sha256": None}


def candidate(market_id, days_left, **fields):
    market = {"market_id": market_id, "platform": "polymarket", "event_id": f"e-{market_id}", "event_title": "Event",
              "question": f"{market_id}?", "outcome_names": ["Yes", "No"], "token_ids": [f"tok-{market_id}", f"no-{market_id}"],
              "observed_outcome": "Yes", "token_id": f"tok-{market_id}", "scheduled_end": NOW + timedelta(days=days_left),
              "market_start": pd.Timestamp("2026-09-01", tz="UTC"), "market_active": True, "market_closed": False,
              "accepting_orders": True, "volume_at_entry": 250_000.0, "liquidity_at_entry": 10_000.0}
    market.update(fields)
    return market


@pytest.fixture
def lab_db(db_url):
    """The test schema with the forward-test migrations applied, as `python -m forward_test migrate` does."""
    with psycopg.connect(db_url, autocommit=True) as conn:
        migrate(conn)
    return db_url


@pytest.fixture
def fake_collector(monkeypatch, tmp_path):
    """A collector we control: markets near entry, pilot markets, asks per token, results."""
    fake = {"near": [], "pilot": [], "asks": {}, "results": {}, "tags": {}}
    c = lab.collector
    monkeypatch.setattr(c, "RAW_DIR", tmp_path)

    def book(token):
        ask = fake["asks"].get(token)
        if ask is None:
            return {"error": "no order book (404)", "requested_at": None, "received_at": None, "sha256": None}
        bids = [] if ask == "no-asks" else [(round(ask - 0.02, 3), 100.0)]
        asks = [] if ask == "no-asks" else [(ask, 1000.0)]
        return {"bids": bids, "asks": asks, "tick_size": 0.01, "min_order_size": 5.0, "last_trade_price": None,
                "book_exchange_time": NOW - timedelta(minutes=1), "book_hash": "h", "requested_at": NOW,
                "received_at": NOW + timedelta(seconds=1), "sha256": None, "error": None}

    monkeypatch.setattr(c, "open_markets_near_entry", lambda now, evidence: [(dict(m), META) for m in fake["near"]])
    monkeypatch.setattr(c, "to_candidate", lambda raw: dict(raw))
    monkeypatch.setattr(c, "order_books", lambda tokens, evidence: {t: book(t) for t in tokens})
    monkeypatch.setattr(c, "event_tags", lambda event_id, evidence, cache: fake["tags"].get(event_id, []))
    monkeypatch.setattr(c, "election_events", lambda evidence: [])
    monkeypatch.setattr(c, "pilot_markets", lambda events, min_volume, evidence:
                        [dict(m, metadata=META, classification="us_midterms_2026", event_tags=["midterms"])
                         for m in fake["pilot"] if m["volume_at_entry"] >= min_volume])
    monkeypatch.setattr(c, "market_result", lambda market_id, evidence: fake["results"].get(
        market_id, {"closed": False, "final": False, "outcome": None, "final_volume": 0.0}))
    return fake


def query(db_url, sql):
    with psycopg.connect(db_url) as conn:
        return conn.execute(sql).fetchall()


def test_collection_refuses_to_run_before_the_migration(db_url, fake_collector):
    with pytest.raises(SchemaNotReady):
        lab.run_collection(db_url, now=NOW)


def test_entries_are_recorded_once_and_linked_to_their_observation(lab_db, fake_collector):
    fake_collector["near"] = [candidate("a", 6.9), candidate("b", 6.9)]
    fake_collector["asks"] = {"tok-a": 0.25, "tok-b": 0.55}

    assert lab.run_collection(lab_db, now=NOW)["entered"] == 2
    assert lab.run_collection(lab_db, now=NOW)["entered"] == 0  # rerunning never duplicates

    rows = query(lab_db, "SELECT e.market_id, e.in_group_a, e.observed_outcome, o.book_exchange_time "
                         "FROM forward_test_entries e JOIN forward_test_observations o USING (observation_id) ORDER BY 1")
    assert [(m, a, o) for m, a, o, _ in rows] == [("a", True, "Yes"), ("b", False, "Yes")]
    assert rows[0][3] == NOW - timedelta(minutes=1)  # the exchange's book time, kept


def test_saving_the_same_run_twice_adds_nothing(lab_db, fake_collector):
    fake_collector["pilot"] = [candidate("p", 20)]
    fake_collector["asks"] = {"tok-p": 0.40}
    result = lab.run_collection(lab_db, now=NOW, experiments=["election_2026_pilot"])
    run_id = query(lab_db, "SELECT max(run_id) FROM forward_test_runs")[0][0]
    rows_before = query(lab_db, "SELECT count(*) FROM forward_test_observations")[0][0]

    again = query(lab_db, f"SELECT * FROM forward_test_observations WHERE run_id = {run_id}")
    assert result["observations"]["election_2026_pilot"] == 1 and len(again) == 1
    with psycopg.connect(lab_db) as conn:  # the same observation saved again (e.g. a retried save)
        cursor = conn.execute(f"SELECT * FROM forward_test_observations WHERE run_id = {run_id}")
        names = [d.name for d in cursor.description]
        row = dict(zip(names, cursor.fetchone()))
        save(conn, observations=[{k: v for k, v in row.items() if k != "observation_id"}])
    assert query(lab_db, "SELECT count(*) FROM forward_test_observations")[0][0] == rows_before


def test_observations_are_append_only(lab_db, fake_collector):
    fake_collector["pilot"] = [candidate("p", 20)]
    fake_collector["asks"] = {"tok-p": 0.40}
    lab.run_collection(lab_db, now=NOW, experiments=["election_2026_pilot"])
    with psycopg.connect(lab_db) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("UPDATE forward_test_observations SET best_ask = 0.99")


def test_a_changed_scheduled_end_keeps_both_observations(lab_db, fake_collector):
    fake_collector["asks"] = {"tok-p": 0.40}
    fake_collector["pilot"] = [candidate("p", 20)]
    lab.run_collection(lab_db, now=NOW, experiments=["election_2026_pilot"])
    fake_collector["pilot"] = [candidate("p", 27)]  # the platform moved the end date a week later
    lab.run_collection(lab_db, now=NOW + timedelta(hours=5), experiments=["election_2026_pilot"])

    ends = [r[0] for r in query(lab_db, "SELECT scheduled_end FROM forward_test_observations ORDER BY observation_id")]
    assert ends == [NOW + timedelta(days=20), NOW + timedelta(days=27)]  # history kept, nothing overwritten


def test_missed_only_when_the_collector_was_absent(lab_db, fake_collector):
    lab.run_collection(lab_db, now=NOW - timedelta(days=5))  # the test began 5 days ago (one run, nothing open)

    # "absent": crossed its mark 3 days ago with no run in its first day; "below": runs DID happen then
    fake_collector["near"] = [candidate("absent", 4)]
    assert lab.run_collection(lab_db, now=NOW)["missed"] == 1
    lab.run_collection(lab_db, now=NOW + timedelta(hours=5))  # a run inside "below"'s first day
    # "below" crossed its mark an hour before NOW; runs at NOW and NOW+5h fell inside its first day,
    # but it is first returned 1.5 days later (it only reached $100,000 then)
    fake_collector["near"] = [candidate("below", 7 - 1 / 24)]
    assert lab.run_collection(lab_db, now=NOW + timedelta(days=1, hours=12))["missed"] == 0
    assert query(lab_db, "SELECT market_id, status FROM forward_test_entries") == [("absent", "missed")]


def test_an_invalid_book_is_recorded_as_invalid_not_traded(lab_db, fake_collector):
    fake_collector["near"] = [candidate("x", 6.9), candidate("empty", 6.9)]
    fake_collector["asks"] = {"tok-empty": "no-asks"}  # tok-x has no book at all
    result = lab.run_collection(lab_db, now=NOW)

    assert result["invalid_entries"] == 2 and result["entered"] == 0
    rows = dict(query(lab_db, "SELECT market_id, book_error FROM forward_test_observations"))
    assert rows == {"x": "no order book (404)", "empty": "no asks: nothing to buy"}
    assert {s for (s,) in query(lab_db, "SELECT status FROM forward_test_entries")} == {"invalid"}


def test_the_two_experiments_never_mix(lab_db, fake_collector):
    # A pilot market priced like Group A is observed, but it never becomes a paper trade
    fake_collector["pilot"] = [candidate("pilot-a", 30)]
    fake_collector["near"] = [candidate("frozen-a", 6.9)]
    fake_collector["asks"] = {"tok-pilot-a": 0.25, "tok-frozen-a": 0.25}
    lab.run_collection(lab_db, now=NOW)

    assert query(lab_db, "SELECT market_id FROM forward_test_entries") == [("frozen-a",)]
    rows = query(lab_db, "SELECT experiment, market_id, meets_group_a_price FROM forward_test_observations ORDER BY 1")
    assert rows == [("election_2026_pilot", "pilot-a", True), ("group_a_7day", "frozen-a", True)]


def test_dry_run_changes_nothing(lab_db, fake_collector, tmp_path):
    fake_collector["near"] = [candidate("a", 6.9)]
    fake_collector["pilot"] = [candidate("p", 20)]
    fake_collector["asks"] = {"tok-a": 0.25, "tok-p": 0.40}
    tables = ["forward_test_runs", "forward_test_entries", "forward_test_observations", "forward_test_raw"]
    before = [query(lab_db, f"SELECT count(*) FROM {t}")[0][0] for t in tables]

    result = lab.run_collection(lab_db, now=NOW, dry_run=True)

    assert result["entered"] == 1 and result["observations"]["election_2026_pilot"] == 1  # it did the work...
    assert [query(lab_db, f"SELECT count(*) FROM {t}")[0][0] for t in tables] == before  # ...and saved nothing
    assert not list(tmp_path.rglob("*.json*"))  # not even files


def test_limit_caps_markets_per_experiment(lab_db, fake_collector):
    fake_collector["near"] = [candidate(f"m{i}", 6.9) for i in range(5)]
    fake_collector["pilot"] = [candidate(f"p{i}", 20) for i in range(5)]
    fake_collector["asks"] = {f"tok-m{i}": 0.25 for i in range(5)} | {f"tok-p{i}": 0.4 for i in range(5)}
    result = lab.run_collection(lab_db, now=NOW, limit=2)
    assert result["entered"] == 2 and result["observations"]["election_2026_pilot"] == 2


def test_recorded_markets_are_settled_once_final(lab_db, fake_collector):
    fake_collector["near"] = [candidate("a", 6.9), candidate("v", 6.9)]
    fake_collector["asks"] = {"tok-a": 0.25, "tok-v": 0.28}
    lab.run_collection(lab_db, now=NOW)
    fake_collector["near"] = []
    fake_collector["results"] = {"a": {"closed": True, "final": True, "outcome": 1, "final_volume": 900_000.0},
                                 "v": {"closed": True, "final": True, "outcome": None, "final_volume": 1.0}}
    assert lab.run_collection(lab_db, now=NOW + timedelta(days=8))["settled"] == 2
    assert dict(query(lab_db, "SELECT market_id, status FROM forward_test_entries")) == {"a": "settled", "v": "void"}


def test_a_failed_run_is_logged_and_saves_nothing(lab_db, fake_collector, monkeypatch):
    fake_collector["near"] = [candidate("a", 6.9)]
    fake_collector["asks"] = {"tok-a": 0.25}
    lab.run_collection(lab_db, now=NOW)

    def broken(market_id, evidence):
        raise RuntimeError("API down")

    monkeypatch.setattr(lab.collector, "market_result", broken)
    fake_collector["near"] = [candidate("b", 6.9)]
    fake_collector["asks"]["tok-b"] = 0.25
    with pytest.raises(RuntimeError):
        lab.run_collection(lab_db, now=NOW)
    assert query(lab_db, "SELECT status, error FROM forward_test_runs ORDER BY run_id")[-1] == ("failed", "RuntimeError: API down")
    assert query(lab_db, "SELECT count(*) FROM forward_test_entries WHERE market_id = 'b'") == [(0,)]


def test_report_waits_for_enough_trades_before_a_verdict(lab_db, fake_collector):
    fake_collector["near"] = [candidate("a", 6.9)]
    fake_collector["asks"] = {"tok-a": 0.25}
    lab.run_collection(lab_db, now=NOW)
    fake_collector["near"] = []
    fake_collector["results"] = {"a": {"closed": True, "final": True, "outcome": 1, "final_volume": 1.0}}
    lab.run_collection(lab_db, now=NOW + timedelta(days=8))
    text = lab.report(load_entries(lab_db))
    assert "Group A (best ask 20-30 cents): 1 entered, 1 settled" in text
    assert "Verdict: not yet (1 of 100 settled Group A trades needed)" in text


def test_each_run_records_its_settings_and_the_volume_floor_is_applied(lab_db, fake_collector, monkeypatch):
    monkeypatch.delenv("GITHUB_WORKFLOW", raising=False)  # GitHub Actions sets it; outside a workflow it's "local"
    fake_collector["pilot"] = [candidate("big", 20), candidate("small", 20, volume_at_entry=50_000.0)]
    fake_collector["asks"] = {"tok-big": 0.4, "tok-small": 0.4}
    lab.run_collection(lab_db, now=NOW, experiments=["election_2026_pilot"], pilot_min_volume=100_000)

    assert query(lab_db, "SELECT market_id FROM forward_test_observations") == [("big",)]
    settings = query(lab_db, "SELECT settings FROM forward_test_runs")[0][0]
    assert settings == {"experiments": ["election_2026_pilot"], "pilot_min_volume": 100_000, "limit": None,
                        "trigger": "local"}


def test_group_a_markets_outside_elections_are_labelled_not_election(lab_db, fake_collector):
    fake_collector["near"] = [candidate("game", 6.9, event_title="Lakers vs. Celtics")]
    fake_collector["asks"] = {"tok-game": 0.25}
    lab.run_collection(lab_db, now=NOW, experiments=["group_a_7day"])
    assert query(lab_db, "SELECT classification FROM forward_test_observations") == [("not_election",)]


def test_a_run_whose_rows_cant_be_read_back_is_logged_as_failed(lab_db, fake_collector, monkeypatch):
    fake_collector["pilot"] = [candidate("p", 20)]
    fake_collector["asks"] = {"tok-p": 0.40}
    monkeypatch.setattr(lab, "saved_counts", lambda conn, run_id: {})  # as if the rows never arrived
    with pytest.raises(RuntimeError, match="read-back check failed"):
        lab.run_collection(lab_db, now=NOW, experiments=["election_2026_pilot"])
    status, error = query(lab_db, "SELECT status, error FROM forward_test_runs")[0]
    assert status == "failed" and "read-back check failed for election_2026_pilot" in error


def test_health_counts_observations_and_flags_a_stale_collector(lab_db, fake_collector):
    fake_collector["pilot"] = [candidate("a", 20), candidate("b", 20), candidate("x", 20)]
    fake_collector["asks"] = {"tok-a": 0.25, "tok-b": 0.55}  # tok-x has no book: an invalid observation
    lab.run_collection(lab_db, now=NOW, experiments=["election_2026_pilot"])
    with psycopg.connect(lab_db) as conn:
        h = health(conn)

    pilot = h["observations"]["election_2026_pilot"]
    assert (pilot["observations"], pilot["valid"], pilot["invalid"], pilot["markets"]) == (3, 2, 1, 3)
    assert pilot["group_a_matches"] == 1
    # observed_at is the real clock (never the simulated run time), so count against the stored times
    before = query(lab_db, "SELECT count(*) FROM forward_test_observations WHERE observed_at < '2026-11-03 23:00+00'")
    assert pilot["before_first_poll_close"] == before[0][0]
    assert h["runs"]["success"] == 1 and h["last_success"]["election_2026_pilot"] == NOW
    _, healthy = lab.health_report(h, NOW + timedelta(minutes=10), timedelta(minutes=30), ["election_2026_pilot"])
    assert healthy
    text, healthy = lab.health_report(h, NOW + timedelta(hours=1), timedelta(minutes=30), ["election_2026_pilot"])
    assert not healthy and "election_2026_pilot: last successful run 2026-10-28 10:00:00 UTC -> STALE" in text


def test_health_confirms_missed_labels_only_when_no_run_happened(lab_db, fake_collector):
    lab.run_collection(lab_db, now=NOW - timedelta(days=5))
    fake_collector["near"] = [candidate("absent", 4)]
    lab.run_collection(lab_db, now=NOW)
    with psycopg.connect(lab_db) as conn:
        h = health(conn)
    assert h["entries"] == {"missed": 1} and h["missed_confirmed"] == 1
