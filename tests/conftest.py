"""Shared test fixtures. pytest loads this file automatically."""
import os

import psycopg
import pytest

# Tests use their own variable, never DATABASE_URL: when DATABASE_URL points at the live
# online database (e.g. in GitHub Actions), tests must still never touch it.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/prediction_markets"
)
TEST_SCHEMA = "pytest_load"


@pytest.fixture
def db_url():
    """A database URL whose tables all live in an empty test schema, dropped afterwards.
    Skips the test if PostgreSQL isn't running."""
    try:
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True, connect_timeout=2) as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE")
            conn.execute(f"CREATE SCHEMA {TEST_SCHEMA}")
    except psycopg.OperationalError:
        message = "PostgreSQL isn't running (start it with: docker compose up -d)"
        if os.environ.get("REQUIRE_DATABASE"):  # set in CI: there, a missing database is an error
            pytest.fail(message)
        pytest.skip(message)

    # search_path makes every unqualified table name (markets, snapshots, ...) point to the test schema
    separator = "&" if "?" in TEST_DATABASE_URL else "?"
    yield f"{TEST_DATABASE_URL}{separator}options=-csearch_path%3D{TEST_SCHEMA}"

    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA {TEST_SCHEMA} CASCADE")
