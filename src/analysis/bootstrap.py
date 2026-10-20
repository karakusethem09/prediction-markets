"""Event-level bootstrap: uncertainty that respects related markets.

Markets in the same event share one underlying outcome (an election with 20 candidate
markets is closer to one piece of evidence than to 20), so treating every market as
independent makes confidence intervals too narrow. Here we resample whole events with
replacement and recompute the statistics each time.

Resampling events is the same as giving every market a weight equal to how many times its
event was drawn, so each bootstrap round is a weighted calculation on the original table.

Statistics:
  calibration error  average gap between price and how often things happened (as in scores())
  calibration slope  b in P(happened) = sigmoid(a + b * logit(price)). b = 1: honest prices;
                     b < 1: reality is closer to 50% than the prices, i.e. prices are too extreme.
                     Fitted only on prices between 5% and 95% (SLOPE_RANGE): near-certain prices
                     sit at the edge of the logit scale, and squashing them there distorted the
                     slope (on long-running markets it moved from 0.97 to 1.21 depending on how).
Works on any platform's data.
"""
import numpy as np
import pandas as pd

from analysis.calibration import N_BINS, price_bin

N_BOOT = 1000
PRICE_CLIP = 0.01  # logit(0) is -infinity; only a safety net, SLOPE_RANGE keeps prices away from 0 and 1
SLOPE_RANGE = (0.05, 0.95)  # the slope is fitted on these "genuinely uncertain" prices
MIN_SLOPE_MARKETS = 30  # with fewer (e.g. after a narrow dashboard filter) the slope isn't meaningful


def logit(prices):
    p = np.clip(np.asarray(prices, dtype=float), PRICE_CLIP, 1 - PRICE_CLIP)
    return np.log(p / (1 - p))


def calibration_slope(prices, outcomes, weights=None, iterations=25):
    """(a, b) of a weighted logistic regression of outcome on logit(price), fitted by Newton's method.

    Returns (nan, nan) when the slope can't be estimated: too few markets, only one kind of
    outcome, or a fit that doesn't settle (e.g. every high price happened and every low one didn't).
    """
    not_available = (np.nan, np.nan)
    x = np.column_stack([np.ones(len(prices)), logit(prices)])
    y = np.asarray(outcomes, dtype=float)
    w = np.ones(len(y)) if weights is None else np.asarray(weights, dtype=float)
    if w.sum() < MIN_SLOPE_MARKETS or len(np.unique(y[w > 0])) < 2:
        return not_available
    beta = np.array([0.0, 1.0])  # start at "perfectly calibrated"
    for _ in range(iterations):
        p = 0.5 * (1 + np.tanh((x @ beta) / 2))  # the sigmoid, written so it can't overflow
        gradient = x.T @ (w * (y - p))
        hessian = (x * (w * p * (1 - p))[:, None]).T @ x
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:  # e.g. all the weight sits on a single price
            return not_available
        beta += step
        if np.abs(step).max() < 1e-10:
            return beta[0], beta[1]
    return not_available  # didn't settle


def calibration_error(prices, outcomes, weights=None, n_bins=N_BINS):
    """Weighted average |average price - happened rate| over price buckets (weights = bucket sizes)."""
    w = np.ones(len(prices)) if weights is None else np.asarray(weights, dtype=float)
    bins = price_bin(prices, n_bins)
    size = np.bincount(bins, weights=w, minlength=n_bins)
    avg_price = np.bincount(bins, weights=w * np.asarray(prices), minlength=n_bins)
    happened = np.bincount(bins, weights=w * np.asarray(outcomes), minlength=n_bins)
    used = size > 0
    gaps = np.abs(avg_price[used] - happened[used]) / size[used]
    return float(np.sum(size[used] * gaps) / size.sum())


def percentile_or_nan(values, q):
    """np.nanpercentile, but NaN (instead of a warning) when there is no usable value at all."""
    values = np.asarray(values, dtype=float)
    return float(np.nanpercentile(values, q)) if np.isfinite(values).any() else np.nan


def bin_rates(prices, outcomes, weights, n_bins=N_BINS):
    """How often things happened in each price bucket (NaN for an empty bucket)."""
    bins = price_bin(prices, n_bins)
    size = np.bincount(bins, weights=weights, minlength=n_bins)
    happened = np.bincount(bins, weights=weights * np.asarray(outcomes), minlength=n_bins)
    with np.errstate(invalid="ignore", divide="ignore"):
        return happened / size


def event_bootstrap(data, n_boot=N_BOOT, seed=0, by_event=True):
    """Per horizon: estimates with 95% intervals for calibration error and slope, and per-bucket
    intervals for the happened rate.

    by_event=False resamples single markets instead (treats them as independent); comparing the two
    shows how much related markets widen the uncertainty.
    Returns (summary, bin_intervals): one row per horizon, and one row per horizon and price bucket.
    """
    rng = np.random.default_rng(seed)
    summary, bin_rows = [], []
    for horizon, group in data.groupby("horizon_days"):
        prices, outcomes = group["price"].to_numpy(), group["outcome"].to_numpy()
        in_range = (prices >= SLOPE_RANGE[0]) & (prices <= SLOPE_RANGE[1])  # markets used for the slope
        # A market with no event is its own event
        units = group["event_id"].fillna(group["market_id"]) if by_event else group["market_id"]
        codes, n_units = pd.factorize(units)[0], units.nunique()

        errors, slopes, rates = [], [], []
        for _ in range(n_boot):
            draws = np.bincount(rng.integers(0, n_units, n_units), minlength=n_units)
            weights = draws[codes]  # each market counts as often as its event was drawn
            errors.append(calibration_error(prices, outcomes, weights))
            slopes.append(calibration_slope(prices[in_range], outcomes[in_range], weights[in_range])[1])
            rates.append(bin_rates(prices, outcomes, weights))

        intercept, slope = calibration_slope(prices[in_range], outcomes[in_range])
        summary.append({
            "horizon_days": horizon, "markets": len(group), "units": n_units,
            "slope_markets": int(in_range.sum()),
            "calibration_error": calibration_error(prices, outcomes),
            "error_low": np.percentile(errors, 2.5), "error_high": np.percentile(errors, 97.5),
            # nanpercentile: rounds where the slope couldn't be estimated are left out
            "slope": slope, "slope_low": percentile_or_nan(slopes, 2.5), "slope_high": percentile_or_nan(slopes, 97.5),
            "intercept": intercept,
        })
        for b, bucket in enumerate(np.array(rates).T):  # a bucket can be empty in every round (narrow filters)
            bin_rows.append({"horizon_days": horizon, "bin": b,
                             "boot_low": percentile_or_nan(bucket, 2.5), "boot_high": percentile_or_nan(bucket, 97.5)})
    return pd.DataFrame(summary), pd.DataFrame(bin_rows)


def apply_event_intervals(table, bins):
    """Swap a calibration_table()'s Wilson intervals for event-bootstrap ones (from event_bootstrap)."""
    table = table.merge(bins, on=["horizon_days", "bin"], how="left")
    table["ci_low"], table["ci_high"] = table.pop("boot_low"), table.pop("boot_high")
    return table


def calibration_table_with_event_intervals(data, n_boot=N_BOOT, seed=0):
    """calibration_table(), but with 95% intervals from resampling events instead of Wilson intervals
    (which treat every market as independent). The chart reads ci_low / ci_high, so it shows these."""
    from analysis.calibration import calibration_table  # imported here: calibration.py doesn't import this module
    _, bins = event_bootstrap(data, n_boot=n_boot, seed=seed)
    return apply_event_intervals(calibration_table(data), bins)
