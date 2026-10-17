"""The snapshot rule: each market's price at fixed horizons (1, 7 and 30 days) before close.

For each market and horizon:
  1. target_time = close_time - horizon.
  2. Skip the horizon if the market wasn't open yet at target_time.
  3. Use the most recent price recorded at or before target_time, never after
     (a later price would leak information about the result).
  4. Skip it if that price is more than 1 day old (too stale to trust).

Works on any platform's normalized tables.
"""
import pandas as pd

from transform.normalize import build_normalized_tables
from transform.schemas import snapshots_schema

HORIZONS_DAYS = [1, 7, 30]
MAX_PRICE_AGE = pd.Timedelta(days=1)


def take_snapshots(markets, prices, horizons_days=HORIZONS_DAYS, max_price_age=MAX_PRICE_AGE):
    """Return one row per (market, horizon) with the price at that horizon."""
    # 1. One target row per market per horizon
    targets = markets[["market_id", "open_time", "close_time"]].merge(
        pd.DataFrame({"horizon_days": horizons_days}), how="cross"
    )
    targets["target_time"] = targets["close_time"] - pd.to_timedelta(targets["horizon_days"], unit="D")

    # 2. A horizon from before the market opened has no meaningful price
    targets = targets[targets["target_time"] >= targets["open_time"]]

    # 3 + 4. For each target, find the latest price at or before it, at most max_price_age old.
    # merge_asof needs both tables sorted by time. Sorting by fidelity too puts "hourly"
    # after "daily" at equal times, so the hourly price wins a tie.
    snapshots = pd.merge_asof(
        targets.sort_values("target_time"),
        prices.sort_values(["time", "fidelity"]).rename(columns={"time": "price_time"}),
        left_on="target_time",
        right_on="price_time",
        by="market_id",  # only match prices of the same market
        direction="backward",  # at or before the target, never after
        tolerance=max_price_age,
    )
    snapshots = snapshots.dropna(subset=["price"])  # no fresh-enough price: skip this horizon

    columns = ["market_id", "horizon_days", "target_time", "price_time", "price"]
    return snapshots[columns].sort_values(["market_id", "horizon_days"]).reset_index(drop=True)


if __name__ == "__main__":
    markets, prices = build_normalized_tables(verbose=True)
    snapshots = snapshots_schema.validate(take_snapshots(markets, prices))

    print(f"\nSnapshots: {len(snapshots):,} (passes schema)")
    summary = snapshots.groupby("horizon_days").agg(
        markets=("market_id", "count"),
        median_price_age=("price_time", lambda t: (snapshots.loc[t.index, "target_time"] - t).median()),
    )
    summary["share_of_markets"] = (summary["markets"] / len(markets)).map("{:.0%}".format)
    print(summary)
