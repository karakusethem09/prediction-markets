"""The forward-test workflows' schedules and commands, read as text from .github/workflows (no YAML parser needed).

They guard what the health thresholds and the results module assume about the schedule."""
import re
from datetime import timedelta
from pathlib import Path

import forward_test.__main__ as lab
from analysis.forward_test_results import cron_hours

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def crons(name):
    return re.findall(r'cron:\s*"([^"]+)"', (WORKFLOWS / name).read_text())


def test_group_a_is_collected_every_12_hours_as_the_health_check_assumes():
    assert crons("forward_test.yml") == ["41 */12 * * *"]
    assert timedelta(hours=cron_hours(crons("forward_test.yml")[0])) == lab.SCHEDULE_EVERY


def test_the_recurring_workflow_collects_only_group_a_then_checks_health():
    commands = re.findall(r"run: (python -m forward_test.*)", (WORKFLOWS / "forward_test.yml").read_text())
    assert commands == ["python -m forward_test --experiment group_a_7day", "python -m forward_test health"]


def test_the_election_pilot_runs_only_on_demand():
    text = (WORKFLOWS / "election_day.yml").read_text()
    assert crons("election_day.yml") == [] and "workflow_dispatch" in text
    assert "python -m forward_test --experiment election_2026_pilot" in text


def test_the_calibration_pipeline_keeps_its_daily_schedule():
    assert crons("pipeline.yml") == ["17 6 * * *"]
