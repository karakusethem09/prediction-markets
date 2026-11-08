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
- Start / stop the local database: `docker compose up -d` / `docker compose down` (data is kept in a Docker volume)
- Load everything into Postgres: `python src/load/postgres.py` (uses `DATABASE_URL` if set, else the local Docker database)
- Open a SQL prompt: `docker compose exec postgres psql -U postgres -d prediction_markets`
- Calibration numbers in the terminal: `python src/analysis/calibration.py`
- Analysis notebook: open `notebooks/calibration.ipynb` in VS Code with the `.venv` kernel (needs the database running)
- Online database (Supabase): secrets live in `.env` (git-ignored, never print it): `SUPABASE_URL` (admin, for loading) and `DASHBOARD_DATABASE_URL` (read-only). Use with `set -a; source .env; set +a`, then e.g. `DATABASE_URL="$SUPABASE_URL" python src/load/postgres.py --skip-prices`
- Lock down Supabase / reset the read-only password: `python src/load/secure_online_database.py` (applies `sql/supabase_security.sql`). After adding a table, run it with `--keep-password`, or the live dashboard loses its database login until the Streamlit secret is updated. `--verify` only checks (RLS on, no web API access, reader read-only) and exits 1 on any problem
- Daily incremental update (only new markets): `python src/pipeline.py` (local; add `--with-prices` to also store prices) or `DATABASE_URL="$SUPABASE_URL" python src/pipeline.py` (online)
- Tests use `TEST_DATABASE_URL` (default: local Docker), never `DATABASE_URL`, so they can't touch the online database. With `REQUIRE_DATABASE=1` (set in CI) a missing database fails the run instead of skipping database tests. Dashboard tests use a synthetic dataset (`tests/test_dashboard.py`), never real data
- Dashboard: `streamlit run app/dashboard.py` (needs the database running). Streamlit keeps imported `src/` modules in memory, so the top of `app/dashboard.py` reloads every project module on each run (~1 ms); add new `src/` modules to that list, in dependency order. The second page, `app/pages/forward_test_election_2026.py`, has its own reload list and reads only the results bundle (no database)
- Forward-test results (read-only; writes `docs/data/forward_test_election_2026/` + `docs/election_2026_*.md`): `set -a; source .env; set +a; DATABASE_URL="$DASHBOARD_DATABASE_URL" python src/analysis/forward_test_results.py` (the read-only login). Needs the raw artifacts saved in `data/raw/forward_test/github_artifacts/<github run id>/` (`gh run download <id> -n forward-test-raw-<id> -D ...`) and, for the pilot section, the local database. Weekly steps: `docs/forward_test_collector.md`, "Updating the published results"; commit the regenerated files only after review, never automatically
- Forward-test health: `python -m forward_test health` (Group A by default; exit code 1 if the latest run failed, no success for 22 h, the gap before the latest run exceeded the 24 h entry window, or the database is over 80% of 500 MB; `--storage-limit-mb 0` on the local database)
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
- Snapshot completeness (`src/transform/completeness.py`, table `processing_status`): a horizon is *possible* if `close_time - horizon >= open_time` (a 3-day market only needs the 1-day snapshot). Status per market: `complete`; `retry_needed` (no price history came back yet → the next daily runs re-download its prices with `refresh=True`); `unavailable` with a `reason` (prices came back but don't cover that moment, or still none after `MAX_ATTEMPTS` = 7 runs / 14 days after close). Never store a market in a way that stops it from being retried while it is `retry_needed`.
- Build file paths from `PROJECT_ROOT = Path(__file__).resolve().parents[N]`, never relative to the current directory.
- Save every raw API response under `data/raw/` before transforming it.
- All HTTP calls go through a retry helper: timeout on every request, exponential backoff on 429/5xx and network errors, no retry on other 4xx.
- Loading into Postgres must be idempotent (upsert): rerunning never duplicates rows.
- Ask before adding a new dependency. Add approved ones to `requirements.txt`.
- Write pytest tests for logic (parsing, unit conversions, the snapshot rule). Mock HTTP in tests; tests never call the real API.
- Work in small steps: one feature per task, then stop so I can review and commit.

## Git commits
- Never add `Co-Authored-By: Claude` or any other AI co-author trailer to a commit message.
- Never add "Generated by Claude Code", AI signatures or similar attribution to a commit message.
- Commit as my existing Git identity (`git config user.name` / `user.email`); never change it.
- Keep commit messages short, clear and professional, and describe the actual changes.
- Before every commit, read the final message and make sure no attribution trailer was added automatically.

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
- Done: load step (`docker-compose.yml`, `sql/schema.sql`, `src/load/postgres.py`, tests). One transaction per run (COPY into a temp table, then upsert), logged in `pipeline_runs`; ~30 s for all tables. Database tests use a throwaway `pytest_load` schema and skip if Postgres isn't running.
- Database plan: local Docker Postgres for everything; later copy only `markets` + `snapshots` to Supabase for the online dashboard (the price table is too big for its free tier).
- Done: calibration analysis (`src/analysis/calibration.py` + tests, `notebooks/calibration.ipynb`). Charts are saved with both interactive and PNG outputs so they show on GitHub. Compare horizons only on markets with all three snapshots (`same_markets_only`). Main finding: well calibrated (1–2 points), but prices are slightly too far from 50%.
- Done: Streamlit dashboard (`app/dashboard.py`, `.streamlit/config.toml`, smoke tests with `AppTest`). Reads only `markets` + `snapshots`; platform names come from the data. "Same markets at every horizon" is on by default so the horizons are compared fairly.
- Done: public dashboard at https://polymarket-calibration.streamlit.app (Streamlit Community Cloud, reads Supabase as `dashboard_reader` via the `DATABASE_URL` secret; Supabase locked down by `sql/supabase_security.sql`). Supabase holds markets + snapshots + pipeline_runs only.
- Done: incremental pipeline `src/pipeline.py`. `collect_updates()` in normalize.py is the only place that lists platforms (via `normalize_<platform>_updates`); it downloads the market list and prices only for eligible markets not in the database. Markets whose price download fails aren't loaded, so the next run retries them. `transform/quality.py` checks (download size ≥ 95% of stored markets, schemas, no orphan snapshots) must all pass before anything loads; every run is logged in `pipeline_runs` with `row_counts`, `checks` and `error`.
- Done: `.github/workflows/pipeline.yml` runs daily at 06:17 UTC (and on demand via "Run workflow"): a `test` job against a throwaway Postgres service, then an `update` job running `src/pipeline.py` with the `DATABASE_URL` repo secret (Supabase admin URL); raw downloads kept as an artifact for 14 days. The dashboard shows the last successful update from `pipeline_runs` and warns if the latest run failed. Dashboard tests skip when the database has no data (as in CI).
- Done: dashboard redesign: dark "trading terminal" theme (`.streamlit/config.toml`; the chart uses `calibration_figure(..., theme="dark")`, while the README image and notebook keep the default light theme), LIVE/UPDATE FAILED status pill, bordered number cards (2 per row on phones via CSS), 1D/7D/30D buttons showing one chart at a time, and a "Recently resolved" feed (`recent_markets`). Checked at 1400 px and 390 px (phone) width.
- Done: snapshot completeness tracking (`processing_status` table, retries in `src/pipeline.py`, non-blocking `snapshot_coverage` check). Local data on 2026-10-20: 10,510 complete, 261 unavailable (prices don't cover the moment), 68 unavailable (no prices, closed long ago).
- Done: `.github/workflows/tests.yml` runs all tests on every push and pull request; `pipeline.yml` reuses it before each daily update. README documents the recovery design ("Recovering from missing data").
- Done: freshness indicator (`analysis.calibration.freshness`): FAILED (latest run failed) > STALE (no success for 36 h) > LIVE > NO UPDATES YET; always shown, with an explanation and an "Update history" panel.
- Done: event-level bootstrap (`src/analysis/bootstrap.py`): resamples whole events for intervals on the calibration gap, the calibration slope (fitted on 5–95% prices only; clipping near-certain prices distorted it) and the chart's per-bucket bars. Notebook section 3 and the README findings use it (notebook: 1,000 resamples; dashboard: 300, so intervals can differ in the second decimal). Finding: slope < 1 (too extreme) for all markets at every horizon; on long-running markets only at 30 days.
- Done: research analyses in `src/analysis/combined_yes_strategy.py` (report `docs/combined_yes_strategy.md`) and `src/analysis/group_a_deep_dive.py` (report `docs/group_a_deep_dive.md`). Retrospective and hypothetical: read-only, never claim tradable returns. Use the knowable entry (7 days before the *scheduled* end, from raw `endDate`), not the actual close, which leaks the result. Fix definitions before computing, and report exploration (before 2026-07-01) and test periods separately.
- Built: forward test of Group A (`src/forward_test/`, `sql/forward_test.sql`, `.github/workflows/forward_test.yml`, docs `forward_test_spec.md` + `forward_test_collector.md`). Paper trades only: never add accounts, wallets or order placement. The spec's rules are fixed; changing them means a new, separately labelled test. Only `collector.py` is Polymarket-specific. Commands: `python -m forward_test` (collect), `python -m forward_test report`.
- Done, deployed 2026-11-07: forward-test lab v2: append-only `forward_test_observations` + `forward_test_raw` (migration `sql/migrations/002_*.sql`, applied only via `python -m forward_test migrate`; applied online 2026-11-07), UTC request/response/exchange timestamps, run `settings`, read-back check after each save, `health`, the `election_2026_pilot` experiment (keyset discovery over tags `midterms`/`elections`/`global-elections`/`politics`; classification from tags, title-only matches kept as `unknown`; ≥$10k locally, `--min-volume 100000` online), `--dry-run`, `--limit`. The 'missed' label now requires that no successful run happened in the first day after the mark. Never backdate entries; never combine the pilot with `group_a_7day`. Deploy order for any new migration: migrate online → `secure_online_database.py --keep-password` + `--verify` → push (pushing first stops the collector with SchemaNotReady). Storage: raw books are kept in Supabase (gzip); the online database was 19 MB on 2026-11-07 (free tier: 500 MB), and `health` reports its size and growth.
- Done (2026-11-07): election-period audit and results (`src/analysis/forward_test_results.py`, `forward_test_report.py`, reports `docs/election_2026_data_audit.md` + `docs/election_2026_forward_test_results.md`, dashboard page). 20 Group A positions, 17 officially settled (2 won), verdict PENDING (needs 100). All 387 stored 'missed' labels are mislabelled (runs happened). The election pilot exists only in the local database (Nov 3 17:29–21:08 UTC, then the computer slept). Positions settle only from the exchange's per-token winner flags; observations are never labelled pre/post poll close (no per-market cutoff in the data).
- Done (2026-11-07): simpler schedule. `forward_test.yml` collects Group A only, every 12 hours (`41 */12 * * *`), then runs `health`; the election pilot runs only on demand (`election_day.yml`). Group A rules unchanged; never extend the entry window for late runs. GitHub skipped 55% of the 3-hourly slots and starts the daily cron ~7 h late, so with 12-hour slots one skip can cost markets: `health` reports every gap over 24 h. The results module reads each scheduled run's cron from `forward_test.yml` at the run's commit (`head_sha`), so slot counts stay right across schedule changes (only the `M */H * * *` form is supported).
- Note: Streamlit Community Cloud puts the app to sleep after a quiet spell; the README tells visitors to click the wake-up button.
- Note: GitHub pauses scheduled workflows in a public repo after 60 days without commits; re-enable on the Actions tab if that happens.
- Earlier ideas: README with the findings and chart; deploy the dashboard (copy `markets` + `snapshots` to Supabase, Streamlit Community Cloud); `src/pipeline.py` to run every step with one command; topic split (Polymarket's `category` is empty, so needs another source, e.g. event tags); Kalshi as a second platform.
