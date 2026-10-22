"""Tests for the forward test's rules (docs/forward_test_spec.md)."""
from datetime import timedelta

import pandas as pd
import pytest

from forward_test.models import book_summary, entry_decision, fill_price, in_group_a, settlement

END = pd.Timestamp("2026-11-01 12:00", tz="UTC")
TEST_START = pd.Timestamp("2026-10-01", tz="UTC")
LONG_AGO = pd.Timestamp("2026-06-01", tz="UTC")


@pytest.mark.parametrize("days_left, market_start, test_start, expected", [
    (8, LONG_AGO, TEST_START, "wait"),  # its 7-day mark hasn't come yet
    (7, LONG_AGO, TEST_START, "enter"),  # exactly at the mark
    (6.5, LONG_AGO, TEST_START, "enter"),  # within the first day after the mark
    (6, LONG_AGO, TEST_START, "missed"),  # a full day late: the collector missed it
    (3, LONG_AGO, TEST_START, "missed"),
    (3, END - timedelta(days=4), TEST_START, "skip"),  # opened after its 7-day mark: never eligible
    (3, LONG_AGO, END - timedelta(days=5), "skip"),  # its mark fell before the test began
])
def test_entry_decision(days_left, market_start, test_start, expected):
    now = END - timedelta(days=days_left)
    assert entry_decision(END, now, market_start, test_start) == expected


def test_book_summary_uses_the_highest_bid_and_lowest_ask():
    # The API lists bids from low to high and asks from high to low
    bids = [(0.01, 1000.0), (0.20, 50.0), (0.22, 30.0)]
    asks = [(0.99, 500.0), (0.26, 40.0), (0.25, 10.0), (0.25, 5.0)]
    summary = book_summary(bids, asks)
    assert summary["best_bid"] == 0.22 and summary["best_ask"] == 0.25
    assert summary["ask_size_at_best"] == 15.0  # both levels at 0.25
    assert summary["midpoint"] == pytest.approx(0.235)
    assert summary["spread"] == pytest.approx(0.03)


def test_empty_book():
    assert book_summary([], []) == {"best_bid": None, "best_ask": None, "ask_size_at_best": None,
                                    "midpoint": None, "spread": None}


def test_fill_price_walks_up_the_asks():
    asks = [(0.30, 100.0), (0.25, 100.0)]  # 100 contracts at 25c ($25), then 100 at 30c ($30)
    assert fill_price(asks, 20) == pytest.approx(0.25)  # fits in the cheapest level
    assert fill_price(asks, 40) == pytest.approx(40 / (100 + 15 / 0.30))  # $25 at 25c, $15 at 30c
    assert fill_price(asks, 100) is None  # the whole book is only worth $55


@pytest.mark.parametrize("best_ask, expected", [(0.20, True), (0.299, True), (0.30, False), (0.19, False), (None, False)])
def test_group_a_uses_the_best_ask(best_ask, expected):
    assert in_group_a(best_ask) is expected


@pytest.mark.parametrize("closed, final, outcome, expected", [
    (False, False, None, ("open", None)),  # still trading
    (True, False, None, ("open", None)),  # closed, result proposed but not final yet
    (True, True, 1, ("settled", 1)),
    (True, True, 0, ("settled", 0)),
    (True, True, None, ("void", None)),  # final but no clear YES/NO (e.g. split 50/50)
])
def test_settlement(closed, final, outcome, expected):
    assert settlement(closed, final, outcome) == expected
