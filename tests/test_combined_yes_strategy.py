"""Tests for the Combined YES Strategy analysis, on small hand-made tables with known answers."""
import pandas as pd
import pytest

from analysis.combined_yes_strategy import (
    build_report, by_group, by_period, close_timing, equal_dollar_performance, event_bootstrap, event_concentration,
    event_keys, knowable_entry_candidates, one_share_performance, price_group, select_markets,
)


def candidates(rows):
    """rows: (market_id, event_id, close day in October 2026, outcome, price)."""
    frame = pd.DataFrame(rows, columns=["market_id", "event_id", "day", "outcome", "price"])
    frame["close_time"] = pd.to_datetime("2026-10-01", utc=True) + pd.to_timedelta(frame.pop("day"), unit="D")
    frame["question"] = "Will " + frame["market_id"] + " happen?"
    return frame


# ---------- selection ----------

@pytest.mark.parametrize("price, group", [
    (0.20, "A"), (0.2999, "A"), (0.30, None),  # lower bound included, upper excluded
    (0.50, "B"), (0.5999, "B"), (0.60, None),
    (0.19, None), (0.45, None), (0.95, None),
])
def test_price_group_boundaries(price, group):
    assert price_group(price) == group


def test_selection_keeps_only_the_two_intervals_and_binary_outcomes():
    data = candidates([("a", "e1", 1, 1, 0.25), ("b", "e2", 2, 0, 0.55), ("out", "e3", 3, 1, 0.40),
                       ("bad", "e4", 4, 2, 0.25)])  # outcome 2 isn't a valid binary result
    assert select_markets(data)["market_id"].tolist() == ["b", "a"]


def test_selection_is_newest_first_with_market_id_breaking_ties():
    data = candidates([("m2", "e", 5, 1, 0.25), ("m1", "e", 5, 1, 0.55), ("old", "e", 1, 1, 0.25)])
    assert select_markets(data)["market_id"].tolist() == ["m1", "m2", "old"]


def test_selection_takes_n_in_total_not_n_per_group():
    # 6 Group A markets (newest) and 6 Group B markets (older): the latest 8 are 6 A + 2 B
    rows = [(f"a{i}", f"e{i}", 20 + i, 1, 0.25) for i in range(6)] + [(f"b{i}", f"f{i}", i, 0, 0.55) for i in range(6)]
    latest = select_markets(candidates(rows), n=8)
    assert latest["group"].value_counts().to_dict() == {"A": 6, "B": 2}


def test_earlier_sample_does_not_overlap_the_latest():
    rows = [(f"m{i:02d}", f"e{i}", i, i % 2, 0.25) for i in range(30)]
    data = candidates(rows)
    latest, earlier = select_markets(data, n=10), select_markets(data, n=10, skip=10)
    assert not set(latest["market_id"]) & set(earlier["market_id"])
    assert earlier["close_time"].max() < latest["close_time"].min()  # earlier really means earlier


def test_n_none_selects_every_qualifying_market():
    rows = [(f"m{i:02d}", f"e{i}", i, i % 2, [0.25, 0.55, 0.40][i % 3]) for i in range(30)]
    every = select_markets(candidates(rows), n=None)
    assert len(every) == 20  # the 10 markets priced 0.40 don't qualify
    assert every["close_time"].is_monotonic_decreasing


# ---------- performance ----------

TRADES = candidates([("w1", "e1", 1, 1, 0.20), ("l1", "e1", 2, 0, 0.30), ("w2", "e2", 3, 1, 0.50)])
TRADES["group"] = ["A", "A", "B"]


def test_one_share_performance_by_hand():
    perf = one_share_performance(TRADES)
    # paid 0.20 + 0.30 + 0.50 = 1.00; payouts 1 + 0 + 1 = 2; profit 1.00
    assert perf["total_paid"] == pytest.approx(1.00)
    assert perf["total_profit"] == pytest.approx(1.00)
    assert perf["roi"] == pytest.approx(1.00)
    assert perf["wins"] == 2 and perf["losses"] == 1
    assert perf["win_rate"] == pytest.approx(2 / 3)
    assert perf["break_even_win_rate"] == pytest.approx(1 / 3)  # average price paid
    assert perf["edge"] == pytest.approx(2 / 3 - 1 / 3)
    assert perf["events"] == 2  # w1 and l1 share event e1


def test_roi_is_total_profit_over_total_paid_not_an_average_of_roIs():
    perf = one_share_performance(TRADES)
    average_of_rois = ((TRADES["outcome"] - TRADES["price"]) / TRADES["price"]).mean()  # (4 - 1 + 1) / 3
    assert perf["roi"] == pytest.approx(1.00)
    assert average_of_rois == pytest.approx(4 / 3)  # a different (wrong) number


def test_costs_raise_the_price_paid_and_the_break_even_point():
    perf = one_share_performance(TRADES, cost=0.02)
    assert perf["total_paid"] == pytest.approx(1.06)
    assert perf["total_profit"] == pytest.approx(2 - 1.06)
    assert perf["roi"] == pytest.approx((2 - 1.06) / 1.06)
    assert perf["break_even_win_rate"] == pytest.approx(1.06 / 3)
    assert perf["edge"] == pytest.approx(1 / 3)  # edge is measured before costs


def test_equal_dollar_sizing_by_hand():
    perf = equal_dollar_performance(TRADES, stake=1.0)
    # $1 buys 5 shares at 0.20 (win: +$4), 3.33 at 0.30 (loss: -$1), 2 at 0.50 (win: +$1)
    assert perf["total_profit"] == pytest.approx(4 - 1 + 1)
    assert perf["roi"] == pytest.approx(4 / 3)
    assert perf["invested"] == 3


def test_by_group_has_both_groups_and_the_combined_row():
    table = by_group(TRADES, one_share_performance)
    assert list(table.index) == ["Group A", "Group B", "Combined"]
    assert table.loc["Group A", "total_profit"] + table.loc["Group B", "total_profit"] == pytest.approx(
        table.loc["Combined", "total_profit"])


# ---------- events and uncertainty ----------

def test_a_market_without_an_event_is_its_own_event():
    trades = TRADES.assign(event_id=["e1", None, None])
    assert event_keys(trades).tolist() == ["e1", "l1", "w2"]


def test_event_bootstrap_resamples_whole_events():
    # Ten copies of one event: resampling events can only ever draw that one event, so no uncertainty
    same_event = candidates([(f"m{i}", "only", i, 1, 0.25) for i in range(10)]).assign(group="A")
    boot = event_bootstrap(same_event, n_boot=200).loc["Combined"]
    assert boot["events"] == 1
    assert boot["total_profit_low"] == pytest.approx(boot["total_profit_high"])


def test_event_bootstrap_is_reproducible():
    trades = candidates([(f"m{i}", f"e{i % 4}", i, i % 2, 0.25 + 0.3 * (i % 2)) for i in range(20)])
    trades["group"] = trades["price"].map(price_group)
    pd.testing.assert_frame_equal(event_bootstrap(trades, n_boot=300, seed=3), event_bootstrap(trades, n_boot=300, seed=3))


def test_event_concentration_profit_without_the_best_event():
    result = event_concentration(TRADES)
    assert result["total_profit"] == pytest.approx(1.00)  # e1: 0.80 - 0.30 = 0.50, e2: 0.50
    assert result["best_event_profit"] == pytest.approx(0.50)
    assert result["profit_without_best_event"] == pytest.approx(0.50)
    assert result["events_with_several_markets"] == 1


def test_by_period_splits_by_half_year_and_group():
    trades = candidates([("a", "e1", 10, 1, 0.25), ("b", "e2", 200, 0, 0.25), ("c", "e3", 210, 1, 0.55)])
    trades["close_time"] = pd.to_datetime(["2025-03-01", "2025-09-01", "2025-09-02"], utc=True)
    trades["group"] = ["A", "A", "B"]
    table = by_period(trades)
    assert table[["Period", "Group", "Markets"]].values.tolist() == [["2025 H1", "A", 1], ["2025 H2", "A", 1],
                                                                       ["2025 H2", "B", 1]]


def test_report_contains_every_section_and_the_disclaimer():
    rows = [(f"m{i:03d}", f"e{i % 30}", i, (i * 7) % 3 == 0, [0.22, 0.27, 0.53, 0.58][i % 4]) for i in range(220)]
    data = candidates(rows)
    data["outcome"] = data["outcome"].astype(int)
    report = build_report(select_markets(data), select_markets(data, skip=100), "test data",
                          pd.Timestamp("2026-10-21", tz="UTC"), all_markets=select_markets(data, n=None))
    for heading in ["## Summary: the latest 100 markets", "## Summary: all 220 eligible markets", "## 1. Research question", "## 2. Strategy rules", "## 3. Selection methodology",
                    "## 4. The latest 100 qualifying markets", "## 5. Group A vs Group B", "## 6. Combined portfolio",
                    "## 7. Three cost scenarios", "## 8. Statistical uncertainty", "## 9. Comparison with an earlier sample",
                    "## 10. Limitations and interpretation", "## 11. What a real backtest would need",
                    "## 12. All eligible markets (full history)", "### Stability over time"]:
        assert heading in report
    assert "not a validated or executable trading strategy" in report


# ---------- look-ahead checks ----------

def utc(text):
    return pd.Timestamp(text, tz="UTC").as_unit("us")


def test_close_timing_compares_actual_close_with_the_scheduled_end():
    trades = candidates([("early", "e1", 1, 1, 0.25), ("on", "e2", 1, 1, 0.25), ("late", "e3", 1, 1, 0.25),
                         ("unknown", "e4", 1, 1, 0.25)])  # all closed 2026-10-02
    ends = pd.DataFrame({"market_id": ["early", "on", "late"],
                         "scheduled_end": [utc("2026-10-10"), utc("2026-10-02 20:00"), utc("2026-09-28")]})
    timing = dict(zip(*close_timing(trades, ends)[["market_id", "timing"]].T.values))
    assert timing == {"early": "early", "on": "on schedule", "late": "late", "unknown": "unknown"}


def knowable_inputs():
    """Three markets scheduled to end 2026-10-20 (entry moment: 2026-10-13), each with prices around then."""
    markets = pd.DataFrame({
        "market_id": ["held", "too_late", "late_list"], "event_id": ["e1", "e2", "e3"],
        "question": ["Held?", "Too late?", "Listed late?"],
        "open_time": [utc("2026-10-01"), utc("2026-10-01"), utc("2026-10-15")],
        # "held" resolved early but AFTER the entry moment; "too_late" had already closed before it
        "close_time": [utc("2026-10-16"), utc("2026-10-12"), utc("2026-10-20")],
        "outcome": [1, 1, 0],
    })
    prices = pd.DataFrame({
        "market_id": ["held", "held", "too_late", "late_list"],
        "time": [utc("2026-10-12 23:00"), utc("2026-10-13 05:00"), utc("2026-10-11"), utc("2026-10-16")],
        "price": [0.25, 0.90, 0.25, 0.55],  # held's 0.90 comes after the entry moment and must not be used
        "fidelity": ["hourly"] * 4,
    })
    ends = pd.DataFrame({"market_id": ["held", "too_late", "late_list"], "scheduled_end": [utc("2026-10-20")] * 3})
    return markets, prices, ends


def test_knowable_entry_uses_the_price_before_scheduled_end_minus_seven_days():
    entries = knowable_entry_candidates(*knowable_inputs())
    assert entries["market_id"].tolist() == ["held"]  # too_late had closed; late_list wasn't open yet
    assert entries["price"].tolist() == [0.25]  # never the later 0.90
    assert entries["outcome"].tolist() == [1]  # an early resolution after entry still counts


def test_report_has_section_13_and_marks_missing_price_history_as_pending():
    rows = [(f"m{i:03d}", f"e{i % 30}", i, int((i * 7) % 3 == 0), [0.22, 0.27, 0.53, 0.58][i % 4]) for i in range(220)]
    data = candidates(rows)
    all_markets = select_markets(data, n=None)
    ends = pd.DataFrame({"market_id": data["market_id"], "scheduled_end": data["close_time"]})
    timed = close_timing(all_markets, ends)
    look_ahead = {"timed": timed, "on_schedule": timed, "knowable": None,
                  "knowable_note": "the local price history could not be read", "scheduled_source": "Test data."}
    report = build_report(select_markets(data), select_markets(data, skip=100), "test data",
                          pd.Timestamp("2026-10-21", tz="UTC"), all_markets=all_markets, look_ahead=look_ahead)
    assert "## 13. Removing the look-ahead: scheduled end dates" in report
    assert "## Summary: without the look-ahead" in report
    assert "**Pending:** the local price history could not be read" in report
