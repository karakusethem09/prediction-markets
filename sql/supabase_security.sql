-- Lock down the online (Supabase) database. Safe to run again.
--
-- Supabase gives every project a public web API whose roles (anon, authenticated)
-- can reach tables in the public schema. This project never uses that API, so:
--   1. those roles get no access at all, now or for tables created later;
--   2. Row Level Security is on for every table, as a second lock;
--   3. dashboard_reader can only read what the public dashboard shows.
-- The pipeline connects as postgres, which owns the tables and keeps full access.

-- 1. No access through the web API
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;

-- 2. Row Level Security: with no policy, a role sees no rows (owners are exempt)
ALTER TABLE markets ENABLE ROW LEVEL SECURITY;
ALTER TABLE price_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE pipeline_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE processing_status ENABLE ROW LEVEL SECURITY;

-- 3. A read-only login for the public dashboard (its password is set separately, never stored here)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'dashboard_reader') THEN
        CREATE ROLE dashboard_reader LOGIN;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO dashboard_reader;
GRANT SELECT ON markets, snapshots, pipeline_runs, processing_status TO dashboard_reader;

-- Row Level Security needs an explicit "may read every row" policy for this role
DROP POLICY IF EXISTS dashboard_reader_select ON markets;
CREATE POLICY dashboard_reader_select ON markets FOR SELECT TO dashboard_reader USING (true);
DROP POLICY IF EXISTS dashboard_reader_select ON snapshots;
CREATE POLICY dashboard_reader_select ON snapshots FOR SELECT TO dashboard_reader USING (true);
DROP POLICY IF EXISTS dashboard_reader_select ON pipeline_runs;
CREATE POLICY dashboard_reader_select ON pipeline_runs FOR SELECT TO dashboard_reader USING (true);
DROP POLICY IF EXISTS dashboard_reader_select ON processing_status;
CREATE POLICY dashboard_reader_select ON processing_status FOR SELECT TO dashboard_reader USING (true);

-- Forward-test tables (sql/forward_test.sql): read-only for the dashboard login, once they exist
DO $$
BEGIN
    IF to_regclass('public.forward_test_entries') IS NOT NULL THEN
        GRANT SELECT ON forward_test_entries, forward_test_runs TO dashboard_reader;
        DROP POLICY IF EXISTS dashboard_reader_select ON forward_test_entries;
        CREATE POLICY dashboard_reader_select ON forward_test_entries FOR SELECT TO dashboard_reader USING (true);
        DROP POLICY IF EXISTS dashboard_reader_select ON forward_test_runs;
        CREATE POLICY dashboard_reader_select ON forward_test_runs FOR SELECT TO dashboard_reader USING (true);
    END IF;
END
$$;
