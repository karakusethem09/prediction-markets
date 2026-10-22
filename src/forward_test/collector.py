"""Read Polymarket's public market list and order books for the forward test.

The only Polymarket-specific file in src/forward_test (like src/extract/ for historical data). Read-only
public endpoints: no account, no wallet, no orders. Every raw response is saved before it is used,
and every HTTP call goes through the project's retry helper.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from extract.polymarket import GAMMA_URL, get_with_retry
from forward_test.models import ENTRY_DAYS, MIN_ENTRY_VOLUME
from transform.normalize import polymarket_outcome

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "forward_test"
MARKET_URL = "https://gamma-api.polymarket.com/markets/{id}"
BOOK_URL = "https://clob.polymarket.com/book"


def save_raw(run_started, kind, name, data):
    """Keep every raw response (the project rule), grouped by run."""
    folder = RAW_DIR / f"{run_started:%Y-%m-%d}" / f"{run_started:%H%M}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{kind}_{name}.json").write_text(json.dumps(data))


def parse_time(text):
    return pd.Timestamp(text).tz_convert("UTC") if text else None


def open_markets_near_entry(now, run_started):
    """Open markets with at least MIN_ENTRY_VOLUME traded whose scheduled end is within ENTRY_DAYS."""
    params = {
        "closed": "false",
        "end_date_min": f"{now:%Y-%m-%dT%H:%M:%SZ}",
        "end_date_max": f"{now + timedelta(days=ENTRY_DAYS):%Y-%m-%dT%H:%M:%SZ}",
        "volume_num_min": MIN_ENTRY_VOLUME,
        "limit": 100,
    }
    markets, page_number, cursor = [], 0, None
    while True:
        page = get_with_retry(GAMMA_URL, {**params, **({"after_cursor": cursor} if cursor else {})})
        save_raw(run_started, "markets", f"page_{page_number:03d}", page)
        markets += page["markets"]
        cursor = page.get("next_cursor")
        if not cursor:
            return markets
        page_number += 1


def to_candidate(raw):
    """The fields the forward test needs, or None for markets it can't use (not binary, no order book)."""
    tokens = json.loads(raw.get("clobTokenIds") or "[]")
    outcomes = json.loads(raw.get("outcomes") or "[]")
    if len(tokens) != 2 or len(outcomes) != 2 or not raw.get("enableOrderBook") or not raw.get("endDate"):
        return None
    events = raw.get("events") or []
    return {
        "market_id": f"poly:{raw['id']}",
        "platform": "polymarket",
        "event_id": f"poly:{events[0]['id']}" if events else None,
        "question": raw["question"],
        "token_id": tokens[0],  # the first outcome (e.g. "Yes"), as everywhere in the project
        "scheduled_end": parse_time(raw["endDate"]),
        "market_start": parse_time(raw.get("startDate") or raw.get("createdAt")),
        "volume_at_entry": float(raw.get("volumeNum") or 0),
        "liquidity_at_entry": float(raw.get("liquidityNum") or 0),
    }


def order_book(token_id, run_started):
    """The YES token's order book: (price, size) levels for bids and asks, plus tick and minimum sizes."""
    book = get_with_retry(BOOK_URL, {"token_id": token_id})
    save_raw(run_started, "book", token_id[-12:], book)
    levels = lambda side: [(float(level["price"]), float(level["size"])) for level in book.get(side, [])]
    number = lambda key: float(book[key]) if book.get(key) not in (None, "") else None
    return {
        "bids": levels("bids"), "asks": levels("asks"),
        "tick_size": number("tick_size"), "min_order_size": number("min_order_size"),
        "last_trade_price": number("last_trade_price"),
    }


def market_result(market_id, run_started):
    """Whether a recorded market has closed, whether its result is final, the outcome, and final volume."""
    raw_id = market_id.removeprefix("poly:")
    market = get_with_retry(MARKET_URL.format(id=raw_id), {})
    save_raw(run_started, "result", raw_id, market)
    return {
        "closed": bool(market.get("closed")),
        "final": market.get("umaResolutionStatus") == "resolved",
        "outcome": polymarket_outcome(market),  # 1, 0, or None (not final yet, or no clear result)
        "final_volume": float(market.get("volumeNum") or 0),
    }


def utc_now():
    return datetime.now(timezone.utc)
