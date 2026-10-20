"""Dashboard tests: run the real dashboard (Streamlit's AppTest, no browser) against a small
synthetic dataset in the throwaway test schema (see conftest.py). Because the data is made up
and fixed, the tests check exact numbers, and they run everywhere, including CI."""
from pathlib import Path

import pandas as pd
import psycopg
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from load.postgres import finish_run, run_load, start_run

DASHBOARD = Path(__file__).resolve().parents[1] / "app" / "dashboard.py"
LONG_MARKETS, SHORT_MARKETS = 40, 10  # long ones have 1/7/30-day snapshots, short ones only 1-day


def sample_tables():
    """50 made-up markets closing every 12 days from March 2025; every 5th traded $30M, the rest $1.5M."""
    markets, snapshots = [], []
    first_close = pd.Timestamp("2025-03-01 12:00", tz="UTC")
    for i in range(LONG_MARKETS + SHORT_MARKETS):
        is_long = i < LONG_MARKETS
        close = first_close + pd.Timedelta(days=12 * i)
        markets.append({
            "market_id": f"poly:{i}", "platform": "polymarket", "event_id": f"poly:e{i % 10}",
            "question": f"Sample market {i}?", "category": None,
            "open_time": close - pd.Timedelta(days=45 if is_long else 3), "close_time": close,
            "outcome": int(i % 3 == 0), "volume_usd": 30_000_000.0 if i % 5 == 0 else 1_500_000.0,
        })
        for horizon in ([1, 7, 30] if is_long else [1]):
            target = close - pd.Timedelta(days=horizon)
            snapshots.append({
                "market_id": f"poly:{i}", "horizon_days": horizon, "target_time": target,
                "price_time": target - pd.Timedelta(hours=1),
                "price": ((i * 7 + horizon) % 19 + 0.5) / 20,  # spread over the whole 0-1 range
            })
    return pd.DataFrame(markets), pd.DataFrame(snapshots)


@pytest.fixture
def dashboard_db(db_url):
    """The test schema filled with the sample data and one successful pipeline run."""
    markets, snapshots = sample_tables()
    run_load({"markets": markets, "snapshots": snapshots}, db_url)  # logs a successful run itself
    return db_url


def open_dashboard(database_url):
    st.cache_data.clear()  # the dashboard caches its data; start every test from the database
    app = AppTest.from_file(str(DASHBOARD), default_timeout=60)
    app.secrets["DATABASE_URL"] = database_url  # the same way Streamlit Cloud passes the address
    return app.run()


@pytest.fixture
def app(dashboard_db):
    return open_dashboard(dashboard_db)


def table_with_column(app, column):
    """The dashboard table that has this column (found by content, not by position on the page)."""
    return next(df.value for df in app.dataframe if column in df.value.columns)


def test_headline_numbers(app):
    assert not app.exception
    assert [m.label for m in app.metric] == ["Markets", "Avg. gap · 1D", "Avg. gap · 7D", "Avg. gap · 30D"]
    assert app.metric[0].value == str(LONG_MARKETS)  # by default only markets with all three horizons


def test_same_markets_toggle(app):
    assert table_with_column(app, "brier")["markets"].tolist() == [40, 40, 40]

    app.toggle[0].set_value(False).run()

    assert not app.exception
    assert table_with_column(app, "brier")["markets"].tolist() == [50, 40, 40]  # short markets join at 1D


def test_volume_filter(app):
    app.select_slider[0].set_value(25_000_000).run()  # only the $30M markets: every 5th of the 40 long ones
    assert app.metric[0].value == "8"


def test_horizon_buttons_switch_the_chart(app):
    for button in ["7D", "30D", "1D"]:
        app.get("button_group")[0].set_value(button).run()
        assert not app.exception


def test_recently_resolved_lists_the_newest_markets_first(app):
    recent = table_with_column(app, "result")
    assert len(recent) == 15
    assert recent["question"].iloc[0] == "Sample market 49?"  # the last one to close
    assert recent["close_time"].is_monotonic_decreasing
    assert set(recent["result"]) <= {"✓", "✗"}


def test_status_pill_shows_the_last_successful_update(app):
    pill = next(m.value for m in app.markdown if 'class="status-pill"' in m.value)
    assert "LIVE" in pill and "50 new markets" in pill


def test_failed_latest_update_shows_a_warning(dashboard_db):
    with psycopg.connect(dashboard_db, autocommit=True) as conn:
        run_id = start_run(conn)
        finish_run(conn, run_id, "failed", error="simulated failure")

    app = open_dashboard(dashboard_db)

    assert not app.exception
    pill = next(m.value for m in app.markdown if 'class="status-pill"' in m.value)
    assert "UPDATE FAILED" in pill


def test_impossible_filter_shows_a_warning_not_an_error(app):
    app.select_slider[0].set_value(50_000_000).run()  # no sample market traded $50M

    assert not app.exception
    assert app.warning[0].value.startswith("No markets match these filters")
