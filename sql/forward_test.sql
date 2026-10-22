-- Forward test of Group A with real order-book prices (docs/forward_test_spec.md).
-- Separate from the pipeline's tables. Safe to run again: IF NOT EXISTS skips tables that exist.

-- One row per collector run
CREATE TABLE IF NOT EXISTS forward_test_runs (
    run_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz,
    status         text NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    entered        integer,                     -- markets recorded at their 7-day mark this run
    missed         integer,                     -- markets the collector reached too late
    settled        integer,                     -- recorded markets whose result became final this run
    error          text
);

-- One row per market at its 7-day mark, whatever its price; settled later
CREATE TABLE IF NOT EXISTS forward_test_entries (
    market_id           text PRIMARY KEY,
    platform            text NOT NULL,
    event_id            text,
    question            text NOT NULL,
    token_id            text NOT NULL,           -- the first outcome's token (e.g. "Yes")
    scheduled_end       timestamptz NOT NULL,
    recorded_at         timestamptz NOT NULL,
    run_id              bigint NOT NULL REFERENCES forward_test_runs (run_id),
    status              text NOT NULL CHECK (status IN ('open', 'missed', 'settled', 'void')),
    -- Order book at entry (empty for missed markets)
    best_bid            double precision,
    best_ask            double precision,        -- the price a buyer pays; decides Group A
    ask_size_at_best    double precision,        -- contracts available at the best ask
    midpoint            double precision,
    spread              double precision,
    last_trade_price    double precision,
    tick_size           double precision,
    min_order_size      double precision,
    fill_price_100      double precision,        -- average price to buy $100 worth (NULL: book too thin)
    fill_price_1000     double precision,        -- average price to buy $1,000 worth
    volume_at_entry     double precision,
    liquidity_at_entry  double precision,
    in_group_a          boolean NOT NULL,        -- best ask in [0.20, 0.30)
    -- Settlement
    settled_at          timestamptz,
    outcome             smallint CHECK (outcome IN (0, 1)),   -- 1 = the first outcome happened
    final_volume        double precision,
    CHECK (best_ask IS NULL OR best_ask BETWEEN 0 AND 1)
);

-- Locked by default; the online database adds a read-only policy for the dashboard login
-- (sql/supabase_security.sql)
ALTER TABLE forward_test_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE forward_test_entries ENABLE ROW LEVEL SECURITY;
