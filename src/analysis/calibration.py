"""Calibration analysis: when the market price says X%, does the event happen X% of the time?

Works on any platform's data: it only uses the snapshots and markets tables.
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from sqlalchemy import create_engine

from load.postgres import DATABASE_URL

N_BINS = 10  # price buckets of 10 percentage points: 0-10%, 10-20%, ..., 90-100%
Z_95 = 1.96  # z-value for a 95% confidence interval

# Chart colors: one series color and recessive gray chrome. The light theme is used for the
# README image and the notebook; the dark one matches the dashboard. Same hues, each stepped
# for its own background so the contrast holds.
THEMES = {
    "light": {"surface": "#fcfcfb", "text": "#0b0b0b", "text_secondary": "#52514e", "muted": "#898781",
              "grid": "#e1e0d9", "axis": "#c3c2b7", "series": "#2a78d6", "hover_bg": "#ffffff"},
    "dark": {"surface": "#1a1a19", "text": "#f0efec", "text_secondary": "#c3c2b7", "muted": "#898781",
             "grid": "#2c2c2a", "axis": "#383835", "series": "#3987e5", "hover_bg": "#0d0d0d"},
}


def read_sql(database_url, *queries):
    """Run read-only queries and return one DataFrame per query, closing the connections afterwards."""
    # "postgresql+psycopg" tells SQLAlchemy to use the psycopg driver we installed
    engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        with engine.connect() as conn:
            return [pd.read_sql(query, conn) for query in queries]
    finally:
        engine.dispose()  # otherwise the engine keeps its pool of connections open


def load_calibration_data(database_url=DATABASE_URL):
    """One row per snapshot, with the market's actual outcome."""
    sql = """
        SELECT s.market_id, s.horizon_days, s.price, m.outcome, m.event_id, m.volume_usd,
               m.platform, m.close_time, m.question
        FROM snapshots s
        JOIN markets m USING (market_id)
    """
    return read_sql(database_url, sql)[0]


STALE_AFTER = pd.Timedelta(hours=36)  # the update runs daily, so 36 h without one means it missed a day


def load_pipeline_status(database_url=DATABASE_URL, history=10):
    """When the data was last updated, whether the latest run failed, and the last `history` runs."""
    last_success, runs = read_sql(
        database_url,
        "SELECT finished_at, row_counts FROM pipeline_runs WHERE status = 'success' "
        "ORDER BY finished_at DESC LIMIT 1",
        "SELECT started_at, status, row_counts->>'markets' AS new_markets, error "
        f"FROM pipeline_runs ORDER BY started_at DESC LIMIT {int(history)}",
    )
    return {
        "last_success_at": last_success["finished_at"].iloc[0] if len(last_success) else None,
        "last_success_rows": last_success["row_counts"].iloc[0] if len(last_success) else None,
        "last_run_failed": bool(len(runs)) and runs["status"].iloc[0] == "failed",
        "recent_runs": runs,
    }


def freshness(status, now, stale_after=STALE_AFTER):
    """How trustworthy the data's freshness is: a state, a short label and a sentence for visitors.

    Checked in this order, first match wins:
      failed   the most recent run failed
      stale    no successful update for longer than stale_after (e.g. the daily job stopped)
      live     the last successful update is recent
      no_data  no successful update was ever recorded
    """
    last_success = status["last_success_at"]
    age = describe_age(last_success, now) if last_success is not None else None
    if status["last_run_failed"]:
        detail = (f"The most recent update failed. The data is from the last successful update, {age}."
                  if age else "The most recent update failed, and no update has succeeded yet.")
        return {"state": "failed", "label": "UPDATE FAILED", "detail": detail}
    if last_success is None:
        return {"state": "no_data", "label": "NO UPDATES YET", "detail": "No update has been recorded yet."}
    if now - last_success > stale_after:
        return {"state": "stale", "label": "STALE",
                "detail": f"No successful update for {age.removesuffix(' ago')}. Updates run daily, "
                          "so the scheduled job has probably stopped."}
    added = (status["last_success_rows"] or {}).get("markets")
    new = "" if added is None else f" · {added:,} new markets" if added else " · no new markets"
    return {"state": "live", "label": "LIVE", "detail": f"updated {age}{new}"}


def recent_markets(data, n=15):
    """The n most recently closed markets, one row each, with their price at every horizon.

    A horizon the market had no snapshot for (e.g. it was open less than 30 days) is left empty.
    """
    prices = data.pivot_table(index="market_id", columns="horizon_days", values="price")
    prices.columns = [f"price_{h}d" for h in prices.columns]
    info = data.drop_duplicates("market_id").set_index("market_id")[["question", "close_time", "outcome"]]
    return (info.join(prices).sort_values("close_time", ascending=False).head(n)
            .reset_index())


def describe_age(then, now):
    """A short, human description of how long ago something happened, e.g. '5 hours ago'."""
    seconds = (now - then).total_seconds()
    for unit, size in [("day", 86_400), ("hour", 3_600), ("minute", 60)]:
        if seconds >= size:
            count = int(seconds // size)
            return f"{count} {unit}{'s' if count > 1 else ''} ago"
    return "just now"


def filter_snapshots(data, platforms=None, min_volume=0, close_years=None):
    """Keep snapshots of markets on the given platforms, with at least min_volume,
    that closed within close_years (an inclusive (first, last) pair). None means no filter."""
    keep = data["volume_usd"] >= min_volume
    if platforms is not None:
        keep &= data["platform"].isin(platforms)
    if close_years is not None:
        keep &= data["close_time"].dt.year.between(*close_years)
    return data[keep]


def same_markets_only(data):
    """Keep only markets that have a snapshot at every horizon.

    Needed to compare horizons fairly: otherwise the 30-day group contains only
    long-running markets and the 1-day group also has short ones (e.g. sports games).
    """
    horizons_per_market = data.groupby("market_id")["horizon_days"].transform("nunique")
    return data[horizons_per_market == data["horizon_days"].nunique()]


def price_bin(prices, n_bins=N_BINS):
    """Bucket number for each price: 0 for 0-10%, 1 for 10-20%, ..., 9 for 90-100%.

    A price of exactly 1.0 goes in the top bucket instead of a bucket of its own.
    """
    return np.minimum((np.asarray(prices) * n_bins).astype(int), n_bins - 1)


def wilson_interval(happened, total, z=Z_95):
    """95% confidence interval for a rate, e.g. 6 happened out of 10.

    The Wilson interval stays sensible for small counts and for rates near 0 or 1,
    where the simpler "rate +/- 1.96 standard errors" can go below 0 or above 1.
    """
    happened, total = np.asarray(happened, dtype=float), np.asarray(total, dtype=float)
    rate = happened / total
    denominator = 1 + z**2 / total
    center = (rate + z**2 / (2 * total)) / denominator
    half_width = z * np.sqrt(rate * (1 - rate) / total + z**2 / (4 * total**2)) / denominator
    return center - half_width, center + half_width


def calibration_table(data, n_bins=N_BINS):
    """Per horizon and price bucket: how many markets, average price, and how often they happened."""
    data = data.assign(bin=price_bin(data["price"], n_bins))
    table = (
        data.groupby(["horizon_days", "bin"])
        .agg(markets=("outcome", "size"), happened=("outcome", "sum"), avg_price=("price", "mean"))
        .reset_index()
    )
    table["happened_rate"] = table["happened"] / table["markets"]
    table["ci_low"], table["ci_high"] = wilson_interval(table["happened"], table["markets"])
    table["bin_label"] = [f"{b * 100 // n_bins}–{(b + 1) * 100 // n_bins}%" for b in table["bin"]]
    return table


def scores(data):
    """Per horizon: Brier score, Brier skill score and calibration error.

    - Brier score: average of (price - outcome)^2. 0 is perfect; lower is better.
    - Brier skill score: how much better than always guessing the overall base rate
      (1 = perfect, 0 = no better than that naive guess).
    - Calibration error: average gap between price and how often things happened,
      weighted by how many markets are in each bucket (0 = perfectly calibrated).
    """
    rows = []
    for horizon, group in data.groupby("horizon_days"):
        brier = ((group["price"] - group["outcome"]) ** 2).mean()
        base_rate = group["outcome"].mean()
        naive_brier = ((base_rate - group["outcome"]) ** 2).mean()
        table = calibration_table(group)
        calibration_error = np.average(
            (table["avg_price"] - table["happened_rate"]).abs(), weights=table["markets"]
        )
        rows.append({
            "horizon_days": horizon,
            "markets": len(group),
            "events": group["event_id"].nunique(),
            "base_rate": base_rate,
            "brier": brier,
            "brier_skill": 1 - brier / naive_brier,
            "calibration_error": calibration_error,
        })
    return pd.DataFrame(rows)


def calibration_figure(table, theme="light"):
    """One calibration chart per horizon, side by side, against the perfect-calibration diagonal.

    Pass a table with a single horizon to get a single chart (the dashboard does this on phones).
    """
    c = THEMES[theme]
    horizons = sorted(table["horizon_days"].unique())
    titles = [
        f"{h} day{'s' if h > 1 else ''} before close · {table.loc[table.horizon_days == h, 'markets'].sum():,} markets"
        for h in horizons
    ]
    fig = make_subplots(rows=1, cols=len(horizons), shared_yaxes=True,
                        subplot_titles=titles, horizontal_spacing=0.04)

    for col, horizon in enumerate(horizons, start=1):
        rows = table[table["horizon_days"] == horizon]
        # Perfect calibration: things priced at X% happen X% of the time
        fig.add_trace(go.Scatter(
            x=[0, 1], y=[0, 1], mode="lines", line=dict(color=c["muted"], width=1),
            hoverinfo="skip", showlegend=False,
        ), row=1, col=col)
        fig.add_trace(go.Scatter(
            x=rows["avg_price"], y=rows["happened_rate"], mode="lines+markers",
            line=dict(color=c["series"], width=2),
            marker=dict(size=8, color=c["series"], line=dict(color=c["surface"], width=2)),
            error_y=dict(type="data", symmetric=False, color=c["series"], thickness=1, width=0,
                         array=rows["ci_high"] - rows["happened_rate"],
                         arrayminus=rows["happened_rate"] - rows["ci_low"]),
            customdata=np.column_stack([rows["bin_label"], rows["markets"], rows["ci_low"], rows["ci_high"]]),
            hovertemplate=(
                "Priced %{customdata[0]} (%{customdata[1]:,} markets)<br>"
                "Average price: %{x:.1%}<br>"
                "Happened: %{y:.1%} (95% CI %{customdata[2]:.1%}–%{customdata[3]:.1%})<extra></extra>"
            ),
            showlegend=False,
        ), row=1, col=col)

    # Label the diagonal once, directly, instead of a legend. The text sits in the
    # bottom-right corner (far below the diagonal), where data points don't go
    fig.add_annotation(x=0.8, y=0.8, ax=0.86, ay=0.42, axref="x", ayref="y",
                       text="Perfect calibration", showarrow=True, arrowhead=0, arrowwidth=1,
                       arrowcolor=c["muted"], font=dict(color=c["text_secondary"], size=12), row=1, col=1)

    axis = dict(range=[0, 1], tickformat=".0%", dtick=0.2, gridcolor=c["grid"], gridwidth=1,
                zeroline=False, showline=True, linecolor=c["axis"], ticks="",
                tickfont=dict(color=c["muted"]))
    fig.update_xaxes(**axis, title_text="Market price", title_font=dict(color=c["text_secondary"]))
    fig.update_yaxes(**axis)
    fig.update_yaxes(title_text="How often it happened", title_font=dict(color=c["text_secondary"]), row=1, col=1)
    fig.update_layout(
        height=420, margin=dict(l=70, r=20, t=50, b=60),
        paper_bgcolor=c["surface"], plot_bgcolor=c["surface"],
        font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif", color=c["text"], size=13),
        hoverlabel=dict(bgcolor=c["hover_bg"], font_color=c["text"]),
    )
    return fig


if __name__ == "__main__":
    data = load_calibration_data()
    table = calibration_table(data)
    pd.set_option("display.width", 140)
    print(scores(data).round(4).to_string(index=False))
    print()
    print(table.pivot(index="bin_label", columns="horizon_days", values=["markets", "avg_price", "happened_rate"])
          .round(3).to_string())
