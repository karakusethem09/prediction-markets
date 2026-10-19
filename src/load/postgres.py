"""Load the normalized tables into PostgreSQL with upserts, and log every run in pipeline_runs.

Upsert = insert new rows, update rows whose key already exists. So rerunning the
load never creates duplicates: it just brings the database up to date.
"""
import argparse
import os
from pathlib import Path

import psycopg
from psycopg.types.json import Json

from transform.normalize import build_normalized_tables
from transform.snapshots import take_snapshots

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_FILE = PROJECT_ROOT / "sql" / "schema.sql"

# The local Docker database by default. To load somewhere else (e.g. Supabase later),
# set the DATABASE_URL environment variable instead of changing this code.
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/prediction_markets"
)

# Each table's primary key: a row whose key already exists is updated, not duplicated
TABLE_KEYS = {
    "markets": ["market_id"],
    "price_history": ["market_id", "fidelity", "time"],
    "snapshots": ["market_id", "horizon_days"],
}
COPY_CHUNK_ROWS = 500_000  # send big tables in pieces so memory use stays small


def create_tables(conn):
    conn.execute(SCHEMA_FILE.read_text())


def upsert(conn, table, df):
    """Upsert a DataFrame into a table. Returns the number of rows sent."""
    columns = ", ".join(df.columns)
    keys = TABLE_KEYS[table]
    staging = f"staging_{table}"  # table names come from TABLE_KEYS, never from user input

    # 1. Stream the rows into a temporary copy of the table with COPY (much faster than INSERTs)
    conn.execute(f"CREATE TEMP TABLE {staging} (LIKE {table}) ON COMMIT DROP")
    with conn.cursor().copy(f"COPY {staging} ({columns}) FROM STDIN (FORMAT csv)") as copy:
        for start in range(0, len(df), COPY_CHUNK_ROWS):
            # In CSV, an empty unquoted value means NULL, which is how pandas writes missing values
            copy.write(df.iloc[start:start + COPY_CHUNK_ROWS].to_csv(index=False, header=False))

    # 2. Move them into the real table: new keys are inserted, existing keys are updated
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in df.columns if c not in keys)
    conn.execute(
        f"INSERT INTO {table} ({columns}) SELECT {columns} FROM {staging} "
        f"ON CONFLICT ({', '.join(keys)}) DO UPDATE SET {updates}"
    )
    return len(df)


def start_run(log_conn):
    """Make sure the tables exist and record a new run as 'running'. Returns its run_id.

    log_conn should be an autocommit connection, so the log survives a failed load.
    """
    create_tables(log_conn)
    return log_conn.execute(
        "INSERT INTO pipeline_runs (status) VALUES ('running') RETURNING run_id"
    ).fetchone()[0]


def finish_run(log_conn, run_id, status, row_counts=None, checks=None, error=None):
    """Record how a run ended: 'success' or 'failed', with row counts, check results and any error."""
    log_conn.execute(
        "UPDATE pipeline_runs SET status = %s, finished_at = now(), row_counts = %s, checks = %s, error = %s "
        "WHERE run_id = %s",
        (status, Json(row_counts) if row_counts is not None else None,
         Json(checks) if checks is not None else None, error, run_id),
    )


def load_tables(tables, database_url=DATABASE_URL):
    """Upsert all tables in one transaction: either every table is loaded, or nothing changes.

    tables: {"markets": df, "price_history": df, "snapshots": df}, in this order
    (markets first, because the other tables refer to it). Returns rows sent per table.
    """
    with psycopg.connect(database_url) as conn:
        return {table: upsert(conn, table, df) for table, df in tables.items()}


def run_load(tables, database_url=DATABASE_URL):
    """Load all tables in one transaction and record the run in pipeline_runs."""
    with psycopg.connect(database_url, autocommit=True) as log_conn:
        run_id = start_run(log_conn)
        try:
            row_counts = load_tables(tables, database_url)
        except Exception as error:
            finish_run(log_conn, run_id, "failed", error=str(error))
            raise
        finish_run(log_conn, run_id, "success", row_counts=row_counts)
    return row_counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--skip-prices", action="store_true",
        help="load only markets and snapshots (the online database doesn't keep the 3M price rows)",
    )
    args = parser.parse_args()

    markets, prices = build_normalized_tables(verbose=True)
    snapshots = take_snapshots(markets, prices)

    tables = {"markets": markets, "price_history": prices, "snapshots": snapshots}
    if args.skip_prices:
        del tables["price_history"]
    row_counts = run_load(tables)
    print(f"\nLoaded: {row_counts}")

    with psycopg.connect(DATABASE_URL) as conn:
        for table in TABLE_KEYS:
            print(f"  {table}: {conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]:,} rows in the database")
        runs = conn.execute("SELECT count(*) FROM pipeline_runs WHERE status = 'success'").fetchone()[0]
        print(f"  pipeline_runs: {runs} successful runs so far")
