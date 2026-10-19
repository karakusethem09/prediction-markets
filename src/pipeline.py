"""One command for a pipeline run: add newly resolved markets to the database.

    python src/pipeline.py                 # local database
    DATABASE_URL=... python src/pipeline.py  # any other database (e.g. Supabase, from GitHub Actions)

Steps: record the run → find markets already in the database → download only what's
new → take snapshots → run data quality checks → load in one transaction → record
success, or failure with the reason. Works for any platform (see collect_updates).
"""
import argparse

import psycopg

from load.postgres import DATABASE_URL, finish_run, load_tables, start_run
from transform.normalize import collect_updates
from transform.quality import run_checks
from transform.snapshots import take_snapshots


class DataQualityError(Exception):
    """A check failed, so nothing was loaded."""


def run_pipeline(database_url=DATABASE_URL, with_prices=False):
    """Run once. Returns the rows loaded per table; raises if anything fails (the run is logged either way)."""
    with psycopg.connect(database_url, autocommit=True) as log_conn:
        run_id = start_run(log_conn)
        checks = None
        try:
            existing = {row[0] for row in log_conn.execute("SELECT market_id FROM markets")}
            updates = collect_updates(existing)
            snapshots = take_snapshots(updates.markets, updates.prices)
            print(f"{len(updates.markets)} new markets, {len(snapshots)} new snapshots, "
                  f"{len(updates.failed_downloads)} price downloads failed (retried next run)")

            checks = run_checks(updates.eligible_markets, len(existing), updates.markets, snapshots)
            for result in checks:
                print(f"  [{'pass' if result['passed'] else 'FAIL'}] {result['name']}: {result['detail']}")
            if not all(result["passed"] for result in checks):
                raise DataQualityError("data quality checks failed, nothing was loaded")

            tables = {"markets": updates.markets, "snapshots": snapshots}
            if with_prices:  # only the local database keeps the full price history
                tables = {"markets": updates.markets, "price_history": updates.prices, "snapshots": snapshots}
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
