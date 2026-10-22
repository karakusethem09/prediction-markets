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

| Where | Command |
|---|---|
| Automatically | GitHub Actions, [`.github/workflows/forward_test.yml`](../.github/workflows/forward_test.yml), every 3 hours at :41. The tests run first. The online database address comes from the repository secret `DATABASE_URL`. |
| By hand, online | GitHub → **Actions** → **Forward test** → **Run workflow** |
| Locally (local database) | `python -m forward_test` |
| Locally, online database | `set -a; source .env; set +a` then `DATABASE_URL="$SUPABASE_URL" python -m forward_test` |

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

## Online setup (once)

1. Run the collector once against the online database. That creates the two `forward_test_*` tables, with row level security on.
2. Apply the security rules without changing the dashboard's password: `python src/load/secure_online_database.py --keep-password`. Then check them with `--verify`.
3. Enable the workflow (it is active once the file is on GitHub).
