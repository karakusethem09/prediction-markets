"""Turn raw API downloads into clean tables with the same columns for every platform.

Platform-specific code lives only in the normalize_<platform>_* functions;
drop_short_markets works on any platform's normalized table.
"""
import json
from pathlib import Path

import pandas as pd

from extract.polymarket import PRICES_DIR, load_markets
from transform.schemas import markets_schema, prices_schema

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POLYMARKET_MARKETS_DIR = PROJECT_ROOT / "data" / "raw" / "polymarket" / "markets"
MIN_OPEN_DURATION = pd.Timedelta(days=1)  # shorter markets are easy to manipulate and have no 1-day horizon


def to_utc(times, **kwargs):
    """Parse times as UTC in microseconds, so every time column has the same type."""
    return pd.to_datetime(times, utc=True, **kwargs).dt.as_unit("us")


def polymarket_outcome(market):
    """Return 1 if the first outcome won, 0 if it lost, None if there's no clear result."""
    if market.get("umaResolutionStatus") != "resolved":
        return None  # not final yet (e.g. "proposed"), so the result could still change
    first_price = float(json.loads(market["outcomePrices"])[0])
    if first_price > 0.99:
        return 1
    if first_price < 0.01:
        return 0
    return None  # e.g. 0.5 / 0.5: the market was cancelled and split evenly


def normalize_polymarket_markets(raw_markets):
    """Build the standard markets table from raw Polymarket markets, keeping only clear results."""
    rows = []
    for market in raw_markets:
        outcome = polymarket_outcome(market)
        if outcome is None:
            continue
        events = market.get("events") or []
        rows.append({
            "market_id": f"poly:{market['id']}",
            "platform": "polymarket",
            "event_id": f"poly:{events[0]['id']}" if events else None,
            "question": market["question"],
            "category": market.get("category"),  # almost always empty on Polymarket now
            "open_time": market.get("startDate") or market["createdAt"],
            # closedTime, not endDate: markets can resolve before their deadline
            "close_time": market["closedTime"],
            "outcome": outcome,
            "volume_usd": float(market["volumeNum"]),
        })

    markets = pd.DataFrame(rows)
    markets["open_time"] = to_utc(markets["open_time"], format="ISO8601")
    markets["close_time"] = to_utc(markets["close_time"], format="ISO8601")
    return markets


def normalize_polymarket_prices(markets, prices_dir=PRICES_DIR):
    """Build the standard prices table (daily + hourly) for the markets in a normalized table."""
    tables = []
    for market_id in markets["market_id"]:
        raw_id = market_id.removeprefix("poly:")
        for fidelity in ["daily", "hourly"]:
            price_file = prices_dir / fidelity / f"{raw_id}.json"
            if not price_file.exists():  # e.g. a market with no token had nothing to download
                continue
            history = json.loads(price_file.read_text())["history"]
            if history:
                table = pd.DataFrame(history)  # columns "t" (unix seconds) and "p" (price)
                table["market_id"] = market_id
                table["fidelity"] = fidelity
                tables.append(table)

    prices = pd.concat(tables, ignore_index=True)
    prices["time"] = to_utc(prices["t"], unit="s")
    # Prices can tick slightly above 1 (e.g. 1.005) just before resolution
    prices["price"] = prices["p"].astype(float).clip(0, 1)
    return prices[["market_id", "time", "price", "fidelity"]]


def drop_short_markets(markets, min_duration=MIN_OPEN_DURATION):
    """Remove markets that were open for less than min_duration."""
    open_for = markets["close_time"] - markets["open_time"]
    return markets[open_for >= min_duration].reset_index(drop=True)


def build_normalized_tables(verbose=False):
    """Normalize every platform's latest raw data and return validated (markets, prices) tables.

    Later steps call this, so they never need to know which platforms exist.
    """
    latest = sorted(d for d in POLYMARKET_MARKETS_DIR.iterdir() if d.is_dir())[-1]
    raw_markets = load_markets(latest)
    markets = normalize_polymarket_markets(raw_markets)
    clear_result_count = len(markets)
    markets = drop_short_markets(markets)
    prices = normalize_polymarket_prices(markets)

    if verbose:
        print(f"Raw markets ({latest.name}): {len(raw_markets)}")
        print(f"With a clear final result:  {clear_result_count}")
        print(f"Open at least 1 day:        {len(markets)}")
        print(f"Price rows:                 {len(prices):,} "
              f"({prices['market_id'].nunique()} markets have prices)")

    return markets_schema.validate(markets), prices_schema.validate(prices)


if __name__ == "__main__":
    markets, prices = build_normalized_tables(verbose=True)
    print("\nBoth tables pass their schemas.")
    print(f"First outcome won in {markets['outcome'].mean():.1%} of markets")
    print(prices.groupby("fidelity")["market_id"].agg(["count", "nunique"]))
