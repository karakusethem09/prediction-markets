"""Tests for the forward-test collector. HTTP is always mocked: no real API calls."""
import gzip
import json

import pandas as pd
import pytest

from forward_test import collector

RUN = pd.Timestamp("2026-10-28 10:00", tz="UTC")


def raw_market(market_id="1", **fields):
    market = {
        "id": market_id, "question": "Which party will win the Senate?", "events": [{"id": "e1", "title": "Senate 2026"}],
        "outcomes": '["Democratic", "Republican"]', "clobTokenIds": '["tok-d", "tok-r"]', "enableOrderBook": True,
        "endDate": "2026-11-04T04:59:00Z", "startDate": "2026-01-01T00:00:00Z", "volumeNum": 250_000,
        "liquidityNum": 20_000, "active": True, "closed": False, "acceptingOrders": True,
        "description": "Resolves to the party that controls the Senate.",
    }
    market.update(fields)
    return market


@pytest.fixture
def fake_api(monkeypatch, tmp_path):
    """Canned responses by URL for GET and POST, raw files kept in tmp_path."""
    monkeypatch.setattr(collector, "RAW_DIR", tmp_path)
    responses, calls = {}, []

    def answer(url, payload):
        calls.append((url, payload))
        response = responses[url]
        if isinstance(response, Exception):
            raise response
        return response.pop(0) if isinstance(response, list) else response

    monkeypatch.setattr(collector, "get_with_retry", answer)
    monkeypatch.setattr(collector, "post_with_retry", answer)
    return responses, calls


def test_to_candidate_keeps_outcome_names_and_tokens_in_order():
    candidate = collector.to_candidate(raw_market())
    assert candidate["outcome_names"] == ["Democratic", "Republican"]
    assert candidate["token_ids"] == ["tok-d", "tok-r"]
    assert (candidate["observed_outcome"], candidate["token_id"]) == ("Democratic", "tok-d")
    assert candidate["scheduled_end"] == pd.Timestamp("2026-11-04 04:59", tz="UTC")


@pytest.mark.parametrize("fields", [
    {"enableOrderBook": False},
    {"clobTokenIds": '["only-one"]'},  # outcomes and tokens don't line up
    {"outcomes": '["A", "B", "C"]', "clobTokenIds": '["a", "b", "c"]'},  # not binary
    {"endDate": None},
])
def test_to_candidate_rejects_unusable_markets(fields):
    assert collector.to_candidate(raw_market(**fields)) is None


def test_requests_are_timed_in_utc_and_in_order(fake_api):
    responses, _ = fake_api
    responses[collector.GAMMA_URL] = {"markets": [raw_market()]}
    (market, timing), = collector.open_markets_near_entry(RUN, collector.Evidence(RUN))
    assert timing["requested_at"].tzinfo is not None and timing["requested_at"].utcoffset().total_seconds() == 0
    assert timing["requested_at"] <= timing["received_at"]


def test_books_keep_exchange_time_and_store_unchanged_content_once(fake_api):
    responses, _ = fake_api
    book = {"asset_id": "tok-d", "bids": [{"price": "0.22", "size": "30"}], "asks": [{"price": "0.25", "size": "10"}],
            "tick_size": "0.01", "min_order_size": "5", "last_trade_price": "0.24", "hash": "h1", "timestamp": "1793229305000"}
    later = {**book, "hash": "h2", "timestamp": "1793229905000"}  # same levels, ten minutes later
    responses[collector.BOOKS_URL] = [[book], [later]]
    evidence = collector.Evidence(RUN, keep_files=False)

    first = collector.order_books(["tok-d"], evidence)["tok-d"]
    second = collector.order_books(["tok-d"], evidence)["tok-d"]

    assert first["book_exchange_time"] == pd.Timestamp("2026-10-28 23:15:05", tz="UTC")
    assert second["book_exchange_time"] == pd.Timestamp("2026-10-28 23:25:05", tz="UTC")
    assert first["sha256"] == second["sha256"]  # identical levels: one archived body
    stored = json.loads(gzip.decompress(evidence.bodies[first["sha256"]]["body_gzip"]))
    assert stored["asks"] == [{"price": "0.25", "size": "10"}] and "timestamp" not in stored


def test_a_token_missing_from_the_batch_becomes_an_explicit_error(fake_api):
    import requests
    responses, _ = fake_api
    responses[collector.BOOKS_URL] = [[]]  # the batch didn't return it
    error = requests.HTTPError(response=type("R", (), {"status_code": 404})())
    responses[collector.BOOK_URL] = error
    book = collector.order_books(["tok-x"], collector.Evidence(RUN, keep_files=False))["tok-x"]
    assert book["error"] == "no order book (404)"


TIMING = {"requested_at": RUN, "received_at": RUN}


def test_pilot_classifies_from_event_tags_and_archives_compact_metadata(fake_api):
    event = {"id": "e1", "title": "Senate 2026", "tags": [{"slug": "midterms"}, {"slug": "elections"}],
             "markets": [raw_market("1"), raw_market("2", volumeNum=500), raw_market("3", closed=True)]}
    evidence = collector.Evidence(RUN, keep_files=False)

    markets = collector.pilot_markets([(event, TIMING)], min_volume=10_000, evidence=evidence)

    assert [m["market_id"] for m in markets] == ["poly:1"]  # too small and closed markets left out
    assert markets[0]["classification"] == "us_midterms_2026"
    archived = json.loads(gzip.decompress(evidence.bodies[markets[0]["metadata"]["sha256"]]["body_gzip"]))
    assert archived["market"]["clobTokenIds"] == '["tok-d", "tok-r"]' and "description" not in archived["market"]
    assert archived["event"]["tags"][0]["slug"] == "midterms"


def test_pilot_keeps_uncertain_events_separately_and_leaves_out_non_election_ones(fake_api):
    events = [
        ({"id": "e1", "title": "Ukraine election called by...?", "tags": [{"slug": "politics"}],
          "markets": [raw_market("1")]}, TIMING),
        ({"id": "e2", "title": "Will Hamas agree to disarm?", "tags": [{"slug": "politics"}],
          "markets": [raw_market("2")]}, TIMING),
    ]
    markets = collector.pilot_markets(events, min_volume=10_000, evidence=collector.Evidence(RUN, keep_files=False))
    assert [(m["market_id"], m["classification"]) for m in markets] == [("poly:1", "unknown")]


def test_a_market_listed_under_two_events_is_observed_once(fake_api):
    events = [({"id": e, "title": "Senate 2026", "tags": [{"slug": "midterms"}], "markets": [raw_market("1")]}, TIMING)
              for e in ["e1", "e2"]]
    markets = collector.pilot_markets(events, min_volume=10_000, evidence=collector.Evidence(RUN, keep_files=False))
    assert [m["market_id"] for m in markets] == ["poly:1"]


def test_archived_metadata_is_unchanged_when_only_volume_and_prices_move(fake_api):
    event = {"id": "e1", "title": "Senate 2026", "tags": [{"slug": "midterms"}]}
    evidence = collector.Evidence(RUN, keep_files=False)
    first = collector.pilot_markets([({**event, "markets": [raw_market("1")]}, TIMING)], 10_000, evidence)
    later = collector.pilot_markets([({**event, "markets": [raw_market("1", volumeNum=300_000, bestAsk=0.3,
                                                                         updatedAt="later")]}, TIMING)], 10_000, evidence)
    moved = collector.pilot_markets([({**event, "markets": [raw_market("1", endDate="2026-11-05T04:59:00Z")]}, TIMING)],
                                    10_000, evidence)
    assert first[0]["metadata"]["sha256"] == later[0]["metadata"]["sha256"]  # stored once
    assert later[0]["volume_at_entry"] == 300_000  # the volume is still recorded, in the observation
    assert moved[0]["metadata"]["sha256"] != first[0]["metadata"]["sha256"]  # a new scheduled end is kept


def test_event_discovery_follows_the_keyset_cursor_and_keeps_each_event_once(fake_api):
    responses, calls = fake_api
    pages = [{"events": [{"id": "e1"}, {"id": "e2"}], "next_cursor": "c1"}, {"events": [{"id": "e3"}]}]
    responses[collector.EVENTS_URL] = [page for _ in collector.ELECTION_TAG_SLUGS for page in pages]

    events = collector.election_events(collector.Evidence(RUN, keep_files=False))

    assert sorted(event["id"] for event, _ in events) == ["e1", "e2", "e3"]  # every tag returned the same events
    first_tag = [payload for url, payload in calls if url == collector.EVENTS_URL][:2]
    assert "after_cursor" not in first_tag[0] and first_tag[1]["after_cursor"] == "c1"
    assert len(calls) == 2 * len(collector.ELECTION_TAG_SLUGS)  # stopped at each tag's last page (no cursor)


def test_event_pages_go_to_files_not_the_database(fake_api, tmp_path):
    responses, _ = fake_api
    responses[collector.EVENTS_URL] = [{"events": [{"id": "e1", "tags": [], "markets": []}]}
                                       for _ in collector.ELECTION_TAG_SLUGS]
    evidence = collector.Evidence(RUN, keep_files=True)
    collector.election_events(evidence)
    assert not any(b["kind"] == "events_page" for b in evidence.bodies.values())
    assert list(tmp_path.rglob("events_page_*.json.gz"))  # but kept in the run's files (the workflow artifact)


@pytest.mark.parametrize("market, expected", [
    ({"closed": False, "umaResolutionStatus": None, "outcomePrices": '["0.3", "0.7"]', "volumeNum": 1},
     {"closed": False, "final": False, "outcome": None}),
    ({"closed": True, "umaResolutionStatus": "resolved", "outcomePrices": '["1", "0"]', "volumeNum": 1},
     {"closed": True, "final": True, "outcome": 1}),
])
def test_market_result(fake_api, market, expected):
    responses, _ = fake_api
    responses[collector.MARKET_URL.format(id="7")] = market
    result = collector.market_result("poly:7", collector.Evidence(RUN, keep_files=False))
    assert {k: result[k] for k in expected} == expected


def test_official_resolution_reads_each_tokens_winner_flag(fake_api):
    responses, _ = fake_api
    responses[collector.MARKET_URL.format(id="7")] = {
        "conditionId": "0xabc", "closed": True, "umaResolutionStatus": "resolved", "closedTime": "2026-11-04 06:44:30+00",
        "outcomes": '["Yes", "No"]', "clobTokenIds": '["tok-y", "tok-n"]'}
    responses[collector.CLOB_MARKET_URL.format(condition_id="0xabc")] = {
        "closed": True, "taker_base_fee": 0,
        "tokens": [{"token_id": "tok-y", "winner": False}, {"token_id": "tok-n", "winner": True}]}
    responses[collector.EVENT_URL.format(id="e7")] = {"title": "Senate 2026", "tags": [{"slug": "midterms"}]}

    result = collector.official_resolution("poly:7", "poly:e7", collector.Evidence(RUN, keep_files=False))

    assert result["final"] and result["winners"] == {"tok-y": False, "tok-n": True}
    assert result["official_close_time"] == pd.Timestamp("2026-11-04 06:44:30", tz="UTC")
    assert result["event_tags"] == ["midterms"] and result["token_ids"] == ["tok-y", "tok-n"]


def test_an_unresolved_market_is_not_final_even_if_closed(fake_api):
    responses, _ = fake_api
    responses[collector.MARKET_URL.format(id="8")] = {"conditionId": "0xdef", "closed": True,
                                                      "umaResolutionStatus": "proposed", "outcomes": "[]",
                                                      "clobTokenIds": "[]"}
    responses[collector.CLOB_MARKET_URL.format(condition_id="0xdef")] = {"closed": True, "tokens": []}
    result = collector.official_resolution("poly:8", None, collector.Evidence(RUN, keep_files=False))
    assert not result["final"]
