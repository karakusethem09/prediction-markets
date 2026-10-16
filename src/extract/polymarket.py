"""Download resolved Polymarket markets and their price histories, saving raw API responses to disk."""

import json
import time
from datetime import date, datetime
from pathlib import Path

import requests

# The plain /markets endpoint refuses offsets above 2000, so we use the
# keyset endpoint: each page gives us a cursor that points to the next page.
GAMMA_URL = "https://gamma-api.polymarket.com/markets/keyset"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "polymarket" / "markets" / date.today().isoformat()
MIN_VOLUME = 1_000_000
END_DATE_MIN = "2020-07-01T00:00:00Z"  # one month after Polymarket launched (June 2020)
PAGE_SIZE = 100  # markets per request (the API's maximum)

PRICES_URL = "https://clob.polymarket.com/prices-history"
# No date folder here: a resolved market's history never changes, so a file
# downloaded once is kept, and a rerun only fetches markets it doesn't have yet.
PRICES_DIR = PROJECT_ROOT / "data" / "raw" / "polymarket" / "prices"
HOURLY_WINDOW_DAYS = 15  # the longest window the API allows for hourly prices

session = requests.Session()

def get_with_retry(url, params, max_retries=5):
    for attempt in range(max_retries):
        try:
            response = session.get(url, params=params,  timeout=30)
        except (requests.ConnectionError , requests.Timeout):
            response = None

        if response is not None and response.status_code < 400:
            return response.json()
        if response is not None and response.status_code < 500 and response.status_code != 429:
            response.raise_for_status()  #400,404...: our mistake ,retrying wont help

        if attempt == max_retries - 1:
            break  # last attempt failed: no point waiting before giving up

        wait = 2 ** attempt  # 1, 2, 4, 8 seconds
        print(f"    attempt {attempt + 1} failed, waiting {wait}s")
        time.sleep(wait)

    raise RuntimeError (f"Gave up on {url} after { max_retries} attempts")


def fetch_resolved_markets():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    page_number = 0
    cursor = None  # no cursor yet: the first request gets the first page
    total = 0

    while True:
        page_file = RAW_DIR / f"page_{page_number:04d}.json"

        if page_file.exists():  # already downloaded today: reuse it
            page = json.loads(page_file.read_text())
        else:
            params = {
                "closed": "true",
                "volume_num_min": MIN_VOLUME,
                "end_date_min": END_DATE_MIN,
                "limit": PAGE_SIZE,
            }
            if cursor:
                params["after_cursor"] = cursor
            page = get_with_retry(GAMMA_URL, params)
            page_file.write_text(json.dumps(page))
            time.sleep(0.2)  

        # A page looks like {"markets": [...], "next_cursor": "..."}
        markets = page["markets"]
        total += len(markets)
        print(f"page {page_number}: {len(markets)} markets (total {total})")

        # The saved page includes its next_cursor, so a rerun can continue from the cache
        cursor = page.get("next_cursor")
        if not cursor:  # the last page has no next_cursor
            break
        page_number += 1

    return total


def load_markets(folder):
    """Read every saved market page in a folder and return one list of markets."""
    markets = []
    for page_file in sorted(folder.glob("page_*.json")):
        markets.extend(json.loads(page_file.read_text())["markets"])
    return markets


def fetch_price_history(market):
    """Download the daily and hourly price history of one market's first outcome.

    Only the first outcome is needed: in a two-outcome market the other price is 1 minus it.
    """
    token_id = json.loads(market["clobTokenIds"])[0]
    close_ts = int(datetime.fromisoformat(market["closedTime"]).timestamp())

    requests_to_make = {
        # Whole history, one price per day (interval=max only works with daily prices)
        "daily": {"market": token_id, "interval": "max", "fidelity": 1440},
        # Last 15 days before close, one price per hour, for accurate 1- and 7-day horizons
        "hourly": {
            "market": token_id,
            "startTs": close_ts - HOURLY_WINDOW_DAYS * 24 * 60 * 60,
            "endTs": close_ts,
            "fidelity": 60,
        },
    }

    for kind, params in requests_to_make.items():
        out_file = PRICES_DIR / kind / f"{market['id']}.json"
        if out_file.exists():  # downloaded on an earlier run: skip it
            continue
        history = get_with_retry(PRICES_URL, params)
        out_file.write_text(json.dumps(history))
        time.sleep(0.2)  # be polite to the API


def fetch_all_price_histories(markets):
    """Download price histories for all markets. Returns the ids of markets that failed."""
    (PRICES_DIR / "daily").mkdir(parents=True, exist_ok=True)
    (PRICES_DIR / "hourly").mkdir(parents=True, exist_ok=True)
    failed = []

    for i, market in enumerate(markets, start=1):
        if not market.get("clobTokenIds"):  # no token means no price history to ask for
            continue
        try:
            fetch_price_history(market)
        except (requests.HTTPError, RuntimeError) as error:
            # One bad market shouldn't stop a multi-hour run; nothing is saved
            # for it, so the next run tries it again
            print(f"    market {market['id']} failed: {error}")
            failed.append(market["id"])

        if i % 100 == 0:
            print(f"prices: {i}/{len(markets)} markets done")

    return failed


if __name__ == "__main__":
    n = fetch_resolved_markets()
    print(f"\nDone: {n} resolved markets saved in {RAW_DIR}\n")

    failed = fetch_all_price_histories(load_markets(RAW_DIR))
    print(f"\nDone: price histories saved in {PRICES_DIR}")
    if failed:
        print(f"{len(failed)} markets failed (rerun to retry them): {failed}")