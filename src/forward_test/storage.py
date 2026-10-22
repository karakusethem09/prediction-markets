"""Read and write the forward-test tables (sql/forward_test.sql). Works on any platform's data."""
from pathlib import Path

import pandas as pd
import psycopg

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_FILE = PROJECT_ROOT / "sql" / "forward_test.sql"

ENTRY_COLUMNS = [
    "market_id", "platform", "event_id", "question", "token_id", "scheduled_end", "recorded_at", "run_id",
    "status", "best_bid", "best_ask", "ask_size_at_best", "midpoint", "spread", "last_trade_price",
    "tick_size", "min_order_size", "fill_price_100", "fill_price_1000", "volume_at_entry",
    "liquidity_at_entry", "in_group_a",
]


def create_tables(conn):
    conn.execute(SCHEMA_FILE.read_text())


def start_run(log_conn, now=None):
    """Make sure the tables exist and record a new run. Returns (run_id, started_at).

    started_at is the moment the run's decisions use: `now` if given (tests simulate other days),
    otherwise the database clock.
    """
    create_tables(log_conn)
    return log_conn.execute(
        "INSERT INTO forward_test_runs (status, started_at) VALUES ('running', coalesce(%s, now())) "
        "RETURNING run_id, started_at", (now,)).fetchone()


def finish_run(log_conn, run_id, status, entered=None, missed=None, settled=None, error=None):
    log_conn.execute(
        "UPDATE forward_test_runs SET status = %s, finished_at = now(), entered = %s, missed = %s, "
        "settled = %s, error = %s WHERE run_id = %s",
        (status, entered, missed, settled, error, run_id))


def test_start(conn):
    """When the forward test began: the start of its first run."""
    return conn.execute("SELECT min(started_at) FROM forward_test_runs").fetchone()[0]


def recorded_market_ids(conn):
    return {row[0] for row in conn.execute("SELECT market_id FROM forward_test_entries")}


def open_market_ids(conn):
    """Recorded markets still waiting for a final result."""
    return [row[0] for row in conn.execute("SELECT market_id FROM forward_test_entries WHERE status = 'open'")]


def save(conn, entries, settlements):
    """Add new entries and record settlements, in the caller's transaction.

    entries: dicts with ENTRY_COLUMNS. A market already recorded is left alone (ON CONFLICT DO
    NOTHING), so re-running a collection never duplicates or overwrites an entry.
    settlements: (market_id, status, outcome, final_volume) for markets whose result became final.
    """
    columns = ", ".join(ENTRY_COLUMNS)
    placeholders = ", ".join(["%s"] * len(ENTRY_COLUMNS))
    with conn.cursor() as cursor:
        cursor.executemany(
            f"INSERT INTO forward_test_entries ({columns}) VALUES ({placeholders}) ON CONFLICT (market_id) DO NOTHING",
            [[entry.get(column) for column in ENTRY_COLUMNS] for entry in entries])
        cursor.executemany(
            "UPDATE forward_test_entries SET status = %s, outcome = %s, final_volume = %s, settled_at = now() "
            "WHERE market_id = %s AND status = 'open'",
            [(status, outcome, final_volume, market_id) for market_id, status, outcome, final_volume in settlements])


def load_entries(database_url):
    """Every recorded entry, for the report (read-only session)."""
    with psycopg.connect(database_url) as conn:
        conn.read_only = True
        cursor = conn.execute(f"SELECT {', '.join(ENTRY_COLUMNS)}, settled_at, outcome, final_volume FROM forward_test_entries")
        rows = cursor.fetchall()
        columns = [d.name for d in cursor.description]
    return pd.DataFrame(rows, columns=columns)
