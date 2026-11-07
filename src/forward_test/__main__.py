"""Forward-test lab: paper trades and observations only (docs/forward_test_spec.md).

Two experiments, stored separately and never combined:
  group_a_7day         the frozen test: enter markets at their 7-day mark, Group A = best ask in [0.20, 0.30)
  election_2026_pilot  observe election markets (event tags midterms/elections) every run, from 2026-10-28

    python -m forward_test migrate                      # create/upgrade the tables (on purpose, once)
    python -m forward_test --dry-run --limit 5          # everything except writing (no database changes)
    python -m forward_test                              # one real collection
    python -m forward_test --experiment election_2026_pilot --min-volume 100000
    python -m forward_test report                       # results so far
    python -m forward_test health                       # is the collector running? (exit code 1 if stale)

Uses DATABASE_URL (the local database if not set). GitHub Actions runs a collection every 3 hours.
"""
import argparse
import os
import time
from datetime import timedelta

import psycopg
import requests

from analysis.combined_yes_strategy import event_bootstrap, money, one_share_performance, pct
from forward_test import collector
from forward_test.models import (
    FILL_SIZES, FIRST_POLL_CLOSE, PILOT_MIN_VOLUME, book_depth, book_summary, classify_event, entry_decision,
    fill_price, in_group_a, is_stale, meets_group_a_volume, settlement, validate_book,
)
from forward_test.storage import (
    finish_run, health, load_entries, migrate, open_market_ids, recorded_market_ids, save, saved_counts, start_run,
    successful_run_times, test_start,
)
from load.postgres import DATABASE_URL

EXPERIMENTS = ["group_a_7day", "election_2026_pilot"]
TRADES_FOR_A_VERDICT = 100  # the spec's decision rule is applied once this many Group A trades have settled


def connect(database_url, attempts=3, **kwargs):
    """psycopg.connect, retried: the online pooler occasionally times out (it failed one run on 2026-10-25)."""
    for attempt in range(attempts):
        try:
            return psycopg.connect(database_url, connect_timeout=15, **kwargs)
        except psycopg.OperationalError:
            if attempt == attempts - 1:
                raise
            time.sleep(5 * (attempt + 1))


def observation_row(experiment, run_id, run_started, candidate, book):
    """One append-only observation: the market as published at this run, plus its order book."""
    error = book.get("error") or validate_book(book["bids"], book["asks"])
    usable = error is None
    summary = book_summary(book["bids"], book["asks"]) if usable else {}
    depth = book_depth(book["bids"], book["asks"]) if "bids" in book else {}
    metadata = candidate["metadata"]
    return {
        "experiment": experiment, "run_id": run_id, "run_started_at": run_started,
        **{k: candidate.get(k) for k in ["market_id", "platform", "event_id", "event_title", "question",
                                         "outcome_names", "token_ids", "observed_outcome", "scheduled_end",
                                         "market_active", "market_closed", "accepting_orders"]},
        "classification": candidate.get("classification", "unknown"),
        "event_tags": candidate.get("event_tags", []),
        "observed_token_id": candidate["token_id"],
        "volume_usd": candidate["volume_at_entry"], "liquidity_usd": candidate["liquidity_at_entry"],
        "metadata_requested_at": metadata["requested_at"], "metadata_received_at": metadata["received_at"],
        "metadata_sha256": metadata["sha256"],
        "book_requested_at": book.get("requested_at"), "book_received_at": book.get("received_at"),
        "book_exchange_time": book.get("book_exchange_time"), "book_sha256": book.get("sha256"),
        "book_hash": book.get("book_hash"), "observed_at": collector.utc_now(),
        "book_valid": usable, "book_error": error,
        **{k: summary.get(k) for k in ["best_bid", "best_ask", "ask_size_at_best", "midpoint", "spread"]},
        **{k: depth.get(k) for k in ["bid_levels", "ask_levels", "bid_depth_shares", "ask_depth_shares", "ask_depth_usd"]},
        **{k: book.get(k) for k in ["tick_size", "min_order_size", "last_trade_price"]},
        "meets_group_a_price": usable and in_group_a(summary["best_ask"]),
        "meets_group_a_volume": meets_group_a_volume(candidate["volume_at_entry"]),
    }


def entry_row(candidate, observation, book, run_id, now):
    """The frozen experiment's one-per-market record at the 7-day mark ('invalid' if the book was)."""
    valid = observation["book_valid"]
    return {
        **{k: candidate[k] for k in ["market_id", "platform", "event_id", "question", "token_id", "scheduled_end",
                                     "volume_at_entry", "liquidity_at_entry", "observed_outcome", "outcome_names",
                                     "token_ids"]},
        **{k: observation[k] for k in ["best_bid", "best_ask", "ask_size_at_best", "midpoint", "spread",
                                       "last_trade_price", "tick_size", "min_order_size", "book_exchange_time",
                                       "book_received_at"]},
        "recorded_at": observation["observed_at"], "run_id": run_id,
        "status": "open" if valid else "invalid",
        **{f"fill_price_{d}": fill_price(book["asks"], d) if valid else None for d in FILL_SIZES},
        "in_group_a": observation["meets_group_a_price"],
    }


def read_state(database_url, dry_run, now):
    """What earlier runs did. A dry run reads it in a read-only session, and goes on without it if needed."""
    try:
        with connect(database_url) as conn:
            if dry_run:
                conn.read_only = True
            return {"began": test_start(conn) or now, "runs": successful_run_times(conn),
                    "recorded": recorded_market_ids(conn), "open": open_market_ids(conn)}
    except (psycopg.OperationalError, psycopg.errors.UndefinedTable) as error:
        if not dry_run:
            raise
        print(f"  (dry run: no database state available: {type(error).__name__}; treating the test as starting now)")
        return {"began": now, "runs": [], "recorded": set(), "open": []}


def collect(state, now, run_id, evidence, experiments, limit, pilot_min_volume=PILOT_MIN_VOLUME):
    """Everything one run would save. Returns (entries, settlements, observations, missed)."""
    entries, observations, missed = [], [], 0

    if "group_a_7day" in experiments:
        to_enter = []
        for raw, metadata in collector.open_markets_near_entry(now, evidence):
            candidate = collector.to_candidate(raw)
            if candidate is None or candidate["market_id"] in state["recorded"]:
                continue
            candidate["metadata"] = metadata
            decision = entry_decision(candidate["scheduled_end"], now, candidate["market_start"],
                                      state["began"], state["runs"])
            if decision == "enter":
                to_enter.append(candidate)
            elif decision == "missed":
                missed += 1
                entries.append({**candidate, "recorded_at": now, "run_id": run_id, "status": "missed",
                                "in_group_a": False})
        to_enter = to_enter[:limit]
        books = collector.order_books([c["token_id"] for c in to_enter], evidence)
        cache = {}
        for candidate in to_enter:
            tags = collector.event_tags(candidate["event_id"], evidence, cache) if candidate["event_id"] else []
            candidate.update(event_tags=tags,
                             classification=classify_event(tags, candidate["event_title"]) or "not_election")
            observation = observation_row("group_a_7day", run_id, now, candidate, books[candidate["token_id"]])
            observations.append(observation)
            entries.append(entry_row(candidate, observation, books[candidate["token_id"]], run_id, now))

    if "election_2026_pilot" in experiments:
        markets = collector.pilot_markets(collector.election_events(evidence), pilot_min_volume, evidence)[:limit]
        books = collector.order_books([m["token_id"] for m in markets], evidence)
        observations += [observation_row("election_2026_pilot", run_id, now, m, books[m["token_id"]]) for m in markets]

    settlements = []
    if "group_a_7day" in experiments:
        for market_id in state["open"][:limit]:
            result = collector.market_result(market_id, evidence)
            status, outcome = settlement(result["closed"], result["final"], result["outcome"])
            if status != "open":
                settlements.append((market_id, status, outcome, result["final_volume"]))
    return entries, settlements, observations, missed


def run_collection(database_url=DATABASE_URL, now=None, dry_run=False, limit=None, experiments=tuple(EXPERIMENTS),
                   pilot_min_volume=PILOT_MIN_VOLUME):
    """One collector run. Returns a summary dict. A dry run makes no database changes and writes no files."""
    if dry_run:
        now = now or collector.utc_now()
        state = read_state(database_url, dry_run, now)
        evidence = collector.Evidence(now, keep_files=False)
        entries, settlements, observations, missed = collect(state, now, None, evidence, experiments, limit,
                                                             pilot_min_volume)
        result = summarize(entries, settlements, observations, missed, len(evidence.bodies))
        print("DRY RUN, nothing saved:", result)
        return {**result, "observation_rows": observations, "entry_rows": entries}

    settings = {"experiments": list(experiments), "pilot_min_volume": pilot_min_volume, "limit": limit,
                "trigger": os.environ.get("GITHUB_WORKFLOW", "local")}
    with connect(database_url, autocommit=True) as log_conn:
        run_id, started = start_run(log_conn, now, settings)
        now = started  # every decision in this run uses the moment the run was logged at
        try:
            state = read_state(database_url, dry_run, now)
            evidence = collector.Evidence(now)
            entries, settlements, observations, missed = collect(state, now, run_id, evidence, experiments, limit,
                                                                 pilot_min_volume)
            with connect(database_url) as conn:  # one transaction: all of this run's changes, or none
                save(conn, entries, settlements, observations, list(evidence.bodies.values()))
            verify_saved(database_url, run_id, observations)
        except Exception as error:
            finish_run(log_conn, run_id, "failed", error=f"{type(error).__name__}: {error}")
            raise
        result = summarize(entries, settlements, observations, missed, len(evidence.bodies))
        finish_run(log_conn, run_id, "success", entered=result["entered"], missed=missed,
                   settled=len(settlements), observed=len(observations), invalid=result["invalid_books"])
    print(f"Run {run_id}:", result)
    return result


def verify_saved(database_url, run_id, observations):
    """Read the run back in a new session: every observation must be there, each linked to its archived book
    (if it had one). Otherwise the run is logged as failed, so a short-lived runner can't exit on a silent loss."""
    with connect(database_url) as conn:
        conn.read_only = True
        saved = saved_counts(conn, run_id)
    for experiment in EXPERIMENTS:
        expected = [o for o in observations if o["experiment"] == experiment]
        found = saved.get(experiment, {"observations": 0, "with_book_evidence": 0})
        with_book = sum(1 for o in expected if o.get("book_sha256"))
        if found["observations"] != len(expected) or found["with_book_evidence"] != with_book:
            raise RuntimeError(f"read-back check failed for {experiment}: expected {len(expected)} observations "
                               f"({with_book} with books), found {found}")


def summarize(entries, settlements, observations, missed, raw_bodies):
    return {
        "entered": sum(1 for e in entries if e["status"] == "open"),
        "entered_group_a": sum(1 for e in entries if e["status"] == "open" and e["in_group_a"]),
        "invalid_entries": sum(1 for e in entries if e["status"] == "invalid"),
        "missed": missed,
        "settled": len(settlements),
        "observations": {x: sum(1 for o in observations if o["experiment"] == x) for x in EXPERIMENTS},
        "invalid_books": sum(1 for o in observations if not o["book_valid"]),
        "raw_bodies": raw_bodies,
    }


def report(entries):
    """Plain-text summary of the frozen Group A test so far."""
    lines = [f"Recorded markets: {len(entries)} "
             f"({', '.join(f'{n} {s}' for s, n in entries['status'].value_counts().items())})"]
    group_a = entries[entries["in_group_a"]]
    settled = group_a[group_a["status"] == "settled"].copy()
    lines.append(f"Group A (best ask 20-30 cents): {len(group_a)} entered, {len(settled)} settled, "
                 f"{int((group_a['status'] == 'void').sum())} void")
    if len(group_a):
        gap = (group_a["best_ask"] - group_a["midpoint"]).mean()
        lines.append(f"  Buying cost at entry: the best ask was {gap * 100:.1f} cents above the midpoint on average")
    if len(settled):
        settled = settled.assign(price=settled["best_ask"], group="A")
        at_ask = one_share_performance(settled)
        lines.append(f"  Settled: {at_ask['wins']} won, {at_ask['losses']} lost; win rate {pct(at_ask['win_rate'])} "
                     f"vs average ask {at_ask['avg_price']:.3f}")
        lines.append(f"  Return at the best ask: {money(at_ask['total_profit'])} on {money(at_ask['total_paid'])} "
                     f"({pct(at_ask['roi'])})")
        if len(settled) >= TRADES_FOR_A_VERDICT:
            ci = event_bootstrap(settled).loc["Combined"]
            supported = at_ask["roi"] > 0 and ci["roi_low"] > 0
            lines.append(f"  95% range for the return at the ask: {pct(ci['roi_low'])} to {pct(ci['roi_high'])}")
            lines.append("  Verdict (spec decision rule): " + ("SUPPORTS the historical finding" if supported else
                         "INCONCLUSIVE" if at_ask["roi"] > 0 else "DOES NOT SUPPORT the historical finding"))
        else:
            lines.append(f"  Verdict: not yet ({len(settled)} of {TRADES_FOR_A_VERDICT} settled Group A trades needed)")
    return "\n".join(lines)


def health_report(h, now, max_age, experiments=tuple(EXPERIMENTS)):
    """Plain-text health check from storage.health(). Returns (text, healthy).
    Only the listed experiments are judged stale or not (each workflow checks its own)."""
    utc = "%Y-%m-%d %H:%M:%S UTC"
    runs, latest = h["runs"], h["latest_run"]
    lines = [f"Forward-test health at {now:{utc}} (stale after {max_age})",
             f"Runs: {runs['total']} ({runs['success']} success, {runs['failed']} failed, {runs['running']} running)"]
    if latest:
        lines.append(f"  Latest run: {latest[0]}, {latest[1]}, started {latest[2]:{utc}}")
    failure = h["latest_failure"]
    lines.append(f"Storage/run errors: {runs['failed']} failed runs" +
                 (f"; latest: run {failure[0]} at {failure[1]:{utc}}: {failure[2]}" if failure else ""))
    healthy = bool(latest) and latest[1] != "failed"
    for experiment in EXPERIMENTS:
        last = h["last_success"].get(experiment)
        checked = experiment in experiments
        stale = checked and is_stale(last, now, max_age)
        healthy = healthy and not stale
        lines.append(f"{experiment}: last successful run {f'{last:{utc}}' if last else 'never'}"
                     f" -> {('STALE' if stale else 'OK') if checked else 'not checked'}")
        obs = h["observations"].get(experiment)
        if obs:
            lines.append(f"  Observations: {obs['observations']:,} ({obs['valid']:,} valid books, {obs['invalid']:,} invalid); "
                         f"{obs['markets']:,} markets / {obs['contracts']:,} contracts in {obs['events']:,} events")
            lines.append(f"  Latest observation: {obs['latest_observed_at']:{utc}}; "
                         f"before {FIRST_POLL_CLOSE:%Y-%m-%d %H:%M} UTC (first US poll closings; a reference time, "
                         "not a per-market cutoff): "
                         f"{obs['before_first_poll_close']:,}")
            lines.append(f"  Matching both Group A filters (ask 20-30 cents, >= $100k traded): {obs['group_a_matches']:,}")
    entries = h["entries"]
    lines.append(f"group_a_7day entries: {sum(entries.values())} "
                 f"({', '.join(f'{n} {status}' for status, n in sorted(entries.items()))}); "
                 f"Group A trades: {h['group_a_entries']}")
    lines.append(f"  Missed opportunities: {entries.get('missed', 0)} labelled missed, of which "
                 f"{h['missed_confirmed']} confirmed (no successful run in their first day after the 7-day mark)")
    return "\n".join(lines), healthy


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Forward-test lab (paper trades and observations only).")
    parser.add_argument("command", nargs="?", choices=["collect", "report", "migrate", "health"], default="collect")
    parser.add_argument("--dry-run", action="store_true", help="do everything except saving: no database changes, no files")
    parser.add_argument("--limit", type=int, help="process at most this many markets per experiment (local testing)")
    parser.add_argument("--experiment", choices=EXPERIMENTS + ["all"], default="all")
    parser.add_argument("--min-volume", type=float, default=PILOT_MIN_VOLUME,
                        help="election pilot: only markets with at least this much traded (default %(default)s)")
    parser.add_argument("--stale-minutes", type=float, default=12 * 60,
                        help="health: an experiment with no successful run for this long is STALE")
    args = parser.parse_args()
    if args.command == "migrate":
        with connect(DATABASE_URL, autocommit=True) as conn:
            migrate(conn)
        print("Forward-test migrations applied.")
    elif args.command == "report":
        print(report(load_entries(DATABASE_URL)))
    elif args.command == "health":
        with connect(DATABASE_URL) as conn:
            conn.read_only = True
            text, healthy = health_report(health(conn), collector.utc_now(), timedelta(minutes=args.stale_minutes),
                                          EXPERIMENTS if args.experiment == "all" else [args.experiment])
        print(text)
        raise SystemExit(0 if healthy else 1)
    else:
        chosen = EXPERIMENTS if args.experiment == "all" else [args.experiment]
        try:
            run_collection(dry_run=args.dry_run, limit=args.limit, experiments=chosen,
                           pilot_min_volume=args.min_volume)
        except requests.RequestException as error:
            raise SystemExit(f"API request failed: {error}")
