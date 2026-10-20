"""Which snapshots a market *could* have, which it has, and what to do about the gap.

A horizon is possible only if the market was already open at that moment
(close_time - horizon >= open_time), the same rule the snapshot step uses. A market open
for 3 days can only ever have a 1-day snapshot, and that is complete, not missing.

Statuses (stored in the processing_status table, with a reason when something is missing):
  complete      every possible snapshot exists
  retry_needed  no price history came back yet; the next runs download its prices again
  unavailable   retrying won't help: prices came back but don't cover that moment (trading
                started later than the listed open time, or a multi-day gap), or still no
                prices after MAX_ATTEMPTS runs / RETRY_WINDOW after close. Worth investigating.
Works on any platform's normalized tables.
"""
import pandas as pd

from transform.snapshots import HORIZONS_DAYS

MAX_ATTEMPTS = 7  # one attempt per daily run, so about a week of retries
RETRY_WINDOW = pd.Timedelta(days=14)  # prices that haven't appeared two weeks after close won't


def possible_horizons(open_time, close_time, horizons_days=HORIZONS_DAYS):
    """The horizons a market was open for, e.g. [1] for a 3-day market."""
    return [h for h in horizons_days if close_time - pd.Timedelta(days=h) >= open_time]


def decide_status(possible, found, attempts, close_time, now, has_prices):
    """(status, reason) - see the module docstring. has_prices: did this run get any price history?"""
    if set(possible) <= set(found):
        return "complete", None
    if has_prices:  # the history exists, it just has no fresh price at that moment
        return "unavailable", "price history doesn't cover that moment (trading started later or had a gap)"
    if attempts >= MAX_ATTEMPTS:
        return "unavailable", f"no price history after {attempts} attempts"
    if now - close_time > RETRY_WINDOW:
        return "unavailable", f"no price history, and the market closed over {RETRY_WINDOW.days} days ago"
    return "retry_needed", "no price history yet"


def processing_status(markets, found_by_market, attempts_by_market, now, markets_with_prices):
    """One status row per market.

    markets: market_id, open_time, close_time
    found_by_market: {market_id: horizons that have a snapshot (from this run and earlier ones)}
    attempts_by_market: {market_id: attempts so far, including this run}
    markets_with_prices: ids of markets for which this run got some price history
    """
    rows = []
    for market in markets.itertuples():
        possible = possible_horizons(market.open_time, market.close_time)
        found = sorted(found_by_market.get(market.market_id, set()))
        attempts = attempts_by_market[market.market_id]
        status, reason = decide_status(possible, found, attempts, market.close_time, now,
                                       market.market_id in markets_with_prices)
        rows.append({
            "market_id": market.market_id,
            "status": status,
            "reason": reason,
            "possible_horizons": possible,
            "found_horizons": found,
            "attempts": attempts,
            "last_attempt_at": now,
        })
    return pd.DataFrame(rows, columns=["market_id", "status", "reason", "possible_horizons",
                                       "found_horizons", "attempts", "last_attempt_at"])
