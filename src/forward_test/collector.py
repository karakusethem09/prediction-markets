"""Read Polymarket's public market data and order books for the forward test.

The only Polymarket-specific file in src/forward_test (like src/extract/ for historical data). Read-only
public endpoints: no account, no wallet, no orders. Every raw response is kept as evidence (see
Evidence), every request is timed in UTC, and every HTTP call goes through the project's retry helpers.
"""
import gzip
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from extract.polymarket import GAMMA_URL, get_with_retry, post_with_retry
from forward_test.models import ENTRY_DAYS, MIN_ENTRY_VOLUME, classify_event, exchange_time, first_outcome
from transform.normalize import polymarket_outcome

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "forward_test"
EVENTS_URL = "https://gamma-api.polymarket.com/events/keyset"  # /events refuses offsets above ~2,000 (HTTP 422)
EVENT_URL = "https://gamma-api.polymarket.com/events/{id}"
MARKET_URL = "https://gamma-api.polymarket.com/markets/{id}"
BOOK_URL = "https://clob.polymarket.com/book"
BOOKS_URL = "https://clob.polymarket.com/books"
# Where the pilot looks. "politics" also catches election events with other tags (senate-elections, ...) and
# election-like titles; models.classify_event decides from each event's own tags and title which are kept.
ELECTION_TAG_SLUGS = ["midterms", "elections", "global-elections", "politics"]
BOOKS_PER_REQUEST = 50
# Market and event fields archived (verbatim) for each observed election market. Only fields that define the
# market: values that change every run are in the observation row (volume, liquidity) or the archived book
# (prices), so this body changes only when the market itself does (e.g. a new scheduled end) and is stored once.
METADATA_FIELDS = ["id", "question", "conditionId", "slug", "outcomes", "clobTokenIds", "endDate", "startDate",
                   "createdAt", "active", "closed", "acceptingOrders", "enableOrderBook", "umaResolutionStatus",
                   "negRisk"]
EVENT_FIELDS = ["id", "title", "slug", "endDate", "negRisk"]


def utc_now():
    return datetime.now(timezone.utc)


class Evidence:
    """Every raw response of a run, stored once per distinct content.

    Each body is identified by the sha256 of its JSON text and gzip-compressed, ready for the
    append-only forward_test_raw table, so the database holds the evidence (not only the 14-day
    GitHub artifacts). Identical content is stored once. keep_files also writes each body under
    data/raw/forward_test/ as <kind>_<sha>.json.gz (off in dry runs), which the workflow uploads as the run's artifact.

    Storage policy (one full pilot run would otherwise add ~17 MB, mostly event pages listing all
    ~25,000 election markets): order books are archived in full except their timestamp and hash, which
    each observation stores in its own columns, so an unchanged book is stored once; election market
    metadata is archived as the verbatim values of METADATA_FIELDS per observed market; full event
    pages (~6 MB each) go to the run's files only.
    """

    def __init__(self, run_started, keep_files=True):
        self.run_started, self.keep_files, self.bodies, self.files_only = run_started, keep_files, {}, set()

    def add(self, kind, data, archive=True):
        """Keep a body; archive=False writes it to the run's files only (too big for the database)."""
        text = json.dumps(data, sort_keys=True)
        sha = hashlib.sha256(text.encode()).hexdigest()
        if sha not in self.bodies and sha not in self.files_only:
            body_gzip = gzip.compress(text.encode())
            if archive:
                self.bodies[sha] = {"sha256": sha, "kind": kind, "body_gzip": body_gzip, "size_bytes": len(text)}
            else:
                self.files_only.add(sha)
            if self.keep_files:  # compressed: one run's event pages are ~190 MB as plain JSON
                folder = RAW_DIR / f"{self.run_started:%Y-%m-%d}" / f"{self.run_started:%H%M}"
                folder.mkdir(parents=True, exist_ok=True)
                (folder / f"{kind}_{sha[:16]}.json.gz").write_bytes(body_gzip)
        return sha


def timed(call):
    """Run an API call and note when it was sent and when the answer arrived (UTC)."""
    requested_at = utc_now()
    data = call()
    return data, {"requested_at": requested_at, "received_at": utc_now()}


def parse_time(text):
    return pd.Timestamp(text).tz_convert("UTC") if text else None


def to_candidate(raw, event=None):
    """The fields the forward test needs from one market, or None if it can't be used
    (not binary, no order book, no scheduled end, or outcomes and tokens that don't line up)."""
    try:
        outcome_names = json.loads(raw.get("outcomes") or "[]")
        token_ids = json.loads(raw.get("clobTokenIds") or "[]")
        observed_outcome, observed_token = first_outcome(outcome_names, token_ids)
    except ValueError:
        return None
    if len(token_ids) != 2 or not raw.get("enableOrderBook") or not raw.get("endDate"):
        return None
    event = event or ((raw.get("events") or [None])[0]) or {}
    return {
        "market_id": f"poly:{raw['id']}",
        "platform": "polymarket",
        "event_id": f"poly:{event['id']}" if event.get("id") else None,
        "event_title": event.get("title"),
        "question": raw["question"],
        "outcome_names": outcome_names,
        "token_ids": token_ids,
        "observed_outcome": observed_outcome,  # the first-listed outcome, as everywhere in the project
        "token_id": observed_token,
        "scheduled_end": parse_time(raw["endDate"]),
        "market_start": parse_time(raw.get("startDate") or raw.get("createdAt")),
        "market_active": raw.get("active"),
        "market_closed": raw.get("closed"),
        "accepting_orders": raw.get("acceptingOrders"),
        "volume_at_entry": float(raw.get("volumeNum") or raw.get("volume") or 0),
        "liquidity_at_entry": float(raw.get("liquidityNum") or raw.get("liquidity") or 0),
    }


# ---------- the frozen Group A universe ----------

def open_markets_near_entry(now, evidence):
    """Open markets with at least MIN_ENTRY_VOLUME traded whose scheduled end is within ENTRY_DAYS.
    Returns (raw market, metadata timing) pairs."""
    params = {
        "closed": "false",
        "end_date_min": f"{now:%Y-%m-%dT%H:%M:%SZ}",
        "end_date_max": f"{now + timedelta(days=ENTRY_DAYS):%Y-%m-%dT%H:%M:%SZ}",
        "volume_num_min": MIN_ENTRY_VOLUME,
        "limit": 100,
    }
    found, cursor = [], None
    while True:
        page, timing = timed(lambda: get_with_retry(GAMMA_URL, {**params, **({"after_cursor": cursor} if cursor else {})}))
        timing["sha256"] = evidence.add("markets_page", page)
        found += [(market, timing) for market in page["markets"]]
        cursor = page.get("next_cursor")
        if not cursor:
            return found


def event_tags(event_id, evidence, cache):
    """An event's tag slugs (from GET /events/{id}), cached for the run. [] if the event can't be read."""
    if event_id not in cache:
        try:
            event, _ = timed(lambda: get_with_retry(EVENT_URL.format(id=event_id.removeprefix("poly:")), {}))
            evidence.add("event", event)
            cache[event_id] = [tag.get("slug") for tag in event.get("tags") or [] if tag.get("slug")]
        except (requests.HTTPError, RuntimeError):
            cache[event_id] = []  # classified as "unknown", never guessed
    return cache[event_id]


# ---------- the election pilot ----------

def election_events(evidence):
    """Every open event tagged with one of ELECTION_TAG_SLUGS, with the timing of the page it came on.
    Pages through /events/keyset: pass each page's next_cursor back as after_cursor; the last page has none."""
    events = {}
    for slug in ELECTION_TAG_SLUGS:
        cursor = None
        while True:
            params = {"tag_slug": slug, "closed": "false", "limit": 100, **({"after_cursor": cursor} if cursor else {})}
            page, timing = timed(lambda: get_with_retry(EVENTS_URL, params))
            evidence.add("events_page", page, archive=False)  # ~0.5 MB each: files only (see Evidence)
            for event in page["events"]:
                events.setdefault(event["id"], (event, timing))  # an event under two tags is kept once
            cursor = page.get("next_cursor")
            if not cursor:
                break
    return list(events.values())


def pilot_markets(events_with_timing, min_volume, evidence):
    """Usable open markets of the election events with at least `min_volume` traded, each with its
    event's tags and a classification made from those tags and the event title (models.classify_event).
    Events that aren't about an election are left out; each market is kept once."""
    markets, seen = [], set()
    for event, timing in events_with_timing:
        tags = [tag.get("slug") for tag in event.get("tags") or [] if tag.get("slug")]
        classification = classify_event(tags, event.get("title"))
        if classification is None:
            continue
        for raw in event.get("markets") or []:
            if raw.get("closed") or raw.get("id") in seen:
                continue
            candidate = to_candidate(raw, event)
            if candidate is None or candidate["volume_at_entry"] < min_volume:
                continue
            metadata = {**timing, "sha256": evidence.add("market", market_metadata(raw, event, evidence))}
            seen.add(raw.get("id"))
            markets.append({**candidate, "event_tags": tags, "classification": classification,
                            "metadata": metadata})
    return markets


def market_metadata(raw, event, evidence):
    """The verbatim values of METADATA_FIELDS for one market, its event's header and tags, and a link
    to its rule text (stored once per distinct text)."""
    description = raw.get("description") or ""
    return {
        "market": {k: raw.get(k) for k in METADATA_FIELDS},
        "event": {**{k: event.get(k) for k in EVENT_FIELDS},
                  "tags": [{"slug": t.get("slug"), "label": t.get("label")} for t in event.get("tags") or []]},
        "description_sha256": evidence.add("description", {"description": description}),
    }


# ---------- order books ----------

def parse_book(book):
    """An order book as numbers: (price, size) levels plus the exchange's own details."""
    levels = lambda side: [(float(level["price"]), float(level["size"])) for level in book.get(side) or []]
    number = lambda key: float(book[key]) if book.get(key) not in (None, "") else None
    return {"bids": levels("bids"), "asks": levels("asks"), "tick_size": number("tick_size"),
            "min_order_size": number("min_order_size"), "last_trade_price": number("last_trade_price"),
            "book_exchange_time": exchange_time(book.get("timestamp")), "book_hash": book.get("hash")}


def order_books(token_ids, evidence):
    """{token_id: book} for many tokens: batches of BOOKS_PER_REQUEST, then one request per token the
    batch didn't return, so a missing book becomes an explicit error instead of a silent gap."""
    books = {}
    for start in range(0, len(token_ids), BOOKS_PER_REQUEST):
        batch = token_ids[start:start + BOOKS_PER_REQUEST]
        found, timing = timed(lambda: post_with_retry(BOOKS_URL, [{"token_id": t} for t in batch]))
        for book in found:
            books[book["asset_id"]] = {**parse_book(book), **timing, "sha256": archive_book(book, evidence), "error": None}
    for token in [t for t in token_ids if t not in books]:
        books[token] = order_book(token, evidence)
    return books


def order_book(token_id, evidence):
    """One token's order book; {"error": ...} if the exchange has none."""
    try:
        book, timing = timed(lambda: get_with_retry(BOOK_URL, {"token_id": token_id}))
    except requests.HTTPError as error:
        return {"error": f"no order book ({error.response.status_code if error.response is not None else 'HTTP error'})",
                "requested_at": None, "received_at": None, "sha256": None}
    return {**parse_book(book), **timing, "sha256": archive_book(book, evidence), "error": None}


def archive_book(book, evidence):
    """Archive a book in full except its timestamp and hash, which each observation keeps in its own
    columns, so a book whose levels haven't changed is stored once."""
    return evidence.add("book", {k: v for k, v in book.items() if k not in ("timestamp", "hash")})


# ---------- settlement ----------

def market_result(market_id, evidence):
    """Whether a recorded market has closed, whether its result is final, the outcome, and final volume."""
    raw_id = market_id.removeprefix("poly:")
    market, _ = timed(lambda: get_with_retry(MARKET_URL.format(id=raw_id), {}))
    evidence.add("result", market)
    return {
        "closed": bool(market.get("closed")),
        "final": market.get("umaResolutionStatus") == "resolved",
        "outcome": polymarket_outcome(market),  # 1, 0, or None (not final yet, or no clear result)
        "final_volume": float(market.get("volumeNum") or 0),
    }


# ---------- read-only audit of recorded positions ----------

CLOB_MARKET_URL = "https://clob.polymarket.com/markets/{condition_id}"


def official_resolution(market_id, event_id, evidence):
    """The exchange's own result for one market, per outcome token, for auditing paper positions.

    Read-only, and never used for an entry price. The order book service marks each token as the winner
    or not; the market list says whether the result is final (resolved by the oracle). Also returns the
    event's title and tags (for exploratory categories) and the fee parameter the market publishes.
    """
    market = get_with_retry(MARKET_URL.format(id=market_id.removeprefix("poly:")), {})
    evidence.add("audit_market", market)
    book_market = get_with_retry(CLOB_MARKET_URL.format(condition_id=market["conditionId"]), {})
    evidence.add("audit_clob_market", book_market)
    event = get_with_retry(EVENT_URL.format(id=event_id.removeprefix("poly:")), {}) if event_id else {}
    evidence.add("audit_event", event)
    return {
        "market_id": market_id,
        "final": bool(market.get("closed")) and bool(book_market.get("closed"))
                 and market.get("umaResolutionStatus") == "resolved",
        "winners": {token["token_id"]: bool(token.get("winner")) for token in book_market.get("tokens") or []},
        "official_close_time": parse_time(market.get("closedTime")),
        "outcome_names": json.loads(market.get("outcomes") or "[]"),
        "token_ids": json.loads(market.get("clobTokenIds") or "[]"),
        "event_title": event.get("title"),
        "event_tags": [tag.get("slug") for tag in event.get("tags") or [] if tag.get("slug")],
        "published_taker_fee": book_market.get("taker_base_fee"),  # units/formula not verified: context only
        "checked_at": utc_now(),
    }
