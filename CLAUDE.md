# Prediction Markets Calibration Project

## What this is
Portfolio project that measures whether Polymarket prices are calibrated: when a market says 70%, does the event happen about 70% of the time? Python ETL → PostgreSQL → statistical analysis → Streamlit dashboard. Version 1 is Polymarket only; Kalshi is added later as a second platform.

## About me
I'm a student learning data engineering while building this, and I want to be able to explain every line in an interview.
- After each change, explain in 3–5 plain-English bullets what you changed and why.
- Prefer simple, readable code over clever code. Add short comments where the "why" isn't obvious.
- If I ask for something that conflicts with these rules, point it out before doing it.

## Stack
macOS, Python 3.13, requests, pandas, pandera, PostgreSQL 16 (Docker Compose), SQLAlchemy, pytest, Streamlit + Plotly.

## Commands
- Activate environment: `source .venv/bin/activate`
- Install dependencies and make `src/` packages importable (once): `pip install -r requirements.txt && pip install -e .`
- Download resolved markets and price histories: `python src/extract/polymarket.py`
- Check downloaded markets: `python src/extract/check_markets.py`
- Normalize and validate: `python src/transform/normalize.py`
- Take snapshots: `python src/transform/snapshots.py`
- Run tests: `pytest`

## Target structure
```
src/extract/      polymarket.py (later kalshi.py): API clients, raw downloads only
src/transform/    normalize.py, snapshots.py, schemas.py (pandera)
src/load/         postgres.py: upserts, pipeline_runs logging
src/pipeline.py   one command runs everything
sql/schema.sql    tables: markets, price_history, snapshots, pipeline_runs
notebooks/        analysis
app/              Streamlit dashboard
tests/            pytest
data/raw/         cached API responses (git-ignored, never commit)
```

## Rules
- Platform-specific code lives only in `src/extract/` and the `normalize_<platform>` functions. Snapshot, analysis and dashboard code must never mention a platform by name.
- Every platform's normalized output has the same columns: `market_id` (`poly:<id>`), `platform`, `event_id`, `question`, `category`, `open_time`, `close_time`, `outcome` (1 = the first outcome happened, e.g. Yes; 0 = it didn't), `volume_usd`. Prices are the first outcome's price, always 0–1. `src/transform/schemas.py` enforces this.
- Keep only markets with a clear, final result, open at least 1 day (shorter markets are easy to manipulate).
- Snapshot rule: for each horizon (1, 7, 30 days), use the latest price at or before `close_time - horizon`, never after, and at most 1 day old; skip the horizon if the market wasn't open yet or no price is fresh enough.
- Build file paths from `PROJECT_ROOT = Path(__file__).resolve().parents[N]`, never relative to the current directory.
- Save every raw API response under `data/raw/` before transforming it.
- All HTTP calls go through a retry helper: timeout on every request, exponential backoff on 429/5xx and network errors, no retry on other 4xx.
- Loading into Postgres must be idempotent (upsert): rerunning never duplicates rows.
- Ask before adding a new dependency. Add approved ones to `requirements.txt`.
- Write pytest tests for logic (parsing, unit conversions, the snapshot rule). Mock HTTP in tests; tests never call the real API.
- Work in small steps: one feature per task, then stop so I can review and commit.

## Polymarket API notes
- Markets: `GET https://gamma-api.polymarket.com/markets` with `closed`, `limit`, `offset`, `order`, `ascending`, `volume_num_min`, `end_date_min`, `end_date_max`.
- `/markets` refuses `offset` above 2000 (HTTP 422). For full downloads use `GET /markets/keyset` (same filters, `limit` max 100): it returns `{"markets": [...], "next_cursor": "..."}`, pass `next_cursor` back as `after_cursor`; the last page has no `next_cursor`.
- Markets can resolve before their deadline (e.g. "…by end of 2027?" settled early): `endDate` is the scheduled deadline, `closedTime` is when it actually closed. Use `closedTime` for `close_time`, and measure the 1/7/30-day horizons from it.
- `outcomes`, `outcomePrices` and `clobTokenIds` are JSON-encoded strings: parse them with `json.loads`. Token order matches outcome order.
- Price history: `GET https://clob.polymarket.com/prices-history?market=<token_id>&interval=max&fidelity=<minutes>` returns `{"history": [{"t": unix_seconds, "p": price}]}`.
  - `interval=max` only works with `fidelity=1440` (daily); hourly (`fidelity=60`) needs `startTs`/`endTs` with a window of at most 15 days. We save daily (full history) + hourly (last 15 days before `closedTime`) per market, for the first outcome's token only.
  - Markets open less than 1 day return an empty daily history; their hourly file has everything. Markets closed before 2023 (pre order book) have no history at all.
  - Prices can slightly exceed 1 (e.g. 1.005) right before resolution: clip to 0–1 when normalizing, never edit the raw files.
- Early (2020) markets can show `outcomePrices` of `["0", "0"]`: treat as bad data.
- A resolved market's final price converges to 0 or 1. Analysis uses prices at fixed horizons (1, 7 and 30 days before close), never the final price.

## Status
- Done: `explore.py`, `polymarket.py` (keyset-paginated market download + daily/hourly price histories, raw cache, retries, tests), `check_markets.py`. Data: 11,368 resolved markets with volume ≥ $1M ending from 2020-07-01.
- Done: transform step (`normalize.py`, `schemas.py`, tests): 10,792 markets kept, 3.1M price rows (daily + hourly).
- Done: snapshots (`snapshots.py`, tests): 20,845 snapshots — 1d: 10,720 markets, 7d: 6,624, 30d: 3,501.
- Next: load markets, prices and snapshots into Postgres (Docker Compose, `sql/schema.sql`, idempotent upserts).
