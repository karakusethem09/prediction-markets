"""Group A forward test: audit, official settlement check and results, as of one moment (read-only).

The forward test (docs/forward_test_spec.md) records markets at their 7-day mark with real order-book
quotes and takes a one-contract paper position in every Group A market (best ask in [0.20, 0.30)).
This module checks what was really captured and how the positions settled, and computes what the spec
defines. Nothing here writes to a database, and a rerun never changes a recorded entry.

Sources (all read-only):
  - the online forward-test tables, forward_test_runs and forward_test_entries: the official test
  - each Group A position's official result, matched by its exact outcome token (collector.official_resolution)
  - the raw order books in the collector's GitHub artifacts, saved locally (ARTIFACTS_DIR)
  - the forward-test workflow's run history, from GitHub's public API
  - the local election-pilot observations (local database): described only, the pilot has no trades
Writes the results bundle in RESULTS_DIR (read by the dashboard page) and two reports:
docs/election_2026_data_audit.md and docs/election_2026_forward_test_results.md.

    set -a; source .env; set +a
    DATABASE_URL="$SUPABASE_URL" python src/analysis/forward_test_results.py
"""
import glob
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg

from extract.polymarket import get_with_retry
from forward_test import collector
from forward_test.models import (
    GROUP_A, MIN_ENTRY_VOLUME, book_summary, classify_event, exchange_time, fill_price, official_settlement,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = PROJECT_ROOT / "docs" / "data" / "forward_test_election_2026"
AUDIT_REPORT = PROJECT_ROOT / "docs" / "election_2026_data_audit.md"
RESULTS_REPORT = PROJECT_ROOT / "docs" / "election_2026_forward_test_results.md"
ARTIFACTS_DIR = PROJECT_ROOT / "data" / "raw" / "forward_test" / "github_artifacts"  # one folder per GitHub run
LOCAL_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/prediction_markets"
GITHUB_RUNS_URL = ("https://api.github.com/repos/karakusethem09/prediction-markets/actions/workflows/"
                   "forward_test.yml/runs")
CRON_EVERY = pd.Timedelta(hours=3)  # forward_test.yml: "41 */3 * * *"

# docs/group_a_deep_dive.md, "7 days before, all": retrospective, recorded (not executable) prices
HISTORICAL = {"markets": 390, "events": 356, "win_rate": 0.333, "avg_price": 0.250, "edge": 0.083,
              "roi": 0.333, "roi_low": 0.149, "roi_high": 0.522}
SCENARIOS = {"1 contract": None, "$100": 100, "$1,000": 1_000}  # None: one contract at the best ask
VERDICT_AT = 100  # the spec's decision rule applies once this many Group A trades have settled
N_BOOT = 10_000
ELECTION_DAY = pd.Timestamp("2026-11-03", tz="UTC")
PILOT_GAP = pd.Timedelta(hours=1)  # pilot runs were 10 minutes apart: a longer gap is worth reporting
NO_DATA = "NO VERIFIED PROSPECTIVE SNAPSHOTS AVAILABLE."


# ---------- pure calculations (tested with synthetic data) ----------

def same(a, b, tolerance=1e-9):
    """Two recorded numbers agree (both missing counts as agreeing)."""
    if a is None or b is None or pd.isna(a) or pd.isna(b):
        return (a is None or pd.isna(a)) and (b is None or pd.isna(b))
    return abs(float(a) - float(b)) <= tolerance


def verify_quote(entry, book):
    """Re-derive an entry's recorded quote from the raw order book it was taken from."""
    bids = [(float(level["price"]), float(level["size"])) for level in book.get("bids") or []]
    asks = [(float(level["price"]), float(level["size"])) for level in book.get("asks") or []]
    summary = book_summary(bids, asks)
    recomputed = {"best_bid": summary["best_bid"], "best_ask": summary["best_ask"],
                  "ask_size_at_best": summary["ask_size_at_best"],
                  "fill_price_100": fill_price(asks, 100), "fill_price_1000": fill_price(asks, 1_000)}
    matches = book.get("asset_id") == entry["token_id"] and all(same(v, entry[k]) for k, v in recomputed.items())
    return {"raw_book": "verified" if matches else "mismatch", "exchange_time": exchange_time(book.get("timestamp")),
            "ask_levels": len(asks), "ask_depth_usd": sum(price * size for price, size in asks)}


def category(tags, title):
    """Exploratory topic of a position's event, from the event's own tags (read at audit time)."""
    if classify_event(tags, title) in ("us_midterms_2026", "election_other"):
        return "Elections"
    for label, tag in [("Sports", "sports"), ("Crypto", "crypto")]:
        if tag in (tags or []):
            return label
    return "Other"


def build_positions(entries, resolutions, raw_checks):
    """One row per Group A paper position: the entry exactly as recorded, its official result (matched by
    its outcome token) and whether its quote could be re-derived from the raw order book."""
    group_a = entries[entries["in_group_a"].fillna(False).astype(bool)]
    resolved = {r["market_id"]: r for r in resolutions}
    rows = []
    for entry in group_a.sort_values("recorded_at").to_dict("records"):
        official = resolved.get(entry["market_id"])
        status, outcome = (official_settlement(official["final"], official["winners"], entry["token_id"])
                           if official else ("not checked", None))
        names, tokens = (official or {}).get("outcome_names") or [], (official or {}).get("token_ids") or []
        raw = raw_checks.get(entry["market_id"], {"raw_book": "not available"})
        stored = entry["outcome"] if entry["status"] == "settled" else None
        rows.append({
            **{k: entry[k] for k in ["market_id", "event_id", "question", "token_id", "recorded_at", "scheduled_end",
                                     "best_bid", "best_ask", "midpoint", "spread", "ask_size_at_best",
                                     "fill_price_100", "fill_price_1000", "volume_at_entry", "min_order_size"]},
            "outcome_name": names[0] if tokens[:1] == [entry["token_id"]] else None,
            "first_listed_token": tokens[:1] == [entry["token_id"]],
            "event_title": (official or {}).get("event_title"),
            "category": category((official or {}).get("event_tags"), (official or {}).get("event_title")),
            "status": status, "outcome": outcome,
            # settled or void: the capital comes back when the market closes; otherwise it stays committed
            "official_close_time": (official or {}).get("official_close_time") if status in ("settled", "void") else None,
            "meets_rules": (GROUP_A[0] <= entry["best_ask"] < GROUP_A[1]) and entry["volume_at_entry"] >= MIN_ENTRY_VOLUME,
            "published_taker_fee": (official or {}).get("published_taker_fee"),
            "stored_status": entry["status"],
            "stored_agrees": (status == entry["status"]) and same(outcome, stored),
            "raw_book": raw["raw_book"], "exchange_time": raw.get("exchange_time"),
            "ask_levels": raw.get("ask_levels"), "ask_depth_usd": raw.get("ask_depth_usd"),
        })
    return pd.DataFrame(rows)


def scenario_rows(positions, dollars=None):
    """Every position under one sizing scenario.

    dollars=None: one contract bought at the best ask. Otherwise an order of `dollars` walked up the asks
    recorded at entry (its average price, fill_price_<dollars>, was recorded then). An order the recorded
    book couldn't fill completely is 'unfillable': kept and counted, never included in the returns.
    Profit = payout - capital, only for officially settled positions. Fees are not included.
    """
    rows = positions[["market_id", "event_id", "category", "status", "outcome", "recorded_at",
                      "official_close_time", "volume_at_entry", "best_ask", "spread", "min_order_size"]].copy()
    price = rows["best_ask"] if dollars is None else positions[f"fill_price_{dollars}"]
    rows["price"] = price.astype(float)
    rows["fillable"] = rows["price"].notna()
    rows["shares"] = 1.0 if dollars is None else dollars / rows["price"]
    rows["capital"] = rows["price"] if dollars is None else float(dollars)
    rows.loc[~rows["fillable"], ["shares", "capital"]] = np.nan
    rows["below_minimum_size"] = rows["fillable"] & (rows["shares"] < rows["min_order_size"].astype(float))
    settled = rows["fillable"] & (rows["status"] == "settled")
    rows["payout"] = (rows["shares"] * rows["outcome"].astype(float)).where(settled)
    rows["profit"] = (rows["payout"] - rows["capital"]).where(settled)
    return rows


def performance(rows):
    """Results of one scenario. ROI = total profit / total capital of settled positions (never an average
    of per-trade ROIs). Unresolved positions are counted, with their capital, but never as wins or losses."""
    fillable = rows[rows["fillable"]]
    settled = fillable[fillable["status"] == "settled"]
    open_ = fillable[fillable["status"] == "open"]
    wins = int(settled["outcome"].sum()) if len(settled) else 0
    invested, profit = settled["capital"].sum(), settled["profit"].sum()
    prices = settled["price"]
    # One contract each: profit is zero exactly when the win rate equals the average price paid, so the edge
    # (win rate - average price) always has the sign of the profit. With equal dollars per position a win at a
    # low price pays more than one at a high price, so no single break-even win rate exists: not reported.
    one_contract = bool((fillable["shares"] == 1).all())
    break_even = prices.mean() if one_contract and len(prices) else np.nan
    return {
        "positions": len(rows), "fillable": len(fillable), "unfillable": int((~rows["fillable"]).sum()),
        "fillable_share": len(fillable) / len(rows) if len(rows) else np.nan,
        "below_minimum_size": int(rows["below_minimum_size"].sum()),
        "settled": len(settled), "open": len(open_), "void": int((fillable["status"] == "void").sum()),
        # no usable official result: the token isn't in the market ("unmatched") or it couldn't be checked
        "no_official_result": int(fillable["status"].isin(["unmatched", "not checked"]).sum()),
        "events": event_ids(settled).nunique(), "wins": wins, "losses": len(settled) - wins,
        "win_rate": wins / len(settled) if len(settled) else np.nan,
        "avg_price": prices.mean() if len(prices) else np.nan,
        "median_price": prices.median() if len(prices) else np.nan,
        "break_even_win_rate": break_even,
        "edge": wins / len(settled) - break_even if len(settled) else np.nan,
        "invested": invested, "payout": settled["payout"].sum(), "gross_profit": profit,
        "gross_roi": profit / invested if invested else np.nan,
        "open_capital": open_["capital"].sum(),
    }


def event_ids(rows):
    """A position's event; one without an event counts as its own event."""
    return rows["event_id"].fillna(rows["market_id"])


def event_bootstrap(rows, n_boot=N_BOOT, seed=0):
    """95% ranges for the win rate and ROI, resampling whole events (positions in one event share a fate).
    NaN with fewer than 2 settled events: there is nothing to resample."""
    settled = rows[rows["fillable"] & (rows["status"] == "settled")]
    codes, events = pd.factorize(event_ids(settled))
    if len(events) < 2:
        return {"events": len(events), "win_rate_low": np.nan, "win_rate_high": np.nan,
                "roi_low": np.nan, "roi_high": np.nan}
    rng = np.random.default_rng(seed)
    outcome, profit, capital = (settled[c].to_numpy(dtype=float) for c in ["outcome", "profit", "capital"])
    win_rates, rois = [], []
    for _ in range(n_boot):
        weights = np.bincount(rng.integers(0, len(events), len(events)), minlength=len(events))[codes]
        win_rates.append(weights @ outcome / weights.sum())
        rois.append(weights @ profit / (weights @ capital))
    return {"events": len(events),
            "win_rate_low": np.percentile(win_rates, 2.5), "win_rate_high": np.percentile(win_rates, 97.5),
            "roi_low": np.percentile(rois, 2.5), "roi_high": np.percentile(rois, 97.5)}


def binomial_at_most(k, n, p):
    """Chance of k or fewer wins in n independent positions that each win with probability p."""
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def exposure(rows, as_of):
    """Capital tied up at the same time: each fillable position from its entry until its official close
    (or as_of while unresolved). Capital is never assumed to be recycled within that window."""
    held = rows[rows["fillable"]]
    changes = [(r.recorded_at, 1, r.capital) for r in held.itertuples()]
    changes += [(r.official_close_time if pd.notna(r.official_close_time) else as_of, -1, -r.capital)
                for r in held.itertuples()]
    open_positions, open_capital = 0, 0.0
    peak = {"max_open_positions": 0, "max_open_capital": 0.0, "max_capital_at": None}
    for when, count, capital in sorted(changes, key=lambda c: (c[0], c[1])):  # exits first at the same moment
        open_positions, open_capital = open_positions + count, open_capital + capital
        peak["max_open_positions"] = max(peak["max_open_positions"], open_positions)
        if open_capital > peak["max_open_capital"] + 1e-9:
            peak.update(max_open_capital=open_capital, max_capital_at=when)
    return {**peak, "total_capital": held["capital"].sum()}


def concentration(rows):
    """How much of the result sits in the largest events and in each (exploratory) category."""
    settled = rows[rows["fillable"] & (rows["status"] == "settled")]
    per_event = settled.groupby(event_ids(settled))["profit"].sum()
    total_abs = per_event.abs().sum()
    return {"settled_events": len(per_event),
            "max_positions_per_event": int(rows.groupby(event_ids(rows)).size().max()) if len(rows) else 0,
            "largest_event_share_of_abs_profit": per_event.abs().max() / total_abs if total_abs else np.nan}


def subgroup_table(rows, by, labels):
    """Exploratory: the same results within subgroups. Never a validated strategy."""
    table = []
    for value, part in rows.groupby(by, dropna=False):
        perf = performance(part)
        table.append({"split": labels, "subgroup": value, "positions": perf["positions"],
                      "settled": perf["settled"], "wins": perf["wins"], "win_rate": perf["win_rate"],
                      "avg_price": perf["avg_price"], "gross_roi": perf["gross_roi"]})
    return table


def missed_check(entries, success_times):
    """Stored 'missed' labels re-checked with the corrected rule: missed only if no successful run happened
    in the first day after the market's 7-day mark (forward_test.models.entry_decision)."""
    missed = entries[entries["status"] == "missed"]
    runs = pd.Series(sorted(success_times))
    mark = missed["scheduled_end"] - pd.Timedelta(days=7)
    confirmed = [not ((runs >= m) & (runs < m + pd.Timedelta(days=1))).any() for m in mark]
    return pd.DataFrame({"market_id": missed["market_id"].to_numpy(), "confirmed": confirmed})


def period(timestamp):
    """Before, on, or after Election Day (UTC calendar days): a time label, not a poll-closing cutoff."""
    if timestamp < ELECTION_DAY:
        return "before Nov 3"
    return "Nov 3" if timestamp < ELECTION_DAY + pd.Timedelta(days=1) else "after Nov 3"


def scheduled_slots(first, last, every=CRON_EVERY, minute=41):
    """How many cron slots ("<minute> */3 * * *") the runs between two moments were for. GitHub queues a
    scheduled run after its slot (often hours later), so counting starts at the last slot at or before
    the first run, the one it served."""
    start = first.floor("h") + pd.Timedelta(minutes=minute)
    start = start if start <= first else start - pd.Timedelta(hours=1)
    while start.hour % 3:
        start -= pd.Timedelta(hours=1)
    return len(pd.date_range(start, last, freq=every))


def analyze(entries, runs, resolutions, raw_checks, github_runs, pilot, as_of, n_boot=N_BOOT):
    """Every number in the bundle and the reports, from the loaded inputs (no I/O here)."""
    positions = build_positions(entries, resolutions, raw_checks)
    scenarios = {name: scenario_rows(positions, dollars) for name, dollars in SCENARIOS.items()} if len(positions) else {}
    perf = {name: {**performance(rows), **event_bootstrap(rows, n_boot)} for name, rows in scenarios.items()}
    success = runs[runs["status"] == "success"]["started_at"]
    recorded = entries[entries["status"] != "missed"]
    missed = missed_check(entries, success)
    gh = github_runs
    one = scenarios.get("1 contract")
    subgroups = []
    if one is not None and len(one):
        one = one.assign(election=np.where(one["category"] == "Elections", "election markets", "other markets"),
                         volume=np.where(one["volume_at_entry"] >= one["volume_at_entry"].median(),
                                         "volume at entry >= median", "volume at entry < median"),
                         spread_size=np.where(one["spread"].round(6) <= 0.01, "spread <= 1 cent", "spread > 1 cent"))
        for by, label in [("category", "category"), ("election", "election vs other"), ("volume", "volume"),
                          ("spread_size", "spread at entry")]:
            subgroups += subgroup_table(one, by, label)
    settled_one = perf.get("1 contract", {})
    audit = {
        "as_of_utc": as_of,
        "online_runs": len(runs), "online_success": len(success),
        "online_logged_failures": int((runs["status"] == "failed").sum()),
        "first_success": success.min() if len(success) else None, "last_success": success.max() if len(success) else None,
        "runs_by_period": success.map(period).value_counts().to_dict() if len(success) else {},
        "github_scheduled_runs": int((gh["event"] == "schedule").sum()) if len(gh) else 0,
        "github_failed_runs": int((gh["conclusion"] == "failure").sum()) if len(gh) else 0,
        "github_failed_times": [t.isoformat() for t in gh.loc[gh["conclusion"] == "failure", "created_at"]] if len(gh) else [],
        "cron_slots": scheduled_slots(gh["created_at"].min(), gh["created_at"].max()) if len(gh) else 0,
        "median_gap_hours": success.sort_values().diff().dt.total_seconds().median() / 3600 if len(success) > 1 else np.nan,
        "max_gap_hours": success.sort_values().diff().dt.total_seconds().max() / 3600 if len(success) > 1 else np.nan,
        "entries": len(entries), "entry_status": entries["status"].value_counts().to_dict(),
        "markets": entries["market_id"].nunique(), "events": entries["event_id"].nunique(),
        "recorded_with_quote": int(recorded["best_ask"].notna().sum()),
        "recorded_without_quote": int(recorded["best_ask"].isna().sum()),
        "entries_by_period": entries["recorded_at"].map(period).value_counts().to_dict() if len(entries) else {},
        "missed_labels": len(missed), "missed_confirmed": int(missed["confirmed"].sum()),
        "recorded_at_is_run_start": int((pd.to_datetime(entries["recorded_at"], utc=True) == pd.to_datetime(
            entries["run_id"].map(runs.set_index("run_id")["started_at"]), utc=True)).sum()) if len(runs) else 0,
        "group_a_positions": len(positions),
        "group_a_events": positions["event_id"].nunique() if len(positions) else 0,
        "group_a_status": positions["status"].value_counts().to_dict() if len(positions) else {},
        "group_a_by_period": positions["recorded_at"].map(period).value_counts().to_dict() if len(positions) else {},
        "raw_books": positions["raw_book"].value_counts().to_dict() if len(positions) else {},
        "stored_agrees": int(positions["stored_agrees"].sum()) if len(positions) else 0,
        "election_positions": int(positions["category"].eq("Elections").sum()) if len(positions) else 0,
        "election_entry_runs": positions.loc[positions["category"] == "Elections", "recorded_at"].nunique()
                               if len(positions) else 0,
        "meets_rules": int(positions["meets_rules"].sum()) if len(positions) else 0,
        "artifact_runs_saved": len([d for d in ARTIFACTS_DIR.iterdir() if d.is_dir()]) if ARTIFACTS_DIR.exists() else 0,
        "election_entries": ((positions.loc[positions["category"] == "Elections", "recorded_at"].min(),
                              positions.loc[positions["category"] == "Elections", "recorded_at"].max())
                             if len(positions) else (None, None)),
        "first_listed_token": int(positions["first_listed_token"].sum()) if len(positions) else 0,
        "pilot": pilot,
    }
    if len(positions):
        lags = (pd.to_datetime(positions["exchange_time"], utc=True)
                - pd.to_datetime(positions["recorded_at"], utc=True)).dt.total_seconds().dropna()
        audit["exchange_lag_seconds"] = (lags.min(), lags.max()) if len(lags) else None
    comparison = []
    if settled_one.get("settled"):
        o = settled_one
        comparison = [
            {"measure": "Win rate", "forward_test": o["win_rate"], "historical": HISTORICAL["win_rate"]},
            {"measure": "Average entry price", "forward_test": o["avg_price"], "historical": HISTORICAL["avg_price"]},
            {"measure": "Edge (win rate - average price)", "forward_test": o["edge"], "historical": HISTORICAL["edge"]},
            {"measure": "Gross ROI, one contract each", "forward_test": o["gross_roi"], "historical": HISTORICAL["roi"]},
            {"measure": "Settled positions", "forward_test": o["settled"], "historical": HISTORICAL["markets"]},
            {"measure": "Distinct events", "forward_test": o["events"], "historical": HISTORICAL["events"]},
        ]
        audit["binomial_at_most_wins"] = binomial_at_most(o["wins"], o["settled"], HISTORICAL["win_rate"])
    return {"audit": audit, "positions": positions, "scenarios": scenarios, "performance": perf,
            "exposure": {name: exposure(rows, as_of) for name, rows in scenarios.items()},
            "concentration": concentration(one) if one is not None else {},
            "subgroups": pd.DataFrame(subgroups), "comparison": pd.DataFrame(comparison), "runs": runs,
            "verdict": verdict(settled_one, has_records=len(entries) > 0)}


def verdict(one_contract, has_records=True):
    """The spec's decision rule needs VERDICT_AT settled trades; before that the verdict is pending."""
    settled = one_contract.get("settled", 0) if one_contract else 0
    if not has_records:
        return NO_DATA
    if settled < VERDICT_AT:
        return f"PENDING: {settled} of {VERDICT_AT} settled Group A positions needed for the spec's verdict."
    supported = one_contract["gross_roi"] > 0 and one_contract["roi_low"] > 0
    return ("SUPPORTS the historical finding" if supported else
            "INCONCLUSIVE" if one_contract["gross_roi"] > 0 else "DOES NOT SUPPORT the historical finding")


# ---------- loading (read-only) ----------

def read_query(database_url, sql, params=None):
    with psycopg.connect(database_url, connect_timeout=15) as conn:
        conn.read_only = True  # this module never writes
        cursor = conn.execute(sql, params)
        return pd.DataFrame(cursor.fetchall(), columns=[d.name for d in cursor.description])


def load_github_runs():
    """Every run of the forward-test workflow (public API, no login needed), raw response saved."""
    runs, page = [], 1
    while True:
        body = get_with_retry(GITHUB_RUNS_URL, {"per_page": 100, "page": page})
        runs += body["workflow_runs"]
        if len(runs) >= body["total_count"] or not body["workflow_runs"]:
            break
        page += 1
    folder = PROJECT_ROOT / "data" / "raw" / "forward_test" / "audit"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"github_runs_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json").write_text(json.dumps(runs))
    frame = pd.DataFrame([{"github_run_id": r["id"], "created_at": pd.Timestamp(r["created_at"]), "event": r["event"],
                           "conclusion": r["conclusion"]} for r in runs])
    return frame.sort_values("created_at").reset_index(drop=True)


def raw_book_checks(positions_entries, runs, github_runs):
    """Find each position's raw order book in the saved GitHub artifacts and re-derive its quote.
    A database run is matched to the GitHub run that started it (within 15 minutes before it)."""
    ok = github_runs[github_runs["conclusion"] == "success"]
    matched = pd.merge_asof(runs.sort_values("started_at"), ok, left_on="started_at", right_on="created_at",
                            direction="backward", tolerance=pd.Timedelta(minutes=15)).set_index("run_id")
    checks = {}
    for entry in positions_entries.to_dict("records"):
        github_run = matched["github_run_id"].get(entry["run_id"])
        if pd.isna(github_run) or not (ARTIFACTS_DIR / str(int(github_run))).exists():
            checks[entry["market_id"]] = {"raw_book": "expired before the audit"}
            continue
        files = glob.glob(str(ARTIFACTS_DIR / str(int(github_run)) / "**" / f"book_{entry['token_id'][-12:]}.json"),
                          recursive=True)
        books = [b for b in (json.loads(Path(f).read_text()) for f in files) if b.get("asset_id") == entry["token_id"]]
        checks[entry["market_id"]] = verify_quote(entry, books[0]) if books else {"raw_book": "missing from artifact"}
    return checks


def load_pilot(database_url=LOCAL_DATABASE_URL):
    """The local election pilot, by UTC day (observations only). None if the local database isn't running."""
    try:
        daily = read_query(database_url, """
            SELECT (observed_at AT TIME ZONE 'UTC')::date AS utc_day, count(DISTINCT run_id) AS runs,
                   count(*) AS observations, count(*) FILTER (WHERE book_valid) AS valid_books,
                   count(*) FILTER (WHERE NOT book_valid) AS invalid_books, count(DISTINCT market_id) AS markets,
                   count(DISTINCT event_id) AS events,
                   count(*) FILTER (WHERE meets_group_a_price AND meets_group_a_volume) AS group_a_filter_observations,
                   count(DISTINCT market_id) FILTER (WHERE meets_group_a_price AND meets_group_a_volume)
                       AS group_a_filter_markets,
                   min(observed_at) AS first_observed_at, max(observed_at) AS last_observed_at
            FROM forward_test_observations WHERE experiment = 'election_2026_pilot' GROUP BY 1 ORDER BY 1""")
        daily[["first_observed_at", "last_observed_at"]] = daily[["first_observed_at", "last_observed_at"]].apply(
            pd.to_datetime, utc=True)
        totals = read_query(database_url, """
            SELECT count(*) AS observations, count(DISTINCT run_id) AS runs, count(DISTINCT market_id) AS markets,
                   count(DISTINCT event_id) AS events, count(*) FILTER (WHERE NOT book_valid) AS invalid_books,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY spread)
                       FILTER (WHERE meets_group_a_price AND meets_group_a_volume) AS median_spread_group_a_filter
            FROM forward_test_observations WHERE experiment = 'election_2026_pilot'""").iloc[0].to_dict()
        failed = read_query(database_url, "SELECT count(*) AS n FROM forward_test_runs WHERE status = 'failed' "
                                          "AND settings ->> 'experiments' LIKE '%election_2026_pilot%'").iloc[0]["n"]
        run_times = read_query(database_url, "SELECT DISTINCT run_started_at FROM forward_test_observations "
                                             "WHERE experiment = 'election_2026_pilot' ORDER BY 1")["run_started_at"]
    except psycopg.OperationalError:
        return None
    gaps = [(before, after) for before, after in zip(run_times[:-1], run_times[1:]) if after - before > PILOT_GAP]
    return {"daily": daily, "totals": totals, "failed_runs_with_settings": int(failed), "gaps": gaps}


def load_inputs(database_url):
    runs = read_query(database_url, "SELECT * FROM forward_test_runs ORDER BY run_id")
    entries = read_query(database_url, "SELECT * FROM forward_test_entries")
    github_runs = load_github_runs()
    group_a = entries[entries["in_group_a"].fillna(False).astype(bool)]
    evidence = collector.Evidence(datetime.now(timezone.utc))  # raw responses kept under data/raw/forward_test/
    resolutions = [collector.official_resolution(e["market_id"], e["event_id"], evidence)
                   for e in group_a.to_dict("records")]
    return entries, runs, resolutions, raw_book_checks(group_a, runs, github_runs), github_runs, load_pilot()


# ---------- writing ----------

def fmt_pct(value, digits=1):
    return "n/a" if value is None or pd.isna(value) else f"{value * 100:.{digits}f}%"


def fmt_money(value):
    return "n/a" if value is None or pd.isna(value) else (f"-${abs(value):,.2f}" if value < 0 else f"${value:,.2f}")


def fmt_time(value):
    if value is None or pd.isna(value):
        return "n/a"
    value = pd.Timestamp(value)
    return f"{value.tz_convert('UTC') if value.tzinfo else value:%Y-%m-%d %H:%M:%S} UTC"


def markdown_table(frame):
    header = "| " + " | ".join(str(c) for c in frame.columns) + " |"
    divider = "|" + "|".join("---" for _ in frame.columns) + "|"
    body = ["| " + " | ".join("" if pd.isna(v) else str(v) for v in row) + " |" for row in frame.itertuples(index=False)]
    return "\n".join([header, divider, *body])


def performance_frame(results):
    rows = []
    for name, p in results["performance"].items():
        exp = results["exposure"][name]
        rows.append({"scenario": name, "positions": p["positions"], "fillable": p["fillable"],
                     "unfillable": p["unfillable"], "fillable_share": p["fillable_share"], "settled": p["settled"],
                     "open": p["open"], "void": p["void"], "events": p["events"], "wins": p["wins"],
                     "losses": p["losses"], "win_rate": p["win_rate"], "avg_price": p["avg_price"],
                     "median_price": p["median_price"], "break_even_win_rate": p["break_even_win_rate"],
                     "edge": p["edge"], "invested": p["invested"], "payout": p["payout"],
                     "gross_profit": p["gross_profit"], "gross_roi": p["gross_roi"],
                     "gross_roi_low": p["roi_low"], "gross_roi_high": p["roi_high"],
                     "win_rate_low": p["win_rate_low"], "win_rate_high": p["win_rate_high"],
                     "net_roi": None, "open_capital": p["open_capital"], "no_official_result": p["no_official_result"],
                     "max_open_positions": exp["max_open_positions"], "max_open_capital": exp["max_open_capital"],
                     "below_minimum_size": p["below_minimum_size"]})
    return pd.DataFrame(rows)


def write_bundle(results, out_dir=RESULTS_DIR):
    """The dashboard's inputs: aggregated tables and the per-position table (no raw payloads, no secrets)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    positions = results["positions"]
    keep = ["market_id", "event_id", "question", "event_title", "category", "outcome_name", "token_id",
            "recorded_at", "scheduled_end", "best_bid", "best_ask", "midpoint", "spread", "ask_size_at_best",
            "fill_price_100", "fill_price_1000", "volume_at_entry", "status", "outcome", "official_close_time",
            "raw_book", "exchange_time", "ask_levels", "stored_agrees"]
    (positions[keep] if len(positions) else pd.DataFrame(columns=keep)).to_csv(out_dir / "positions.csv", index=False)
    performance_frame(results).to_csv(out_dir / "performance.csv", index=False)
    results["comparison"].to_csv(out_dir / "comparison.csv", index=False)
    results["subgroups"].to_csv(out_dir / "subgroups.csv", index=False)
    runs = results["runs"]
    runs[["run_id", "started_at", "finished_at", "status", "entered", "missed", "settled"]].to_csv(
        out_dir / "runs.csv", index=False)
    pilot = results["audit"]["pilot"]
    (pilot["daily"] if pilot else pd.DataFrame()).to_csv(out_dir / "pilot_daily.csv", index=False)
    audit = {k: v for k, v in results["audit"].items() if k != "pilot"}
    if pilot:
        audit["pilot_totals"] = pilot["totals"]
    summary = {"as_of_utc": results["audit"]["as_of_utc"], "verdict": results["verdict"],
               "historical": HISTORICAL, "audit": audit, "concentration": results["concentration"],
               "exposure": results["exposure"],
               "sources": {"official_test": "online database, forward_test_runs and forward_test_entries (read-only)",
                           "settlement": "the exchange's per-token winner flags and resolution status, read at as_of_utc",
                           "raw_books": "collector GitHub artifacts saved locally before they expired",
                           "pilot": "local database, forward_test_observations (election_2026_pilot), local only"}}
    (out_dir / "summary.json").write_text(json.dumps(without_nan(summary), indent=2, default=json_default,
                                                     allow_nan=False))


def without_nan(value):
    """NaN isn't valid JSON: write it as null."""
    if isinstance(value, dict):
        return {k: without_nan(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [without_nan(v) for v in value]
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def json_default(value):
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if np.isnan(value) else float(value)
    if isinstance(value, pd.Timedelta):
        return value.total_seconds()
    return str(value)


def main():
    from analysis.forward_test_report import audit_markdown, results_markdown  # imports helpers from here
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("Set DATABASE_URL to the online database (read-only use), e.g. DATABASE_URL=\"$SUPABASE_URL\"")
    as_of = pd.Timestamp.now(tz="UTC")
    results = analyze(*load_inputs(database_url), as_of=as_of)
    write_bundle(results)
    AUDIT_REPORT.write_text(audit_markdown(results))
    RESULTS_REPORT.write_text(results_markdown(results))
    print(f"As of {fmt_time(as_of)}: {results['audit']['group_a_positions']} Group A positions; {results['verdict']}")
    print(f"Wrote {RESULTS_DIR.relative_to(PROJECT_ROOT)}/, {AUDIT_REPORT.name} and {RESULTS_REPORT.name}")


if __name__ == "__main__":
    main()
