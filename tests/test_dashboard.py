"""Smoke test: the dashboard runs without errors on the real database.
Skipped if PostgreSQL isn't running (start it with: docker compose up -d)."""
from pathlib import Path

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from load.postgres import DATABASE_URL

DASHBOARD = Path(__file__).resolve().parents[1] / "app" / "dashboard.py"


@pytest.fixture
def app():
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=2) as conn:
            has_data = conn.execute("SELECT to_regclass('snapshots') IS NOT NULL").fetchone()[0] and \
                conn.execute("SELECT EXISTS (SELECT FROM snapshots)").fetchone()[0]
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL isn't running (start it with: docker compose up -d)")
    if not has_data:  # e.g. the empty test database in GitHub Actions
        pytest.skip("The database has no data to show (run src/load/postgres.py first)")
    return AppTest.from_file(str(DASHBOARD), default_timeout=60).run()


def test_dashboard_runs_and_shows_headline_numbers(app):
    assert not app.exception
    assert app.metric[0].label == "Markets"
    assert len(app.metric) == 4  # markets + one gap per horizon (1, 7, 30 days)


def table_with_column(app, column):
    """The dashboard table that has this column (found by content, not by position on the page)."""
    return next(df.value for df in app.dataframe if column in df.value.columns)


def test_same_markets_is_the_default_and_can_be_switched_off(app):
    scores = table_with_column(app, "brier")  # the "Accuracy scores" table
    assert scores["markets"].nunique() == 1  # by default every horizon uses the same markets

    app.toggle[0].set_value(False).run()

    assert not app.exception
    scores = table_with_column(app, "brier")
    assert scores["markets"].is_monotonic_decreasing  # all markets: fewer at longer horizons


def test_horizon_buttons_switch_the_chart(app):
    for button in ["7D", "30D", "1D"]:
        app.get("button_group")[0].set_value(button).run()
        assert not app.exception


def test_recently_resolved_lists_the_newest_markets_first(app):
    recent = table_with_column(app, "result")
    assert 0 < len(recent) <= 15
    assert recent["close_time"].is_monotonic_decreasing
    assert set(recent["result"]) <= {"✓", "✗"}


def test_impossible_filter_shows_a_warning_not_an_error(app):
    app.select_slider[0].set_value(50_000_000).run()  # $50M minimum volume...
    first_year = app.slider[0].min
    app.slider[0].set_value((first_year, first_year)).run()  # ...in the first year only

    assert not app.exception
    assert app.warning or app.metric  # either "no markets" or a smaller result, never a crash
