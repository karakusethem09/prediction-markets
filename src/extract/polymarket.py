"Download resolved Polymarket markets and save the raw API pages to disk."""

import json
import time
from datetime import date
from pathlib import Path

import requests

GAMMA_URL = "https://gamma-api.polymarket.com/markets"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "polymarket" / "markets" / date.today().isoformat()
MIN_VOLUME = 1_000 

session = requests.Sessiom()

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

        wait = 2 ** attempt #1, 2, 4, 8, 18 seconds
        print(f"    attempt {attempt + 1} failed, waiting {wait}s")
        time.sleep(wait)

    raise RuntimeError (f"Gave up on {url} after { max_retries} attempts")


def fetch_resolved_markets():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    offset = 0
    total = 0

    while True:
        page_file = RAW_DIR / f"page_{offset:06d}.json"

        if page_file.exists():  # already downloaded today: reuse it
            page = json.loads(page_file.read_text())
        else:
            params = {
                "closed": "true",
                "volume_num_min": MIN_VOLUME,
                "order": "id",
                "ascending": "true",
                "limit": PAGE_SIZE,
                "offset": offset,
            }
            page = get_with_retry(GAMMA_URL, params)
            page_file.write_text(json.dumps(page))
            time.sleep(0.2)  # be polite to the API

        total += len(page)
        print(f"offset {offset}: {len(page)} markets (total {total})")

        if len(page) < PAGE_SIZE:  # a short page means we reached the end
            break
        offset += PAGE_SIZE

    return total

if __name__ == "__main__":
    n = fetch_resolved_markets()
    print(f"\nDone: {n} resolved markets saved in {RAW_DIR}")