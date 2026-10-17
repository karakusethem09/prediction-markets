"""Pandera schemas: the shape every platform's normalized tables must have.

Validating against these makes bad data fail loudly instead of quietly skewing the analysis.
"""
import pandas as pd
import pandera.pandas as pa

# All times are UTC, stored in microseconds (pandas 3's default when parsing dates)
UTC_TIME = pd.DatetimeTZDtype("us", "UTC")

markets_schema = pa.DataFrameSchema(
    {
        "market_id": pa.Column(str, unique=True),  # "<platform prefix>:<id>", e.g. "poly:123"
        "platform": pa.Column(str),
        "event_id": pa.Column(str, nullable=True),
        "question": pa.Column(str),
        "category": pa.Column(str, nullable=True),
        "open_time": pa.Column(UTC_TIME),
        "close_time": pa.Column(UTC_TIME),
        "outcome": pa.Column(int, pa.Check.isin([0, 1])),  # 1 = the first outcome happened
        "volume_usd": pa.Column(float, pa.Check.ge(0)),
    },
    checks=pa.Check(lambda df: df["close_time"] >= df["open_time"], error="market closes before it opens"),
    strict=True,  # no extra columns: every platform must produce exactly these
)

prices_schema = pa.DataFrameSchema(
    {
        "market_id": pa.Column(str),
        "time": pa.Column(UTC_TIME),
        "price": pa.Column(float, pa.Check.in_range(0, 1)),  # price of the first outcome
        "fidelity": pa.Column(str, pa.Check.isin(["daily", "hourly"])),
    },
    strict=True,
)

snapshots_schema = pa.DataFrameSchema(
    {
        "market_id": pa.Column(str),
        "horizon_days": pa.Column(int, pa.Check.isin([1, 7, 30])),
        "target_time": pa.Column(UTC_TIME),  # close_time minus the horizon
        "price_time": pa.Column(UTC_TIME),  # when the price we used was recorded
        "price": pa.Column(float, pa.Check.in_range(0, 1)),
    },
    checks=[
        # The core rule: never use a price from after the snapshot moment
        pa.Check(lambda df: df["price_time"] <= df["target_time"], error="price from the future"),
        pa.Check(lambda df: df["target_time"] - df["price_time"] <= pd.Timedelta(days=1),
                 error="price more than 1 day old"),
    ],
    unique=["market_id", "horizon_days"],  # one snapshot per market per horizon
    strict=True,
)
