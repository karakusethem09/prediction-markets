"""Tests for loading into PostgreSQL. They need the local database (docker compose up -d)
and are skipped if it isn't running. They work in a throwaway schema, never the real tables."""
import pandas as pd
import psycopg
import pytest

from load.postgres import DATABASE_URL, run_load

TEST_SCHEMA = "pytest_load"


@pytest.fixture
def db_url():
    """A database URL whose tables all live in an empty test schema, dropped afterwards."""
    try:
        with psycopg.connect(DATABASE_URL, autocommit=True, connect_timeout=2) as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE")
            conn.execute(f"CREATE SCHEMA {TEST_SCHEMA}")
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL isn't running (start it with: docker compose up -d)")

    # search_path makes every unqualified table name (markets, snapshots, ...) point to the test schema
    separator = "&" if "?" in DATABASE_URL else "?"
    yield f"{DATABASE_URL}{separator}options=-csearch_path%3D{TEST_SCHEMA}"

    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA {TEST_SCHEMA} CASCADE")


def utc(text):
    return pd.Timestamp(text, tz="UTC")


def sample_tables(price=0.4):
    markets = pd.DataFrame([{
        "market_id": "poly:1", "platform": "polymarket", "event_id": None,
        "question": 'Will "X", Y\nhappen?',  # quotes, a comma and a newline survive the CSV copy
        "category": None, "open_time": utc("2025-01-01"), "close_time": utc("2025-01-10"),
        "outcome": 1, "volume_usd": 2_000_000.0,
    }])
    prices = pd.DataFrame([{"market_id": "poly:1", "time": utc("2025-01-08 23:00"), "price": price, "fidelity": "hourly"}])
    snapshots = pd.DataFrame([{
        "market_id": "poly:1", "horizon_days": 1, "target_time": utc("2025-01-09"),
        "price_time": utc("2025-01-08 23:00"), "price": price,
    }])
    return {"markets": markets, "price_history": prices, "snapshots": snapshots}


def query(db_url, sql):
    with psycopg.connect(db_url) as conn:
        return conn.execute(sql).fetchall()


def test_loading_twice_never_duplicates_rows(db_url):
    run_load(sample_tables(), db_url)
    run_load(sample_tables(), db_url)

    for table in ["markets", "price_history", "snapshots"]:
        assert query(db_url, f"SELECT count(*) FROM {table}") == [(1,)]


def test_reload_updates_existing_rows(db_url):
    run_load(sample_tables(price=0.4), db_url)
    run_load(sample_tables(price=0.6), db_url)

    assert query(db_url, "SELECT price FROM price_history") == [(0.6,)]
    assert query(db_url, "SELECT price FROM snapshots") == [(0.6,)]


def test_text_and_missing_values_load_exactly(db_url):
    run_load(sample_tables(), db_url)

    assert query(db_url, "SELECT question, category, event_id FROM markets") == [('Will "X", Y\nhappen?', None, None)]


def test_runs_are_logged_including_failures(db_url):
    run_load(sample_tables(), db_url)

    bad = sample_tables()
    bad["price_history"]["price"] = 1.5  # breaks the database's CHECK (price BETWEEN 0 AND 1)
    with pytest.raises(psycopg.errors.CheckViolation):
        run_load(bad, db_url)

    runs = query(db_url, "SELECT status, row_counts->>'markets', error IS NOT NULL FROM pipeline_runs ORDER BY run_id")
    assert runs == [("success", "1", False), ("failed", None, True)]
    # The failed run changed nothing: its transaction was rolled back
    assert query(db_url, "SELECT price FROM price_history") == [(0.4,)]
