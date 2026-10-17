-- Tables for the calibration project. Safe to run again: IF NOT EXISTS skips tables that exist.
-- Columns match src/transform/schemas.py; the CHECKs repeat its rules so bad rows can't get in.

CREATE TABLE IF NOT EXISTS markets (
    market_id   text PRIMARY KEY,               -- "poly:<id>"
    platform    text NOT NULL,
    event_id    text,
    question    text NOT NULL,
    category    text,
    open_time   timestamptz NOT NULL,
    close_time  timestamptz NOT NULL,
    outcome     smallint NOT NULL CHECK (outcome IN (0, 1)),  -- 1 = the first outcome happened
    volume_usd  double precision NOT NULL CHECK (volume_usd >= 0),
    CHECK (close_time >= open_time)
);

CREATE TABLE IF NOT EXISTS price_history (
    market_id  text NOT NULL REFERENCES markets (market_id),
    fidelity   text NOT NULL CHECK (fidelity IN ('daily', 'hourly')),
    time       timestamptz NOT NULL,
    price      double precision NOT NULL CHECK (price BETWEEN 0 AND 1),  -- first outcome's price
    PRIMARY KEY (market_id, fidelity, time)
);

CREATE TABLE IF NOT EXISTS snapshots (
    market_id     text NOT NULL REFERENCES markets (market_id),
    horizon_days  smallint NOT NULL CHECK (horizon_days IN (1, 7, 30)),
    target_time   timestamptz NOT NULL,         -- close_time minus the horizon
    price_time    timestamptz NOT NULL,         -- when the price used was recorded
    price         double precision NOT NULL CHECK (price BETWEEN 0 AND 1),
    PRIMARY KEY (market_id, horizon_days),
    CHECK (price_time <= target_time)           -- never a price from the future
);

-- One row per load, so we can see when data was loaded and whether it worked
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    status       text NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    row_counts   jsonb,                         -- e.g. {"markets": 10792, ...}
    error        text
);
