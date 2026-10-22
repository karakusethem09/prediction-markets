"""Forward-test rules, independent of any platform (see docs/forward_test_spec.md).

Everything here is a pure function of numbers and times, so the rules can be tested exactly.
"""
from datetime import timedelta

ENTRY_DAYS = 7  # a market is entered when its scheduled end is this close...
MISSED_DAYS = 6  # ...unless it's already this close: then the collector was too late, and it's "missed"
MIN_ENTRY_VOLUME = 100_000  # dollars traded at the moment of entry (the approved universe)
GROUP_A = (0.20, 0.30)  # best ask in [0.20, 0.30): lower bound included, upper excluded
FILL_SIZES = (100, 1_000)  # dollars: what would buying this much have cost on average?


def entry_decision(scheduled_end, now, market_start, test_start):
    """What to do with an open market at this collector run.

    - "wait":   its 7-day mark hasn't come yet
    - "enter":  it crossed its 7-day mark within the last day: record it now
    - "missed": it crossed its 7-day mark while the test was running, but more than a day ago
    - "skip":   it never had a 7-day mark during the test (it started trading later, or the mark
                fell before the test began), so it was never eligible
    """
    remaining = scheduled_end - now
    if remaining > timedelta(days=ENTRY_DAYS):
        return "wait"
    seven_day_mark = scheduled_end - timedelta(days=ENTRY_DAYS)
    if market_start is not None and market_start > seven_day_mark:
        return "skip"
    if remaining > timedelta(days=MISSED_DAYS):
        return "enter"
    return "missed" if seven_day_mark >= test_start else "skip"


def book_summary(bids, asks):
    """Best bid, best ask, size at the best ask, midpoint and spread from (price, size) levels."""
    best_bid = max((price for price, _ in bids), default=None)
    best_ask = min((price for price, _ in asks), default=None)
    ask_size = sum(size for price, size in asks if price == best_ask) if best_ask is not None else None
    both = best_bid is not None and best_ask is not None
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "ask_size_at_best": ask_size,
        "midpoint": (best_bid + best_ask) / 2 if both else None,
        "spread": best_ask - best_bid if both else None,
    }


def fill_price(asks, dollars):
    """Average price per contract to buy `dollars` worth, taking the cheapest asks first.

    Returns None if the book doesn't hold enough contracts to spend that much.
    """
    remaining, shares = dollars, 0.0
    for price, size in sorted(asks):
        if price <= 0:
            continue
        cost = price * size
        if cost >= remaining:
            shares += remaining / price
            return dollars / shares
        remaining -= cost
        shares += size
    return None


def in_group_a(best_ask):
    """Group A: the price a buyer pays (the best ask) is in [0.20, 0.30)."""
    return best_ask is not None and GROUP_A[0] <= best_ask < GROUP_A[1]


def settlement(closed, final, outcome):
    """('open', None) until the result is final; then ('settled', 1 or 0), or ('void', None) for a
    market that ended without a clear YES or NO (e.g. cancelled and split 50/50)."""
    if not (closed and final):
        return "open", None
    if outcome in (0, 1):
        return "settled", outcome
    return "void", None
