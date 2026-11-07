"""Tests for the forward-test results analysis, on synthetic data only (no network, no database)."""
import json
import re

import numpy as np
import pandas as pd
import pytest

from analysis import forward_test_results as ftr
from analysis.forward_test_report import audit_markdown, results_markdown

T0 = pd.Timestamp("2026-10-28 10:00", tz="UTC")


def entry(market_id, ask, status="settled", outcome=0.0, event_id=None, group_a=True, fill_100=None, fill_1000=None,
          hours=0, **fields):
    row = {"market_id": market_id, "event_id": event_id or f"e-{market_id}", "question": f"{market_id}?",
           "token_id": f"tok-{market_id}", "recorded_at": T0 + pd.Timedelta(hours=hours), "run_id": 1,
           "scheduled_end": T0 + pd.Timedelta(days=7), "best_bid": ask - 0.01, "best_ask": ask, "midpoint": ask - 0.005,
           "spread": 0.01, "ask_size_at_best": 500.0, "fill_price_100": ask if fill_100 is None else fill_100,
           "fill_price_1000": ask if fill_1000 is None else fill_1000, "volume_at_entry": 200_000.0,
           "min_order_size": 5.0, "in_group_a": group_a, "status": status, "outcome": outcome}
    row.update(fields)
    return row


def resolution(market_id, final=True, won=False, closed_hours=48, tags=("sports",)):
    return {"market_id": market_id, "final": final,
            "winners": {f"tok-{market_id}": won, f"other-{market_id}": final and not won},
            "official_close_time": T0 + pd.Timedelta(hours=closed_hours) if final else None,
            "outcome_names": ["Yes", "No"], "token_ids": [f"tok-{market_id}", f"other-{market_id}"],
            "event_title": "Event", "event_tags": list(tags), "published_taker_fee": 0}


def positions_for(entries, resolutions, raw_checks=None):
    return ftr.build_positions(pd.DataFrame(entries), resolutions, raw_checks or {})


# ---------- matching and verification ----------

def test_positions_take_the_official_result_of_their_own_token():
    entries = [entry("a", 0.25, outcome=1.0), entry("b", 0.22, status="open", outcome=np.nan),
               entry("c", 0.55, group_a=False)]  # not Group A: never a position
    positions = positions_for(entries, [resolution("a", won=True), resolution("b", final=False)])

    assert list(positions["market_id"]) == ["a", "b"]
    assert list(positions["status"]) == ["settled", "open"] and positions["outcome"].iloc[0] == 1
    assert positions["stored_agrees"].all() and positions["outcome_name"].iloc[0] == "Yes"


def test_a_stored_result_that_disagrees_with_the_official_one_is_flagged():
    positions = positions_for([entry("a", 0.25, outcome=1.0)], [resolution("a", won=False)])
    assert positions["outcome"].iloc[0] == 0 and not positions["stored_agrees"].iloc[0]


def test_the_outcome_name_is_only_given_when_the_token_is_the_first_listed_one():
    official = resolution("a") | {"token_ids": ["other-a", "tok-a"], "outcome_names": ["No", "Yes"]}
    positions = positions_for([entry("a", 0.25)], [official])
    assert positions["outcome_name"].iloc[0] is None and not positions["first_listed_token"].iloc[0]


def book(asks, bids=((0.24, 100.0),), asset="tok-a"):
    return {"asset_id": asset, "timestamp": "1793181647000",
            "asks": [{"price": str(p), "size": str(s)} for p, s in asks],
            "bids": [{"price": str(p), "size": str(s)} for p, s in bids]}


def test_a_quote_is_verified_only_if_every_number_matches_the_raw_book():
    recorded = entry("a", 0.25, ask_size_at_best=1000.0, best_bid=0.24, fill_100=0.25, fill_1000=None)
    recorded["fill_price_1000"] = None  # $250 on offer: a $1,000 order can't fill
    assert ftr.verify_quote(recorded, book([(0.25, 1000.0)]))["raw_book"] == "verified"
    assert ftr.verify_quote(recorded, book([(0.26, 1000.0)]))["raw_book"] == "mismatch"
    assert ftr.verify_quote(recorded, book([(0.25, 1000.0)], asset="tok-b"))["raw_book"] == "mismatch"


# ---------- scenarios and performance ----------

def test_roi_divides_total_profit_by_total_capital_never_averages_trade_rois():
    positions = positions_for([entry("win", 0.20, outcome=1.0), entry("lose", 0.29)],
                              [resolution("win", won=True), resolution("lose")])
    perf = ftr.performance(ftr.scenario_rows(positions))
    assert perf["gross_roi"] == pytest.approx((1 - 0.20 - 0.29) / (0.20 + 0.29))  # 0.51 / 0.49
    assert perf["gross_roi"] != pytest.approx(np.mean([(1 - 0.20) / 0.20, -1.0]))  # average of trade ROIs: 1.5
    assert perf["break_even_win_rate"] == pytest.approx(0.245) and perf["edge"] == pytest.approx(0.5 - 0.245)


def test_dollar_orders_buy_dollars_over_the_average_fill_price():
    positions = positions_for([entry("a", 0.25, outcome=1.0, fill_100=0.26)], [resolution("a", won=True)])
    rows = ftr.scenario_rows(positions, 100)
    assert rows["shares"].iloc[0] == pytest.approx(100 / 0.26) and rows["capital"].iloc[0] == 100
    assert rows["profit"].iloc[0] == pytest.approx(100 / 0.26 - 100)


def test_unfillable_orders_are_counted_but_never_in_the_returns():
    entries = [entry("deep", 0.25, outcome=1.0), entry("thin", 0.25)]
    entries[1]["fill_price_1000"] = None  # the recorded book couldn't take $1,000
    positions = positions_for(entries, [resolution("deep", won=True), resolution("thin")])
    perf = ftr.performance(ftr.scenario_rows(positions, 1_000))
    assert (perf["fillable"], perf["unfillable"], perf["settled"]) == (1, 1, 1)
    assert perf["invested"] == 1_000 and perf["gross_roi"] == pytest.approx(1 / 0.25 - 1)


def test_unresolved_positions_are_neither_wins_nor_losses():
    positions = positions_for([entry("a", 0.25), entry("b", 0.20, status="open", outcome=np.nan)],
                              [resolution("a"), resolution("b", final=False)])
    perf = ftr.performance(ftr.scenario_rows(positions))
    assert (perf["settled"], perf["open"], perf["wins"], perf["losses"]) == (1, 1, 0, 1)
    assert perf["invested"] == pytest.approx(0.25) and perf["open_capital"] == pytest.approx(0.20)


def test_one_contract_is_flagged_below_the_minimum_order_size():
    positions = positions_for([entry("a", 0.25)], [resolution("a")])
    assert ftr.performance(ftr.scenario_rows(positions))["below_minimum_size"] == 1
    assert ftr.performance(ftr.scenario_rows(positions, 100))["below_minimum_size"] == 0  # 400 contracts


# ---------- uncertainty, exposure, concentration ----------

def test_bootstrap_resamples_whole_events():
    # Two events with two positions each: one event won both, the other lost both
    entries = [entry("a1", 0.25, outcome=1.0, event_id="A"), entry("a2", 0.25, outcome=1.0, event_id="A"),
               entry("b1", 0.25, event_id="B"), entry("b2", 0.25, event_id="B")]
    positions = positions_for(entries, [resolution("a1", won=True), resolution("a2", won=True),
                                        resolution("b1"), resolution("b2")])
    boot = ftr.event_bootstrap(ftr.scenario_rows(positions), n_boot=500)
    assert boot["events"] == 2
    assert boot["win_rate_low"] == 0 and boot["win_rate_high"] == 1  # all-or-nothing, never 1/4 or 3/4


def test_bootstrap_needs_at_least_two_settled_events():
    positions = positions_for([entry("a", 0.25)], [resolution("a")])
    assert np.isnan(ftr.event_bootstrap(ftr.scenario_rows(positions))["roi_low"])


def test_binomial_tail():
    assert ftr.binomial_at_most(0, 1, 0.3) == pytest.approx(0.7)
    assert ftr.binomial_at_most(2, 2, 0.3) == pytest.approx(1.0)


def test_exposure_counts_overlapping_capital_without_recycling_it():
    entries = [entry("a", 0.20, hours=0), entry("b", 0.30, hours=1), entry("c", 0.25, hours=48)]
    positions = positions_for(entries, [resolution("a", closed_hours=10), resolution("b", closed_hours=48),
                                        resolution("c", closed_hours=60)])
    exp = ftr.exposure(ftr.scenario_rows(positions), as_of=T0 + pd.Timedelta(days=10))
    # a and b overlap; b closes at the moment c opens, so b's capital is freed first
    assert exp["max_open_positions"] == 2 and exp["max_open_capital"] == pytest.approx(0.50)
    assert exp["total_capital"] == pytest.approx(0.75)


# ---------- audit helpers ----------

def test_missed_labels_are_confirmed_only_without_a_run_in_the_first_day():
    entries = pd.DataFrame([entry("x", 0.25, status="missed"), entry("y", 0.25, status="missed",
                                                                    scheduled_end=T0 + pd.Timedelta(days=30))])
    mark_x = T0  # x's 7-day mark
    checked = ftr.missed_check(entries, [mark_x + pd.Timedelta(hours=5)])
    assert dict(zip(checked["market_id"], checked["confirmed"])) == {"x": False, "y": True}


def test_scheduled_slots_count_from_the_slot_the_first_run_served():
    # GitHub queues a scheduled run after its slot: a run created at 12:19 served the 09:41 slot (or earlier)
    first = pd.Timestamp("2026-10-22 12:19", tz="UTC")
    assert ftr.scheduled_slots(first, pd.Timestamp("2026-10-22 18:41", tz="UTC")) == 4  # 09:41 ... 18:41


def test_period_labels_are_utc_days_not_poll_closing_times():
    assert ftr.period(pd.Timestamp("2026-11-02 23:59", tz="UTC")) == "before Nov 3"
    assert ftr.period(pd.Timestamp("2026-11-03 23:30", tz="UTC")) == "Nov 3"
    assert ftr.period(pd.Timestamp("2026-11-04 00:00", tz="UTC")) == "after Nov 3"


# ---------- whole analysis and reports ----------

RUNS = pd.DataFrame({"run_id": [1], "started_at": [T0], "finished_at": [T0], "status": ["success"],
                     "entered": [2], "missed": [0], "settled": [0]})
GITHUB = pd.DataFrame({"github_run_id": [10], "created_at": [T0 - pd.Timedelta(minutes=2)], "event": ["schedule"],
                       "conclusion": ["success"]})


def analyze(entries, resolutions):
    return ftr.analyze(pd.DataFrame(entries), RUNS, resolutions, {}, GITHUB, None,
                       as_of=T0 + pd.Timedelta(days=10), n_boot=200)


def test_reports_say_no_data_when_nothing_was_recorded():
    empty = pd.DataFrame(columns=list(entry("a", 0.25)))
    results = ftr.analyze(empty, RUNS.iloc[:0], [], {}, GITHUB.iloc[:0], None, as_of=T0)
    assert ftr.NO_DATA in results_markdown(results) and ftr.NO_DATA in audit_markdown(results)
    assert results["verdict"] == ftr.NO_DATA


def test_without_settled_positions_no_realized_return_is_reported():
    results = analyze([entry("a", 0.25, status="open", outcome=np.nan)], [resolution("a", final=False)])
    text = results_markdown(results)
    assert "No position has officially settled yet" in text and "Gross result at the best ask" not in text
    assert results["verdict"].startswith("PENDING: 0 of 100")


def test_the_verdict_stays_pending_below_100_settled_positions():
    results = analyze([entry("a", 0.25, outcome=1.0), entry("b", 0.25)], [resolution("a", won=True), resolution("b")])
    assert results["verdict"].startswith("PENDING: 2 of 100")
    assert "SUPPORTS" not in results_markdown(results)


def test_the_bundle_holds_aggregates_and_positions_but_no_secrets(tmp_path):
    results = analyze([entry("a", 0.25, outcome=1.0), entry("b", 0.25)], [resolution("a", won=True), resolution("b")])
    ftr.write_bundle(results, tmp_path)
    assert {p.name for p in tmp_path.iterdir()} == {"positions.csv", "performance.csv", "comparison.csv",
                                                    "subgroups.csv", "runs.csv", "pilot_daily.csv", "summary.json"}
    text = " ".join(p.read_text() for p in tmp_path.iterdir())
    assert "postgresql://" not in text and "password" not in text.lower()
    performance = pd.read_csv(tmp_path / "performance.csv")
    assert list(performance["scenario"]) == ["1 contract", "$100", "$1,000"]
    assert performance["net_roi"].isna().all()  # no verified fee model: never a net figure


# ---------- regressions found by the independent review ----------

def test_dollar_scenarios_report_no_single_break_even_or_edge():
    # 15 cheap losers and 5 dearer winners: the plain win rate beats the harmonic mean, yet $100 each loses
    entries = [entry(f"l{i}", 0.20) for i in range(15)] + [entry(f"w{i}", 0.299, outcome=1.0) for i in range(5)]
    resolutions = [resolution(f"l{i}") for i in range(15)] + [resolution(f"w{i}", won=True) for i in range(5)]
    perf = ftr.performance(ftr.scenario_rows(positions_for(entries, resolutions), 100))
    assert perf["gross_roi"] < 0
    assert np.isnan(perf["edge"]) and np.isnan(perf["break_even_win_rate"])  # never a misleading positive edge


def test_a_void_position_returns_its_capital_when_it_closes():
    entries = [entry("void", 0.25, status="void", outcome=np.nan), entry("later", 0.25, hours=100)]
    official_void = resolution("void", closed_hours=2) | {"winners": {"tok-void": False, "other-void": False}}
    positions = positions_for(entries, [official_void, resolution("later", closed_hours=120)])
    assert positions["status"].iloc[0] == "void"
    exp = ftr.exposure(ftr.scenario_rows(positions), as_of=T0 + pd.Timedelta(days=30))
    assert exp["max_open_positions"] == 1 and exp["max_open_capital"] == pytest.approx(0.25)


def test_positions_without_a_usable_official_result_are_counted():
    entries = [entry("a", 0.25), entry("x", 0.25)]
    unmatched = resolution("x") | {"winners": {"someone-else": True, "other": False}}
    perf = ftr.performance(ftr.scenario_rows(positions_for(entries, [resolution("a"), unmatched])))
    assert (perf["settled"], perf["open"], perf["void"], perf["no_official_result"]) == (1, 0, 0, 1)


def test_group_a_membership_is_rechecked_against_the_rules():
    entries = [entry("ok", 0.25), entry("dear", 0.30), entry("thin", 0.25, volume_at_entry=50_000.0)]
    positions = positions_for(entries, [resolution(m) for m in ["ok", "dear", "thin"]])
    assert list(positions["meets_rules"]) == [True, False, False]


def test_with_nothing_settled_the_report_claims_no_direction():
    text = results_markdown(analyze([entry("a", 0.25, status="open", outcome=np.nan)], [resolution("a", final=False)]))
    assert "Nothing has settled, so there is no direction yet" in text
    assert "agree in direction" not in text and not re.search(r"\bnan\b", text)


def test_at_100_settled_positions_the_verdict_applies_and_pending_wording_goes():
    entries = [entry(f"m{i}", 0.25, outcome=float(i % 2)) for i in range(120)]
    results = analyze(entries, [resolution(f"m{i}", won=bool(i % 2)) for i in range(120)])
    text = results_markdown(results)
    assert not results["verdict"].startswith("PENDING")
    assert "Fewer than 100 settled positions" not in text and "provisional" not in text


def test_records_without_group_a_positions_are_pending_not_missing_data():
    results = analyze([entry("a", 0.55, group_a=False)], [])
    assert results["verdict"].startswith("PENDING: 0 of 100")
    assert ftr.NO_DATA not in audit_markdown(results)


def test_summary_json_is_strict_json(tmp_path):
    results = analyze([entry("a", 0.25, status="open", outcome=np.nan)], [resolution("a", final=False)])
    ftr.write_bundle(results, tmp_path)
    def no_nan(constant):
        raise ValueError(f"not valid JSON: {constant}")
    json.loads((tmp_path / "summary.json").read_text(), parse_constant=no_nan)


def test_times_are_always_shown_in_utc():
    assert ftr.fmt_time(pd.Timestamp("2026-11-03 18:00", tz="America/New_York")) == "2026-11-03 23:00:00 UTC"
