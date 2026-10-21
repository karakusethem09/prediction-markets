"""Tests for the Group A deep dive: the fixed definitions and the segment tables."""
import pandas as pd
import pytest

from analysis.group_a_deep_dive import TEST_PERIOD_START, build_report, group_a, is_single_game, period, segment_rows


@pytest.mark.parametrize("question, single_game", [
    ("Commanders vs. Chiefs", True),
    ("T20 World Cup: Zimbabwe vs West Indies", True),
    ("Will Jordan vs. Algeria end in a draw?", True),
    ("Will Real Betis Balompié win on 2026-09-14?", True),
    ("Canada vs. Qatar: O/U 3.5", True),
    ("Spread: Seahawks (-6.5)", True),
    ("Will Arizona win the 2026 NCAA Tournament?", False),  # a season-long future, not one game
    ("Israel strikes Iran by January 31, 2026?", False),
    ("Will Ethereum reach $3,750 in November?", False),
    ("Jerome Powell out as Fed Chair by May 15, 2026?", False),
])
def test_single_game_rule(question, single_game):
    assert is_single_game(pd.Series([question])).iloc[0] == single_game


def test_period_split_at_the_test_start():
    times = pd.Series([TEST_PERIOD_START - pd.Timedelta(seconds=1), TEST_PERIOD_START])
    assert period(times).tolist() == ["exploration", "test"]


def trades(rows):
    """rows: (market_id, event_id, close date, outcome, price)."""
    frame = pd.DataFrame(rows, columns=["market_id", "event_id", "close_time", "outcome", "price"])
    frame["close_time"] = pd.to_datetime(frame["close_time"], utc=True)
    frame["question"] = frame["market_id"] + " vs. somebody"
    frame["volume_usd"] = 2_000_000.0
    return frame


def test_group_a_keeps_only_the_20_to_30_percent_interval():
    data = trades([("a", "e1", "2026-03-01", 1, 0.25), ("b", "e2", "2026-03-02", 0, 0.55), ("c", "e3", "2026-03-03", 0, 0.29)])
    assert group_a(data)["market_id"].tolist() == ["c", "a"]  # newest first, Group B left out


def test_segment_rows_give_exploration_test_and_all():
    data = trades([("x1", "e1", "2026-03-01", 1, 0.25), ("x2", "e2", "2026-04-01", 0, 0.25),
                   ("y1", "e3", "2026-08-01", 1, 0.20)]).assign(group="A", segment="S")
    table = segment_rows(data, "segment", ["S"]).set_index("Period")
    assert table["Markets"].to_dict() == {"exploration": 2, "test": 1, "all": 3}
    assert table.loc["all", "_roi"] == pytest.approx((2 - 0.70) / 0.70)  # two wins, paid 0.25 + 0.25 + 0.20


def test_empty_segment_period_is_reported_not_crashed():
    data = trades([("x1", "e1", "2026-03-01", 1, 0.25)]).assign(group="A", segment="S")
    table = segment_rows(data, "segment", ["S"]).set_index("Period")
    assert table.loc["test", "Markets"] == 0


def test_report_has_every_question_and_the_definitions():
    rows = [(f"m{i:03d}", f"e{i % 40}", f"2026-{1 + i % 10:02d}-15", int(i % 3 == 0), 0.21 + (i % 9) / 100) for i in range(120)]
    data = trades(rows)
    data.loc[data.index % 2 == 0, "question"] = "Will something happen by June 30?"  # half are "other"
    data["volume_usd"] = [1_000_000.0 + 10_000 * i for i in range(120)]
    by_timing = {days: group_a(data) for days in [3, 7, 14]}
    report = build_report(by_timing, pd.Timestamp("2026-10-21", tz="UTC"), "2026-10-20")
    for heading in ["## Summary", "## Definitions", "## 1. Is it a sports effect?", "## 2. Liquidity",
                    "## 3. Timing", "## Limitations"]:
        assert heading in report
    assert "Retrospective and hypothetical" in report
