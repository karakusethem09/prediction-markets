"""Forward-test rules, independent of any platform (see docs/forward_test_spec.md).

Everything here is a pure function of numbers and times, so the rules can be tested exactly.
"""
import re
from datetime import datetime, timedelta, timezone

ENTRY_DAYS = 7  # a market is entered when its scheduled end is this close...
MISSED_DAYS = 6  # ...unless it's already this close: then the collector was too late, and it's "missed"
MIN_ENTRY_VOLUME = 100_000  # dollars traded at the moment of entry (the approved universe)
PILOT_MIN_VOLUME = 10_000  # election pilot: markets with at least this much traded (keeps storage affordable)
GROUP_A = (0.20, 0.30)  # best ask in [0.20, 0.30): lower bound included, upper excluded
FILL_SIZES = (100, 1_000)  # dollars: what would buying this much have cost on average?


def entry_decision(scheduled_end, now, market_start, test_start, successful_runs=()):
    """What to do with an open market at this collector run (the frozen rule, docs/forward_test_spec.md).

    - "wait":   its 7-day mark hasn't come yet
    - "enter":  it crossed its 7-day mark within the last day: record it now
    - "missed": it crossed its 7-day mark while the test was running, more than a day ago, and NO
                successful collector run happened in that first day, so the collector really was absent
    - "skip":   it was never eligible while the collector was looking: it opened after its mark, its
                mark came before the test began, or collector runs DID happen in its first day without
                returning it (e.g. it had less than $100,000 traded then)

    successful_runs: start times of earlier successful runs. Without them, a late market can't be told
    apart from one the collector skipped, which mislabelled 12 markets as missed before 2026-10-28.
    """
    remaining = scheduled_end - now
    if remaining > timedelta(days=ENTRY_DAYS):
        return "wait"
    seven_day_mark = scheduled_end - timedelta(days=ENTRY_DAYS)
    if market_start is not None and market_start > seven_day_mark:
        return "skip"
    if remaining > timedelta(days=MISSED_DAYS):
        return "enter"
    if seven_day_mark < test_start:
        return "skip"
    window_end = seven_day_mark + timedelta(days=ENTRY_DAYS - MISSED_DAYS)
    collector_was_running = any(seven_day_mark <= run < window_end for run in successful_runs)
    return "skip" if collector_was_running else "missed"


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
    return fill_order(asks, dollars)["average_price"]


def fill_order(asks, dollars, min_order_size=None):
    """A hypothetical buy of `dollars` worth, walking up the asks from the cheapest.

    Returns contracts bought, the volume-weighted average price, the capital actually spent, and
    whether the order filled completely. A partial fill is reported (filled=False, average_price None),
    never counted as a full one. below_minimum: fewer contracts than the exchange's minimum order size.
    Fees are not included: they're kept separate until a verified fee model exists.
    """
    remaining, shares = dollars, 0.0
    for price, size in sorted(asks):
        if price <= 0:
            continue
        if price * size >= remaining:
            shares += remaining / price
            remaining = 0.0
            break
        remaining -= price * size
        shares += size
    filled = remaining == 0
    return {
        "dollars": dollars, "filled": filled, "shares": shares, "capital": dollars - remaining,
        "average_price": dollars / shares if filled else None,
        "below_minimum": min_order_size is not None and shares < min_order_size,
    }


def in_group_a(best_ask):
    """Group A: the price a buyer pays (the best ask) is in [0.20, 0.30)."""
    return best_ask is not None and GROUP_A[0] <= best_ask < GROUP_A[1]


def official_settlement(final, winners, token_id):
    """A paper position's result from the exchange's own final result, matched by its exact outcome token.

    winners: {token_id: True/False} for every outcome token of the market.
    - ("settled", 1 or 0): the market is final and exactly one token is marked the winner
    - ("open", None):      not final yet (news, race calls or current prices never count)
    - ("void", None):      final, but not exactly one winner (e.g. cancelled and split 50/50)
    - ("unmatched", None): the position's token isn't one of the market's tokens: a data error to investigate
    """
    if token_id not in winners:
        return "unmatched", None
    if not final:
        return "open", None
    if sum(bool(won) for won in winners.values()) != 1:
        return "void", None
    return "settled", int(bool(winners[token_id]))


def settlement(closed, final, outcome):
    """('open', None) until the result is final; then ('settled', 1 or 0), or ('void', None) for a
    market that ended without a clear YES or NO (e.g. cancelled and split 50/50)."""
    if not (closed and final):
        return "open", None
    if outcome in (0, 1):
        return "settled", outcome
    return "void", None


# ---------- order-book evidence ----------

def validate_book(bids, asks):
    """None if the book is usable, otherwise a short explanation (never silently ignored)."""
    if not asks:
        return "no asks: nothing to buy"
    for price, size in [*bids, *asks]:
        if not 0 < price < 1:
            return f"price outside (0, 1): {price}"
        if size <= 0:
            return f"non-positive size: {size}"
    if bids and max(p for p, _ in bids) >= min(p for p, _ in asks):
        return "crossed or locked book: best bid >= best ask"
    return None


def book_depth(bids, asks):
    """How much is on offer: levels, shares on each side, and the dollar cost of every share offered."""
    return {
        "bid_levels": len(bids), "ask_levels": len(asks),
        "bid_depth_shares": sum(size for _, size in bids),
        "ask_depth_shares": sum(size for _, size in asks),
        "ask_depth_usd": sum(price * size for price, size in asks),
    }


def exchange_time(milliseconds):
    """The exchange's book timestamp (milliseconds since 1970, as text) as a UTC time; None if absent."""
    if milliseconds in (None, ""):
        return None
    return datetime.fromtimestamp(int(milliseconds) / 1000, tz=timezone.utc)


# ---------- markets ----------

MIDTERM_TAGS = {"midterms"}  # Polymarket's tag for the 2026 US midterm (general) election
# Words that make an event *look* election-related when it has no election tag (title only: descriptions
# mention elections in the rules of many unrelated markets, e.g. "Will Hamas agree to disarm")
ELECTION_TITLE_WORDS = re.compile(
    r"\b(elections?|senate|governor|gubernatorial|congressional|primary|mayor(al)?|prime minister|presidential|"
    r"referendum|ballot|proposition|runoff|popular vote|electoral|midterms?)\b", re.IGNORECASE)


def classify_event(tag_slugs, title=""):
    """Is this event about an election, and how sure are we?

    - "us_midterms_2026": tagged midterms
    - "election_other":   any other tag with "election" in it (elections, global-elections, senate-elections, ...)
    - "unknown":          no election tag, but the title looks election-related: kept, as a separate bucket
    - None:               not an election event (e.g. a politics event about something else)
    """
    tags = {tag for tag in tag_slugs or [] if tag}
    if tags & MIDTERM_TAGS:
        return "us_midterms_2026"
    if any("election" in tag for tag in tags):
        return "election_other"
    if ELECTION_TITLE_WORDS.search(title or ""):
        return "unknown"
    return None


def first_outcome(outcome_names, token_ids):
    """The first-listed outcome and its token, after checking the two lists line up one-to-one."""
    if len(outcome_names) != len(token_ids) or len(outcome_names) < 2:
        raise ValueError(f"outcomes and tokens don't line up: {len(outcome_names)} outcomes, {len(token_ids)} tokens")
    return outcome_names[0], token_ids[0]


def meets_group_a_volume(volume_usd):
    return volume_usd is not None and volume_usd >= MIN_ENTRY_VOLUME


# ---------- election day and collector health ----------

FIRST_POLL_CLOSE = datetime(2026, 11, 3, 23, 0, tzinfo=timezone.utc)  # first US polls close (6 pm Eastern)


def before_first_poll_close(observed_at):
    """Assembled strictly before the first US polls closed: a reference time for counts, not a per-market
    cutoff (each race closes at its own time, which the data doesn't hold). Uses the observation time,
    the latest of the observation's timestamps, so nothing later slips in."""
    return observed_at < FIRST_POLL_CLOSE


def is_stale(last_success, now, max_age):
    """True if the collector hasn't succeeded within max_age (or never has)."""
    return last_success is None or now - last_success > max_age
