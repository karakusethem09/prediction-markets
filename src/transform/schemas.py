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
