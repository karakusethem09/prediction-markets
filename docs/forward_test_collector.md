# Forward-test collector: how to run, check and pause it

The rules are in [`forward_test_spec.md`](forward_test_spec.md). This page is the operating manual. **Paper trades only:** the collector reads public Polymarket data and writes to the project's own database. It has no account, no wallet and never places orders.

## What one run does

1. Logs the run in `forward_test_runs` as *running*.
2. Lists open markets with at least $100,000 traded whose scheduled end is within 7 days.
3. For each market not recorded yet:
   - **enter** it if it crossed its 7-day mark within the last day: read its order book and record the best bid/ask, the size at the best ask, the midpoint, and the average price to buy $100 and $1,000 worth;
   - mark it **missed** if it crossed the mark more than a day ago while the test was running;
   - **skip** it if it never had a 7-day mark during the test (it opened later, or the mark came before the test started).
4. Checks every *open* entry: once its market has closed with a final result, marks it **settled** (YES/NO) or **void** (no clear result).
5. Saves all of this in **one transaction**, so a failed run changes nothing. Then it logs the run as *success* or *failed*.

Re-running is safe: a market is recorded once and never overwritten. Every raw API response is saved under `data/raw/forward_test/<date>/<time>/`.

## Running it

The schema is never changed by a collection. Apply migrations on purpose first; a collection refuses to run until they are applied.

| What | Command |
|---|---|
| Apply migrations (once per database; safe to repeat) | `python -m forward_test migrate` |
| Dry run: everything except saving (no database changes, no files) | `python -m forward_test --dry-run --limit 5` |
| One real collection, both experiments | `python -m forward_test` |
| Only one experiment | `python -m forward_test --experiment election_2026_pilot` (or `group_a_7day`) |
| Fewer markets, for local testing | `--limit N` (at most N markets per experiment, and N settlement checks) |
| Against the online database | `set -a; source .env; set +a` then prefix any command with `DATABASE_URL="$SUPABASE_URL"` |
| Election pilot with a higher volume floor | `python -m forward_test --experiment election_2026_pilot --min-volume 100000` |
| Is it healthy? | `python -m forward_test health`: Group A only by default (`--experiment election_2026_pilot --stale-minutes 30` checks the pilot instead). Exit code 1 on any problem, see [Checking on it](#checking-on-it). Add `--storage-limit-mb 0` on the local database, which has no size limit |
| Automatically | GitHub Actions, [`.github/workflows/forward_test.yml`](../.github/workflows/forward_test.yml): **Group A only, every 12 hours** (`41 */12 * * *`: 00:41 and 12:41 UTC; every 3 hours until 2026-11-07), then the health check. GitHub starts scheduled runs late (6.7 h on median for the daily update) and skipped 55% of the 3-hourly slots. |
| Election pilot, on demand | [`.github/workflows/election_day.yml`](../.github/workflows/election_day.yml): one run when started with **Run workflow** (its Nov 3–4 schedule was removed unused, and the 3-hourly pilot step on 2026-11-07). Pilot only, markets ≥ $100,000. |

**Storage**, measured online on 2026-11-07:
- **A Group A run** adds about 65 kB (the market list and the settlement checks), plus about 2 kB for each market it enters (about 23 a day, whatever the schedule). At 2 runs a day that's about 0.2 MB a day, or 6 MB a month.
- **A pilot run** at the $100,000 floor observes about 320 markets and adds about 0.7 MB: observation rows about 0.43 MB, plus the books that changed. At the default $10,000 floor (local) it's about 2,170 markets and 3.4 MB.
- Market metadata and rule texts are stored only when they change. Raw files (gzip) are kept under `data/raw/forward_test/` and in the run's artifact.

After every save the collector reads the run back in a new session. If any observation, or its link to an archived book, is missing, the run is logged as **failed**.

## Checking on it

- **Results so far:** `python -m forward_test report` (add `DATABASE_URL=...` for the online database). It shows:
  - how many markets were recorded,
  - how many Group A trades were entered and settled,
  - how far the asking price sat above the midpoint,
  - once trades settle, the win rate and the return at the asking price and at the $100 / $1,000 fill prices.
  
  The spec's verdict appears only once **100 Group A trades** have settled.
- **Health:** `python -m forward_test health` runs at the end of every scheduled run (read-only). It fails the run, and GitHub emails the repository owner, if:
  - **the latest run failed**;
  - **Group A is stale:** no successful run for 22 hours (the 12-hour schedule plus up to 10 hours of GitHub delay). Run by hand, this flags a collector that has stopped;
  - **an entry window was missed:** the gap before the latest successful run was longer than 24 hours, so markets whose 7-day mark fell in it got no run in their window. The run after a gap fails once; the total count of such gaps stays in every report;
  - **storage is nearly full:** the database is over 80% of Supabase's 500 MB free tier. Each report shows the size, the largest tables and the forward-test tables' growth over the last 7 days.
- **Run history:** the `forward_test_runs` table (status, markets entered / missed / settled, any error), or the Actions tab.
- **Raw responses of an online run:** download the `forward-test-raw-<run id>` file from the run's page (kept 14 days).
- **Missed markets** mean no successful run happened in the first day after their 7-day mark. With runs 12 hours apart, one skipped slot can be enough. A few are harmless; many mean the schedule stopped.
- **If storage runs low:** the forward-test tables are append-only by design, so nothing is deleted automatically. The options are a larger Supabase plan, or a reviewed, deliberate clean-up (e.g. moving old raw evidence to files). Decide before the database becomes read-only, which would also stop the daily calibration update.

## Updating the published results (weekly)

The website's **Forward Test — Election 2026** page reads only the committed bundle in `docs/data/forward_test_election_2026/`. It never reads the database. Updating it is a manual, reviewed step, never automatic:

1. **Start from the latest code:** `git checkout main && git pull`, then `git checkout -b results/<date>`.
2. **Save the new runs' raw files** before GitHub deletes them (14 days), so quotes can be re-checked against the raw order books:
   ```bash
   for id in $(gh run list --workflow forward_test.yml --status success --limit 40 --json databaseId --jq '.[].databaseId'); do
     [ -d data/raw/forward_test/github_artifacts/$id ] || gh run download $id -n forward-test-raw-$id -D data/raw/forward_test/github_artifacts/$id
   done
   ```
3. **Start the local database** (`docker compose up -d`): the pilot section reads the local election observations.
4. **Regenerate with the read-only login.** It reads the database and the public APIs, writes no rows, and rewrites the bundle and both reports (`docs/election_2026_*.md`):
   ```bash
   set -a; source .env; set +a
   DATABASE_URL="$DASHBOARD_DATABASE_URL" python src/analysis/forward_test_results.py
   ```
5. **Check it:** `pytest tests/test_forward_test_results.py tests/test_dashboard_forward_test.py`, then `git diff --stat docs/` and read the changed lines of both reports. A week normally adds a few positions or settlements and moves the as-of time. Look again if old positions changed, the verdict changed, or a new gap or missed market appears.
6. **Preview it:** `streamlit run app/dashboard.py`, then open the forward-test page.
7. **Commit and publish only after review:** commit the bundle and the two reports on the branch, then merge into `main` and push. Streamlit Cloud redeploys from `main`.

Weekly is enough: positions settle over days, and the raw files last 14 days.

## Pausing and stopping

- **Pause:** GitHub → **Actions** → **Forward test** → **⋯** → **Disable workflow**. Re-enable the same way. Markets that cross their 7-day mark while it's paused for more than a day are recorded as *missed* once it resumes.
- GitHub also pauses scheduled workflows after 60 days without commits to the repository; re-enable them the same way.
- **Stop for good:** disable the workflow. The recorded data stays in the database for the report.

## Online setup and upgrades

1. **Apply the migration** to the online database: `DATABASE_URL="$SUPABASE_URL" python -m forward_test migrate`. It only adds tables, columns and rules; existing rows are kept.
2. **Apply the security rules** without changing the dashboard's password: `python src/load/secure_online_database.py --keep-password`, then `--verify`.
3. **Then push the new code.** The workflow runs whatever code is on GitHub; until the code that matches the migration is pushed, it keeps running the previous version.
