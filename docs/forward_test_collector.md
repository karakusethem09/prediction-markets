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
| Is it running? | `python -m forward_test health` (add `--experiment election_2026_pilot --stale-minutes 30` to check one experiment; exit code 1 if stale or the latest run failed) |
| Automatically | GitHub Actions, [`.github/workflows/forward_test.yml`](../.github/workflows/forward_test.yml), every 3 hours at :41 (GitHub often starts scheduled runs late: 2–9 hours apart in practice). Group A and the pilot run as separate steps, so a pilot failure can't block Group A. |
| Election pilot, on demand | [`.github/workflows/election_day.yml`](../.github/workflows/election_day.yml): one run when started with **Run workflow** (its Nov 3–4 schedule was removed unused). Pilot only, markets ≥ $100,000. |

**Storage:** at the default $10,000 floor a pilot run observes ~2,170 markets and adds about 3.4 MB to the database: observation rows ~2.3 MB, plus the books that changed, ~1.1 MB (on election day almost every book changes between runs). Market metadata and rule texts are stored only when they change. At the $100,000 floor used online it's ~550 markets and about 1 MB. Raw files (gzip) are kept under `data/raw/forward_test/` and in the run's artifact.

After every save the collector reads the run back in a new session. If any observation, or its link to an archived book, is missing, the run is logged as **failed**.

## Checking on it

- **Results so far:** `python -m forward_test report` (add `DATABASE_URL=...` for the online database). It shows:
  - how many markets were recorded,
  - how many Group A trades were entered and settled,
  - how far the asking price sat above the midpoint,
  - once trades settle, the win rate and the return at the asking price and at the $100 / $1,000 fill prices.
  
  The spec's verdict appears only once **100 Group A trades** have settled.
- **Run history:** the `forward_test_runs` table (status, markets entered / missed / settled, any error), or the Actions tab. GitHub emails the repository owner when a scheduled run fails.
- **Raw responses of an online run:** download the `forward-test-raw-<run id>` file from the run's page (kept 14 days).
- **Missed markets** mean the collector didn't run for over a day. A few are harmless; many mean the schedule stopped.

## Pausing and stopping

- **Pause:** GitHub → **Actions** → **Forward test** → **⋯** → **Disable workflow**. Re-enable the same way. Markets that cross their 7-day mark while it's paused for more than a day are recorded as *missed* once it resumes.
- GitHub also pauses scheduled workflows after 60 days without commits to the repository; re-enable them the same way.
- **Stop for good:** disable the workflow. The recorded data stays in the database for the report.

## Online setup and upgrades

1. **Apply the migration** to the online database: `DATABASE_URL="$SUPABASE_URL" python -m forward_test migrate`. It only adds tables, columns and rules; existing rows are kept.
2. **Apply the security rules** without changing the dashboard's password: `python src/load/secure_online_database.py --keep-password`, then `--verify`.
3. **Then push the new code.** The workflow runs whatever code is on GitHub; until the code that matches the migration is pushed, it keeps running the previous version.
