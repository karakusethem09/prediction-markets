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
- Download resolved markets: `python src/extract/polymarket.py`
- Check downloaded markets: `python src/extract/check_markets.py`
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
- Every platform's normalized output has the same columns: `market_id` (`poly:<id>`), `platform`, `event_id`, `question`, `category`, `close_time`, `outcome` (1 = Yes happened, 0 = No), `volume_usd`. Prices are always 0–1.
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
- Early (2020) markets can show `outcomePrices` of `["0", "0"]`: treat as bad data.
- A resolved market's final price converges to 0 or 1. Analysis uses prices at fixed horizons (1, 7 and 30 days before close), never the final price.

## Status
- Done: `explore.py`, `polymarket.py` (paginated market download with raw cache and retries), `check_markets.py`.
- Next: confirm the market download works, add tests for it, then build the price-history extractor.
