-- Migration 002 (2026-10-28): append-only observations, durable raw evidence, election pilot.
--
-- Only ADDS tables, columns and rules: existing rows in forward_test_runs and forward_test_entries are
-- kept as they are. Safe to run again (IF NOT EXISTS / OR REPLACE everywhere). Apply it on purpose:
--     python -m forward_test migrate          (the collector never changes the schema by itself)

-- 1. Raw API responses, stored once per distinct content (sha256 of the JSON), gzip-compressed.
--    Durable evidence: the 14-day GitHub artifacts are no longer the only copy.
CREATE TABLE IF NOT EXISTS forward_test_raw (
    sha256       text PRIMARY KEY,                 -- of the uncompressed JSON body
    kind         text NOT NULL,                    -- e.g. 'book', 'events_page', 'markets_page', 'event'
    body_gzip    bytea NOT NULL,
    size_bytes   integer NOT NULL,                 -- uncompressed size
    stored_at    timestamptz NOT NULL DEFAULT now()
);

-- 2. Every observation of a market's order book, one row per experiment, run and market. Never updated.
CREATE TABLE IF NOT EXISTS forward_test_observations (
    observation_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    experiment             text NOT NULL CHECK (experiment IN ('group_a_7day', 'election_2026_pilot')),
    run_id                 bigint NOT NULL REFERENCES forward_test_runs (run_id),
    market_id              text NOT NULL,
    platform               text NOT NULL,
    event_id               text,
    event_title            text,
    classification         text NOT NULL,  -- allowed values: see the constraint below the table
    event_tags             text[] NOT NULL DEFAULT '{}',   -- tag slugs of the market's event, as published
    question               text NOT NULL,
    outcome_names          text[] NOT NULL,                -- in the platform's order
    token_ids              text[] NOT NULL,                -- same order as outcome_names
    observed_outcome       text NOT NULL,                  -- the first-listed outcome, whose book is recorded
    observed_token_id      text NOT NULL,
    scheduled_end          timestamptz NOT NULL,           -- as published at this observation (history is kept)
    market_active          boolean,
    market_closed          boolean,
    accepting_orders       boolean,
    volume_usd             double precision,               -- cumulative volume at this observation
    liquidity_usd          double precision,
    -- When things happened (all UTC)
    run_started_at         timestamptz NOT NULL,
    metadata_requested_at  timestamptz NOT NULL,
    metadata_received_at   timestamptz NOT NULL,
    book_requested_at      timestamptz,
    book_received_at       timestamptz,
    book_exchange_time     timestamptz,                    -- the exchange's own timestamp on the book
    observed_at            timestamptz NOT NULL,           -- when this observation was assembled
    -- The order book (first-listed outcome)
    book_valid             boolean NOT NULL,
    book_error             text,                           -- why the book is invalid, if it is
    best_bid               double precision,
    best_ask               double precision,
    ask_size_at_best       double precision,
    midpoint               double precision,
    spread                 double precision,
    bid_levels             integer,
    ask_levels             integer,
    bid_depth_shares       double precision,
    ask_depth_shares       double precision,
    ask_depth_usd          double precision,               -- cost of buying every share offered
    tick_size              double precision,
    min_order_size         double precision,
    last_trade_price       double precision,
    book_hash              text,                           -- the exchange's hash of the book
    meets_group_a_price    boolean NOT NULL,               -- best ask in [0.20, 0.30)
    meets_group_a_volume   boolean NOT NULL,               -- cumulative volume >= $100,000
    metadata_sha256        text REFERENCES forward_test_raw (sha256),
    book_sha256            text REFERENCES forward_test_raw (sha256),
    UNIQUE (experiment, run_id, market_id)                 -- rerunning a run never duplicates
);
CREATE INDEX IF NOT EXISTS forward_test_observations_market ON forward_test_observations (market_id, observed_at);

-- 3. Append-only, enforced by the database: these rows can be added but never changed or deleted
CREATE OR REPLACE FUNCTION forward_test_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: rows cannot be changed or deleted', TG_TABLE_NAME;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS forward_test_observations_append_only ON forward_test_observations;
CREATE TRIGGER forward_test_observations_append_only
    BEFORE UPDATE OR DELETE ON forward_test_observations
    FOR EACH ROW EXECUTE FUNCTION forward_test_append_only();
DROP TRIGGER IF EXISTS forward_test_raw_append_only ON forward_test_raw;
CREATE TRIGGER forward_test_raw_append_only
    BEFORE UPDATE OR DELETE ON forward_test_raw
    FOR EACH ROW EXECUTE FUNCTION forward_test_append_only();

-- 4. The one-entry-per-market table keeps its rows; new columns link it to its evidence
ALTER TABLE forward_test_entries ADD COLUMN IF NOT EXISTS observed_outcome text;
ALTER TABLE forward_test_entries ADD COLUMN IF NOT EXISTS outcome_names text[];
ALTER TABLE forward_test_entries ADD COLUMN IF NOT EXISTS token_ids text[];
ALTER TABLE forward_test_entries ADD COLUMN IF NOT EXISTS book_exchange_time timestamptz;
ALTER TABLE forward_test_entries ADD COLUMN IF NOT EXISTS book_received_at timestamptz;
ALTER TABLE forward_test_entries ADD COLUMN IF NOT EXISTS observation_id bigint REFERENCES forward_test_observations (observation_id);
-- An entry whose book was invalid at its 7-day mark is recorded as 'invalid' (no paper trade)
ALTER TABLE forward_test_entries DROP CONSTRAINT IF EXISTS forward_test_entries_status_check;
ALTER TABLE forward_test_entries ADD CONSTRAINT forward_test_entries_status_check
    CHECK (status IN ('open', 'missed', 'settled', 'void', 'invalid'));

-- Election classification of the observed market's event (models.classify_event). 'unknown' = no election tag,
-- but an election-like title (kept as a separate, uncertain bucket); 'not_election' = a Group A market whose
-- event isn't about an election
ALTER TABLE forward_test_observations DROP CONSTRAINT IF EXISTS forward_test_observations_classification_check;
ALTER TABLE forward_test_observations ADD CONSTRAINT forward_test_observations_classification_check
    CHECK (classification IN ('us_midterms_2026', 'election_other', 'unknown', 'not_election'));

-- 5. Run log: what each run observed
ALTER TABLE forward_test_runs ADD COLUMN IF NOT EXISTS observed integer;     -- observation rows written
ALTER TABLE forward_test_runs ADD COLUMN IF NOT EXISTS invalid integer;      -- of which with an invalid book
-- How the run was configured: experiments, the pilot's volume floor, --limit, which workflow started it
ALTER TABLE forward_test_runs ADD COLUMN IF NOT EXISTS settings jsonb;

-- 6. Locked by default, like every table (sql/supabase_security.sql adds dashboard read access)
ALTER TABLE forward_test_raw ENABLE ROW LEVEL SECURITY;
ALTER TABLE forward_test_observations ENABLE ROW LEVEL SECURITY;

-- 7. Which migrations have been applied
CREATE TABLE IF NOT EXISTS forward_test_migrations (
    name        text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO forward_test_migrations (name) VALUES ('002_observations_and_election_pilot') ON CONFLICT DO NOTHING;
ALTER TABLE forward_test_migrations ENABLE ROW LEVEL SECURITY;
