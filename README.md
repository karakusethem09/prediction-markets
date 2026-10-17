# Are Prediction Markets Calibrated?

Every day, people around the world try to predict the future: who will win an election, whether the central bank will cut rates, which team will win on Sunday. On a prediction market they back those predictions with money. Every trade goes into a shared pool, and the price that comes out of that pool is the crowd's combined estimate of the probability. A "Yes" share trading at 70 cents means the market thinks there is a 70% chance it will happen.

A single market is just one guess, and one guess can't be right or wrong about a probability. If something priced at 70% doesn't happen, the market wasn't necessarily wrong, because 30% outcomes happen all the time. So I got curious about a bigger question: **what happens when you add thousands of these markets together?** Of all the things the crowd priced at 70%, did about 70% actually happen? Of everything priced at 10%, did about one in ten come true?

If the answer is yes at every price level, the market is **calibrated**: its prices can be read as honest probabilities. This project measures that for [Polymarket](https://polymarket.com), using 10,792 resolved markets that each traded at least $1 million.

![Calibration curves: market price vs how often the outcome happened, 1, 7 and 30 days before close](docs/calibration.png)

*Each dot groups markets with similar prices. On the gray diagonal, prices match reality exactly. Vertical bars are 95% confidence intervals. Chart: the 3,499 markets that have a price at all three horizons.*

## What I found

1. **The crowd is well calibrated.** On average, prices were only **1.2, 1.9 and 2.1 percentage points** away from what actually happened, 1, 7 and 30 days before the market closed.
2. **Prices get sharper as the deadline approaches.** On the same markets, the Brier score (lower is better) falls from **0.089** at 30 days to **0.067** at 7 days to **0.046** at 1 day.
3. **There is one consistent bias: prices are a little too far from 50%.** Across all 10,720 markets priced one day before close, outcomes priced at 10–20% averaged a price of **14.9%** but happened **25.0%** of the time (532 markets). In that group, every price bucket below 50% happened more often than priced, and every bucket above 50% less often. This is the opposite of the well-known *favorite–longshot bias*, where long shots are usually overpriced.
4. **No single event drives the pattern.** It holds when every event counts only once.

The full analysis, with the robustness checks and caveats, is in [`notebooks/calibration.ipynb`](notebooks/calibration.ipynb).

## How it works

```mermaid
flowchart LR
    A[Polymarket APIs] -->|extract| B[Raw JSON<br/>data/raw/]
    B -->|normalize + validate| C[Clean tables]
    C -->|snapshot rule| D[Prices 1, 7, 30<br/>days before close]
    C --> E[(PostgreSQL)]
    D --> E
    E --> F[Analysis notebook]
    E --> G[Streamlit dashboard]
```

| Step | Code | What it does |
|---|---|---|
| Extract | [`src/extract/polymarket.py`](src/extract/polymarket.py) | Downloads resolved markets (cursor pagination) and each market's daily and hourly price history. Every raw response is saved before anything else touches it. Retries with exponential backoff. |
| Transform | [`src/transform/normalize.py`](src/transform/normalize.py) | Builds clean `markets` and `price_history` tables. Keeps only markets with a clear final result that were open at least a day. Validated with [pandera](https://pandera.readthedocs.io). |
| Snapshots | [`src/transform/snapshots.py`](src/transform/snapshots.py) | Takes each market's price 1, 7 and 30 days before it closed: the latest price *at or before* that moment, never after, so the result can't leak in. |
| Load | [`src/load/postgres.py`](src/load/postgres.py) | Upserts everything into PostgreSQL in one transaction (rerunning never duplicates rows) and logs each run. |
| Analyze | [`src/analysis/calibration.py`](src/analysis/calibration.py) | Price buckets, Wilson confidence intervals, Brier score, Brier skill score and calibration error. |
| Show | [`app/dashboard.py`](app/dashboard.py) | Interactive Streamlit dashboard with filters for volume, years and platform. |

### Design choices worth knowing

- **Never the final price.** A resolved market's last price is already 0 or 1, because by then everyone knows the answer. The analysis only uses prices from 1, 7 and 30 days before close.
- **The real close time, not the deadline.** Many markets resolve before their deadline (a question like "…by the end of 2027?" can be settled long before then), so horizons are measured from when the market actually closed.
- **Fair comparisons between horizons.** The 1-day group naturally contains thousands of short markets, like sports games, that weren't open 30 days earlier. Horizons are therefore compared on the same set of markets.
- **Short markets are excluded.** Markets open for less than a day are easier to move with a single large trade, and they have no meaningful 1-day horizon.

## Tech stack

Python 3.13 · requests · pandas · pandera · PostgreSQL 16 (Docker Compose) · psycopg · SQLAlchemy · Plotly · Streamlit · pytest

## Getting started

You need Python 3.13 and [Docker Desktop](https://www.docker.com/products/docker-desktop/).

```bash
git clone https://github.com/karakusethem09/prediction-markets.git
cd prediction-markets

python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .                      # makes the packages in src/ importable

docker compose up -d                  # start PostgreSQL

python src/extract/polymarket.py      # download markets + price histories (several hours the first time; safe to stop and resume)
python src/load/postgres.py           # normalize, take snapshots, load into PostgreSQL (~30 s)

streamlit run app/dashboard.py        # open the dashboard at http://localhost:8501
```

To explore the analysis, open `notebooks/calibration.ipynb` in VS Code or Jupyter with the `.venv` kernel.

## Tests

```bash
pytest
```

37 tests cover the retry logic, pagination, normalization, the snapshot rule (including a check that it never uses a future price), the database upserts and the calibration math. All HTTP calls are mocked. The database and dashboard tests run against a throwaway schema and are skipped automatically if PostgreSQL isn't running.

## Project structure

```
src/extract/      API clients: raw downloads only
src/transform/    normalize.py, snapshots.py, schemas.py (pandera)
src/load/         postgres.py: upserts and pipeline_runs logging
src/analysis/     calibration.py: the calibration math and chart
sql/schema.sql    tables: markets, price_history, snapshots, pipeline_runs
notebooks/        the analysis write-up
app/              Streamlit dashboard
tests/            pytest
data/raw/         cached API responses (git-ignored)
```

## Limitations

- Only markets with at least $1M volume, ending from July 2020 onward. Markets that closed before 2023 have no price history in Polymarket's API.
- Markets within the same event (for example many candidates in one election) are related, so the true uncertainty is somewhat wider than the confidence bars show.
- "Happened" refers to the first listed outcome of each market (for example "Yes").
- Prices are not trades: a real trader would also pay the bid–ask spread and fees.
- Polymarket leaves most markets without a category, so the results can't yet be split by topic, such as sports vs politics.

## What's next

- Split the results by topic, to see where the bias in finding 3 comes from.
- Add [Kalshi](https://kalshi.com) as a second platform. The pipeline is built so that only the extract and normalize steps are platform-specific.
- Put the dashboard online (Streamlit Community Cloud with a hosted PostgreSQL database).
- A single `pipeline.py` command that runs every step.
