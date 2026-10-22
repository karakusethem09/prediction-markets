# Forward test: Group A with real order-book prices

**Status: approved on 2026-10-21, with the $100,000-at-entry volume rule. The rules below are fixed.**

## Why

The historical studies ([`combined_yes_strategy.md`](combined_yes_strategy.md), [`group_a_deep_dive.md`](group_a_deep_dive.md)) found that YES contracts priced at 20–30 cents seven days before a market's scheduled end settled YES about 33% of the time. That is more often than their price implied, and worth roughly +33% on capital before costs. But those studies used **recorded prices**, not prices anyone could actually have bought at. Historical order books are not available, so the only honest way to test the idea with real buying prices is to **record them from now on** and see how the trades settle.

This is a **forward test with paper trades only**: public market data is read and hypothetical entries are recorded. There is no wallet, no account and no order placement, now or later.

## The rules, fixed before any data is collected

These rules are final once this spec is approved. Changing them later means starting a new, separately labelled test, never editing this one.

| Rule | Definition |
|---|---|
| **Universe** | Open Polymarket markets with a binary outcome, an order book, and **at least $100,000 traded at the moment of entry**. Only information available at entry is used: unlike the historical studies, the final volume isn't known yet. |
| **Entry moment** | The first collector run at which the market's **scheduled end** (`endDate`) is **7 days or less** away. If more than a day has passed (6 days or less remain, e.g. because the collector was down), the market is recorded as *missed*, not entered late. |
| **What is recorded at entry** | The YES token's full order book: best bid, **best ask** and its size, midpoint, last trade price, spread, tick size, minimum order size, and the **average price to buy $100 and $1,000 worth** by walking up the asks. Also the volume and liquidity at entry. One record per market, whatever its price. |
| **Group A trade** | **Best ask** in [0.20, 0.30): the price a buyer actually pays, not the midpoint. |
| **Paper position** | One YES contract per Group A market, bought at the **best ask** and held to settlement. Payout $1 if YES, $0 if NO. The $100 and $1,000 fill prices show what larger positions would have cost. |
| **Settlement** | Later runs check each recorded market. Once the market is closed and its result final, it's marked YES or NO using the same rule as the rest of the project. A market that settles at 50/50 or is cancelled is **void**: excluded, with its cost returned. |
| **Comparison group** | Every record is kept, so the same data also answers how far the asking price sits above the midpoint for 20–30 cent contracts. That is the buying cost the historical studies could not see. |

## What gets measured

When at least **100 Group A trades** have settled:

- **Win rate vs average asking price**, and **return on capital at the best ask**, with a 95% range from resampling whole events (the method used in the historical studies).
- The same returns at the **$100 and $1,000 fill prices**, to see how much size the edge can take.
- **Ask vs midpoint gap** for 20–30 cent contracts: the buying cost per contract.
- **Historical-style comparison:** the same trades selected by *midpoint* in [0.20, 0.30) instead of ask, to measure how much the historical studies were flattered by using recorded prices.

**Decision rule, fixed now:** the forward test **supports** the historical finding only if the return at the best ask is positive *and* the lower end of its 95% range is above zero. A positive but uncertain return is reported as **inconclusive**, not as success.

## Expected pace

Historically about 6.5% of markets with a usable 7-day price were in Group A (390 of 5,952). Today about 30 markets per day with at least $100k traded reach the 7-day mark, which suggests roughly **2 Group A trades per day**. At that rate 100 trades would be entered in about **2 months**, and settled shortly after. This is an estimate from one day's count; the real pace will be reported as it comes in.

## How it runs

- A **GitHub Actions** job runs every **3 hours**, so each market is entered within about 3 hours of its 7-day mark.
- Each run:
  1. records the run as started,
  2. finds markets that crossed the 7-day mark,
  3. reads their order books,
  4. records the entries,
  5. settles markets that have closed,
  6. logs the run.
- Data goes into **new tables in the online database** (Supabase), separate from the existing ones, so the pipeline, dashboard and existing tables are untouched. The new tables get the same lockdown as the others: row level security on, and no access through Supabase's public web API.
- Every raw API response is saved for 14 days as a downloadable file of the run, as the project's rules require.
- All HTTP calls go through the project's existing retry helper.

## Files

| File | Purpose |
|---|---|
| `docs/forward_test_spec.md` | This spec: the rules, fixed in advance |
| `docs/forward_test_collector.md` | How to run, monitor and pause the collector |
| `src/forward_test/collector.py` | Reads Polymarket's public market list and order books. This is the only Polymarket-specific file, the same rule as `src/extract/` (see note below). |
| `src/forward_test/models.py` | Platform-free logic: the entry window, best ask, fill prices for $100 / $1,000, Group A, settlement |
| `src/forward_test/storage.py` | Reads and writes the forward-test tables, in one transaction per run |
| `src/forward_test/__main__.py` | `python -m forward_test` runs one collection; `python -m forward_test report` summarizes results so far |
| `sql/forward_test.sql` | The new tables: `forward_test_entries` (one row per market at its 7-day mark, settled later) and `forward_test_runs` (run log) |
| `.github/workflows/forward_test.yml` | The 3-hourly schedule |
| `tests/test_forward_test_*.py` | Tests for the entry window, book calculations, Group A rule and settlement. HTTP is mocked, as everywhere in the project. |

**Note on project rules:** CLAUDE.md keeps platform-specific code in `src/extract/` and the `normalize_<platform>` functions. Reading live order books is platform-specific by nature, so it is confined to `collector.py`, and everything else in `src/forward_test/` stays platform-free.

## What this will and won't show

- **Will show:** whether 20–30 cent long shots still settle YES more often than their *asking* price implies, and what buying costs and position sizes do to the return.
- **Won't show:** fees beyond the spread (Polymarket's fee schedule must be checked separately), the effect of our own orders on prices, or how the pattern behaves outside this period. A positive result would justify a closer look, not a trading decision.
