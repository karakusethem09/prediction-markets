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

- A **GitHub Actions** job runs every **3 hours**, so each market is entered within about 3 hours of its 7-day mark. *(Every 12 hours since 2026-11-07; the rules are unchanged, see the note at the end.)*
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


## Addendum, 2026-10-28: audit corrections and the election pilot

**The rules above are unchanged.** This addendum records what the 2026-10-28 audit corrected in how they are *implemented*, and a second, separate experiment.

### Corrections to the Group A test (`group_a_7day`)

- **"Missed" now means what the rules say:** the collector was absent. A market counts as missed only if **no successful collector run** happened during the first day after its 7-day mark. Before this fix, 12 markets were labelled missed although 5–24 runs happened after their mark. They simply had less than $100,000 traded then, so they were never eligible. The stored rows are not edited; reports re-classify them from the run history.
- **Times are recorded as they happen, in UTC:** the run start, when each market list and order book was requested and received, the exchange's own timestamp on the book, and when each observation was assembled. Previously the run start was used for everything.
- **The first-listed outcome's name and both tokens are stored**, after checking that names and tokens line up.
- **An unusable order book** (no asks, crossed or locked, prices outside 0–1) is recorded with the reason, and the market's entry is marked **invalid**: no paper trade. Previously such a book would have produced an empty entry.
- **Every observation is kept, append-only**, in `forward_test_observations` (the database rejects edits and deletions), with the order book archived in `forward_test_raw`. The 14-day GitHub artifacts are no longer the only copy.
- **The test's start date is not reset:** it remains the first online run, 2026-10-22 12:21 UTC.

Note for the 2026-11-03 US general election: Polymarket schedules most election-night markets to end at **2026-11-04 04:59 UTC** (11:59 pm US Eastern on November 3), so their 7-day mark was **2026-10-28 04:59 UTC**. The scheduled collector recorded 123 such markets at 10:00 UTC that day, within the first-day window, under the original rules.

### A separate experiment: the 2026 election observation pilot (`election_2026_pilot`)

Started 2026-10-28. It **observes** election markets; it creates no paper trades and is never combined with the Group A test.

| Rule | Definition |
|---|---|
| Which markets | Open markets in events that Polymarket tags `midterms` or `elections`, with an order book and at least **$10,000** traded at the observation (keeps storage affordable). Markets settling after November 3 are included. |
| How often | Every collector run (one observation per market per run, append-only). |
| Classification | From the event's published tags only: `us_midterms_2026` (tag `midterms`), `election_other` (tags `elections`, `world-elections`, `us-elections`), otherwise `unknown`. Never from candidate or question names. |
| What is recorded | Market and event ids, question, outcome names and token ids, scheduled end, status, cumulative volume and liquidity; the first-listed outcome's best bid and ask, size at the best ask, midpoint, spread, depth on both sides; the timestamps listed above; and whether the observation **would** meet Group A's price filter (best ask in [0.20, 0.30)) and volume filter ($100,000). |
| What it is not | Not a trading test, not backdated, and no profit figures until its own analysis plan is written. |

### Addendum, 2026-11-03 (US Election Day): wider discovery and election-day snapshots

Written at 17:45 UTC on November 3, before the first polls closed and before any of these markets settled.
**The Group A rules and the classification of earlier observations are unchanged.**

- **Discovery.** `/events` refuses offsets above about 2,000 (HTTP 422), and the `elections` tag alone had 1,670 open events, so the pilot now pages through `/events/keyset`. It also searches the `global-elections` and `politics` tags, which found 59 more tagged election markets (e.g. Czech mayoral races, the California voter ID referendum).
- **Classification**, from the event's own data, in this order: tag `midterms` → `us_midterms_2026`; any tag containing "election" (e.g. `elections`, `global-elections`, `senate-elections`) → `election_other`; no election tag but an election word in the **title** → `unknown`, a separate uncertain bucket (16 markets, e.g. "Ukraine election called by…?"); anything else is not observed. Descriptions are not used: matching them pulled in 422 unrelated events (e.g. "Will Hamas agree to disarm?"). Group A observations of non-election markets are labelled `not_election`.
- **Election-day reference time:** 2026-11-03 23:00:00 UTC, when the first US polls closed. It is a reference time, **not a per-market cutoff** (corrected 2026-11-07): each race's polls close at their own time, and the data holds no per-market closing time, so observations are reported by their real UTC timestamps and never labelled pre- or post-poll-close.
- **Cadence and volume floor.** These differ by where the collection runs; each run records them in `forward_test_runs.settings`:

  | Where | Cadence | Volume floor |
  |---|---|---|
  | Online, `election_day.yml` | every 10 minutes on Nov 3–4 (UTC) | $100,000 (Group A's volume rule; keeps the free database within its limit) |
  | Online, `forward_test.yml` | every 3 hours | $100,000 |
  | Locally | while the computer is on | $10,000 |

  Markets between $10,000 and $100,000 are therefore observed only locally, and only when the computer is on. Their observations are complete and correctly timed, but not continuous.
- **Archived metadata** now holds only the fields that define a market (question, outcomes, tokens, dates, status). Volume and liquidity are in each observation row and prices are in the archived book, so the metadata body changes only when the market itself changes (e.g. a new scheduled end) and is stored once.
- **Paper execution, if a pilot trade analysis is written later:** computed from the archived books for one contract at the best ask, and for $100 and $1,000 by walking up the asks (contracts, volume-weighted average price, capital spent). Orders the book can't fill completely are counted separately and never included in a full-fill return. Fees are kept separate until a verified fee model exists. Trades stay open until the market itself settles on Polymarket; race calls by the media are not settlement.

### Note, 2026-11-07: what actually ran

- **The cloud collection in the table above was prepared but never deployed.** The online database didn't receive migration 002, and the 10-minute election-day workflow never ran. Its schedule was removed afterwards; it remains as a manual "Run workflow" button (`.github/workflows/election_day.yml`).
- **The election pilot ran only on the author's computer** (local database): every 10 minutes from 17:29 UTC on November 3. The computer went to sleep at 21:12 UTC, so the last observation on November 3 is from 21:08:53 UTC; collection resumed at 00:39 UTC on November 4. These observations exist only locally.
- **The frozen Group A test kept running online** with the original code (`forward_test.yml`, every 3 hours as scheduled; GitHub started runs a median of 6.5 hours apart). Its election-night positions were all entered at 10:00 UTC on 2026-10-28.
- Results and the data audit: [`election_2026_forward_test_results.md`](election_2026_forward_test_results.md), [`election_2026_data_audit.md`](election_2026_data_audit.md).

### Note, 2026-11-07: collection every 12 hours

The election is over, so the collector's schedule was simplified. **The Group A rules are unchanged:** the $100,000 volume rule, the best-ask range [0.20, 0.30), the 7-day target and the 24-hour entry window. Nothing is backdated, and the window is not extended for late runs.

- **Group A is collected every 12 hours** (`41 */12 * * *`: 00:41 and 12:41 UTC) instead of every 3 hours. A market is still entered at the first successful run after its 7-day mark, so entries now come later within the same 24-hour window: up to 12 hours plus GitHub's start delay after the mark, instead of about 3 hours.
- **Markets can be missed more easily.** Two slots fall in each 24-hour window. If GitHub skips one, a market whose mark fell in the gap may get no run in its window; it is then recorded as *missed* (or not at all, if it has already closed by the next run), and never entered late. `python -m forward_test health` reports every gap between successful runs longer than 24 hours, and the workflow fails on the run that follows a new one.
- **The election pilot is no longer collected on a schedule.** It can still be collected on demand (`.github/workflows/election_day.yml`). Its observations, tables and reports are kept as they are.
