"""Apply sql/supabase_security.sql to the online database and give dashboard_reader a new password.

Run once (safe to rerun):  set -a; source .env; set +a; python src/load/secure_online_database.py
After adding a table, rerun with --keep-password: it applies the rules without changing the
dashboard's password (a new password would also have to go into the Streamlit secret).

Reads SUPABASE_URL from the environment and appends DASHBOARD_DATABASE_URL (the read-only
login for the public dashboard) to .env. Passwords are never printed.
"""
import argparse
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SECURITY_SQL = PROJECT_ROOT / "sql" / "supabase_security.sql"
ENV_FILE = PROJECT_ROOT / ".env"
READER = "dashboard_reader"


def reader_url(admin_url, password):
    """The admin connection URL with the read-only user and password swapped in.

    Supabase's pooler expects the user as "<role>.<project ref>", like "postgres.<ref>".
    """
    parts = urlsplit(admin_url)
    project_ref = parts.username.split(".", 1)[1]
    host = parts.netloc.rsplit("@", 1)[1]
    # token_urlsafe passwords only use letters, digits, - and _, so no escaping is needed
    return urlunsplit(parts._replace(netloc=f"{READER}.{project_ref}:{password}@{host}"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lock down the online database.")
    parser.add_argument("--keep-password", action="store_true",
                        help="only apply the security rules; leave dashboard_reader's password as it is")
    args = parser.parse_args()
    admin_url = os.environ["SUPABASE_URL"]

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

    lines = [line for line in ENV_FILE.read_text().splitlines() if not line.startswith("DASHBOARD_DATABASE_URL=")]
    lines.append(f"DASHBOARD_DATABASE_URL={reader_url(admin_url, password)}")
    ENV_FILE.write_text("\n".join(lines) + "\n")
    print("DASHBOARD_DATABASE_URL saved in .env (not printed).")
