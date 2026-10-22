"""Forward test of Group A with real order-book prices (docs/forward_test_spec.md). Paper trades only.

    python -m forward_test           # one collection: record markets at their 7-day mark, settle closed ones
    python -m forward_test report    # summarize the results so far

Uses DATABASE_URL (the local database if not set). GitHub Actions runs a collection every 3 hours.
"""
import argparse

import pandas as pd
import psycopg
import requests

from analysis.combined_yes_strategy import event_bootstrap, money, one_share_performance, pct
from forward_test import collector
from forward_test.models import FILL_SIZES, book_summary, entry_decision, fill_price, in_group_a, settlement
from forward_test.storage import finish_run, load_entries, open_market_ids, recorded_market_ids, save, start_run, test_start
from load.postgres import DATABASE_URL

TRADES_FOR_A_VERDICT = 100  # the spec's decision rule is applied once this many Group A trades have settled


def entry_row(candidate, book, run_id, now):
    """One recorded market at its 7-day mark, with its order book."""
    summary = book_summary(book["bids"], book["asks"])
    return {
        **{k: candidate[k] for k in ["market_id", "platform", "event_id", "question", "token_id", "scheduled_end",
                                     "volume_at_entry", "liquidity_at_entry"]},
        **summary,
        "recorded_at": now, "run_id": run_id, "status": "open",
        "last_trade_price": book["last_trade_price"], "tick_size": book["tick_size"],
        "min_order_size": book["min_order_size"],
        **{f"fill_price_{dollars}": fill_price(book["asks"], dollars) for dollars in FILL_SIZES},
        "in_group_a": in_group_a(summary["best_ask"]),
    }


def run_collection(database_url=DATABASE_URL, now=None):
    """One collector run. Returns (entered, missed, settled); the run is logged either way."""
    with psycopg.connect(database_url, autocommit=True) as log_conn:
        run_id, started = start_run(log_conn, now)
        now = started  # every decision in this run uses the same moment the run was logged at
        try:
            began, recorded = test_start(log_conn), recorded_market_ids(log_conn)
            entries, missed = [], 0
            for raw in collector.open_markets_near_entry(now, started):
                candidate = collector.to_candidate(raw)
                if candidate is None or candidate["market_id"] in recorded:
                    continue
                decision = entry_decision(candidate["scheduled_end"], now, candidate["market_start"], began)
                if decision == "enter":
                    try:
                        book = collector.order_book(candidate["token_id"], started)
                    except (requests.HTTPError, RuntimeError) as error:  # try again next run, still inside the window
                        print(f"  order book for {candidate['market_id']} failed: {error}")
                        continue
                    entries.append(entry_row(candidate, book, run_id, now))
                elif decision == "missed":
                    missed += 1
                    entries.append({**candidate, "recorded_at": now, "run_id": run_id, "status": "missed",
                                    "in_group_a": False})

            settlements = []
            for market_id in open_market_ids(log_conn):
                result = collector.market_result(market_id, started)
                status, outcome = settlement(result["closed"], result["final"], result["outcome"])
                if status != "open":
                    settlements.append((market_id, status, outcome, result["final_volume"]))

            with psycopg.connect(database_url) as conn:  # one transaction: all of this run's changes, or none
                save(conn, entries, settlements)
        except Exception as error:
            finish_run(log_conn, run_id, "failed", error=f"{type(error).__name__}: {error}")
            raise
        entered = sum(1 for e in entries if e["status"] == "open")
        finish_run(log_conn, run_id, "success", entered=entered, missed=missed, settled=len(settlements))
    print(f"Entered {entered} markets ({sum(1 for e in entries if e['in_group_a'])} in Group A), "
          f"missed {missed}, settled {len(settlements)}.")
    return entered, missed, len(settlements)


def report(entries):
    """Plain-text summary of the forward test so far."""
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
        for dollars in FILL_SIZES:
            filled = settled[settled[f"fill_price_{dollars}"].notna()]
            if len(filled):
                perf = one_share_performance(filled.assign(price=filled[f"fill_price_{dollars}"]))
                lines.append(f"  Return buying ${dollars:,} each at the average fill price: {pct(perf['roi'])} "
                             f"({len(filled)} trades the book could fill)")
        if len(settled) >= TRADES_FOR_A_VERDICT:
            ci = event_bootstrap(settled).loc["Combined"]
            supported = at_ask["roi"] > 0 and ci["roi_low"] > 0
            lines.append(f"  95% range for the return at the ask: {pct(ci['roi_low'])} to {pct(ci['roi_high'])}")
            lines.append("  Verdict (spec decision rule): " + ("SUPPORTS the historical finding" if supported else
                         "INCONCLUSIVE" if at_ask["roi"] > 0 else "DOES NOT SUPPORT the historical finding"))
        else:
            lines.append(f"  Verdict: not yet ({len(settled)} of {TRADES_FOR_A_VERDICT} settled Group A trades needed)")
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Forward test of Group A (paper trades only).")
    parser.add_argument("command", nargs="?", choices=["collect", "report"], default="collect")
    args = parser.parse_args()
    if args.command == "report":
        print(report(load_entries(DATABASE_URL)))
    else:
        run_collection()
