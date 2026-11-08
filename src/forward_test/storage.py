"""Read and write the forward-test tables. Works on any platform's data.

The collector never changes the schema: `migrate` is a separate, deliberate command
(python -m forward_test migrate), and a collection refuses to run until the migrations are applied.
"""
from pathlib import Path

import pandas as pd
import psycopg
from psycopg.types.json import Jsonb

from forward_test.models import FIRST_POLL_CLOSE

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_FILES = [
    PROJECT_ROOT / "sql" / "forward_test.sql",  # the original tables
    PROJECT_ROOT / "sql" / "migrations" / "002_observations_and_election_pilot.sql",
]
REQUIRED_MIGRATION = "002_observations_and_election_pilot"

ENTRY_COLUMNS = [
    "market_id", "platform", "event_id", "question", "token_id", "scheduled_end", "recorded_at", "run_id",
    "status", "best_bid", "best_ask", "ask_size_at_best", "midpoint", "spread", "last_trade_price",
    "tick_size", "min_order_size", "fill_price_100", "fill_price_1000", "volume_at_entry",
    "liquidity_at_entry", "in_group_a", "observed_outcome", "outcome_names", "token_ids",
    "book_exchange_time", "book_received_at",
]
OBSERVATION_COLUMNS = [
    "experiment", "run_id", "market_id", "platform", "event_id", "event_title", "classification", "event_tags",
    "question", "outcome_names", "token_ids", "observed_outcome", "observed_token_id", "scheduled_end",
    "market_active", "market_closed", "accepting_orders", "volume_usd", "liquidity_usd", "run_started_at",
    "metadata_requested_at", "metadata_received_at", "book_requested_at", "book_received_at",
    "book_exchange_time", "observed_at", "book_valid", "book_error", "best_bid", "best_ask", "ask_size_at_best",
    "midpoint", "spread", "bid_levels", "ask_levels", "bid_depth_shares", "ask_depth_shares", "ask_depth_usd",
    "tick_size", "min_order_size", "last_trade_price", "book_hash", "meets_group_a_price",
    "meets_group_a_volume", "metadata_sha256", "book_sha256",
]


class SchemaNotReady(Exception):
    """The database hasn't had the forward-test migrations applied."""


def migrate(conn):
    """Create or upgrade the forward-test tables. Only run on purpose; additive and safe to repeat."""
    for path in SCHEMA_FILES:
        conn.execute(path.read_text())


def check_schema(conn):
    ready = conn.execute("SELECT to_regclass('forward_test_migrations') IS NOT NULL").fetchone()[0] and \
        conn.execute("SELECT EXISTS (SELECT FROM forward_test_migrations WHERE name = %s)",
                     (REQUIRED_MIGRATION,)).fetchone()[0]
    if not ready:
        raise SchemaNotReady(f"migration {REQUIRED_MIGRATION} isn't applied; run: python -m forward_test migrate")


def start_run(log_conn, now=None, settings=None):
    """Record a new run (the schema must already be in place). Returns (run_id, started_at).

    started_at is the moment the run's decisions use: `now` if given (tests simulate other days),
    otherwise the database clock, in UTC. settings: how the run is configured (experiments, volume floor).
    """
    check_schema(log_conn)
    return log_conn.execute(
        "INSERT INTO forward_test_runs (status, started_at, settings) VALUES ('running', coalesce(%s, now()), %s) "
        "RETURNING run_id, started_at", (now, Jsonb(settings) if settings is not None else None)).fetchone()


def finish_run(log_conn, run_id, status, entered=None, missed=None, settled=None, observed=None, invalid=None,
               error=None):
    log_conn.execute(
        "UPDATE forward_test_runs SET status = %s, finished_at = now(), entered = %s, missed = %s, settled = %s, "
        "observed = %s, invalid = %s, error = %s WHERE run_id = %s",
        (status, entered, missed, settled, observed, invalid, error, run_id))


def test_start(conn):
    """When the forward test began: the start of its first run. Never reset."""
    return conn.execute("SELECT min(started_at) FROM forward_test_runs").fetchone()[0]


def successful_run_times(conn):
    return [row[0] for row in conn.execute("SELECT started_at FROM forward_test_runs WHERE status = 'success'")]


def recorded_market_ids(conn):
    return {row[0] for row in conn.execute("SELECT market_id FROM forward_test_entries")}


def open_market_ids(conn):
    """Recorded markets still waiting for a final result."""
    return [row[0] for row in conn.execute("SELECT market_id FROM forward_test_entries WHERE status = 'open'")]


def save(conn, entries=(), settlements=(), observations=(), raw=()):
    """Write one run's results in the caller's transaction (all of it, or nothing).

    - raw: evidence bodies; content already stored is skipped (same sha256)
    - observations: append-only; a rerun of the same run and market is skipped, never overwritten
    - entries: one per market; a market already recorded is left alone. Each entry is linked to its
      group_a_7day observation from the same run.
    - settlements: (market_id, status, outcome, final_volume) for entries whose result became final
    """
    observation_columns = ", ".join(OBSERVATION_COLUMNS)
    entry_columns = ", ".join(ENTRY_COLUMNS)
    with conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO forward_test_raw (sha256, kind, body_gzip, size_bytes) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (sha256) DO NOTHING",
            [(r["sha256"], r["kind"], r["body_gzip"], r["size_bytes"]) for r in raw])
        cursor.executemany(
            f"INSERT INTO forward_test_observations ({observation_columns}) "
            f"VALUES ({', '.join(['%s'] * len(OBSERVATION_COLUMNS))}) "
            "ON CONFLICT (experiment, run_id, market_id) DO NOTHING",
            [[o.get(c) for c in OBSERVATION_COLUMNS] for o in observations])
        cursor.executemany(
            f"INSERT INTO forward_test_entries ({entry_columns}, observation_id) "
            f"VALUES ({', '.join(['%s'] * len(ENTRY_COLUMNS))}, "
            "(SELECT observation_id FROM forward_test_observations "
            " WHERE experiment = 'group_a_7day' AND run_id = %s AND market_id = %s)) "
            "ON CONFLICT (market_id) DO NOTHING",
            [[e.get(c) for c in ENTRY_COLUMNS] + [e["run_id"], e["market_id"]] for e in entries])
        cursor.executemany(
            "UPDATE forward_test_entries SET status = %s, outcome = %s, final_volume = %s, settled_at = now() "
            "WHERE market_id = %s AND status = 'open'",
            [(status, outcome, final_volume, market_id) for market_id, status, outcome, final_volume in settlements])


def saved_counts(conn, run_id):
    """What the database now holds for one run, read back after it was saved: observation rows per
    experiment, and how many of them point to archived book evidence."""
    rows = conn.execute(
        "SELECT experiment, count(*), count(r.sha256) FROM forward_test_observations o "
        "LEFT JOIN forward_test_raw r ON r.sha256 = o.book_sha256 WHERE o.run_id = %s GROUP BY experiment",
        (run_id,)).fetchall()
    return {experiment: {"observations": n, "with_book_evidence": with_book} for experiment, n, with_book in rows}


def health(conn):
    """The numbers behind `python -m forward_test health` (read-only queries)."""
    runs = conn.execute(
        "SELECT count(*), count(*) FILTER (WHERE status = 'success'), count(*) FILTER (WHERE status = 'failed'), "
        "count(*) FILTER (WHERE status = 'running') FROM forward_test_runs").fetchone()
    latest_run = conn.execute(
        "SELECT run_id, status, started_at, error FROM forward_test_runs ORDER BY run_id DESC LIMIT 1").fetchone()
    latest_failure = conn.execute(
        "SELECT run_id, started_at, error FROM forward_test_runs WHERE status = 'failed' "
        "ORDER BY run_id DESC LIMIT 1").fetchone()
    # Each successful run's experiments. Runs logged before 2026-11-03 have no settings: their experiments are the
    # ones they saved observations for, and runs without any (all online runs before migration 002) ran Group A
    success_times = {}
    for experiment, started_at in conn.execute(
            "SELECT x.experiment, r.started_at FROM forward_test_runs r CROSS JOIN LATERAL "
            "  jsonb_array_elements_text(coalesce(r.settings -> 'experiments', "
            "    (SELECT jsonb_agg(DISTINCT o.experiment) FROM forward_test_observations o WHERE o.run_id = r.run_id), "
            "    '[\"group_a_7day\"]')) AS x(experiment) "
            "WHERE r.status = 'success' ORDER BY r.started_at"):
        success_times.setdefault(experiment, []).append(started_at)
    last_success = {experiment: times[-1] for experiment, times in success_times.items()}
    observations = {row[0]: dict(zip(
        ["observations", "valid", "invalid", "markets", "contracts", "events", "latest_observed_at",
         "before_first_poll_close", "group_a_matches"], row[1:])) for row in conn.execute(
        "SELECT experiment, count(*), count(*) FILTER (WHERE book_valid), count(*) FILTER (WHERE NOT book_valid), "
        "count(DISTINCT market_id), count(DISTINCT observed_token_id), count(DISTINCT event_id), max(observed_at), "
        "count(*) FILTER (WHERE observed_at < %s), "
        "count(*) FILTER (WHERE meets_group_a_price AND meets_group_a_volume) "
        "FROM forward_test_observations GROUP BY experiment", (FIRST_POLL_CLOSE,))}
    entries = dict(conn.execute("SELECT status, count(*) FROM forward_test_entries GROUP BY status").fetchall())
    group_a = conn.execute("SELECT count(*) FROM forward_test_entries WHERE in_group_a").fetchone()[0]
    # A stored 'missed' label is confirmed only if no successful run happened in the market's first day
    # after its 7-day mark (the corrected rule, models.entry_decision)
    missed_confirmed = conn.execute(
        "SELECT count(*) FROM forward_test_entries e WHERE e.status = 'missed' AND NOT EXISTS ("
        "  SELECT FROM forward_test_runs r WHERE r.status = 'success' "
        "  AND r.started_at >= e.scheduled_end - interval '7 days' AND r.started_at < e.scheduled_end - interval '6 days')"
        ).fetchone()[0]
    # Storage: the whole database (what the hosting plan limits), its largest tables, and how much the
    # forward-test tables grew in the last 7 days (raw bodies exactly; observation rows at their average size)
    database_bytes, raw_week, observations_week, observation_rows, observation_bytes = conn.execute(
        "SELECT pg_database_size(current_database()), "
        "(SELECT coalesce(sum(length(body_gzip)), 0) FROM forward_test_raw WHERE stored_at > now() - interval '7 days'), "
        "(SELECT count(*) FROM forward_test_observations WHERE observed_at > now() - interval '7 days'), "
        "(SELECT count(*) FROM forward_test_observations), pg_total_relation_size('forward_test_observations')").fetchone()
    largest_tables = conn.execute(
        "SELECT c.relname, pg_total_relation_size(c.oid) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relkind = 'r' AND n.nspname = ANY(current_schemas(false)) ORDER BY 2 DESC LIMIT 5").fetchall()
    week_bytes = raw_week + (observations_week * observation_bytes / observation_rows if observation_rows else 0)
    return {"runs": dict(zip(["total", "success", "failed", "running"], runs)), "latest_run": latest_run,
            "latest_failure": latest_failure, "last_success": last_success, "success_times": success_times,
            "observations": observations, "entries": entries, "group_a_entries": group_a,
            "missed_confirmed": missed_confirmed,
            "storage": {"database_bytes": database_bytes, "largest_tables": largest_tables,
                        "forward_test_week_bytes": week_bytes}}


def read_table(database_url, sql):
    """Run a read-only query (read-only session) and return a DataFrame."""
    with psycopg.connect(database_url) as conn:
        conn.read_only = True
        cursor = conn.execute(sql)
        rows = cursor.fetchall()
        columns = [d.name for d in cursor.description]
    return pd.DataFrame(rows, columns=columns)


def load_entries(database_url):
    """Every recorded entry, for the report (read-only session)."""
    return read_table(database_url, "SELECT * FROM forward_test_entries")
