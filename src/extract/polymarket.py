"""Download resolved Polymarket markets and save the raw API pages to disk."""

import json
import time
from datetime import date
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
            time.sleep(0.2)  # be polite to the API

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

if __name__ == "__main__":
    n = fetch_resolved_markets()
    print(f"\nDone: {n} resolved markets saved in {RAW_DIR}")