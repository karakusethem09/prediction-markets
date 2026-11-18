"""Tests for saving the dashboard's login in the env file. Temporary files only: never the real .env,
never a database."""
import stat

import pytest

from load import secure_online_database as secure


def test_a_missing_env_file_stops_the_script_before_anything_changes(tmp_path):
    with pytest.raises(SystemExit, match="not found: nothing was changed"):
        secure.env_lines(tmp_path / ".env")


def test_the_old_dashboard_login_is_replaced_and_everything_else_kept(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("SUPABASE_URL=postgresql://admin\nDASHBOARD_DATABASE_URL=postgresql://old\nOTHER=1\n")
    secure.write_env(env_file, secure.env_lines(env_file) + ["DASHBOARD_DATABASE_URL=postgresql://new"])
    assert env_file.read_text() == ("SUPABASE_URL=postgresql://admin\nOTHER=1\n"
                                    "DASHBOARD_DATABASE_URL=postgresql://new\n")


def test_the_env_file_is_written_readable_only_by_its_owner_with_no_temporary_file_left(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("SUPABASE_URL=postgresql://admin\n")
    env_file.chmod(0o644)
    secure.write_env(env_file, ["SUPABASE_URL=postgresql://admin", "DASHBOARD_DATABASE_URL=postgresql://new"])
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == [".env"]


def test_a_failed_write_leaves_the_old_env_file_as_it_was(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("SUPABASE_URL=postgresql://admin\n")

    def broken_rename(source, destination):
        raise OSError("disk full")

    monkeypatch.setattr(secure.os, "replace", broken_rename)
    with pytest.raises(OSError, match="disk full"):
        secure.write_env(env_file, ["SUPABASE_URL=postgresql://admin", "DASHBOARD_DATABASE_URL=postgresql://new"])
    assert env_file.read_text() == "SUPABASE_URL=postgresql://admin\n"
    assert [p.name for p in tmp_path.iterdir()] == [".env"]  # no stray copy of the new login
