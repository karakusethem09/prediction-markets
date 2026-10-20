"""One command for a pipeline run: add newly resolved markets, and retry markets whose snapshots
are still missing.

    python src/pipeline.py                   # local database
    DATABASE_URL=... python src/pipeline.py  # any other database (e.g. Supabase, from GitHub Actions)

Steps: record the run → read what's stored (markets, their processing status) → download new
markets and re-download markets marked retry_needed → take snapshots → work out each market's
status (complete / retry_needed / unavailable) → data quality checks → load everything in one
transaction → record success, or failure with the reason. Works for any platform (see collect_updates).
"""
import argparse

import pandas as pd
import psycopg

from load.postgres import DATABASE_URL, finish_run, load_tables, start_run
from transform.completeness import processing_status
from transform.normalize import collect_updates
from transform.quality import run_checks
from transform.snapshots import take_snapshots


class DataQualityError(Exception):
    """A blocking check failed, so nothing was loaded."""


def stored_markets(log_conn):
    """Every stored market with its times, stored snapshot horizons and processing status (if any)."""
    rows = log_conn.execute("""
        SELECT m.market_id, m.open_time, m.close_time,
               coalesce(array_agg(s.horizon_days) FILTER (WHERE s.horizon_days IS NOT NULL), '{}'),
               p.status, p.attempts
        FROM markets m
        LEFT JOIN snapshots s USING (market_id)
        LEFT JOIN processing_status p USING (market_id)
        GROUP BY m.market_id, m.open_time, m.close_time, p.status, p.attempts
    """).fetchall()
    return pd.DataFrame(rows, columns=["market_id", "open_time", "close_time", "found", "status", "attempts"])


def run_pipeline(database_url=DATABASE_URL, with_prices=False, now=None):
    """Run once. Returns the rows loaded per table; raises if anything fails (the run is logged either way)."""
    now = now or pd.Timestamp.now(tz="UTC")
    with psycopg.connect(database_url, autocommit=True) as log_conn:
        run_id = start_run(log_conn)
        checks = None
        try:
            stored = stored_markets(log_conn)
            to_retry = stored[stored["status"] == "retry_needed"]
            # Markets saved before statuses existed: give them a status from what's stored
            untracked = stored[stored["status"].isna()]

            updates = collect_updates(set(stored["market_id"]), set(to_retry["market_id"]))
            processed = pd.concat([updates.markets, updates.retried], ignore_index=True)
            snapshots = take_snapshots(processed, updates.prices)

            # Which horizons each market has now: earlier snapshots plus this run's
            found = {row.market_id: set(row.found) for row in stored.itertuples()}
            for snapshot in snapshots.itertuples():
                found.setdefault(snapshot.market_id, set()).add(snapshot.horizon_days)
            # This run is attempt 1 for new and untracked markets, one more for every retry
            # (also for a retry the download no longer lists, so it eventually stops being retried)
            attempts = {market_id: 1 for market_id in pd.concat([updates.markets["market_id"], untracked["market_id"]])}
            attempts.update({row.market_id: int(row.attempts) + 1 for row in to_retry.itertuples()})
            needs_status = pd.concat([updates.markets, to_retry, untracked], ignore_index=True)
            # Had price history: got some this run, or already has a snapshot from an earlier one
            markets_with_prices = set(updates.prices["market_id"]) | {m for m, hs in found.items() if hs}
            statuses = processing_status(needs_status[["market_id", "open_time", "close_time"]],
                                         found, attempts, now, markets_with_prices)

            print(f"{len(updates.markets)} new markets, {len(updates.retried)} retried, "
                  f"{len(snapshots)} snapshots, {len(updates.failed_downloads)} price downloads failed")
            checks = run_checks(updates.eligible_markets, len(stored), updates.markets, processed,
                                snapshots, statuses)
            for result in checks:
                label = "pass" if result["passed"] else ("FAIL" if result["blocking"] else "warn")
                print(f"  [{label}] {result['name']}: {result['detail']}")
            if not all(result["passed"] for result in checks if result["blocking"]):
                raise DataQualityError("data quality checks failed, nothing was loaded")

            # markets first: the other tables refer to it
            tables = {"markets": updates.markets}
            if with_prices:  # only the local database keeps the full price history
                tables["price_history"] = updates.prices
            tables["snapshots"] = snapshots
            tables["processing_status"] = statuses
            row_counts = load_tables(tables, database_url)
        except Exception as error:
            finish_run(log_conn, run_id, "failed", checks=checks, error=f"{type(error).__name__}: {error}")
            raise
        finish_run(log_conn, run_id, "success", row_counts=row_counts, checks=checks)
    return row_counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Add newly resolved markets to the database.")
    parser.add_argument("--with-prices", action="store_true", help="also load price history (local database only)")
    args = parser.parse_args()
    print("Loaded:", run_pipeline(with_prices=args.with_prices))
