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

# Chart colors (light theme): one series color, recessive gray chrome
SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
MUTED = "#898781"
GRIDLINE = "#e1e0d9"
SERIES = "#2a78d6"


def load_calibration_data(database_url=DATABASE_URL):
    """One row per snapshot, with the market's actual outcome."""
    # "postgresql+psycopg" tells SQLAlchemy to use the psycopg driver we installed
    engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://", 1))
    sql = """
        SELECT s.market_id, s.horizon_days, s.price, m.outcome, m.event_id, m.volume_usd,
               m.platform, m.close_time
        FROM snapshots s
        JOIN markets m USING (market_id)
    """
    return pd.read_sql(sql, engine)


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


def calibration_figure(table):
    """One calibration chart per horizon, side by side, against the perfect-calibration diagonal."""
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
            x=[0, 1], y=[0, 1], mode="lines", line=dict(color=MUTED, width=1),
            hoverinfo="skip", showlegend=False,
        ), row=1, col=col)
        fig.add_trace(go.Scatter(
            x=rows["avg_price"], y=rows["happened_rate"], mode="lines+markers",
            line=dict(color=SERIES, width=2),
            marker=dict(size=8, color=SERIES, line=dict(color=SURFACE, width=2)),
            error_y=dict(type="data", symmetric=False, color=SERIES, thickness=1, width=0,
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
                       arrowcolor=MUTED, font=dict(color=TEXT_SECONDARY, size=12), row=1, col=1)

    axis = dict(range=[0, 1], tickformat=".0%", dtick=0.2, gridcolor=GRIDLINE, gridwidth=1,
                zeroline=False, showline=True, linecolor="#c3c2b7", ticks="",
                tickfont=dict(color=MUTED))
    fig.update_xaxes(**axis, title_text="Market price", title_font=dict(color=TEXT_SECONDARY))
    fig.update_yaxes(**axis)
    fig.update_yaxes(title_text="How often it happened", title_font=dict(color=TEXT_SECONDARY), row=1, col=1)
    fig.update_layout(
        height=420, margin=dict(l=70, r=20, t=50, b=60),
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
        font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif", color=TEXT_PRIMARY, size=13),
        hoverlabel=dict(bgcolor="white", font_color=TEXT_PRIMARY),
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
