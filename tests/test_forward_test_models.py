"""Tests for the forward test's rules (docs/forward_test_spec.md)."""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from forward_test.models import (
    before_first_poll_close, book_depth, book_summary, classify_event, entry_decision, exchange_time, fill_order,
    fill_price, first_outcome, in_group_a, is_stale, official_settlement, settlement, validate_book,
)

END = pd.Timestamp("2026-11-04 04:59", tz="UTC")  # how Polymarket schedules election-night markets
MARK = END - timedelta(days=7)  # 2026-10-28 04:59 UTC: the 7-day mark
TEST_START = pd.Timestamp("2026-10-22 12:21", tz="UTC")  # when the online test began
LONG_AGO = pd.Timestamp("2026-06-01", tz="UTC")


# ---------- entry eligibility ----------

@pytest.mark.parametrize("hours_after_mark, expected", [
    (-1, "wait"),  # one hour before the mark
    (0, "enter"),  # exactly at the mark
    (5, "enter"),  # the real 2026-10-28 run, 5 hours after the mark
    (23.9, "enter"),  # still inside the first day
])
def test_entry_window_is_the_first_day_after_the_mark(hours_after_mark, expected):
    now = MARK + timedelta(hours=hours_after_mark)
    assert entry_decision(END, now, LONG_AGO, TEST_START) == expected


def test_market_first_seen_too_late_with_no_run_in_its_window_is_missed():
    now = MARK + timedelta(days=2)
    runs = [MARK - timedelta(hours=10), MARK + timedelta(days=1, hours=5)]  # none inside its first day
    assert entry_decision(END, now, LONG_AGO, TEST_START, runs) == "missed"


def test_market_first_seen_late_although_runs_happened_is_not_missed():
    # The 2026-10-28 audit: 12 markets had runs after their mark but weren't returned (under $100k then)
    now = MARK + timedelta(days=2)
    runs = [MARK + timedelta(hours=5), MARK + timedelta(hours=12)]
    assert entry_decision(END, now, LONG_AGO, TEST_START, runs) == "skip"


def test_late_start_never_turns_earlier_marks_into_misses():
    # The test starts today: a market whose mark came before the start was never eligible
    late_start = MARK + timedelta(hours=30)
    assert entry_decision(END, late_start + timedelta(hours=1), LONG_AGO, late_start) == "skip"


def test_market_that_opened_after_its_mark_is_skipped():
    assert entry_decision(END, MARK + timedelta(hours=2), MARK + timedelta(hours=1), TEST_START) == "skip"


def test_a_changed_scheduled_end_is_judged_at_each_run_by_the_end_published_then():
    now = pd.Timestamp("2026-10-28 10:00", tz="UTC")
    assert entry_decision(END, now, LONG_AGO, TEST_START) == "enter"  # published end: Nov 4
    later_end = END + timedelta(days=3)  # if the market's end date had been moved to Nov 7
    assert entry_decision(later_end, now, LONG_AGO, TEST_START) == "wait"


# ---------- order books ----------

def test_book_summary_uses_the_highest_bid_and_lowest_ask():
    bids = [(0.01, 1000.0), (0.20, 50.0), (0.22, 30.0)]  # the API lists bids low to high...
    asks = [(0.99, 500.0), (0.26, 40.0), (0.25, 10.0), (0.25, 5.0)]  # ...and asks high to low
    summary = book_summary(bids, asks)
    assert summary["best_bid"] == 0.22 and summary["best_ask"] == 0.25
    assert summary["ask_size_at_best"] == 15.0
    assert summary["midpoint"] == pytest.approx(0.235)
    assert summary["spread"] == pytest.approx(0.03)


@pytest.mark.parametrize("bids, asks, error", [
    ([(0.20, 10.0)], [], "no asks: nothing to buy"),
    ([(0.30, 10.0)], [(0.25, 10.0)], "crossed or locked book: best bid >= best ask"),
    ([(0.25, 10.0)], [(0.25, 10.0)], "crossed or locked book: best bid >= best ask"),
    ([], [(1.20, 10.0)], "price outside (0, 1): 1.2"),
    ([], [(0.25, 0.0)], "non-positive size: 0.0"),
    ([(0.20, 10.0)], [(0.25, 10.0)], None),
    ([], [(0.25, 10.0)], None),  # no bids is fine: there is still something to buy
])
def test_invalid_books_are_named_explicitly(bids, asks, error):
    assert validate_book(bids, asks) == error


def test_book_depth():
    depth = book_depth([(0.20, 10.0), (0.21, 5.0)], [(0.25, 100.0), (0.30, 50.0)])
    assert depth == {"bid_levels": 2, "ask_levels": 2, "bid_depth_shares": 15.0, "ask_depth_shares": 150.0,
                     "ask_depth_usd": pytest.approx(25 + 15)}


def test_exchange_timestamp_is_utc():
    assert exchange_time("1793229305000") == datetime(2026, 10, 28, 23, 15, 5, tzinfo=timezone.utc)
    assert exchange_time(None) is None


def test_fill_price_walks_up_the_asks():
    asks = [(0.30, 100.0), (0.25, 100.0)]
    assert fill_price(asks, 20) == pytest.approx(0.25)
    assert fill_price(asks, 40) == pytest.approx(40 / (100 + 15 / 0.30))
    assert fill_price(asks, 100) is None


def test_fill_order_reports_contracts_average_price_and_capital():
    asks = [(0.30, 100.0), (0.25, 100.0)]  # 100 contracts at 25 cents ($25), then 100 at 30 cents ($30)
    order = fill_order(asks, 40)
    assert order["filled"] and order["capital"] == pytest.approx(40)
    assert order["shares"] == pytest.approx(100 + 15 / 0.30)  # all 100 at 0.25, then $15 worth at 0.30
    assert order["average_price"] == pytest.approx(40 / order["shares"])  # volume-weighted


def test_an_order_the_book_cant_fill_is_flagged_not_counted_as_filled():
    order = fill_order([(0.25, 100.0), (0.30, 100.0)], 1_000)  # only $55 on offer
    assert not order["filled"] and order["average_price"] is None
    assert order["shares"] == pytest.approx(200) and order["capital"] == pytest.approx(55)


def test_fill_order_flags_orders_below_the_minimum_size():
    assert fill_order([(0.25, 1000.0)], 1, min_order_size=5)["below_minimum"]  # 4 contracts < 5
    assert not fill_order([(0.25, 1000.0)], 100, min_order_size=5)["below_minimum"]


@pytest.mark.parametrize("observed_at, expected", [
    (pd.Timestamp("2026-11-03 22:59:59.999", tz="UTC"), True),
    (pd.Timestamp("2026-11-03 23:00:00", tz="UTC"), False),  # strictly before
    (pd.Timestamp("2026-11-03 18:00:00", tz="America/New_York"), False),  # 6 pm Eastern is 23:00 UTC
    (pd.Timestamp("2026-11-03 17:29:51", tz="UTC"), True),
])
def test_election_day_baseline_is_strictly_before_the_first_poll_close(observed_at, expected):
    assert before_first_poll_close(observed_at) is expected


def test_collector_is_stale_without_a_recent_success():
    now = pd.Timestamp("2026-11-03 18:00", tz="UTC")
    assert not is_stale(now - pd.Timedelta(minutes=20), now, pd.Timedelta(minutes=30))
    assert is_stale(now - pd.Timedelta(minutes=31), now, pd.Timedelta(minutes=30))
    assert is_stale(None, now, pd.Timedelta(minutes=30))


@pytest.mark.parametrize("best_ask, expected", [(0.20, True), (0.299, True), (0.30, False), (0.19, False), (None, False)])
def test_group_a_uses_the_best_ask(best_ask, expected):
    assert in_group_a(best_ask) is expected


# ---------- markets ----------

@pytest.mark.parametrize("tags, title, expected", [
    (["senate-elections", "midterms", "elections"], "Texas Senate", "us_midterms_2026"),
    (["world-elections", "elections"], "Next Mayor of Prague", "election_other"),
    (["politics", "global-elections"], "Next Mayor of Brno", "election_other"),  # any tag with "election"
    (["politics", "ukraine"], "Ukraine election called by...?", "unknown"),  # no election tag: uncertain bucket
    (["politics", "middle-east"], "Will Hamas agree to disarm by...?", None),  # not an election event
    ([], "", None),
])
def test_classification_uses_tags_first_and_keeps_title_matches_separate(tags, title, expected):
    assert classify_event(tags, title) == expected


def test_first_outcome_keeps_names_and_tokens_aligned():
    assert first_outcome(["Democratic", "Republican"], ["tok-d", "tok-r"]) == ("Democratic", "tok-d")
    with pytest.raises(ValueError):
        first_outcome(["Yes", "No"], ["only-one-token"])


@pytest.mark.parametrize("closed, final, outcome, expected", [
    (False, False, None, ("open", None)),
    (True, False, None, ("open", None)),
    (True, True, 1, ("settled", 1)),
    (True, True, 0, ("settled", 0)),
    (True, True, None, ("void", None)),
])
def test_settlement(closed, final, outcome, expected):
    assert settlement(closed, final, outcome) == expected


@pytest.mark.parametrize("final, winners, expected", [
    (True, {"tok": True, "other": False}, ("settled", 1)),
    (True, {"tok": False, "other": True}, ("settled", 0)),
    (False, {"tok": False, "other": False}, ("open", None)),  # not final: race calls and prices don't count
    (True, {"tok": False, "other": False}, ("void", None)),  # final but no single winner
    (True, {"x": True, "y": False}, ("unmatched", None)),  # our token isn't in this market: a data error
])
def test_official_settlement_matches_the_exact_outcome_token(final, winners, expected):
    assert official_settlement(final, winners, "tok") == expected
