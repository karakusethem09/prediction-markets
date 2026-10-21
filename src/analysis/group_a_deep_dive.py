"""Group A deep dive: is the 20-30% YES edge a sports effect, a thin-market effect, or a timing fluke?

Builds on combined_yes_strategy.py and uses its knowable entry: the price 7 days before each market's
*scheduled* end, while the market was still open. Three questions, each defined before looking at
the results (see DEFINITIONS), and each answered separately for an exploration period and a later
test period, so a pattern has to show up twice to count.

Retrospective and hypothetical, like the parent analysis: recorded prices, not executable quotes.

    python src/analysis/group_a_deep_dive.py --report docs/group_a_deep_dive.md

Reads the local database (full price history) in read-only sessions, and scheduled end dates from
the latest raw download. Works on any platform's data.
"""
import argparse
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import psycopg

from analysis.combined_yes_strategy import (
    COSTS, GROUPS, LOCAL_DATABASE_URL, event_bootstrap, event_keys, knowable_entry_candidates,
    load_markets_and_prices, markdown_table, money, one_share_performance, pct, select_markets,
)

TEST_PERIOD_START = pd.Timestamp("2026-07-01", tz="UTC")  # exploration before, test from here on
TIMINGS_DAYS = [3, 7, 14]
N_BOOT = 5_000
# A single game or match: "A vs. B", "Will A win on 2026-05-01?", a point spread "(-1.5)" or "O/U 3.5"
SINGLE_GAME = re.compile(r"\bvs\.?\s|win on \d{4}-\d{2}-\d{2}|\(-?\d+(?:\.5)?\)|\bO/U\b")

DEFINITIONS = f"""Fixed before computing any result in this report:

| Question | Definition |
|---|---|
| Entry | The **knowable entry**: the price {TIMINGS_DAYS[1]} days before each market's *scheduled* end (latest price at or before that moment, at most 1 day old), only if the market was still open. Group A = YES price in [{GROUPS['A'][0]:.2f}, {GROUPS['A'][1]:.2f}). One share per market, held to resolution. |
| 1. Sports | **Single game or match** if the question contains `vs`, `win on <date>`, a point spread like `(-1.5)`, or `O/U`; otherwise **other** (politics, crypto, "by date" events, season-long sports futures). The rule was written from question wording only, without looking at prices or outcomes. |
| 2. Liquidity | Total traded volume, split at the **median** of the Group A markets into lower and higher volume. Volume is the market's final total, not the volume available at entry. |
| 3. Timing | The same rules with entry {", ".join(map(str, TIMINGS_DAYS))} days before the scheduled end. A real effect should look similar at nearby entry times. |
| Hold-out | **Exploration:** markets that closed before {TEST_PERIOD_START:%Y-%m-%d}. **Test:** closed on or after it. Each result is shown for both, and for all markets together. |
| Uncertainty | 95% intervals from an event-level bootstrap ({N_BOOT:,} resamples of whole events). ROI is also shown with +{money(max(COSTS.values()))} per share of illustrative costs. |"""


def is_single_game(questions):
    """True for questions about one game or match (see SINGLE_GAME)."""
    return questions.str.contains(SINGLE_GAME, regex=True)


def period(close_times):
    return pd.Series(["test" if t >= TEST_PERIOD_START else "exploration" for t in close_times], index=close_times.index)


def group_a(candidates):
    """Every qualifying market, kept only if its entry price is in Group A."""
    trades = select_markets(candidates, n=None)
    return trades[trades["group"] == "A"].reset_index(drop=True)


def segment_rows(trades, segment_column, segments):
    """One row per segment and period (exploration, test, all): performance with a 95% interval."""
    rows = []
    for segment in segments:
        part = trades[trades[segment_column] == segment]
        for label, piece in [("exploration", part[period(part["close_time"]) == "exploration"]),
                             ("test", part[period(part["close_time"]) == "test"]), ("all", part)]:
            if piece.empty:
                rows.append({"Segment": segment, "Period": label, "Markets": 0})
                continue
            perf = one_share_performance(piece)
            ci = event_bootstrap(piece, n_boot=N_BOOT).loc["Combined"]
            costly = one_share_performance(piece, cost=max(COSTS.values()))
            rows.append({
                "Segment": segment, "Period": label, "Markets": perf["markets"], "Events": perf["events"],
                "Win rate": pct(perf["win_rate"]), "Avg price": f"{perf['avg_price']:.3f}",
                "Edge": f"{perf['edge'] * 100:+.1f} pts", "ROI": pct(perf["roi"]),
                "ROI 95% interval": f"{pct(ci['roi_low'])} to {pct(ci['roi_high'])}",
                f"ROI +{money(max(COSTS.values()))}/share": pct(costly["roi"]),
                "_roi": perf["roi"], "_low": ci["roi_low"], "_high": ci["roi_high"],
            })
    return pd.DataFrame(rows)


def visible(table):
    """The table without the helper columns used for the summary."""
    return table[[c for c in table.columns if not c.startswith("_")]].fillna("")


def describe(table, segment):
    """One generated sentence: does this segment's edge hold in both periods?"""
    rows = table[table["Segment"] == segment].set_index("Period")
    if rows.loc[["exploration", "test"], "Markets"].min() == 0:
        return f"**{segment}:** not enough markets in both periods to compare."
    signs = ["positive" if rows.loc[p, "_roi"] > 0 else "negative" for p in ["exploration", "test"]]
    all_row = rows.loc["all"]
    confident = all_row["_low"] > 0
    return (f"**{segment}:** ROI {all_row['ROI']} over {int(all_row['Markets'])} markets "
            f"(95% interval {all_row['ROI 95% interval']}{', above zero' if confident else ', includes zero' if all_row['_high'] > 0 else ', below zero'}); "
            f"{signs[0]} in exploration ({rows.loc['exploration', 'ROI']}, {int(rows.loc['exploration', 'Markets'])} markets) "
            f"and {signs[1]} in test ({rows.loc['test', 'ROI']}, {int(rows.loc['test', 'Markets'])} markets).")


def build_report(by_timing, generated_at, raw_folder):
    seven = by_timing[TIMINGS_DAYS[1]].copy()
    seven["type"] = is_single_game(seven["question"]).map({True: "Single game or match", False: "Other"})
    median_volume = seven["volume_usd"].median()
    median_label = f"${median_volume / 1e6:.2f}M"
    seven["liquidity"] = (seven["volume_usd"] > median_volume).map(
        {True: f"Higher volume (> {median_label})", False: f"Lower volume (≤ {median_label})"})
    timing = pd.concat([d.assign(timing=f"{days} days before") for days, d in by_timing.items()], ignore_index=True)

    sports = segment_rows(seven, "type", ["Single game or match", "Other"])
    liquidity = segment_rows(seven, "liquidity", sorted(seven["liquidity"].unique()))
    timings = segment_rows(timing, "timing", [f"{d} days before" for d in TIMINGS_DAYS])

    summary = (
        [describe(timings, f"{TIMINGS_DAYS[1]} days before").replace(f"**{TIMINGS_DAYS[1]} days before:**",
                                                                     "**All Group A (7 days before):**")]
        + [describe(sports, s) for s in ["Single game or match", "Other"]]
        + [describe(liquidity, s) for s in sorted(seven["liquidity"].unique())]
        + [describe(timings, f"{d} days before") for d in TIMINGS_DAYS if d != TIMINGS_DAYS[1]]
    )
    examples = seven.assign(event=event_keys(seven)).groupby("type")["question"].apply(lambda q: list(q.head(4)))
    sections = [
        "# Group A deep dive: buying YES at 20–30%",
        f"*Generated {generated_at:%Y-%m-%d %H:%M} UTC by `src/analysis/group_a_deep_dive.py` from the local "
        f"database (read-only) and the raw market download of {raw_folder}. Every number is computed from recorded "
        "prices and outcomes.*",
        "> **Retrospective and hypothetical.** Entries use recorded prices, not executable bid/ask quotes, and the "
        "20–30% interval was chosen after looking at this history. This explores *where* the historical pattern "
        "appears; it does not show that it could have been traded. See `docs/combined_yes_strategy.md` for the "
        "full methodology and limitations.",
        "## Summary\n\n" + "\n".join(f"- {s}" for s in summary)
        + "\n\nA segment is more convincing when its ROI is positive in **both** the exploration and the test period "
          "and its interval for all markets stays above zero. Small segments swing a lot by chance.",
        "## Definitions\n\n" + DEFINITIONS,
        f"## 1. Is it a sports effect?\n\nExamples of each type: single game or match: "
        + "; ".join(f"*{q}*" for q in examples.get("Single game or match", [])[:3])
        + ". Other: " + "; ".join(f"*{q}*" for q in examples.get("Other", [])[:3]) + ".\n\n" + markdown_table(visible(sports)),
        f"## 2. Liquidity: lower vs higher volume\n\nSplit at the median total volume of these Group A markets "
        f"({median_label}). If the edge only appears in lower-volume markets, it is more likely to vanish "
        "once spreads and price impact are paid.\n\n" + markdown_table(visible(liquidity)),
        "## 3. Timing: 3, 7 and 14 days before the scheduled end\n\nEach row uses a different entry moment, so the "
        "set of markets in Group A changes too (a market priced 25% at 14 days may be 40% at 3 days). A real "
        "effect should not depend on one exact entry day.\n\n" + markdown_table(visible(timings)),
        "## Limitations\n\n"
        "- Recorded prices, not executable quotes; no spread, fees or liquidity beyond the illustrative +$0.02.\n"
        "- The 20–30% interval comes from the full history's calibration chart, so even the test period is not a "
        "fully independent test of that choice.\n"
        "- Volume is each market's final total volume, which includes trading after entry.\n"
        "- The sports rule is a text heuristic; a few markets will be misclassified.\n"
        "- Scheduled end dates come from the latest raw download and may have changed while a market traded.",
    ]
    return "\n\n".join(sections) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", type=Path, help="write the Markdown report to this file")
    parser.add_argument("--database-url", default=LOCAL_DATABASE_URL, help="database with the full price history")
    args = parser.parse_args()

    from transform.normalize import POLYMARKET_MARKETS_DIR, scheduled_end_times  # platform-specific reading lives there
    try:
        markets, prices = load_markets_and_prices(args.database_url)
    except psycopg.OperationalError as error:
        message = f"Database unavailable ({type(error).__name__}); no results computed."
        if args.report:
            args.report.write_text(f"# Group A deep dive\n\n**Results pending:** {message}\n")
        raise SystemExit(message)
    scheduled_ends = scheduled_end_times()
    by_timing = {days: group_a(knowable_entry_candidates(markets, prices, scheduled_ends, horizon_days=days))
                 for days in TIMINGS_DAYS}
    for days, trades in by_timing.items():
        print(f"{days} days before: {len(trades)} Group A markets")
    if args.report:
        raw_folder = sorted(d.name for d in POLYMARKET_MARKETS_DIR.iterdir() if d.is_dir())[-1]
        args.report.write_text(build_report(by_timing, datetime.now(timezone.utc), raw_folder))
        print(f"Report written to {args.report}")
