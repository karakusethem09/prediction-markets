"""Tests for the "Forward Test — Election 2026" page (Streamlit's AppTest, no browser).

The page reads only a results bundle. Each test writes a synthetic one with the real analysis code
(analysis.forward_test_results) into a temporary folder: never real data, never a database."""
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from analysis import forward_test_results as ftr

PAGE = Path(__file__).resolve().parents[1] / "app" / "pages" / "forward_test_election_2026.py"
T0 = pd.Timestamp("2026-10-28 10:00", tz="UTC")


def entry(i, ask, status, outcome, category_tag="sports"):
    return {"market_id": f"m{i}", "event_id": f"e{i}", "question": f"Sample market {i}?", "token_id": f"tok{i}",
            "recorded_at": T0 + pd.Timedelta(hours=i), "run_id": 1, "scheduled_end": T0 + pd.Timedelta(days=7),
            "best_bid": ask - 0.01, "best_ask": ask, "midpoint": ask - 0.005, "spread": 0.01, "ask_size_at_best": 900.0,
            "fill_price_100": ask, "fill_price_1000": None if i == 0 else ask + 0.002, "volume_at_entry": 250_000.0,
            "min_order_size": 5.0, "in_group_a": True, "status": status, "outcome": outcome}


def resolution(i, final, won):
    return {"market_id": f"m{i}", "final": final, "winners": {f"tok{i}": won, f"no{i}": final and not won},
            "official_close_time": T0 + pd.Timedelta(days=2) if final else None, "outcome_names": ["Yes", "No"],
            "token_ids": [f"tok{i}", f"no{i}"], "event_title": f"Event {i}", "event_tags": ["sports"],
            "published_taker_fee": 0}


def write_bundle(folder, settled=True):
    """Six positions: with settled=True, four settled (one win) and two unresolved."""
    entries, resolutions = [], []
    for i in range(6):
        final = settled and i < 4
        entries.append(entry(i, 0.20 + 0.01 * i, "settled" if final else "open", float(i == 1) if final else np.nan))
        resolutions.append(resolution(i, final, won=(i == 1)))
    runs = pd.DataFrame({"run_id": [1], "started_at": [T0], "finished_at": [T0], "status": ["success"],
                         "entered": [6], "missed": [0], "settled": [0]})
    github = pd.DataFrame({"github_run_id": [10], "created_at": [T0 - pd.Timedelta(minutes=2)],
                           "event": ["schedule"], "conclusion": ["success"], "every_hours": [3]})
    results = ftr.analyze(pd.DataFrame(entries), runs, resolutions, {}, github, None,
                          as_of=T0 + pd.Timedelta(days=5), n_boot=200)
    ftr.write_bundle(results, folder)
    return results


def open_page(folder, monkeypatch):
    monkeypatch.setenv("FORWARD_TEST_RESULTS_DIR", str(folder))
    st.cache_data.clear()
    return AppTest.from_file(str(PAGE), default_timeout=60).run()


@pytest.fixture
def no_database(monkeypatch):
    """Rendering must be read-only by construction: any database connection fails the test."""
    def refuse(*args, **kwargs):
        raise AssertionError("the forward-test page must not connect to a database")
    monkeypatch.setattr(psycopg, "connect", refuse)


def test_populated_page_shows_every_section_without_a_database(tmp_path, monkeypatch, no_database):
    write_bundle(tmp_path)
    app = open_page(tmp_path, monkeypatch)

    assert not app.exception
    assert [h.value for h in app.subheader] == [
        "Overview", "Collection timeline", "Performance by position size", "Group A versus the historical study",
        "Spread and execution price", "Individual positions", "Data quality", "Limitations and method"]
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Group A positions"] == "6" and metrics["Officially settled"] == "4"
    assert metrics["Unresolved"] == "2" and metrics["Won / lost"] == "1 / 3"
    assert metrics["$1,000 orders fillable"] == "5 of 6"  # one recorded book couldn't take $1,000
    assert "PENDING: 4 of 100" in app.info[0].value  # never a success indicator before the spec's verdict


def test_search_filters_the_position_table(tmp_path, monkeypatch, no_database):
    write_bundle(tmp_path)
    app = open_page(tmp_path, monkeypatch)
    app.text_input[0].set_value("market 3").run()
    assert "1 of 6 positions." in " ".join(c.value for c in app.caption)


def test_no_realized_return_before_anything_settles(tmp_path, monkeypatch, no_database):
    write_bundle(tmp_path, settled=False)
    app = open_page(tmp_path, monkeypatch)
    assert not app.exception
    assert any("No position has officially settled yet" in m.value for m in app.markdown)
    assert {m.label: m.value for m in app.metric}["Won / lost"] == "n/a"


def test_without_records_the_page_says_so(tmp_path, monkeypatch, no_database):
    app = open_page(tmp_path / "empty", monkeypatch)
    assert not app.exception
    assert [i.value for i in app.info] == ["No verified prospective observations available."]
    assert not app.metric  # no numbers are invented


def test_the_calibration_page_links_to_the_forward_test():
    text = (PAGE.parents[1] / "dashboard.py").read_text()
    assert 'st.page_link("pages/forward_test_election_2026.py"' in text
