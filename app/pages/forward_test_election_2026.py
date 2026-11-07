"""Forward Test — Election 2026: the Group A paper positions recorded with real order-book quotes.

Reads only the results bundle written by src/analysis/forward_test_results.py (docs/data/forward_test_election_2026/),
never a database, so rendering is read-only by construction. Same dark look as the calibration page.
"""
import importlib
import json
import os
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# Same reason as in app/dashboard.py: Streamlit keeps imported modules between runs, so reload ours
for module_name in ["load.postgres", "analysis.calibration"]:
    importlib.reload(importlib.import_module(module_name))

from analysis.calibration import THEMES  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = Path(os.environ.get("FORWARD_TEST_RESULTS_DIR",
                                  PROJECT_ROOT / "docs" / "data" / "forward_test_election_2026"))
REPORT_URL = ("https://github.com/karakusethem09/prediction-markets/blob/main/docs/"
              "election_2026_forward_test_results.md")
AUDIT_URL = "https://github.com/karakusethem09/prediction-markets/blob/main/docs/election_2026_data_audit.md"
C = THEMES["dark"]
VERDICT_AT = 100  # the spec's decision rule applies once this many Group A positions have settled
DATES = {"positions.csv": ["recorded_at", "scheduled_end", "official_close_time", "exchange_time"],
         "runs.csv": ["started_at", "finished_at"]}

st.set_page_config(page_title="Forward Test — Election 2026", layout="wide")
# As on the calibration page: equal-width digits, and number cards two per row on a phone
st.markdown("""
<style>
[data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; }
@media (max-width: 640px) {
  div[data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) { flex-wrap: wrap; gap: 0.5rem; }
  div[data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) > div {
    flex: 1 1 calc(50% - 0.5rem) !important; min-width: calc(50% - 0.5rem) !important; }
}
</style>
""", unsafe_allow_html=True)


@st.cache_data
def load_bundle(folder, modified):  # `modified` makes a new bundle replace the cached one
    folder = Path(folder)
    if not (folder / "summary.json").exists():
        return None
    tables = {}
    for name in ["positions.csv", "performance.csv", "comparison.csv", "subgroups.csv", "runs.csv", "pilot_daily.csv"]:
        try:
            tables[name] = pd.read_csv(folder / name)
        except (FileNotFoundError, pd.errors.EmptyDataError):
            tables[name] = pd.DataFrame()
        for column in DATES.get(name, []):
            if column in tables[name]:
                tables[name][column] = pd.to_datetime(tables[name][column], utc=True, format="ISO8601")
    return {"summary": json.loads((folder / "summary.json").read_text()), **tables}


def pct(value, digits=1):
    return "n/a" if pd.isna(value) else f"{value * 100:.{digits}f}%"


def layout(fig, height=320, showlegend=False, margin=None, **kwargs):
    """The calibration chart's dark look: recessive grid, muted axes, the page's surface."""
    fig.update_layout(height=height, margin=margin or dict(l=20, r=20, t=30, b=50), paper_bgcolor=C["surface"],
                      plot_bgcolor=C["surface"], showlegend=showlegend,
                      font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif", color=C["text"], size=13),
                      hoverlabel=dict(bgcolor=C["hover_bg"], font_color=C["text"]), **kwargs)
    fig.update_xaxes(gridcolor=C["grid"], linecolor=C["axis"], tickfont=dict(color=C["muted"]),
                     title_font=dict(color=C["text_secondary"]), zeroline=False, automargin=True)
    fig.update_yaxes(gridcolor=C["grid"], linecolor=C["axis"], tickfont=dict(color=C["muted"]),
                     title_font=dict(color=C["text_secondary"]), zeroline=False, automargin=True)
    return fig


def timeline_figure(runs):
    """Markets recorded by each collector run, at the moment the run started."""
    ok = runs[runs["status"] == "success"]
    fig = go.Figure(go.Bar(x=ok["started_at"], y=ok["entered"], marker=dict(color=C["series"]), width=3 * 3600e3,
                           customdata=ok[["run_id"]],
                           hovertemplate="Run %{customdata[0]}<br>%{x|%Y-%m-%d %H:%M} UTC<br>%{y} markets recorded"
                                         "<extra></extra>"))
    election_day = pd.Timestamp("2026-11-03", tz="UTC")
    fig.add_vline(x=election_day, line=dict(color=C["muted"], width=1, dash="dot"))
    fig.add_annotation(x=election_day, y=1, yref="paper", text="Nov 3", showarrow=False, xanchor="left",
                       font=dict(color=C["text_secondary"], size=12))
    return layout(fig, xaxis_title="Run start (UTC)", yaxis_title="Markets recorded")


def roi_figure(performance):
    """Gross ROI per sizing scenario with its 95% event-resampling range (settled positions only)."""
    p = performance[performance["settled"] > 0]
    labels = [f"{s}<br>n = {n} settled" for s, n in zip(p["scenario"], p["settled"])]
    fig = go.Figure(go.Scatter(
        x=labels, y=p["gross_roi"], mode="markers+text", marker=dict(size=11, color=C["series"],
                                                                    line=dict(color=C["surface"], width=2)),
        text=[pct(v) for v in p["gross_roi"]], textposition="middle right", cliponaxis=False,
        textfont=dict(color=C["text"], size=12),
        error_y=dict(type="data", symmetric=False, array=p["gross_roi_high"] - p["gross_roi"],
                     arrayminus=p["gross_roi"] - p["gross_roi_low"], color=C["series"], thickness=1.5, width=0),
        hovertemplate="%{x}<br>Gross ROI %{y:.1%}<extra></extra>"))
    fig.add_hline(y=0, line=dict(color=C["muted"], width=1))
    # Room on the right for the last point's label (on a phone it would be cut off)
    return layout(fig, yaxis_title="Gross ROI", yaxis_tickformat=".0%", xaxis_range=[-0.4, len(p) - 0.1])


def comparison_figure(one, historical):
    """Win rate (dot, with its event-resampling range) against the average price paid (the break-even)."""
    rows = [f"Forward test<br>n = {int(one['settled'])} settled", f"Historical study<br>n = {historical['markets']}"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        y=rows[:1], x=[one["win_rate"]], mode="markers", name="Win rate (share of positions that won)",
        marker=dict(size=12, color=C["series"], line=dict(color=C["surface"], width=2)),
        error_x=dict(type="data", symmetric=False, array=[one["win_rate_high"] - one["win_rate"]],
                     arrayminus=[one["win_rate"] - one["win_rate_low"]], color=C["series"], thickness=1.5, width=0),
        hovertemplate="Win rate %{x:.1%}<extra></extra>"))
    fig.add_trace(go.Scatter(
        y=rows[1:], x=[historical["win_rate"]], mode="markers", name="Win rate", showlegend=False,
        marker=dict(size=12, color=C["series"], line=dict(color=C["surface"], width=2)),
        hovertemplate="Win rate %{x:.1%}<extra></extra>"))
    fig.add_trace(go.Scatter(
        y=rows, x=[one["avg_price"], historical["avg_price"]], mode="markers+text", name="Average price paid",
        marker=dict(symbol="line-ns-open", size=22, color=C["muted"], line=dict(width=3)),
        text=["break-even", "break-even"], textposition="top center", textfont=dict(color=C["text_secondary"], size=11),
        cliponaxis=False,  # the top row's label sits above the plot area
        hovertemplate="Average price paid %{x:.3f} (break-even win rate)<extra></extra>"))
    layout(fig, height=280, showlegend=True, xaxis_tickformat=".0%", xaxis_range=[0, 0.6],
           margin=dict(l=20, r=20, t=50, b=40))
    fig.update_layout(legend=dict(orientation="h", y=-0.2, font=dict(color=C["text_secondary"])))
    return fig


def slippage_figure(positions):
    """How much more than the best ask a $1,000 order paid per contract, walking up the recorded book."""
    p = positions.dropna(subset=["fill_price_1000"]).assign(
        extra=lambda d: (d["fill_price_1000"] - d["best_ask"]) * 100).sort_values("extra")
    names = p["question"].map(lambda q: q if len(q) <= 45 else q[:44] + "…")
    fig = go.Figure(go.Bar(x=p["extra"], y=names, orientation="h",
                           marker=dict(color=C["series"]), customdata=p[["best_ask", "fill_price_1000"]],
                           hovertemplate="%{y}<br>Best ask %{customdata[0]:.3f}, $1,000 average "
                                         "%{customdata[1]:.4f}<br>+%{x:.2f} cents per contract<extra></extra>"))
    layout(fig, height=40 + 24 * len(p), xaxis_title="Cents per contract above the best ask ($1,000 order)")
    fig.update_yaxes(tickfont=dict(size=11, color=C["text_secondary"]), gridcolor=C["surface"])
    return fig


st.title("Forward Test — Election 2026")
st.markdown("Does the historical **Group A** pattern hold with prices a buyer could really pay? Paper positions "
            "recorded with live order-book quotes at each market's 7-day mark, held until the market officially "
            "settles. **Paper trades only:** no orders, no account, no wallet.")

folder = RESULTS_DIR
summary_file = folder / "summary.json"
bundle = load_bundle(str(folder), summary_file.stat().st_mtime if summary_file.exists() else 0)
if bundle is None or bundle["positions.csv"].empty:
    st.info("No verified prospective observations available.", icon="ℹ️")
    st.markdown(f"Methodology and data audit: [results report]({REPORT_URL}) · [data audit]({AUDIT_URL})")
    st.stop()

summary, positions, performance = bundle["summary"], bundle["positions.csv"], bundle["performance.csv"]
audit = summary["audit"]
one = performance.set_index("scenario").loc["1 contract"]
settled = int(one["settled"])
st.caption(f"As of {pd.Timestamp(summary['as_of_utc']):%Y-%m-%d %H:%M} UTC · official results matched by outcome "
           "token · every number computed from the recorded data")
final = settled >= VERDICT_AT  # the spec's decision rule can be applied
st.info(f"**Research verdict: {summary['verdict']}**" + ("" if final else
        " Until then, results are provisional and descriptive."))

# 1. Overview
st.subheader("Overview")
cards = st.columns(6)
cards[0].metric("Group A positions", f"{len(positions)}", help="One contract each, best ask in [0.20, 0.30)")
cards[1].metric("Officially settled", f"{settled}")
cards[2].metric("Unresolved", f"{int(one['open'])}", help="Not counted as wins or losses")
cards[3].metric("Won / lost", f"{int(one['wins'])} / {int(one['losses'])}" if settled else "n/a")
cards[4].metric("Collector runs", f"{audit['online_success']}", help="Successful runs of the online collector")
cards[5].metric("Markets quoted", f"{audit['recorded_with_quote']:,}",
                help=f"Recorded with a real order-book quote at their 7-day mark ({audit['recorded_without_quote']} more "
                     f"had an empty book; {audit['entry_status'].get('missed', 0)} carry a 'missed' label)")

# 2. Collection timeline
st.subheader("Collection timeline")
st.plotly_chart(timeline_figure(bundle["runs.csv"]), theme=None, width="stretch")
st.caption(f"Each bar is one online collector run ({audit['online_success']} runs, "
           f"{pd.Timestamp(audit['first_success']):%b %d} – {pd.Timestamp(audit['last_success']):%b %d} UTC). "
           + (f"Scheduled every 3 hours, GitHub started them a median of {audit['median_gap_hours']:.1f} hours apart."
              if audit.get("median_gap_hours") is not None else ""))
if not bundle["pilot_daily.csv"].empty:
    with st.expander("Election pilot: observations collected on a local computer (no trades)"):
        st.dataframe(bundle["pilot_daily.csv"], hide_index=True)
        st.caption("Observed every 10 minutes while the computer was on; these never reached the online database "
                   "and have no paper trades by design.")

# 3. Performance
st.subheader("Performance by position size")
if settled:
    st.plotly_chart(roi_figure(performance), theme=None, width="stretch")
    st.caption("Gross ROI = total profit / total capital of the officially settled positions. Bars: 95% range from "
               "resampling whole events. Fees are not included: no verified fee model is available.")
else:
    st.markdown("No position has officially settled yet, so no realized result is shown.")
st.dataframe(
    performance.assign(win_rate=performance["win_rate"] * 100, gross_roi=performance["gross_roi"] * 100)
    [["scenario", "fillable", "unfillable", "settled", "open", "wins", "win_rate", "avg_price",
      "invested", "payout", "gross_profit", "gross_roi", "open_capital", "max_open_capital"]],
    column_config={
        "scenario": "Scenario", "fillable": "Fillable", "unfillable": "Unfillable", "settled": "Settled",
        "open": "Unresolved", "wins": "Wins",
        "win_rate": st.column_config.NumberColumn("Win rate", format="%.1f%%"),
        "avg_price": st.column_config.NumberColumn("Avg price paid", format="%.4f"),
        "invested": st.column_config.NumberColumn("Invested (settled)", format="dollar"),
        "payout": st.column_config.NumberColumn("Payout", format="dollar"),
        "gross_profit": st.column_config.NumberColumn("Gross P/L", format="dollar"),
        "gross_roi": st.column_config.NumberColumn("Gross ROI", format="%.1f%%"),
        "open_capital": st.column_config.NumberColumn("Capital still open", format="dollar"),
        "max_open_capital": st.column_config.NumberColumn("Most capital open at once", format="dollar"),
    }, hide_index=True)

# 4. Group A versus the historical hypothesis
st.subheader("Group A versus the historical study")
if settled:
    st.plotly_chart(comparison_figure(one, summary["historical"]), theme=None, width="stretch")
    st.caption("A position makes money on average only if the win rate is above the average price paid. The "
               "historical study used recorded prices chosen after looking at the history: context, not a benchmark "
               "proven to be tradable. Its 95% range for ROI was "
               f"{pct(summary['historical']['roi_low'])} to {pct(summary['historical']['roi_high'])}.")
def shown_value(measure, value):
    """Rates as %, the edge in points, prices as prices, counts as whole numbers."""
    if measure.startswith("Edge"):
        return f"{value * 100:+.1f} pts"
    if measure.startswith("Average"):
        return f"{value:.3f}"
    return pct(value) if measure.startswith(("Win", "Gross")) else f"{int(value)}"


comparison = bundle["comparison.csv"]
if not comparison.empty:
    st.dataframe(pd.DataFrame({
        "Measure": comparison["measure"],
        "Forward test": [shown_value(m, v) for m, v in zip(comparison["measure"], comparison["forward_test"])],
        "Historical (retrospective)": [shown_value(m, v) for m, v in zip(comparison["measure"], comparison["historical"])],
    }), hide_index=True)

# 5. Spread and execution price
st.subheader("Spread and execution price")
spread_cols = st.columns(4)
spread_cols[0].metric("Median spread at entry", f"{positions['spread'].median() * 100:.1f}¢")
spread_cols[1].metric("Best ask above midpoint", f"{(positions['best_ask'] - positions['midpoint']).mean() * 100:.1f}¢",
                      help="Average over positions: the buying cost the historical study couldn't see")
for col, scenario in zip(spread_cols[2:], ["$100", "$1,000"]):
    row = performance.set_index("scenario").loc[scenario]
    col.metric(f"{scenario} orders fillable", f"{int(row['fillable'])} of {int(row['positions'])}",
               help="The recorded book held enough contracts to spend the full amount")
st.plotly_chart(slippage_figure(positions), theme=None, width="stretch")

# 6. Individual markets
st.subheader("Individual positions")
query = st.text_input("Search by market or event", placeholder="e.g. governor, Senate, Open")
shown = positions
if query:
    text = (positions["question"].fillna("") + " " + positions["event_title"].fillna("")).str.lower()
    shown = positions[text.str.contains(query.lower(), regex=False)]
shown = shown.assign(result=[("won" if o == 1 else "lost") if s == "settled" else s
                             for s, o in zip(shown["status"], shown["outcome"])])
st.dataframe(
    shown[["recorded_at", "question", "category", "outcome_name", "best_bid", "best_ask", "fill_price_100",
           "fill_price_1000", "volume_at_entry", "result", "raw_book"]],
    column_config={
        "recorded_at": st.column_config.DatetimeColumn("Entry (UTC)", format="YYYY-MM-DD HH:mm"),
        "question": st.column_config.TextColumn("Market", width="medium"), "category": "Category",
        "outcome_name": "Outcome bought", "best_bid": st.column_config.NumberColumn("Bid", format="%.3f"),
        "best_ask": st.column_config.NumberColumn("Ask", format="%.3f"),
        "fill_price_100": st.column_config.NumberColumn("$100 avg", format="%.4f"),
        "fill_price_1000": st.column_config.NumberColumn("$1,000 avg", format="%.4f"),
        "volume_at_entry": st.column_config.NumberColumn("Volume at entry", format="dollar"),
        "result": "Official result", "raw_book": "Raw order book"},
    hide_index=True)
st.caption(f"{len(shown)} of {len(positions)} positions. Category comes from the event's tags (exploratory).")

# 7. Data quality and missed opportunities
st.subheader("Data quality")
raw = audit["raw_books"]
quality = pd.DataFrame([
    ("Quotes re-derived exactly from the raw order book", f"{raw.get('verified', 0)} of {len(positions)}"),
    ("Raw order books expired before the audit", f"{raw.get('expired before the audit', 0)}"),
    ("Bought on the market's first-listed outcome token", f"{audit['first_listed_token']} of {len(positions)}"),
    ("Stored result agrees with the official per-token result", f"{audit['stored_agrees']} of {len(positions)}"),
    ("Missed 7-day opportunities (collector absent)", f"{audit['missed_confirmed']} confirmed of "
                                                      f"{audit['missed_labels']} stored labels"),
    ("Recorded markets with an empty book (no quote)", f"{audit['recorded_without_quote']}"),
    ("Positions meeting the Group A rules when re-checked", f"{audit['meets_rules']} of {len(positions)}"),
    ("Positions without a usable official result", f"{int(one['no_official_result'])}"),
    ("Scheduled cron slots without a run", f"{max(audit['cron_slots'] - audit['github_scheduled_runs'], 0)} "
                                           f"of {audit['cron_slots']}"),
], columns=["Check", "Result"])
st.dataframe(quality, hide_index=True, width="stretch")

# 8. Limitations, method, downloads
st.subheader("Limitations and method")
st.markdown(
    (f"- **Fewer than {VERDICT_AT} settled positions:** the verdict rule fixed in advance can't be applied yet; "
     f"intervals are wide, and {int((positions['category'] == 'Elections').sum())} of {len(positions)} positions are "
     "election markets, whose outcomes are related.\n" if not final else "")
    + "- **Paper fills:** the prices were real quotes, but nobody bought at them; one contract is below the exchange's "
    "minimum order size, and larger orders could have moved the price.\n"
    "- **Fees** are not included (no verified fee model). **Exploratory** categories are not tested strategies.\n"
    f"- Full method, tables and caveats: [results report]({REPORT_URL}) · [data audit]({AUDIT_URL})")
downloads = st.columns(3)
for col, name, label in zip(downloads, ["performance.csv", "comparison.csv", "positions.csv"],
                            ["Results by scenario (CSV)", "Comparison with history (CSV)", "Positions (CSV)"]):
    col.download_button(label, (folder / name).read_bytes(), file_name=f"forward_test_{name}", mime="text/csv")
