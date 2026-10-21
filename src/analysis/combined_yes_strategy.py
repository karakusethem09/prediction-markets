"""Combined YES Strategy: a retrospective, hypothetical look at two price intervals.

Question: markets priced 20-30% or 50-60% seven days before close looked underpriced on the
7-day calibration chart. Would buying YES in those intervals have made money historically?

This is NOT a backtest of an executable strategy. The 7-day snapshot is taken 7 days before
each market's *actual* close, which a trader doesn't know in advance (look-ahead bias), and the
prices are recorded prices, not executable bid/ask quotes. See the report's limitations section.

Read-only: every session is opened read-only, and the online database should be read with the
dashboard's read-only login:

    set -a; source .env; set +a
    DATABASE_URL="$DASHBOARD_DATABASE_URL" python src/analysis/combined_yes_strategy.py --report docs/combined_yes_strategy.md

The look-ahead checks (report section 13) also read scheduled end dates from the latest raw download
and, for the knowable-entry check, the full price history from the local database (read-only).

Works on any platform's data (only the markets and snapshots tables).
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg

from load.postgres import DATABASE_URL
from transform.snapshots import take_snapshots

GROUPS = {"A": (0.20, 0.30), "B": (0.50, 0.60)}  # YES price intervals, lower bound included, upper excluded
HORIZON_DAYS = 7
SAMPLE_SIZE = 100  # in total across both groups
COSTS = {"A": 0.00, "B": 0.01, "C": 0.02}  # illustrative extra cost per share, NOT verified fees or spreads
STAKE = 1.0  # dollars per trade in the equal-dollar scenario
N_BOOT = 10_000
ON_SCHEDULE = pd.Timedelta(days=1)  # "closed on schedule": within a day of the scheduled end
# Only the local database keeps the full price history, which the knowable-entry check needs
LOCAL_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/prediction_markets"

CANDIDATES_SQL = """
    SELECT s.market_id, m.event_id, m.question, m.close_time, m.outcome, s.price
    FROM snapshots s
    JOIN markets m USING (market_id)
    WHERE s.horizon_days = %s AND m.outcome IN (0, 1)
"""


# ---------- data and selection ----------

def load_candidates(database_url=DATABASE_URL, horizon_days=HORIZON_DAYS):
    """Every market with a usable snapshot at this horizon and a binary outcome (read-only session)."""
    with psycopg.connect(database_url) as conn:
        conn.read_only = True  # the database refuses any write in this session
        rows = conn.execute(CANDIDATES_SQL, (horizon_days,)).fetchall()
    candidates = pd.DataFrame(rows, columns=["market_id", "event_id", "question", "close_time", "outcome", "price"])
    candidates["close_time"] = pd.to_datetime(candidates["close_time"], utc=True)
    return candidates


def price_group(price):
    """'A' for [0.20, 0.30), 'B' for [0.50, 0.60), None otherwise."""
    for group, (low, high) in GROUPS.items():
        if low <= price < high:
            return group
    return None


def select_markets(candidates, n=SAMPLE_SIZE, skip=0):
    """The n most recently closed qualifying markets, after skipping the `skip` most recent ones
    (n=None: every qualifying market).

    Qualifying: price in group A or B, outcome 0 or 1. Sorted by close_time (newest first), with
    market_id as a tie-breaker so the selection is the same every time. The n are counted across
    both groups together; each group gets however many naturally fall into it.
    """
    pool = candidates[candidates["outcome"].isin([0, 1]) & candidates["price"].notna()].copy()
    pool["group"] = pool["price"].map(price_group)
    pool = pool[pool["group"].notna()]
    pool = pool.sort_values(["close_time", "market_id"], ascending=[False, True])
    return pool.iloc[skip:None if n is None else skip + n].reset_index(drop=True)


def event_keys(trades):
    """Which event each market belongs to; a market without an event counts as its own event."""
    return trades["event_id"].fillna(trades["market_id"])


# ---------- look-ahead checks ----------

def close_timing(trades, scheduled_ends, tolerance=ON_SCHEDULE):
    """Add each market's scheduled end and whether it closed early, on schedule or late.

    Markets that close early often do so *because* YES happened ("...by December 31?" resolves the
    day it happens), so measuring "7 days before close" from the actual close favours YES.
    """
    timed = trades.merge(scheduled_ends, on="market_id", how="left")
    gap = timed["close_time"] - timed["scheduled_end"]
    timed["timing"] = np.select([gap.isna(), gap < -tolerance, gap > tolerance],
                                ["unknown", "early", "late"], default="on schedule")
    return timed


def timing_table(timed):
    rows = []
    for timing in ["early", "on schedule", "late", "unknown"]:
        part = timed[timed["timing"] == timing]
        if len(part):
            perf = one_share_performance(part)
            rows.append({"Closed": timing, "Markets": perf["markets"], "Win rate": pct(perf["win_rate"]),
                         "Avg price": f"{perf['avg_price']:.3f}", "Edge": f"{perf['edge'] * 100:+.1f} pts",
                         "Total P/L": money(perf["total_profit"]), "ROI": pct(perf["roi"])})
    return pd.DataFrame(rows)


def load_markets_and_prices(database_url=LOCAL_DATABASE_URL):
    """Markets and their full price history (read-only session), typed as the snapshot step expects."""
    with psycopg.connect(database_url) as conn:
        conn.read_only = True
        markets = pd.DataFrame(conn.execute(
            "SELECT market_id, event_id, question, open_time, close_time, outcome, volume_usd FROM markets").fetchall(),
            columns=["market_id", "event_id", "question", "open_time", "close_time", "outcome", "volume_usd"])
        prices = pd.DataFrame(conn.execute("SELECT market_id, time, price, fidelity FROM price_history").fetchall(),
                              columns=["market_id", "time", "price", "fidelity"])
    for frame, columns in [(markets, ["open_time", "close_time"]), (prices, ["time"])]:
        for column in columns:
            frame[column] = pd.to_datetime(frame[column], utc=True).dt.as_unit("us")
    for frame, columns in [(markets, ["market_id"]), (prices, ["market_id", "fidelity"])]:
        frame[columns] = frame[columns].astype(str)
    return markets, prices


def knowable_entry_candidates(markets, prices, scheduled_ends, horizon_days=HORIZON_DAYS):
    """Entry prices a trader could actually have targeted: `horizon_days` before the *scheduled* end.

    Uses the project's snapshot rule (latest price at or before the moment, at most 1 day old), measured
    from the scheduled end instead of the actual close. A market that had already closed by then is
    left out: nobody can buy into a finished market.
    """
    timed = markets.merge(scheduled_ends, on="market_id")
    moments = timed.assign(close_time=timed["scheduled_end"].dt.as_unit("us"))[["market_id", "open_time", "close_time"]]
    entries = take_snapshots(moments, prices, horizons_days=[horizon_days])
    extra = [c for c in ["volume_usd"] if c in timed.columns]  # carried along when available
    entries = entries.merge(timed[["market_id", "event_id", "question", "close_time", "outcome", *extra]], on="market_id")
    still_open = entries["target_time"] < entries["close_time"]
    return entries.loc[still_open, ["market_id", "event_id", "question", "close_time", "outcome", "price", *extra]]


# ---------- performance ----------

def one_share_performance(trades, cost=0.0):
    """Buy exactly one YES share per market at the snapshot price (+ cost) and hold to resolution.

    Profit per share = payout (1 if YES won, else 0) - price - cost. ROI = total profit / total paid,
    aggregated over all shares (not an average of per-trade ROIs).
    """
    price, outcome = trades["price"], trades["outcome"]
    paid = price + cost
    profit = outcome - paid
    return {
        "markets": len(trades),
        "events": event_keys(trades).nunique(),
        "avg_price": price.mean(),
        "median_price": price.median(),
        "wins": int(outcome.sum()),
        "losses": int(len(trades) - outcome.sum()),
        "win_rate": outcome.mean(),
        # One share per market: profit is zero when wins = sum of prices paid, i.e. win rate = average price paid
        "break_even_win_rate": paid.mean(),
        "avg_profit_per_share": profit.mean(),
        "total_profit": profit.sum(),
        "total_paid": paid.sum(),
        "roi": profit.sum() / paid.sum(),
        "edge": outcome.mean() - price.mean(),  # realized win rate minus average price (before costs)
    }


def equal_dollar_performance(trades, cost=0.0, stake=STAKE):
    """Invest the same dollar amount in every market: stake / (price + cost) shares each.

    Cheap contracts get more shares than expensive ones, so this weighs trades differently from
    the one-share scenario. ROI = total profit / total invested.
    """
    shares = stake / (trades["price"] + cost)
    profit = shares * trades["outcome"] - stake
    invested = stake * len(trades)
    return {
        "markets": len(trades),
        "invested": invested,
        "total_profit": profit.sum(),
        "avg_profit_per_trade": profit.mean(),
        "roi": profit.sum() / invested,
    }


def by_group(trades, performance, **kwargs):
    """One row per group (A, B) and one for the combined sample."""
    rows = {}
    for group in GROUPS:
        rows[f"Group {group}"] = performance(trades[trades["group"] == group], **kwargs)
    rows["Combined"] = performance(trades, **kwargs)
    return pd.DataFrame(rows).T


def by_period(trades):
    """One-share gross performance per half-year of close, per group: is an edge stable over time?"""
    halves = trades["close_time"].dt.year.astype(str) + " H" + ((trades["close_time"].dt.month > 6) + 1).astype(str)
    rows = []
    for (period, group), part in trades.groupby([halves, "group"]):
        perf = one_share_performance(part)
        rows.append({"Period": period, "Group": group, "Markets": perf["markets"], "Win rate": pct(perf["win_rate"]),
                     "Avg price": f"{perf['avg_price']:.3f}", "Edge": f"{perf['edge'] * 100:+.1f} pts",
                     "Total P/L": money(perf["total_profit"]), "ROI": pct(perf["roi"])})
    return pd.DataFrame(rows)


# ---------- uncertainty ----------

def event_bootstrap(trades, cost=0.0, n_boot=N_BOOT, seed=0):
    """95% intervals for total profit and ROI, resampling whole events (as in analysis/bootstrap.py).

    Markets in one event share an outcome, so each resample draws events with replacement and a
    market counts as often as its event was drawn. Returns one row per group and for the combined sample.
    """
    rng = np.random.default_rng(seed)
    rows = {}
    for label, part in [*((f"Group {g}", trades[trades["group"] == g]) for g in GROUPS), ("Combined", trades)]:
        if part.empty:
            continue
        codes, units = pd.factorize(event_keys(part))
        price, outcome = part["price"].to_numpy(), part["outcome"].to_numpy()
        paid = price + cost
        share_profit = outcome - paid
        dollar_profit = STAKE * outcome / paid - STAKE
        totals, rois, dollar_rois = [], [], []
        for _ in range(n_boot):
            weights = np.bincount(rng.integers(0, len(units), len(units)), minlength=len(units))[codes]
            totals.append(weights @ share_profit)
            rois.append((weights @ share_profit) / (weights @ paid))
            dollar_rois.append((weights @ dollar_profit) / (STAKE * weights.sum()))
        rows[label] = {
            "events": len(units),
            "total_profit_low": np.percentile(totals, 2.5), "total_profit_high": np.percentile(totals, 97.5),
            "roi_low": np.percentile(rois, 2.5), "roi_high": np.percentile(rois, 97.5),
            "equal_dollar_roi_low": np.percentile(dollar_rois, 2.5),
            "equal_dollar_roi_high": np.percentile(dollar_rois, 97.5),
            "share_of_resamples_profitable": np.mean(np.array(totals) > 0),
        }
    return pd.DataFrame(rows).T


def event_concentration(trades, top=5):
    """How much of the one-share gross profit comes from a few events."""
    per_event = (trades.assign(event=event_keys(trades), profit=trades["outcome"] - trades["price"])
                 .groupby("event")
                 .agg(markets=("market_id", "size"), groups=("group", lambda g: "+".join(sorted(set(g)))),
                      profit=("profit", "sum"), example_question=("question", "first"))
                 .sort_values("profit", key=abs, ascending=False))
    total = per_event["profit"].sum()
    best = per_event["profit"].idxmax()
    return {
        "table": per_event.head(top).reset_index(),
        "events": len(per_event),
        "events_with_several_markets": int((per_event["markets"] > 1).sum()),
        "largest_event_markets": int(per_event["markets"].max()),
        "total_profit": total,
        "profit_without_best_event": total - per_event.loc[best, "profit"],
        "best_event_profit": per_event.loc[best, "profit"],
    }


# ---------- report ----------

def money(value):
    return f"-${abs(value):,.2f}" if value < 0 else f"${value:,.2f}"


def pct(value, digits=1):
    return f"{value * 100:.{digits}f}%"


def markdown_table(frame):
    """A DataFrame as a Markdown table (no extra dependency needed)."""
    header = "| " + " | ".join(str(c) for c in frame.columns) + " |"
    divider = "|" + "|".join("---" for _ in frame.columns) + "|"
    body = ["| " + " | ".join(str(v) for v in row) + " |" for row in frame.itertuples(index=False)]
    return "\n".join([header, divider, *body])


def performance_table(trades, cost=0.0):
    perf = by_group(trades, one_share_performance, cost=cost)
    return pd.DataFrame({
        "": perf.index,
        "Markets": perf["markets"].astype(int),
        "Events": perf["events"].astype(int),
        "Avg price": perf["avg_price"].map(lambda v: f"{v:.3f}"),
        "Median price": perf["median_price"].map(lambda v: f"{v:.3f}"),
        "Wins / losses": [f"{int(w)} / {int(l)}" for w, l in zip(perf["wins"], perf["losses"])],
        "Win rate": perf["win_rate"].map(pct),
        "Break-even win rate": perf["break_even_win_rate"].map(pct),
        "Edge (win rate − avg price)": perf["edge"].map(lambda v: f"{v * 100:+.1f} pts"),
        "Avg P/L per share": perf["avg_profit_per_share"].map(money),
        "Total P/L": perf["total_profit"].map(money),
        "Total paid": perf["total_paid"].map(money),
        "ROI": perf["roi"].map(pct),
    })


def scenario_table(trades):
    rows = []
    for scenario, cost in COSTS.items():
        shares = by_group(trades, one_share_performance, cost=cost)
        dollars = by_group(trades, equal_dollar_performance, cost=cost)
        for label in shares.index:
            rows.append({
                "Scenario": f"{scenario} (+{money(cost)}/share)", "": label,
                "One share: total P/L": money(shares.loc[label, "total_profit"]),
                "One share: ROI": pct(shares.loc[label, "roi"]),
                "One share: break-even win rate": pct(shares.loc[label, "break_even_win_rate"]),
                f"${STAKE:.0f} per trade: total P/L": money(dollars.loc[label, "total_profit"]),
                f"${STAKE:.0f} per trade: ROI": pct(dollars.loc[label, "roi"]),
            })
    return pd.DataFrame(rows)


def uncertainty_table(trades, cost=0.0):
    boot = event_bootstrap(trades, cost=cost)
    return pd.DataFrame({
        "": boot.index,
        "Events": boot["events"].astype(int),
        "Total P/L, 95% interval": [f"{money(lo)} to {money(hi)}" for lo, hi in zip(boot["total_profit_low"], boot["total_profit_high"])],
        "One-share ROI, 95% interval": [f"{pct(lo)} to {pct(hi)}" for lo, hi in zip(boot["roi_low"], boot["roi_high"])],
        f"${STAKE:.0f}-per-trade ROI, 95% interval": [f"{pct(lo)} to {pct(hi)}" for lo, hi in zip(boot["equal_dollar_roi_low"], boot["equal_dollar_roi_high"])],
        "Resamples with a profit": boot["share_of_resamples_profitable"].map(lambda v: pct(v, 0)),
    }), boot


def sample_listing(trades):
    return pd.DataFrame({
        "#": range(1, len(trades) + 1),
        "Closed": trades["close_time"].dt.strftime("%Y-%m-%d"),
        "Group": trades["group"],
        "Market": trades["question"].str.replace("|", "/", regex=False).str.slice(0, 70),
        "Event": event_keys(trades),
        "7-day price": trades["price"].map(lambda v: f"{v:.3f}"),
        "YES won?": trades["outcome"].map({1: "yes", 0: "no"}),
        "P/L (1 share)": (trades["outcome"] - trades["price"]).map(money),
    })


def summary_points(latest, earlier, boot):
    """Plain-English findings, generated from the numbers (no hand-typed results)."""
    perf = by_group(latest, one_share_performance)
    costs = {s: by_group(latest, one_share_performance, cost=c).loc["Combined"] for s, c in COSTS.items()}
    combined, ci = perf.loc["Combined"], boot.loc["Combined"]
    points = []
    counts = ", ".join(f"Group {g}: {int(perf.loc[f'Group {g}', 'markets'])}" for g in GROUPS)
    points.append(f"**Sample.** The latest {int(combined['markets'])} qualifying markets split naturally into {counts}, "
                  f"from {int(combined['events'])} distinct events.")
    for g in GROUPS:
        row = perf.loc[f"Group {g}"]
        points.append(f"**Group {g}** ({GROUPS[g][0]:.2f}–{GROUPS[g][1]:.2f}): YES won {int(row['wins'])} of "
                      f"{int(row['markets'])} ({pct(row['win_rate'])}) against an average price of {row['avg_price']:.3f}, "
                      f"so one share each would have made {money(row['total_profit'])} (ROI {pct(row['roi'])}).")
    points.append(f"**Combined, gross.** {money(combined['total_profit'])} on {money(combined['total_paid'])} paid: "
                  f"ROI {pct(combined['roi'])} (one share per market).")
    points.append("**With costs.** " + "; ".join(
        f"+{money(c)}/share: {money(costs[s]['total_profit'])} (ROI {pct(costs[s]['roi'])})" for s, c in COSTS.items() if c > 0) + ".")
    excludes_zero = ci["total_profit_low"] > 0 or ci["total_profit_high"] < 0
    points.append(f"**Uncertainty.** Resampling whole events, the 95% interval for the combined gross P/L is "
                  f"{money(ci['total_profit_low'])} to {money(ci['total_profit_high'])}. " +
                  ("It excludes zero, but that alone does not make the result tradable (see limitations)."
                   if excludes_zero else "It includes zero, so the result is consistent with luck."))
    signs = {g: perf.loc[f"Group {g}", "total_profit"] > 0 for g in GROUPS}
    points.append("**Which group drives it.** " + (
        "Both groups were profitable." if all(signs.values()) else
        "Neither group was profitable." if not any(signs.values()) else
        f"Only Group {next(g for g, s in signs.items() if s)} was profitable; the other lost money."))
    if len(earlier):
        early = by_group(earlier, one_share_performance)
        per_group = "; ".join(f"Group {g} {money(early.loc[f'Group {g}', 'total_profit'])} "
                              f"(edge {early.loc[f'Group {g}', 'edge'] * 100:+.1f} pts)" for g in GROUPS)
        same_sign = all((early.loc[label, "total_profit"] > 0) == (perf.loc[label, "total_profit"] > 0)
                        for label in perf.index)
        points.append(f"**Earlier sample.** The {len(earlier)} qualifying markets before these made "
                      f"{money(early.loc['Combined', 'total_profit'])} combined (ROI {pct(early.loc['Combined', 'roi'])}, "
                      f"edge {early.loc['Combined', 'edge'] * 100:+.1f} pts): {per_group}. "
                      + ("Every group pointed the same way as in the latest sample."
                         if same_sign else "Not every group pointed the same way as in the latest sample, so the "
                                           "pattern is not stable across these two periods."))
    return points


def full_history_points(all_markets, all_boot):
    """Plain-English findings for every eligible market, generated from the numbers."""
    perf = by_group(all_markets, one_share_performance)
    points = [f"**All eligible markets.** {len(all_markets)} markets from {int(perf.loc['Combined', 'events'])} events, "
              f"closing {all_markets['close_time'].min():%Y-%m-%d} to {all_markets['close_time'].max():%Y-%m-%d}."]
    for label in perf.index:
        row, ci = perf.loc[label], all_boot.loc[label]
        verdict = ("interval above zero" if ci["roi_low"] > 0 else
                   "interval below zero" if ci["roi_high"] < 0 else "interval includes zero")
        points.append(f"**{label}:** {int(row['markets'])} markets, win rate {pct(row['win_rate'])} vs average price "
                      f"{row['avg_price']:.3f} (edge {row['edge'] * 100:+.1f} pts), {money(row['total_profit'])} on "
                      f"{money(row['total_paid'])} paid: ROI {pct(row['roi'])}, 95% interval {pct(ci['roi_low'])} to "
                      f"{pct(ci['roi_high'])} ({verdict}).")
    return points


def verdict(ci):
    return "above zero" if ci["roi_low"] > 0 else "below zero" if ci["roi_high"] < 0 else "includes zero"


def look_ahead_points(look_ahead):
    """Plain-English findings for the look-ahead checks, generated from the numbers."""
    timed = look_ahead["timed"]
    points = []
    edges = {t: one_share_performance(timed[timed["timing"] == t])["edge"]
             for t in ["early", "on schedule"] if (timed["timing"] == t).any()}
    if len(edges) == 2:
        points.append(f"**Where the edge came from.** Markets that closed more than a day *early* had an edge of "
                      f"{edges['early'] * 100:+.1f} pts; markets that closed on schedule {edges['on schedule'] * 100:+.1f} pts. "
                      "Early closes often happen because YES happened, which the original entry rule quietly exploits.")
    checks = [("Closed on schedule", look_ahead["on_schedule"])]
    if look_ahead.get("knowable") is not None:
        checks.append(("Entry 7 days before the scheduled end", look_ahead["knowable"]))
    for name, trades in checks:
        perf, boot = by_group(trades, one_share_performance), event_bootstrap(trades)
        costly = by_group(trades, one_share_performance, cost=max(COSTS.values()))
        parts = [f"Group {g}: {int(perf.loc[f'Group {g}', 'markets'])} markets, edge "
                 f"{perf.loc[f'Group {g}', 'edge'] * 100:+.1f} pts, ROI {pct(perf.loc[f'Group {g}', 'roi'])} "
                 f"(95% interval {pct(boot.loc[f'Group {g}', 'roi_low'])} to {pct(boot.loc[f'Group {g}', 'roi_high'])}, "
                 f"{verdict(boot.loc[f'Group {g}'])}; {pct(costly.loc[f'Group {g}', 'roi'])} with "
                 f"+{money(max(COSTS.values()))}/share)" for g in GROUPS]
        parts.append(f"combined ROI {pct(perf.loc['Combined', 'roi'])} ({verdict(boot.loc['Combined'])})")
        points.append(f"**{name}.** " + "; ".join(parts) + ".")
    return points


def build_report(latest, earlier, source, generated_at, all_markets=None, look_ahead=None):
    perf_note = ("One YES share per market, bought at the recorded 7-day snapshot price and held to resolution "
                 "(payout $1 if YES won, else $0).")
    uncertainty, boot = uncertainty_table(latest)
    concentration = event_concentration(latest)
    if all_markets is not None:
        all_uncertainty, all_boot = uncertainty_table(all_markets)
    sections = [
        "# Combined YES Strategy: a retrospective, hypothetical analysis",
        f"*Generated {generated_at:%Y-%m-%d %H:%M} UTC by `src/analysis/combined_yes_strategy.py` from {source}. "
        "Every number below is computed from recorded prices and outcomes; nothing is estimated by hand.*",
        "> **This is not a validated or executable trading strategy.** The entry price is taken 7 days before each "
        "market's *actual* close, which a trader would not have known in advance, and recorded prices are not "
        "executable bid/ask quotes. Read the results as a description of past prices, not as returns that could "
        "have been earned. See section 10.",
        f"## Summary: the latest {len(latest)} markets\n\n" + "\n".join(f"- {p}" for p in summary_points(latest, earlier, boot)),
        *([f"## Summary: all {len(all_markets)} eligible markets\n\nThe same rules on every qualifying market in the "
           "database (section 12), to see whether the latest 100 were typical.\n\n"
           + "\n".join(f"- {p}" for p in full_history_points(all_markets, all_boot))] if all_markets is not None else []),
        *(["## Summary: without the look-ahead\n\nThe original entry rule uses each market's *actual* close, which "
           "a trader could not know. Section 13 removes that advantage in two ways.\n\n"
           + "\n".join(f"- {p}" for p in look_ahead_points(look_ahead))] if look_ahead is not None else []),
        "## 1. Research question\n\nThe 7-day calibration chart suggests that markets priced **20–30%** and **50–60%** "
        "seven days before close resolved YES more often than their prices implied. If so, buying YES in those two "
        "intervals would have earned more than it cost. Did it, on the most recent markets, and how sure can we be?",
        "## 2. Strategy rules\n\n"
        f"- **Group A:** YES price in [{GROUPS['A'][0]:.2f}, {GROUPS['A'][1]:.2f}). **Group B:** YES price in "
        f"[{GROUPS['B'][0]:.2f}, {GROUPS['B'][1]:.2f}). The lower bound is included, the upper one excluded.\n"
        "- Buy YES at the 7-day snapshot price and hold until the market resolves.\n"
        f"- **One-share scenario:** exactly one share per market; profit = payout − price − costs; "
        "ROI = total profit ÷ total paid (aggregated over all shares, not an average of per-trade ROIs).\n"
        f"- **Equal-dollar scenario:** ${STAKE:.0f} per market, buying ${STAKE:.0f} ÷ (price + costs) shares; "
        "ROI = total profit ÷ total invested. Cheap contracts get more shares, so the two scenarios weigh trades "
        "differently and are reported separately.\n"
        "- The intervals were fixed before looking at these results and are not tuned afterwards.",
        "## 3. Selection methodology\n\n"
        f"- Markets with a usable **{HORIZON_DAYS}-day snapshot** (the latest price at or before 7 days before close, at "
        "most 1 day old, as defined by the project's snapshot rule) and a binary final outcome.\n"
        "- Price in Group A or Group B. A 30-day snapshot is not required.\n"
        f"- Sorted by close time, newest first, with `market_id` breaking ties; the first **{SAMPLE_SIZE} in total** "
        "across both groups. The split between groups is whatever occurs naturally.\n"
        "- Events: markets that share an event (e.g. several outcomes of one election) are grouped by `event_id`; a "
        "market without one is its own event.",
        f"## 4. The latest {len(latest)} qualifying markets\n\n"
        f"Closing between {latest['close_time'].min():%Y-%m-%d} and {latest['close_time'].max():%Y-%m-%d}.\n\n"
        + markdown_table(sample_listing(latest)),
        f"## 5. Group A vs Group B\n\n{perf_note} Gross, no costs.\n\n" + markdown_table(performance_table(latest)),
        "## 6. Combined portfolio\n\nThe *Combined* row above is the portfolio of all "
        f"{len(latest)} positions. The equal-dollar version (${STAKE:.0f} per market) is in the next table.",
        "## 7. Three cost scenarios\n\nCosts are **illustrative assumptions**, added per share to the entry price: "
        "not verified Polymarket fees or historical spreads.\n\n" + markdown_table(scenario_table(latest)),
        "## 8. Statistical uncertainty\n\nGross results, 95% intervals from an **event-level bootstrap** "
        f"({N_BOOT:,} resamples of whole events with replacement), so markets from the same event are not "
        "treated as independent evidence. \"Resamples with a profit\" is the share of resamples whose total P/L was "
        "above zero: a rough guide to stability, not a probability that the strategy works.\n\n"
        + markdown_table(uncertainty)
        + f"\n\n**Event concentration.** {concentration['events']} events; {concentration['events_with_several_markets']} "
        f"of them contribute more than one market (the largest has {concentration['largest_event_markets']}). The "
        f"single most profitable event contributed {money(concentration['best_event_profit'])} of the "
        f"{money(concentration['total_profit'])} total; without it the total would be "
        f"{money(concentration['profit_without_best_event'])}. Events with the largest effect (one share, gross):\n\n"
        + markdown_table(concentration["table"].assign(profit=concentration["table"]["profit"].map(money))
                         .rename(columns={"event": "Event", "markets": "Markets", "groups": "Groups",
                                          "profit": "P/L", "example_question": "Example market"})),
    ]
    if len(earlier):
        comparison = pd.concat([performance_table(latest).assign(Sample=f"Latest {len(latest)}"),
                                performance_table(earlier).assign(Sample=f"Earlier {len(earlier)}")])
        comparison = comparison[["Sample", "", "Markets", "Events", "Win rate", "Avg price",
                                 "Edge (win rate − avg price)", "Total P/L", "ROI"]]
        sections.append(
            f"## 9. Comparison with an earlier sample\n\nThe {len(earlier)} qualifying markets that closed just before "
            f"the latest sample (no overlap), closing between {earlier['close_time'].min():%Y-%m-%d} and "
            f"{earlier['close_time'].max():%Y-%m-%d}. Same rules, gross.\n\n" + markdown_table(comparison))
    else:
        sections.append("## 9. Comparison with an earlier sample\n\nNo earlier qualifying markets were available.")
    sections += [
        "## 10. Limitations and interpretation\n\n"
        "- **Look-ahead bias.** The entry moment is 7 days before the market's *actual* close. Many markets close "
        "earlier or later than their scheduled end date, so a trader could not have known when \"7 days before "
        "close\" was. This alone means the results are retrospective, not achievable. Section 13 measures how much "
        "of the result this explains.\n"
        "- **Recorded prices, not executable ones.** The snapshot is a recorded price from the price-history API. "
        "Buying at it would have meant paying the ask, plus fees and slippage, which these numbers don't include "
        "beyond the illustrative cost scenarios.\n"
        "- **Liquidity.** Nothing here says how many shares were actually available at those prices.\n"
        f"- **Small sample.** {len(latest)} markets from {concentration['events']} events is small; the intervals in "
        "section 8 show how wide the plausible range is.\n"
        "- **Chosen after looking.** The two intervals came from the calibration chart of the full history, which "
        "includes these markets, so this is not an independent test of an idea formed beforehand.",
        "## 11. What a real backtest would need\n\n"
        "- The **expected resolution time known at entry** (e.g. the scheduled end date), with entries timed from it "
        "instead of from the actual close.\n"
        "- **Historical executable bid/ask prices** (order-book snapshots), not recorded trade or mid prices.\n"
        "- **Trading fees and slippage** for realistic order sizes.\n"
        "- **Available order-book liquidity** at the time of entry, to know what size could have been filled.\n"
        "- **Chronological out-of-sample validation:** choose the intervals on one period, then test them only on "
        "later markets that played no part in choosing them.",
    ]
    if all_markets is not None:
        all_concentration = event_concentration(all_markets)
        comparison = pd.concat([performance_table(latest).assign(Sample=f"Latest {len(latest)}"),
                                performance_table(earlier).assign(Sample=f"Earlier {len(earlier)}"),
                                performance_table(all_markets).assign(Sample=f"All {len(all_markets)}")])
        comparison = comparison[["Sample", "", "Markets", "Events", "Win rate", "Avg price",
                                 "Edge (win rate − avg price)", "Total P/L", "ROI"]]
        sections.append(
            f"## 12. All eligible markets (full history)\n\n"
            f"The same rules applied to **every** qualifying market in the database: {len(all_markets)} markets "
            f"from {all_concentration['events']} events, closing {all_markets['close_time'].min():%Y-%m-%d} to "
            f"{all_markets['close_time'].max():%Y-%m-%d}. This includes the latest and earlier samples above. "
            "With more markets, a lucky streak matters less, so these numbers show whether the latest 100 were typical. "
            "The same limitations apply (section 10).\n\n"
            f"### Performance\n\n{perf_note} Gross, no costs.\n\n" + markdown_table(performance_table(all_markets))
            + "\n\n### Cost scenarios\n\n" + markdown_table(scenario_table(all_markets))
            + f"\n\n### Uncertainty (event-level bootstrap, {N_BOOT:,} resamples)\n\n" + markdown_table(all_uncertainty)
            + f"\n\n**Event concentration.** {all_concentration['events_with_several_markets']} of "
            f"{all_concentration['events']} events contribute more than one market (the largest has "
            f"{all_concentration['largest_event_markets']}); without the single most profitable event the total would be "
            f"{money(all_concentration['profit_without_best_event'])} instead of {money(all_concentration['total_profit'])}."
            + "\n\n### Latest 100 vs earlier 100 vs all\n\n" + markdown_table(comparison)
            + "\n\n### Stability over time (half-years, gross)\n\nIf an edge is real it should appear in most periods, "
            "not just one. Periods with few markets swing a lot by chance.\n\n" + markdown_table(by_period(all_markets)))
    if look_ahead is not None:
        on_schedule, knowable = look_ahead["on_schedule"], look_ahead.get("knowable")
        section = (
            "## 13. Removing the look-ahead: scheduled end dates\n\n"
            "Every market also has a **scheduled** end date (`endDate`), which a trader knows in advance. "
            f"{look_ahead['scheduled_source']} Comparing it with the actual close shows where the original "
            "edge came from (all eligible markets, original entry rule, one share each, gross):\n\n"
            + markdown_table(timing_table(look_ahead["timed"]))
            + f"\n\n### (a) Only markets that closed on schedule\n\nThe original selection, keeping only markets "
            f"that closed within {ON_SCHEDULE.days} day of their scheduled end. For these, \"7 days before close\" "
            "was (almost) knowable in advance.\n\n" + markdown_table(performance_table(on_schedule))
            + "\n\n" + markdown_table(uncertainty_table(on_schedule)[0])
            + "\n\n### (b) Entry 7 days before the scheduled end\n\n")
        if knowable is not None:
            section += (
                "A stricter test that needs no filtering: for every market, the entry price is taken "
                f"{HORIZON_DAYS} days before the **scheduled** end with the same snapshot rule (latest price at or "
                "before that moment, at most 1 day old), and only if the market was still open then. Markets "
                "that resolved early after entry are kept, as a trader would have held them. Then the same two "
                f"price intervals are applied: {len(knowable)} markets qualify.\n\n"
                + markdown_table(performance_table(knowable)) + "\n\n" + markdown_table(uncertainty_table(knowable)[0])
                + "\n\n" + markdown_table(scenario_table(knowable)))
        else:
            section += f"**Pending:** {look_ahead.get('knowable_note', 'the price history could not be read')}. No numbers are shown rather than made up."
        section += (
            "\n\n### Caveats for this section\n\n"
            "- The scheduled end dates come from the latest raw download. Polymarket can change a market's end date "
            "while it trades, so the date a trader saw at entry may have been different.\n"
            "- Prices are still recorded prices, not executable quotes, and the intervals were still chosen after "
            "looking at the full history (section 10).\n"
            "- Many of these markets are single sports games, so a result here may describe sports markets more "
            "than prediction markets in general.")
        sections.append(section)
    return "\n\n".join(sections) + "\n"


def pending_report(error):
    return ("# Combined YES Strategy: a retrospective, hypothetical analysis\n\n"
            f"**Numerical results are pending:** the database could not be read ({type(error).__name__}). "
            "No numbers are shown rather than made up. Run `src/analysis/combined_yes_strategy.py` again "
            "once the database is available.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", type=Path, help="write the Markdown report to this file")
    parser.add_argument("--source", default="the project's PostgreSQL database (read-only session)",
                        help="how to describe the data source in the report")
    parser.add_argument("--prices-database-url", default=LOCAL_DATABASE_URL,
                        help="database with the full price history, for the knowable-entry check (default: local Docker)")
    args = parser.parse_args()

    try:
        candidates = load_candidates()
    except psycopg.OperationalError as error:
        if args.report:
            args.report.write_text(pending_report(error))
        raise SystemExit(f"Database unavailable ({type(error).__name__}); report marked as pending.")

    latest = select_markets(candidates)
    earlier = select_markets(candidates, skip=SAMPLE_SIZE)
    all_markets = select_markets(candidates, n=None)
    print(f"Latest {len(latest)}:\n" + performance_table(latest).to_string(index=False))
    print(f"\nAll {len(all_markets)} eligible:\n" + performance_table(all_markets).to_string(index=False))

    # Look-ahead checks (section 13). Scheduled ends come from the latest raw download.
    from transform.normalize import POLYMARKET_MARKETS_DIR, scheduled_end_times  # platform-specific reading lives there
    scheduled_ends = scheduled_end_times()
    raw_folder = sorted(d.name for d in POLYMARKET_MARKETS_DIR.iterdir() if d.is_dir())[-1]
    timed = close_timing(all_markets, scheduled_ends)
    look_ahead = {
        "timed": timed,
        "on_schedule": timed[timed["timing"] == "on schedule"].reset_index(drop=True),
        "scheduled_source": f"The scheduled end dates come from the raw market download of {raw_folder}; "
                            f"{int((timed['timing'] != 'unknown').sum())} of {len(timed)} eligible markets have one.",
    }
    try:
        markets, prices = load_markets_and_prices(args.prices_database_url)
        look_ahead["knowable"] = select_markets(knowable_entry_candidates(markets, prices, scheduled_ends), n=None)
    except psycopg.OperationalError as error:
        look_ahead["knowable"] = None
        look_ahead["knowable_note"] = f"the local price history could not be read ({type(error).__name__})"
    for name in ["on_schedule", "knowable"]:
        if look_ahead[name] is not None:
            print(f"\n{name}:\n" + performance_table(look_ahead[name]).to_string(index=False))

    if args.report:
        args.report.write_text(build_report(latest, earlier, args.source, datetime.now(timezone.utc), all_markets, look_ahead))
        print(f"\nReport written to {args.report}")
