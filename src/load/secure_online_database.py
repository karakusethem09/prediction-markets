"""Apply sql/supabase_security.sql to the online database and give dashboard_reader a new password.

Run once (safe to rerun):  set -a; source .env; set +a; python src/load/secure_online_database.py

Reads SUPABASE_URL from the environment and appends DASHBOARD_DATABASE_URL (the read-only
login for the public dashboard) to .env. Passwords are never printed.
"""
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
    admin_url = os.environ["SUPABASE_URL"]
    password = secrets.token_urlsafe(24)

    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(SECURITY_SQL.read_text())
        # sql.Literal quotes the password safely; ALTER ROLE can't take a normal %s parameter
        conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(READER), sql.Literal(password)))
    print("Security settings applied and dashboard_reader password set.")

    lines = [line for line in ENV_FILE.read_text().splitlines() if not line.startswith("DASHBOARD_DATABASE_URL=")]
    lines.append(f"DASHBOARD_DATABASE_URL={reader_url(admin_url, password)}")
    ENV_FILE.write_text("\n".join(lines) + "\n")
    print("DASHBOARD_DATABASE_URL saved in .env (not printed).")
