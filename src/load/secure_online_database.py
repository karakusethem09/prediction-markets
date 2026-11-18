"""Apply sql/supabase_security.sql to the online database and give dashboard_reader a new password.

Run once (safe to rerun):  set -a; source .env; set +a; python src/load/secure_online_database.py
After adding a table, rerun with --keep-password: it applies the rules without changing the
dashboard's password (a new password would also have to go into the Streamlit secret).
Check that everything is locked down, without changing anything:  ... secure_online_database.py --verify

Reads SUPABASE_URL from the environment and appends DASHBOARD_DATABASE_URL (the read-only
login for the public dashboard) to .env. Passwords are never printed.
"""
import argparse
import os
import secrets
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SECURITY_SQL = PROJECT_ROOT / "sql" / "supabase_security.sql"
ENV_FILE = PROJECT_ROOT / ".env"
READER = "dashboard_reader"


def security_problems(conn):
    """Every way the database is less locked down than intended. An empty list means all good."""
    problems = []
    for (table,) in conn.execute("SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace "
                                 "AND relkind = 'r' AND NOT relrowsecurity"):
        problems.append(f"{table}: row level security is off")
    for grantee, table, privileges in conn.execute(
            "SELECT grantee, table_name, string_agg(privilege_type, ',') FROM information_schema.role_table_grants "
            "WHERE table_schema = 'public' AND grantee IN ('anon', 'authenticated') GROUP BY 1, 2"):
        problems.append(f"{table}: the public web API role {grantee} can {privileges}")
    for table, privileges in conn.execute(
            "SELECT table_name, string_agg(privilege_type, ',') FROM information_schema.role_table_grants "
            "WHERE table_schema = 'public' AND grantee = %s AND privilege_type <> 'SELECT' GROUP BY 1", (READER,)):
        problems.append(f"{table}: {READER} can {privileges}, but it should only read")
    return problems


def reader_url(admin_url, password):
    """The admin connection URL with the read-only user and password swapped in.

    Supabase's pooler expects the user as "<role>.<project ref>", like "postgres.<ref>".
    """
    parts = urlsplit(admin_url)
    project_ref = parts.username.split(".", 1)[1]
    host = parts.netloc.rsplit("@", 1)[1]
    # token_urlsafe passwords only use letters, digits, - and _, so no escaping is needed
    return urlunsplit(parts._replace(netloc=f"{READER}.{project_ref}:{password}@{host}"))


def env_lines(env_file):
    """The env file's lines without the old DASHBOARD_DATABASE_URL. Read before the password changes:
    without the file there would be nowhere to save the new one, and it would be lost."""
    if not env_file.exists():
        raise SystemExit(f"{env_file} not found: nothing was changed. Create it (with SUPABASE_URL) and rerun.")
    return [line for line in env_file.read_text().splitlines() if not line.startswith("DASHBOARD_DATABASE_URL=")]


def write_env(env_file, lines):
    """Replace the env file in one step: write a temporary file next to it, readable only by its owner
    (mkstemp makes it 0600), then rename it over the old one. A crash leaves the old file or the new one."""
    handle, temporary = tempfile.mkstemp(dir=env_file.parent, prefix=f"{env_file.name}.")
    try:
        with os.fdopen(handle, "w") as file:
            file.write("\n".join(lines) + "\n")
        os.replace(temporary, env_file)
    except BaseException:
        os.unlink(temporary)  # never leave a stray copy of the login next to it
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lock down the online database.")
    parser.add_argument("--keep-password", action="store_true",
                        help="only apply the security rules; leave dashboard_reader's password as it is")
    parser.add_argument("--verify", action="store_true", help="only check, change nothing")
    args = parser.parse_args()
    admin_url = os.environ["SUPABASE_URL"]

    if args.verify:
        with psycopg.connect(admin_url) as conn:
            problems = security_problems(conn)
        print("\n".join(problems) if problems else "All tables locked down: RLS on, no web API access, reader is read-only.")
        raise SystemExit(1 if problems else 0)

    lines = None if args.keep_password else env_lines(ENV_FILE)  # before any change: no .env, nothing changes

    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(SECURITY_SQL.read_text())
    if args.keep_password:
        print("Security settings applied; dashboard_reader's password unchanged.")
        raise SystemExit

    password = secrets.token_urlsafe(24)
    with psycopg.connect(admin_url, autocommit=True) as conn:
        # sql.Literal quotes the password safely; ALTER ROLE can't take a normal %s parameter
        conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(READER), sql.Literal(password)))
    print("Security settings applied and dashboard_reader password set.")

    write_env(ENV_FILE, lines + [f"DASHBOARD_DATABASE_URL={reader_url(admin_url, password)}"])
    print("DASHBOARD_DATABASE_URL saved in .env (not printed).")
