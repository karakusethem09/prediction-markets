"""Tests for the event-level bootstrap, using simulated data where the true answer is known."""
import numpy as np
import pandas as pd
import pytest

from analysis.bootstrap import (
    calibration_error, calibration_slope, calibration_table_with_event_intervals, event_bootstrap,
)
from analysis.calibration import calibration_table, scores


def simulate(n, slope, seed=0):
    """Prices spread over 5-95%, and outcomes that happen with probability sigmoid(slope * logit(price))."""
    rng = np.random.default_rng(seed)
    prices = rng.uniform(0.05, 0.95, n)
    true_probability = 1 / (1 + np.exp(-slope * np.log(prices / (1 - prices))))
    return prices, (rng.uniform(size=n) < true_probability).astype(int)


@pytest.mark.parametrize("true_slope", [1.0, 0.7])
def test_slope_recovers_the_true_value(true_slope):
    prices, outcomes = simulate(50_000, true_slope)
    intercept, slope = calibration_slope(prices, outcomes)
    assert slope == pytest.approx(true_slope, abs=0.04)
    assert intercept == pytest.approx(0, abs=0.04)


def test_slope_weights_act_like_repeated_rows():
    prices, outcomes = simulate(500, 0.8)
    weights = np.random.default_rng(1).integers(0, 3, 500)
    repeated = np.repeat(np.arange(500), weights)
    assert calibration_slope(prices, outcomes, weights)[1] == pytest.approx(
        calibration_slope(prices[repeated], outcomes[repeated])[1])


def test_calibration_error_matches_the_dashboard_scores():
    prices, outcomes = simulate(2_000, 0.8)
    data = pd.DataFrame({"market_id": range(2_000), "horizon_days": 1, "price": prices,
                         "outcome": outcomes, "event_id": range(2_000)})
    assert calibration_error(prices, outcomes) == pytest.approx(scores(data)["calibration_error"].iloc[0])


def sample_events(n_events, markets_per_event, seed=0):
    """Events whose markets all share one outcome, like several markets on the same question."""
    prices, outcomes = simulate(n_events, 1.0, seed)
    return pd.DataFrame({
        "market_id": range(n_events * markets_per_event),
        "event_id": np.repeat(range(n_events), markets_per_event),
        "horizon_days": 1,
        "price": np.repeat(prices, markets_per_event),
        "outcome": np.repeat(outcomes, markets_per_event),
    })


def test_resampling_events_widens_intervals_when_markets_move_together():
    data = sample_events(n_events=300, markets_per_event=8)

    by_event = event_bootstrap(data, n_boot=300)[0].iloc[0]
    by_market = event_bootstrap(data, n_boot=300, by_event=False)[0].iloc[0]

    # 8 copies of each outcome look like 2,400 independent markets, but are really 300 pieces of evidence
    width = lambda row: row["slope_high"] - row["slope_low"]
    assert width(by_event) > 2 * width(by_market)
    assert by_event["units"] == 300 and by_market["units"] == 2_400


def test_bootstrap_is_reproducible_with_a_seed():
    data = sample_events(n_events=100, markets_per_event=2)
    first, _ = event_bootstrap(data, n_boot=100, seed=7)
    again, _ = event_bootstrap(data, n_boot=100, seed=7)
    pd.testing.assert_frame_equal(first, again)


def test_well_calibrated_prices_have_a_slope_interval_that_includes_one():
    data = sample_events(n_events=3_000, markets_per_event=1)
    summary, bins = event_bootstrap(data, n_boot=300)
    assert summary["slope_low"].iloc[0] < 1 < summary["slope_high"].iloc[0]
    assert len(bins) == 10 and (bins["boot_low"] <= bins["boot_high"]).all()


def test_calibration_table_with_event_intervals_keeps_the_table_and_swaps_the_intervals():
    data = sample_events(n_events=400, markets_per_event=3)
    wilson, boot = calibration_table(data), calibration_table_with_event_intervals(data, n_boot=200)

    assert list(boot.columns) == list(wilson.columns)  # same shape, so the chart works unchanged
    pd.testing.assert_series_equal(boot["happened_rate"], wilson["happened_rate"])
    assert ((boot["ci_low"] <= boot["happened_rate"]) & (boot["happened_rate"] <= boot["ci_high"])).all()


@pytest.mark.parametrize("prices, outcomes", [
    ([0.3, 0.6], [0, 1]),  # far too few markets
    ([0.2, 0.4, 0.6, 0.8] * 20, [1] * 80),  # only one kind of outcome
    ([0.2] * 40 + [0.8] * 40, [0] * 40 + [1] * 40),  # perfectly separated: the fit would run off to infinity
])
def test_slope_is_not_available_instead_of_crashing(prices, outcomes):
    assert np.isnan(calibration_slope(np.array(prices), np.array(outcomes))[1])
