"""Calibration dashboard: when a market's price says X%, does it happen X% of the time?

Run: streamlit run app/dashboard.py   (needs the database: docker compose up -d)

Dark "trading terminal" look (colors in .streamlit/config.toml) and phone-friendly: one chart
at a time, picked with 1D / 7D / 30D buttons. Uses only the markets, snapshots and
pipeline_runs tables. Platform names come from the data, never from this code.
"""
import html

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from analysis.calibration import (
    calibration_figure, calibration_table, describe_age, filter_snapshots, load_calibration_data,
    load_pipeline_status, recent_markets, same_markets_only, scores,
)
from load.postgres import DATABASE_URL

VOLUME_STEPS = [1_000_000, 2_000_000, 5_000_000, 10_000_000, 25_000_000, 50_000_000]
HORIZON_BUTTONS = {"1D": 1, "7D": 7, "30D": 30}
STATUS_GOOD, STATUS_CRITICAL = "#0ca30c", "#d03b3b"  # reserved for pipeline health, always shown with a label

st.set_page_config(page_title="Prediction market calibration", layout="wide")

# A little CSS on top of the theme: equal-width digits in the number cards (so they line up
# like a price board) and the style of the status pill
st.markdown("""
<style>
[data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; }
.status-pill { display: inline-flex; align-items: center; gap: 0.5rem; padding: 0.25rem 0.75rem;
               border: 1px solid rgba(255,255,255,0.10); border-radius: 999px; background: #1a1a19;
               font-size: 0.85rem; color: #c3c2b7; }
.status-pill b { color: #f0efec; letter-spacing: 0.05em; }
.status-dot { width: 0.55rem; height: 0.55rem; border-radius: 50%; }
/* On a phone, Streamlit stacks every column; keep the number cards two per row instead */
@media (max-width: 640px) {
  div[data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) { flex-wrap: wrap; gap: 0.5rem; }
  div[data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) > div {
    flex: 1 1 calc(50% - 0.5rem) !important; min-width: calc(50% - 0.5rem) !important; }
}
</style>
""", unsafe_allow_html=True)


def database_url():
    # Online (Streamlit Cloud), the read-only database address is stored as a secret.
    # On your own computer there's no secrets file, so use the local database.
    try:
        return st.secrets["DATABASE_URL"]
    except (StreamlitSecretNotFoundError, KeyError):
        return DATABASE_URL


@st.cache_data(ttl=3600)  # read the database at most once an hour, not on every click
def get_data():
    return load_calibration_data(database_url())


@st.cache_data(ttl=600)  # the "last updated" line is cheap, so refresh it more often
def get_status():
    return load_pipeline_status(database_url())


def status_pill(status):
    """'● LIVE · updated 2 hours ago · 36 new markets', or a red warning if the last run failed."""
    if status["last_success_at"] is None:
        return ""
    age = describe_age(status["last_success_at"], pd.Timestamp.now(tz="UTC"))
    added = (status["last_success_rows"] or {}).get("markets")
    if status["last_run_failed"]:
        color, label, text = STATUS_CRITICAL, "UPDATE FAILED", f"showing data from {age}"
    else:
        color, label = STATUS_GOOD, "LIVE"
        text = f"updated {age}" + ("" if added is None else f" · {added:,} new markets" if added else " · no new markets")
    return (f'<span class="status-pill"><span class="status-dot" style="background:{color}"></span>'
            f'<b>{label}</b> {html.escape(text)}</span>')


data = get_data()

st.title("Are prediction market prices calibrated?")
st.markdown(status_pill(get_status()), unsafe_allow_html=True)
st.markdown(
    "When a market's price says **70%**, does the event happen about **70%** of the time? "
    "Each dot groups markets with similar prices. On the diagonal, prices were honest "
    "probabilities; above it, things happened more often than the price said. "
    "Updated automatically every day."
)

# Filters, in one row (Streamlit stacks them on a phone)
platform_col, volume_col, years_col, same_col = st.columns(4)
platform_options = sorted(data["platform"].unique())
platforms = platform_col.multiselect(
    "Platform", platform_options, default=platform_options, format_func=str.title
)
min_volume = volume_col.select_slider(
    "Minimum trading volume", VOLUME_STEPS, format_func=lambda v: f"${v / 1e6:,.0f}M"
)
first_year, last_year = int(data["close_time"].dt.year.min()), int(data["close_time"].dt.year.max())
close_years = years_col.slider("Market closed between", first_year, last_year, (first_year, last_year))
same_only = same_col.toggle(
    "Same markets at every horizon",
    value=True,  # on by default: otherwise the horizons compare different sets of markets
    help="Compare 1, 7 and 30 days on exactly the same markets. Switch off to see every market "
         "at each horizon, including short ones (e.g. sports games) that weren't open 30 days.",
)

all_filtered = filter_snapshots(data, platforms, min_volume, close_years)
filtered = same_markets_only(all_filtered) if same_only else all_filtered
if filtered.empty:
    st.warning("No markets match these filters. Try a lower volume or a wider year range.")
    st.stop()

# Headline numbers, as bordered cards
horizon_scores = scores(filtered).set_index("horizon_days")
tiles = st.columns(1 + len(horizon_scores))
tiles[0].metric("Markets", f"{filtered['market_id'].nunique():,}",
                help=f"In {filtered['event_id'].nunique():,} events", border=True)
for tile, (horizon, row) in zip(tiles[1:], horizon_scores.iterrows()):
    tile.metric(
        f"Avg. gap · {horizon}D",
        f"{row['calibration_error'] * 100:.1f} pts",
        help=f"{horizon} day{'s' if horizon > 1 else ''} before close: the average distance between the "
             "price and how often things happened, in percentage points, weighted by how many markets "
             "are at each price. 0 = perfect.",
        border=True,
    )

# One chart at a time: readable on a phone, and like switching timeframes on a trading chart
picked = st.segmented_control("Time before close", list(HORIZON_BUTTONS), default="1D")
horizon = HORIZON_BUTTONS[picked or "1D"]  # clicking the selected button again un-selects it
table = calibration_table(filtered)
# theme=None keeps the chart's own colors instead of Streamlit's default chart theme
st.plotly_chart(calibration_figure(table[table["horizon_days"] == horizon], theme="dark"),
                theme=None, width="stretch")
st.caption("Vertical bars are 95% confidence intervals: groups with fewer markets have longer bars.")

# The newest markets the daily pipeline added, like a feed of settled trades
st.subheader("Recently resolved")
recent = recent_markets(all_filtered)  # all markets, so short ones without a 30-day price appear too
recent["result"] = recent["outcome"].map({1: "✓", 0: "✗"})  # short, so it fits on a phone screen
price_columns = ["price_1d", "price_7d", "price_30d"]
recent[price_columns] = recent[price_columns] * 100  # as percentages, so they show as e.g. 48.0%
price_column = lambda label: st.column_config.ProgressColumn(
    label, min_value=0, max_value=100, format="%.1f%%", width="small")
st.dataframe(
    # Market, result and 1D first: on a phone those fit on screen without scrolling sideways
    recent[["question", "result", "price_1d", "price_7d", "price_30d", "close_time"]],
    height=36 * (len(recent) + 1) + 2,  # tall enough for every row, so the list doesn't scroll
    column_config={
        "question": st.column_config.TextColumn("Market", width="medium"),
        "close_time": st.column_config.DatetimeColumn("Closed", format="MMM D, YYYY"),
        "price_30d": price_column("30D"),
        "price_7d": price_column("7D"),
        "price_1d": price_column("1D"),
        "result": st.column_config.TextColumn(
            "Happened?", width="small", help="✓ = the first listed outcome (e.g. Yes) happened, ✗ = it didn't"),
    },
    hide_index=True,
)
st.caption("Price of the first listed outcome (e.g. Yes) 30, 7 and 1 day before the market closed. "
           "Empty when the market wasn't open that long.")

with st.expander("Table view"):
    st.dataframe(
        table.assign(avg_price=table["avg_price"] * 100, happened_rate=table["happened_rate"] * 100)
        [["horizon_days", "bin_label", "markets", "avg_price", "happened_rate"]],
        column_config={
            "horizon_days": "Days before close",
            "bin_label": "Price group",
            "markets": st.column_config.NumberColumn("Markets", format="%d"),
            "avg_price": st.column_config.NumberColumn("Average price", format="%.1f%%"),
            "happened_rate": st.column_config.NumberColumn("Happened", format="%.1f%%"),
        },
        hide_index=True,
    )

with st.expander("Accuracy scores"):
    st.dataframe(
        horizon_scores[["markets", "events", "brier", "brier_skill"]].reset_index(),
        column_config={
            "horizon_days": "Days before close",
            "brier": st.column_config.NumberColumn("Brier score", help="0 = perfect, lower is better", format="%.3f"),
            "brier_skill": st.column_config.NumberColumn(
                "Brier skill", help="1 = perfect, 0 = no better than always guessing the base rate", format="%.2f"),
        },
        hide_index=True,
    )

st.markdown(
    "**Notes.** Only resolved markets with at least $1M volume, open at least a day. "
    "Prices are taken 1, 7 and 30 days before each market closed, never the final price. "
    "\"Happened\" refers to the first listed outcome (e.g. *Yes*). Markets in the same event "
    "are related, so the true uncertainty is a little wider than the bars show."
)
