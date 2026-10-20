"""Tests for possible snapshots and processing statuses."""
import pandas as pd
import pytest

from transform.completeness import MAX_ATTEMPTS, decide_status, possible_horizons, processing_status

CLOSE = pd.Timestamp("2026-10-10 12:00", tz="UTC")
NOW = pd.Timestamp("2026-10-11 12:00", tz="UTC")  # one day after close


@pytest.mark.parametrize("days_open, expected", [
    (3, [1]),  # can't have a 7- or 30-day price: that's complete, not missing
    (7, [1, 7]),  # open exactly 7 days: the 7-day moment is its opening moment
    (10, [1, 7]),
    (45, [1, 7, 30]),
])
def test_possible_horizons_depend_on_how_long_the_market_was_open(days_open, expected):
    assert possible_horizons(CLOSE - pd.Timedelta(days=days_open), CLOSE) == expected


def status(possible, found, attempts=1, now=NOW, has_prices=False):
    return decide_status(possible, found, attempts, CLOSE, now, has_prices)


def test_all_possible_snapshots_present_is_complete():
    assert status([1], [1], has_prices=True) == ("complete", None)


def test_no_price_history_yet_is_retried():
    assert status([1, 7], [1]) == ("retry_needed", "no price history yet")


def test_prices_that_dont_cover_the_moment_are_not_retried():
    # Prices came back but none at the 7-day moment (e.g. trading started later): retrying can't fix that
    result, reason = status([1, 7], [1], has_prices=True)
    assert result == "unavailable"
    assert "doesn't cover that moment" in reason


def test_gives_up_after_max_attempts():
    assert status([1], [], attempts=MAX_ATTEMPTS) == ("unavailable", f"no price history after {MAX_ATTEMPTS} attempts")


def test_gives_up_on_markets_that_closed_long_ago():
    result, reason = status([1], [], now=CLOSE + pd.Timedelta(days=15))
    assert result == "unavailable"
    assert "closed over 14 days ago" in reason


def test_processing_status_rows():
    markets = pd.DataFrame({
        "market_id": ["short", "gap", "waiting"],
        "open_time": [CLOSE - pd.Timedelta(days=3), CLOSE - pd.Timedelta(days=40), CLOSE - pd.Timedelta(days=3)],
        "close_time": [CLOSE, CLOSE, CLOSE],
    })
    found = {"short": {1}, "gap": {1, 30}}  # "gap" lacks its 7-day snapshot; "waiting" has none yet

    rows = processing_status(markets, found, {"short": 1, "gap": 2, "waiting": 1}, NOW,
                             markets_with_prices={"short", "gap"}).set_index("market_id")

    assert rows.loc["short", "status"] == "complete"
    assert rows.loc["gap", "status"] == "unavailable"  # it has prices, just none at the 7-day moment
    assert rows.loc["gap", "possible_horizons"] == [1, 7, 30]
    assert rows.loc["gap", "found_horizons"] == [1, 30]
    assert rows.loc["waiting", "status"] == "retry_needed"  # no prices at all yet
    assert rows.loc["waiting", "attempts"] == 1
