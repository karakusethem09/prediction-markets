"""The two Markdown reports of the Group A forward test, written from analysis.forward_test_results.analyze().

Every number comes from the results dict; the wording only changes with what the data shows (no data,
nothing settled yet, fewer than the spec's 100 settled positions).
"""
import pandas as pd

from analysis.forward_test_results import (
    ELECTION_DAY, HISTORICAL, MIN_ENTRY_VOLUME, NO_DATA, VERDICT_AT, fmt_money, fmt_pct, fmt_time, markdown_table,
    performance_frame,
)


def hours(delta):
    return delta.total_seconds() / 3600


def schedule_text(a, counts=True):
    """How the workflow was scheduled, from each run's own workflow file: e.g. "every 3 hours (Oct 22 – Nov 07:
    58 runs for 126 slots), then every 12 hours (Nov 07 – Nov 20: 25 runs for 27 slots)"."""
    return ", then ".join(f"every {s['every_hours']} hours ({s['first_run']:%b %d} – {s['last_run']:%b %d}"
                          + (f": {s['scheduled_runs']} runs for {s['slots']} slots" if counts else "") + ")"
                          for s in a["schedules"]) or "never (no scheduled runs)"


def checklist(results):
    """The audit's 13 questions, answered from the data."""
    a, positions = results["audit"], results["positions"]
    by_period = lambda counts: ", ".join(f"{counts.get(p, 0)} {p}" for p in ["before Nov 3", "Nov 3", "after Nov 3"])
    raw = a["raw_books"]
    lag = a.get("exchange_lag_seconds")
    status = a["group_a_status"]
    rows = [
        ("1. Earliest and latest successful collection", f"{fmt_time(a['first_success'])} → {fmt_time(a['last_success'])} (run start, database clock)"),
        ("2. Successful, failed and missing scheduled runs",
         f"{a['online_success']} successful runs logged; {a['github_failed_runs']} failed on GitHub "
         f"({', '.join(a['github_failed_times']) or 'none'}), absent from the database's run log; "
         f"scheduled {schedule_text(a)}, so {max(a['cron_slots'] - a['github_scheduled_runs'], 0)} of "
         f"{a['cron_slots']} slots produced no run"),
        ("3. Distinct markets discovered and recorded", f"{a['markets']:,} markets ({', '.join(f'{n} {s}' for s, n in sorted(a['entry_status'].items()))})"),
        ("4. Real order books captured", f"{a['recorded_with_quote']:,} recorded markets with a quote; {a['recorded_without_quote']} recorded with an empty book (no quote)"),
        ("5–7. Runs before / on / after November 3 (UTC days)", by_period(a["runs_by_period"])),
        ("   Markets recorded before / on / after November 3", by_period(a["entries_by_period"])),
        ("8. Qualifying Group A positions", f"{a['group_a_positions']} ({by_period(a['group_a_by_period'])}); "
         f"{a['meets_rules']} of them re-checked against the rules (best ask in [0.20, 0.30), at least "
         f"${MIN_ENTRY_VOLUME:,} traded at entry)"),
        ("9. Missed seven-day opportunities", f"{a['missed_confirmed']} confirmed; {a['missed_labels']} stored 'missed' labels, "
         f"{a['missed_labels'] - a['missed_confirmed']} of which had a successful run in their first day (mislabelled)"),
        ("10. Officially settled, unresolved and void positions",
         f"{status.get('settled', 0)} settled, {status.get('open', 0)} unresolved, {status.get('void', 0)} void"
         + (f", {status.get('unmatched', 0)} unmatched" if status.get("unmatched") else "")),
        ("11. Unique events", f"{a['events']:,} among recorded markets; {a['group_a_events']} among Group A positions"),
        ("12. Original timestamps preserved",
         f"Each entry's time equals its run's start (database clock) for {a['recorded_at_is_run_start']} of "
         f"{a['entries']} entries" + (f"; the exchange's own book timestamps are {lag[0]:+.2f} s to {lag[1]:+.2f} s "
                                      "from it (negative: the book's time was just before the run start)" if lag else "")
         + ". The collector only ever updates an entry's settlement fields"
         + ("; the quotes still match the raw books (13)." if not raw.get("mismatch") else
            f"; {raw['mismatch']} quotes do NOT match their raw books (13).")),
        ("13. Quotes and depth verifiable from raw books",
         f"{raw.get('verified', 0)} of {a['group_a_positions']} positions re-derived exactly; "
         f"{raw.get('expired before the audit', 0)} raw books expired before the audit; {raw.get('mismatch', 0)} mismatches"),
    ]
    return pd.DataFrame(rows, columns=["Question", "Answer (from the data)"])


def poll_closing_text(a):
    """What can and can't be said about poll closing times, from the entry times alone."""
    rule = ("The data holds no per-market poll-closing time, so **no observation is labelled pre- or "
            "post-poll-close**; election-pilot observations are reported by UTC time only.")
    if not a.get("election_positions"):
        return "No Group A position is an election market. " + rule
    first, last = a["election_entries"]
    when = f"at {fmt_time(first)}" if first == last else f"between {fmt_time(first)} and {fmt_time(last)}"
    before = ", before Election Day, so they precede every poll closing" if pd.Timestamp(last) < ELECTION_DAY else ""
    return f"The {a['election_positions']} election positions were entered {when}{before}. " + rule


def audit_markdown(results):
    a = results["audit"]
    if not a["group_a_positions"] and not a["entries"]:
        return f"# Election 2026 forward test: data audit\n\nAs of {fmt_time(a['as_of_utc'])}.\n\n**{NO_DATA}**\n"
    pilot = a["pilot"]
    lines = [
        "# Election 2026 forward test: data audit",
        "",
        f"As of **{fmt_time(a['as_of_utc'])}**. Generated by `src/analysis/forward_test_results.py` with read-only "
        "access; every number below is computed from the data. Nothing was written to any database.",
        "",
        "## Verdict on the data",
        "",
        f"**Verified prospective data exists.** The official forward test has {a['markets']:,} market records "
        f"({a['recorded_with_quote']:,} with a real order-book quote, {a['entry_status'].get('missed', 0)} labelled "
        f"missed) from runs between {fmt_time(a['first_success'])} and "
        f"{fmt_time(a['last_success'])}, and holds "
        f"**{a['group_a_positions']} Group A paper positions**. Their settlements were checked against the exchange's "
        "own per-token result, and their quotes against the raw order books where those still exist.",
        "",
        "## Sources and where each timestamp comes from",
        "",
        markdown_table(pd.DataFrame([
            ("Official test: runs", "online database, `forward_test_runs`", "`started_at`: the database clock when a run began"),
            ("Official test: entries", "online database, `forward_test_entries`", "`recorded_at` = its run's `started_at` (the old collector stored no request times)"),
            ("Order books at entry", "raw responses in the collector's GitHub artifacts, saved locally before they expired",
             "`timestamp` in each book: the exchange's own time, in milliseconds"),
            ("Official settlement", "the exchange's market data, read at the audit", "`closedTime`: when the market closed; the audit time is `as_of_utc`"),
            ("Run history", "GitHub's public API for the workflow", "`created_at`: when GitHub queued the run"),
            ("Election pilot", "local database only, `forward_test_observations`", "`observed_at` and request/response times, recorded by the new collector"),
        ], columns=["Source", "What", "Timestamp source"])),
        "",
        "## The 13 audit questions",
        "",
        markdown_table(checklist(results)),
        "",
        "## Collection reliability",
        "",
        f"- The workflow was scheduled {schedule_text(a, counts=False)}; GitHub started runs a median of "
        f"{a['median_gap_hours']:.1f} h apart "
        f"(longest gap {a['max_gap_hours']:.1f} h). A market's entry window is 24 hours, "
        + ("so every gap was shorter than an entry window." if a["max_gap_hours"] < 24 else
           "so at least one gap was longer than an entry window: markets could have been missed then."),
        f"- **Missed opportunities:** {a['missed_labels']} markets carry a stored *missed* label, but the run history shows "
        "a successful run in the first day after the 7-day mark for "
        + ("every one of them" if not a["missed_confirmed"] else f"all but {a['missed_confirmed']}") + ". They "
        "weren't missed: the collector was running, and these markets weren't eligible yet when it looked (most likely "
        f"under ${MIN_ENTRY_VOLUME:,} traded, or a later scheduled end), then appeared in a later run. The stored labels "
        "come from the original collector's rule; they are re-checked here with the corrected rule "
        "(`forward_test.models.entry_decision`), and the stored rows are left unchanged.",
        f"- **Empty books:** {a['recorded_without_quote']} recorded markets had no asks at entry and were stored as open "
        "entries without a quote. None of them can be a Group A position (no best ask). The new collector marks such "
        "entries *invalid*.",
        "",
        "## Raw evidence",
        "",
        "The collector's raw API responses were kept only as GitHub artifacts for 14 days. The artifacts of "
        f"{a['artifact_runs_saved']} runs, all that still existed when they were downloaded, are saved in "
        "`data/raw/forward_test/github_artifacts/` (git-ignored). For each "
        "position with a raw book, the best bid, best ask, size at the best ask and the $100 / $1,000 average fill "
        "prices were recomputed from the book and compared with the stored entry.",
        "",
        "## Poll closing times",
        "",
        poll_closing_text(a),
        "",
        "## The election pilot (local only, observations without trades)",
        "",
    ]
    if pilot:
        t = pilot["totals"]
        daily = pilot["daily"].assign(first_observed_at=lambda d: d["first_observed_at"].map(fmt_time),
                                      last_observed_at=lambda d: d["last_observed_at"].map(fmt_time))
        lines += [
            f"{int(t['observations']):,} observations of {int(t['markets']):,} markets in {int(t['events']):,} events, "
            f"over {int(t['runs'])} successful local runs ({int(t['invalid_books']):,} with an unusable book, recorded with "
            f"the reason); {pilot['failed_runs_with_settings']} local pilot runs failed and are logged with their error. "
            "They exist only in the local database on the author's computer, not online: the "
            "online collector for the pilot was never deployed. Gaps of more than an hour between successful runs: "
            + ("; ".join(f"{fmt_time(g0)} → {fmt_time(g1)}" for g0, g1 in pilot["gaps"]) or "none")
            + ". (The computer's power log, outside the database, shows it went to sleep at 21:12 UTC on November 3.)",
            "",
            markdown_table(daily),
            "",
        ]
    else:
        lines += ["The local database wasn't running when this audit was generated, so the pilot isn't described here.", ""]
    lines += [
        "## What the collector must capture next time",
        "",
        "- Request, response and exchange timestamps for every book (the new collector does).",
        "- Full order books in a durable database table, not only in 14-day artifacts (the new collector does).",
        "- A cloud schedule that is deployed and verified before the event, with an observation table that records "
        "every run (the election pilot ran only on one computer, which went to sleep before the first polls closed).",
        "- Per-market resolution rules and timing (e.g. poll closing per state) if poll-close comparisons are wanted.",
        "",
    ]
    return "\n".join(lines)


def positions_table(positions):
    shown = positions.assign(
        entry=positions["recorded_at"].map(lambda t: f"{t:%Y-%m-%d %H:%M}"),
        outcome_label=positions["outcome_name"],
        bid=positions["best_bid"], ask=positions["best_ask"],
        volume=positions["volume_at_entry"].map(lambda v: f"${v:,.0f}"),
        fill_100=positions["fill_price_100"].map(lambda v: "unfillable" if pd.isna(v) else f"{v:.4f}"),
        fill_1000=positions["fill_price_1000"].map(lambda v: "unfillable" if pd.isna(v) else f"{v:.4f}"),
        result=[("won" if o == 1 else "lost") if s == "settled" else s for s, o in zip(positions["status"], positions["outcome"])],
        raw=positions["raw_book"])
    return shown[["entry", "question", "category", "outcome_label", "bid", "ask", "fill_100", "fill_1000",
                  "volume", "result", "raw"]].rename(columns={
        "entry": "Entry (UTC)", "question": "Market", "category": "Category", "outcome_label": "Outcome bought",
        "bid": "Bid", "ask": "Ask", "fill_100": "$100 avg", "fill_1000": "$1,000 avg", "volume": "Volume at entry",
        "result": "Official result", "raw": "Raw book"})


def settled_summary(one, a):
    """Executive-summary lines that exist only once positions have officially settled."""
    return [
        f"- **{one['settled']} have officially settled: {one['wins']} won, {one['losses']} lost** "
        f"(win rate {fmt_pct(one['win_rate'])}, against an average entry ask of {one['avg_price']:.3f}). "
        f"{one['open']} remain unresolved and are not counted as wins or losses.",
        f"- **Gross result at the best ask, one contract each:** {fmt_money(one['gross_profit'])} on "
        f"{fmt_money(one['invested'])} invested, ROI **{fmt_pct(one['gross_roi'])}** (95% range from resampling events: "
        f"{fmt_pct(one['roi_low'])} to {fmt_pct(one['roi_high'])}). Net of fees: not available (no verified fee model).",
        f"- **Historical Group A** (retrospective, recorded prices): win rate {fmt_pct(HISTORICAL['win_rate'])} at an "
        f"average price of {HISTORICAL['avg_price']:.3f}, ROI {fmt_pct(HISTORICAL['roi'])} over {HISTORICAL['markets']} "
        f"markets. If the true win rate were {fmt_pct(HISTORICAL['win_rate'])}, {one['wins']} or fewer wins in "
        f"{one['settled']} independent positions would happen with probability "
        f"{a.get('binomial_at_most_wins', float('nan')):.3f}. "
        + ("That is a warning sign, not a verdict: the sample is small"
           + (f", and {a['election_positions']} of the {a['group_a_positions']} positions are election markets entered "
              f"in {a['election_entry_runs']} run(s), whose outcomes are related." if a["election_positions"] > 1 else ".")
           if one["settled"] < VERDICT_AT else "The spec's decision rule above is the verdict."),
    ]


def results_markdown(results):
    a, positions, perf = results["audit"], results["positions"], results["performance"]
    if not len(positions):
        return (f"# Group A forward test: results\n\nAs of {fmt_time(a['as_of_utc'])}.\n\n**{NO_DATA}**\n\n"
                "No retrospective substitute is shown here: see the data audit.\n")
    one = perf["1 contract"]
    table = performance_frame(results)
    settled = positions[positions["status"] == "settled"]
    election = positions["category"].eq("Elections").sum()
    per_run = positions.groupby("recorded_at").size()
    same_run, busiest_run = per_run.max(), per_run.idxmax()
    busiest_end = positions.loc[positions["recorded_at"] == busiest_run, "scheduled_end"].mode().iloc[0]
    to_close = (pd.to_datetime(settled["official_close_time"], utc=True)
                - pd.to_datetime(settled["recorded_at"], utc=True)).map(hours)
    close_by_category = (settled.assign(h=to_close).groupby("category")["h"].median()
                         .map(lambda h: f"{h:.1f} h").to_dict())
    exp = results["exposure"]
    conc = results["concentration"]
    comparison = results["comparison"].copy()
    final = one["settled"] >= VERDICT_AT  # the spec's decision rule can be applied
    elections = positions[positions["category"] == "Elections"]["recorded_at"]
    election_runs = (f"{len(elections)} are election markets, entered in {elections.nunique()} run(s) "
                     f"({', '.join(sorted({f'{t:%Y-%m-%d}' for t in elections}))})" if len(elections) else
                     "none are election markets")
    lines = [
        "# Group A forward test: results through the 2026 US election",
        "",
        f"> **As of {fmt_time(a['as_of_utc'])}.** Paper positions only: no orders were placed, no account or wallet "
        "exists. Prices are real order-book quotes recorded at entry, but a hypothetical fill is not a guarantee of "
        "real execution. Generated by `src/analysis/forward_test_results.py`; every number is computed from the "
        "data. Data audit: [`election_2026_data_audit.md`](election_2026_data_audit.md).",
        "",
        "## Executive summary",
        "",
        f"- **Verdict: {results['verdict']}** The rules fixed in advance ([spec](forward_test_spec.md)) judge the "
        f"hypothesis only after {VERDICT_AT} settled positions. "
        + ("That threshold is reached, so the verdict applies." if final else
           "Everything below is provisional and descriptive."),
        f"- **{len(positions)} Group A paper positions** in {a['group_a_events']} distinct events were recorded with real "
        f"quotes between {fmt_time(positions['recorded_at'].min())} and {fmt_time(positions['recorded_at'].max())}; "
        f"{election_runs}.",
        *(settled_summary(one, a) if one["settled"] else [
            f"- **No position has officially settled yet** ({one['open']} unresolved), so no realized result is shown."]),
        "",
        "## Research question and frozen hypothesis",
        "",
        "Do contracts whose **best ask** is in [0.20, 0.30) seven days before their scheduled end settle YES more often "
        "than that ask implies? The rules were fixed on 2026-10-21, before any data was collected, and have not "
        "changed:",
        "",
        f"- Universe: open binary markets with an order book and at least **${MIN_ENTRY_VOLUME:,} traded at the moment "
        "of entry** (not final volume).",
        "- Entry: the first collector run at which the **scheduled end** is 7 days or less away (within a day of the "
        "7-day mark).",
        "- Position: **one contract of the first-listed outcome** (its name is shown in the table below), bought at the "
        "**best ask** in [0.20, 0.30), held until the market officially settles.",
        "- Settlement: the exchange's final result, matched by the exact outcome token. Race calls, news and current "
        "prices never settle a position.",
        "",
        "## Data provenance and collection timeline",
        "",
        f"- Official test: {a['online_success']} successful collector runs from {fmt_time(a['first_success'])} to "
        f"{fmt_time(a['last_success'])} (GitHub Actions, results stored in the online database).",
        f"- Positions entered by UTC day: {', '.join(f'{n} {p}' for p, n in a['group_a_by_period'].items())}.",
        f"- {same_run} positions were entered in a single run ({fmt_time(busiest_run)}), when markets scheduled to end "
        f"at {fmt_time(busiest_end)} crossed their 7-day mark.",
        "- The election pilot (observations only) ran on a local computer and has no trades by design; it is "
        "described in the data audit, not used here.",
        "",
        "## Usable and unusable observations",
        "",
        f"- Recorded markets with a real quote: {a['recorded_with_quote']:,}; recorded with an empty book: "
        f"{a['recorded_without_quote']} (unusable, none of them Group A).",
        f"- Group A positions whose quote was re-derived exactly from the raw order book: "
        f"{a['raw_books'].get('verified', 0)} of {len(positions)}; raw book expired before the audit: "
        f"{a['raw_books'].get('expired before the audit', 0)}.",
        f"- Positions bought on the market's first-listed token: {a['first_listed_token']} of {len(positions)}. "
        f"Stored settlement agrees with the official per-token result: {a['stored_agrees']} of {len(positions)}. "
        f"Positions meeting the Group A rules when re-checked: {a['meets_rules']} of {len(positions)}."
        + (f" Positions without a usable official result (token not in the market, or not checked): "
           f"{one['no_official_result']}." if one["no_official_result"] else ""),
        "",
        "## Positions",
        "",
        markdown_table(positions_table(positions)),
        "",
        "*Bid / Ask: best prices at entry. $100 avg / $1,000 avg: average price per contract to spend that much, "
        "walking up the asks recorded at entry.*",
        "",
        "## Performance by position size (gross, officially settled positions only)",
        "",
        markdown_table(pd.DataFrame({
            "Scenario": table["scenario"],
            "Fillable": [f"{f} of {p} ({fmt_pct(s, 0)})" for f, p, s in zip(table["fillable"], table["positions"], table["fillable_share"])],
            "Settled": table["settled"], "Unresolved": table["open"], "Wins": table["wins"],
            "Win rate": table["win_rate"].map(fmt_pct),
            "Avg price paid": table["avg_price"].map(lambda v: "n/a" if pd.isna(v) else f"{v:.4f}"),
            "Break-even win rate": table["break_even_win_rate"].map(fmt_pct),
            "Invested": table["invested"].map(fmt_money), "Payout": table["payout"].map(fmt_money),
            "Gross P/L": table["gross_profit"].map(fmt_money), "Gross ROI": table["gross_roi"].map(fmt_pct),
            "95% range (events)": [f"{fmt_pct(lo)} to {fmt_pct(hi)}" for lo, hi in zip(table["gross_roi_low"], table["gross_roi_high"])],
            "Capital still open": table["open_capital"].map(fmt_money),
        })),
        "",
        "ROI = total profit / total capital of the settled positions, never an average of per-trade ROIs. "
        "Break-even win rate: the average price paid, for one contract each. With equal dollars per position a "
        "cheap winner pays more than an expensive one, so there is no single break-even rate (n/a). "
        "**Fees:** not included. The exchange's market data publishes a fee parameter for these "
        f"markets ({', '.join(sorted({str(v) for v in positions['published_taker_fee'].dropna()}))}), but its units and "
        "formula weren't verified, so no net figure is shown.",
        "",
        "## Order-book execution",
        "",
        f"- One contract each is below the exchange's minimum order size on {one['below_minimum_size']} of "
        f"{len(positions)} positions (minimum {positions['min_order_size'].dropna().astype(float).min():.0f} contracts): "
        "it's an accounting unit, not an order that could be placed.",
        f"- A $100 order would have filled completely on {perf['$100']['fillable']} of {len(positions)}, a $1,000 order on "
        f"{perf['$1,000']['fillable']} of {len(positions)}."
        + (f" Average price paid: {perf['$100']['avg_price']:.4f} ($100) and {perf['$1,000']['avg_price']:.4f} ($1,000), "
           f"against {one['avg_price']:.4f} at the best ask (settled positions)." if one["settled"] else ""),
        f"- Spread at entry: median {positions['spread'].median():.3f}; the best ask sat on average "
        f"{(positions['best_ask'] - positions['midpoint']).mean() * 100:.1f} cents above the midpoint.",
        "",
        "## Capital exposure and event concentration",
        "",
        markdown_table(pd.DataFrame([{
            "Scenario": name, "Total capital deployed": fmt_money(e["total_capital"]),
            "Most positions open at once": e["max_open_positions"],
            "Most capital open at once": fmt_money(e["max_open_capital"]),
            "When": fmt_time(e["max_capital_at"])} for name, e in exp.items()])),
        "",
        f"- Each position is held from entry until its official close (or today, if unresolved); capital is never "
        "assumed to be recycled. Category counts: "
        f"{', '.join(f'{n} {c}' for c, n in positions['category'].value_counts().items())}.",
        f"- At most {conc.get('max_positions_per_event', 0)} position(s) per event"
        + (", so no two positions share an event" if conc.get("max_positions_per_event", 0) == 1 else
           ": positions in the same event share their outcome")
        + (f"; the largest single event accounts for {fmt_pct(conc.get('largest_event_share_of_abs_profit'))} of the "
           "absolute profit and loss." if one["settled"] else "."),
        *([f"- Median time from entry to official close: {', '.join(f'{c} {h}' for c, h in close_by_category.items())}. "
           "Sports markets list a scheduled end days after the match, so a '7 days before the scheduled end' entry can "
           "fall just before or during the match."] if close_by_category else []),
        "",
        "## Comparison with the historical Group A study",
        "",
    ]
    if len(comparison):
        def shown(measure, value):
            if measure.startswith("Edge"):
                return f"{value * 100:+.1f} pts"
            if measure.startswith("Average"):
                return f"{value:.3f}"
            return fmt_pct(value) if measure.startswith(("Win", "Gross")) else f"{int(value)}"
        for column, label in [("forward_test", "Forward test"), ("historical", "Historical (retrospective)")]:
            comparison[label] = [shown(m, v) for m, v in zip(comparison["measure"], comparison[column])]
        lines += [markdown_table(comparison[["measure", "Forward test", "Historical (retrospective)"]].rename(
            columns={"measure": "Measure"})), ""]
    lines += [
        "The historical numbers come from recorded prices selected after looking at the history "
        "([`group_a_deep_dive.md`](group_a_deep_dive.md)); they are context, not a proven expected return.",
        "",
        "## Exploratory subgroups (not validated strategies)",
        "",
        "Defined after seeing the data, from tiny samples. They describe where the results came from; none of them "
        "is a tested rule.",
        "",
    ]
    if len(results["subgroups"]):
        sub = results["subgroups"].assign(win_rate=lambda d: d["win_rate"].map(fmt_pct),
                                          avg_price=lambda d: d["avg_price"].map(lambda v: "n/a" if pd.isna(v) else f"{v:.3f}"),
                                          gross_roi=lambda d: d["gross_roi"].map(fmt_pct))
        lines += [markdown_table(sub.rename(columns={"split": "Split", "subgroup": "Subgroup", "positions": "Positions",
                                                     "settled": "Settled", "wins": "Wins", "win_rate": "Win rate",
                                                     "avg_price": "Avg ask", "gross_roi": "Gross ROI"})), ""]
    lines += [
        "## Why the results may differ from the historical study",
        "",
        f"- **Sample size:** {one['settled']} settled positions against {HISTORICAL['markets']}. A single win more or "
        f"fewer moves the win rate by {100 / max(one['settled'], 1):.1f} points.",
        *([f"- **Related outcomes:** {election_runs}. Races decided on the same election day are not independent "
           "of each other, even across different events (e.g. a national swing affects every governor race)."]
          if election > 1 else []),
        "- **Executable prices:** the forward test pays the best ask; the historical study used recorded prices, which "
        "sit closer to the midpoint.",
        "- **Selection:** the 20–30 cent range was chosen after looking at the historical data, so part of its "
        "historical edge may be selection luck.",
        "",
        "## Limitations",
        "",
        *([f"- Fewer than {VERDICT_AT} settled positions: no verdict, wide intervals, and the bootstrap can't fully "
           "reflect dependence between different election events."] if not final else []),
        f"- {a['raw_books'].get('expired before the audit', 0)} positions can't be re-checked against raw books "
        "(artifacts expired); their quotes rest on the stored entry only.",
        "- Entry times are the run's start; the book was read a few seconds later.",
        "- No fees, no market impact of our own orders, no partial fills: a filled paper order isn't a real fill.",
        f"- {one['open']} positions are unresolved; the result can change when they settle.",
        "",
        "## Conclusion: evidence versus speculation",
        "",
        (f"- **Evidence:** {one['settled']} real-quote Group A positions have settled; {one['wins']} won. At the best "
         f"ask this {'lost' if one['gross_profit'] < 0 else 'made'} {fmt_money(abs(one['gross_profit']))} on "
         f"{fmt_money(one['invested'])} ({fmt_pct(one['gross_roi'])})." if one["settled"] else
         f"- **Evidence:** {len(positions)} real-quote Group A positions were recorded; none has settled yet.")
        + f" The quotes behind {a['raw_books'].get('verified', 0)} positions are verified against raw order books.",
        ("- **Not evidence (yet):** whether Group A has an edge. Nothing has settled, so there is no direction yet."
         if not one["settled"] else
         "- **Not evidence (yet):** whether Group A has an edge. The forward test has added new, honestly recorded data "
         f"points that so far {'point the other way from' if one['gross_roi'] < 0 else 'agree in direction with'} the "
         "historical study, but they are too few to support or reject it." if not final else
         f"- **Verdict (spec decision rule):** {results['verdict']}"),
        "- **Speculation (not tested):** that election favourites were simply priced well in 2026, or that the "
        "historical edge came from recorded prices that couldn't be traded.",
        "",
        "## What the next prospective experiment should test",
        "",
        f"- Keep the frozen test running unchanged until {VERDICT_AT} positions settle; report them by month and with "
        "election markets separately.",
        "- Deploy the upgraded collector (request/response/exchange timestamps, full books stored in the database, "
        "invalid books flagged) and verify a cloud run before relying on it.",
        "- For events like elections, fix in advance how correlated contracts are counted (e.g. one position per "
        "event-day cluster), before the data comes in.",
        "- Write the election pilot's own analysis plan before looking at its settlements.",
        "",
    ]
    return "\n".join(lines)
