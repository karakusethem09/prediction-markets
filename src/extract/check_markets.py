"""Quick check of the downloaded Polymarket markets."""
import json
from pathlib import Path

import pandas as pd

# Find the project folder from this file's location, so it works from anywhere
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MARKETS_DIR = PROJECT_ROOT / "data" / "raw" / "polymarket" / "markets"

if not MARKETS_DIR.exists():
    raise SystemExit(f"No data found in {MARKETS_DIR}\nRun src/extract/polymarket.py first.")

# Each download day has its own folder (e.g. 2026-10-13); take the newest one
day_folders = sorted(d for d in MARKETS_DIR.iterdir() if d.is_dir())
if not day_folders:
    raise SystemExit("The markets folder is empty. Run src/extract/polymarket.py first.")
latest = day_folders[-1]

# Read every page file and put all markets into one list
pages = sorted(latest.glob("page_*.json"))
rows = []
for page in pages:
    markets_on_page = json.loads(page.read_text())
    rows.extend(markets_on_page)

df = pd.DataFrame(rows)  # one row per market, one column per field

print(f"Folder:  {latest.name}")
print(f"Pages:   {len(pages)}")
print(f"Markets: {len(df)}  (columns: {df.shape[1]})")
print()
print(df[["question", "endDate", "volumeNum"]].head())
print()
print("Date range:   ", df["endDate"].min(), "to", df["endDate"].max())
print("Median volume:", round(df["volumeNum"].median()))