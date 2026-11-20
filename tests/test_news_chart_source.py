# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0
"""CompanyNews wire contract. The only double is the upstream /news payload."""

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from openbb_core.api.rest_api import app
from openbb_core.app.model.credentials import Credentials
from openbb_core.app.model.user_settings import UserSettings
from openbb_core.app.service.user_service import UserService

from openbq_eodhd.models import news

NEUTRAL = {
    "date": "2024-03-01T00:45:00+01:00",
    "title": "Multi-symbol report",
    "content": "Synthetic body kept in the legacy row.",
    "link": "https://example.com/neutral",
    "symbols": ["AAPL.US", "MSFT.US"],
    "tags": ["TECH"],
    "sentiment": {"polarity": 0, "pos": 0.7, "neg": 0.1},
}
UNSCORED = {
    "date": "2024-03-01T04:00:00Z",
    "title": "Unscored report",
    "link": "https://example.com/unscored",
    "symbols": ["AAPL.US"],
    "sentiment": {"pos": 0.9, "neu": 0.1},
}


@pytest.fixture
def news_http(monkeypatch):
    monkeypatch.setattr(
        UserService, "read_from_file",
        staticmethod(lambda: UserSettings.model_construct(
            credentials=Credentials(eodhd_api_key="demo"),
        )),
    )
    rest = Mock(return_value=[NEUTRAL, UNSCORED])
    monkeypatch.setattr(news, "rest_json", rest)
    return TestClient(app, raise_server_exceptions=False), rest


@pytest.mark.parametrize("prefix", ["", "/eodhd"])
def test_company_page_adds_opt_in_polarity_and_preserves_legacy(prefix, news_http):
    client, rest = news_http
    path = f"/api/v1{prefix}/news/company"
    params = {"provider": "eodhd", "symbol": "aapl"}
    chart = client.get(path, params={**params, "chart_source": True})
    assert chart.status_code == 200, chart.text
    rows = chart.json()["results"]
    assert rows[0]["polarity"] == 0 and rows[0]["symbols"] == "AAPL.US,MSFT.US"
    assert rows[0]["date"] == "2024-03-01T00:45:00+01:00"
    assert rows[1]["polarity"] is None
    receipt, = chart.json()["extra"]["results_metadata"]["chart_sources"]["receipts"]
    assert receipt["field_origins"] == {
        "timestamp": "date", "articleId": "url", "instrument": "symbols", "polarity": "polarity",
    }
    assert receipt["instrument"] == "AAPL.US"
    assert receipt["polarity_unit"] == "polarity[-1,1]"
    assert receipt["scope"] == {"kind": "sample", "start": None, "end": None, "limit": 50, "offset": 0}
    assert receipt["coordinates"] == [
        {"article_id": NEUTRAL["link"], "utc_day": "2024-02-29", "scored": True},
        {"article_id": UNSCORED["link"], "utc_day": "2024-03-01", "scored": False},
    ]
    assert receipt["diagnostics"] == ["unscored"]
    assert "Synthetic body" not in str(receipt)
    rest.assert_called_once()
    assert rest.call_args.args[1:] == (
        "news", {"s": "AAPL.US", "from": None, "to": None, "limit": 50, "offset": 0},
    )

    legacy = client.get(path, params={**params, "chart_source": False})
    assert legacy.status_code == 200, legacy.text
    assert "results_metadata" not in legacy.json()["extra"]
    assert "polarity" not in legacy.json()["results"][0]
    assert legacy.json()["results"][0]["sentiment"] == NEUTRAL["sentiment"]
    assert rest.call_args.args[1:] == (
        "news", {"s": "AAPL.US", "from": None, "to": None, "limit": None},
    )


def test_raw_news_faults_are_retained_before_drops_and_exact_duplicates(news_http):
    client, rest = news_http
    rest.return_value = [
        NEUTRAL,
        dict(NEUTRAL),
        dict(NEUTRAL, date="2024-02-30T01:00:00Z", title=""),
        dict(NEUTRAL, link="javascript:foo"),
        dict(NEUTRAL, link="https://example.com/wrong", symbols=["MSFT.US"]),
        dict(UNSCORED, title=""),
        UNSCORED,
    ]
    response = client.get("/api/v1/eodhd/news/company", params={
        "symbol": "AAPL.US", "chart_source": True, "start_date": "2024-02-29",
        "end_date": "2024-03-01", "offset": 15, "limit": 7,
    })
    assert response.status_code == 200, response.text
    payload = response.json()
    assert [row["url"] for row in payload["results"]] == [NEUTRAL["link"], UNSCORED["link"]]
    receipt, = payload["extra"]["results_metadata"]["chart_sources"]["receipts"]
    assert (receipt["raw_count"], receipt["normalized_count"], receipt["delivered_count"]) == (7, 2, 2)
    assert receipt["scope"] == {
        "kind": "sample", "start": "2024-02-29", "end": "2024-03-01", "limit": 7, "offset": 15,
    }
    assert receipt["unsafe"] == {
        "invalid_identity": True, "invalid_timestamp": True, "duplicate_conflict": True, "unit_conflict": False,
    }
    assert set(receipt["diagnostics"]) == {
        "invalid_identity", "invalid_timestamp", "duplicate_conflict", "exact_duplicate", "missing_title", "unscored",
    }
    rest.assert_called_once()
    assert rest.call_args.args[1:] == (
        "news", {"s": "AAPL.US", "from": "2024-02-29", "to": "2024-03-01", "limit": 7, "offset": 15},
    )


@pytest.mark.parametrize("polarity", [True, "0.3", 1.01, -2.0, float("nan"), None])
def test_invalid_polarity_is_unscored_without_coercion(polarity):
    query = news.EODHDCompanyNewsFetcher.transform_query({"symbol": "AAPL", "chart_source": True})
    result = news.EODHDCompanyNewsFetcher.transform_data(query, [
        dict(NEUTRAL, sentiment={"polarity": polarity, "pos": 1}),
    ])
    assert result.result[0].polarity is None
    assert result.metadata["chart_sources"]["receipts"][0]["coordinates"][0]["scored"] is False


@pytest.mark.parametrize("value,status", [([], 200), ({"message": "denied"}, 502), (None, 502)])
def test_empty_page_and_upstream_failure_remain_distinct(news_http, value, status):
    client, rest = news_http
    rest.return_value = value
    path = "/api/v1/eodhd/news/company"
    result = client.get(path, params={"symbol": "AAPL", "chart_source": True})
    assert result.status_code == status, result.text
    if value == []:
        assert result.json()["results"] == []
        receipt, = result.json()["extra"]["results_metadata"]["chart_sources"]["receipts"]
        assert receipt["raw_count"] == receipt["normalized_count"] == receipt["delivered_count"] == 0
        assert receipt["coordinates"] == []
        assert client.get(path, params={"symbol": "AAPL"}).status_code == 204
    else:
        assert "chart_sources" not in result.text


@pytest.mark.parametrize("params", [
    {"symbol": "AAPL,MSFT"},
    {"symbol": "AAPL", "limit": 0},
    {"symbol": "AAPL", "limit": 1001},
    {"symbol": "AAPL", "offset": -1},
    {"symbol": "AAPL", "offset": 9999, "limit": 2},
    {"symbol": "AAPL", "start_date": "2024-03-03", "end_date": "2024-03-01"},
])
def test_invalid_chart_query_never_acquires(params, news_http):
    client, rest = news_http
    result = client.get("/api/v1/eodhd/news/company", params={**params, "chart_source": True})
    assert result.status_code in (400, 422), result.text
    rest.assert_not_called()


@pytest.mark.parametrize("params", [{"end_date": "2024-02-28"}, {"limit": 1}])
def test_acquisition_refuses_response_outside_the_claimed_window_or_limit(news_http, params):
    client, rest = news_http
    result = client.get("/api/v1/eodhd/news/company", params={"symbol": "AAPL", "chart_source": True, **params})
    assert result.status_code == 502, result.text
    rest.assert_called_once()


def test_news_wire_model_requires_explicit_facts_and_rejects_open_or_inconsistent_payloads():
    import json

    from pydantic import ValidationError

    from openbq_eodhd.models._news_chart_source import NewsSource

    query = news.EODHDCompanyNewsFetcher.transform_query({"symbol": "AAPL", "chart_source": True})
    result = news.EODHDCompanyNewsFetcher.transform_data(query, [NEUTRAL])
    receipt, = result.metadata["chart_sources"]["receipts"]
    assert NewsSource.model_validate_json(json.dumps(receipt)).instrument == "AAPL.US"
    for key in ("version", "kind", "polarity_unit", "scope"):
        with pytest.raises(ValidationError):
            NewsSource.model_validate_json(json.dumps({k: v for k, v in receipt.items() if k != key}))
    for changed in [
        {"hidden_body": "unexpected"},
        {"field_origins": {}},
        {"unsafe": {**receipt["unsafe"], "invalid_timestamp": True}},
        {"scope": {**receipt["scope"], "kind": "complete"}},
        {"scope": {"kind": "bounded", "start": "2024-02-29", "end": "2024-03-01", "limit": None, "offset": None},
         "raw_count": 2, "normalized_count": 2},
        {"coordinates": [{**receipt["coordinates"][0], "polarity": 0}]},
        {"coordinates": [{**receipt["coordinates"][0], "scored": 1}]},
    ]:
        with pytest.raises(ValidationError):
            NewsSource.model_validate_json(json.dumps({**receipt, **changed}))


def test_same_payload_instance_is_still_a_duplicate():
    query = news.EODHDCompanyNewsFetcher.transform_query({"symbol": "AAPL", "chart_source": True})
    result = news.EODHDCompanyNewsFetcher.transform_data(query, [NEUTRAL, NEUTRAL])
    assert len(result.result) == 1
    receipt, = result.metadata["chart_sources"]["receipts"]
    assert receipt["diagnostics"] == ["exact_duplicate"]


@pytest.mark.parametrize(("url", "accepted"), [
    ("https://news.example/article?edition=2#section", True),
    ("HTTP://WWW.EXAMPLE.COM.:8443/story", True),
    ("http://192.0.2.1/story", True),
    ("https://[2001:db8::1]:443/story", True),
    ("https://news.example/crónica", True),
    ("https://999.999.999.999/article", False),
    ("http://192.00.2.1/story", False),
    ("http://0x7f.1/story", False),
    ("https://news.123/article", False),
    ("https://news..example/article", False),
    ("https://-news.example/article", False),
    ("https://xn--bcher-kva.example/article", False),
    ("https://[2001:db8:::1]/story", False),
    ("https://news.example:65536/story", False),
    ("https://user:pass@news.example/story", False),
    ("https://news.example/story\u0085", False),
    ("https://news.example/story\ufeff", False),
])
def test_news_original_link_matches_the_client_identity_contract(url, accepted):
    from datetime import date

    from pydantic import ValidationError

    from openbq_eodhd.models._news_chart_source import NewsCoordinate

    wire = {"article_id": url, "utc_day": date(2024, 2, 29), "scored": True}
    if accepted:
        assert NewsCoordinate.model_validate(wire).article_id == url
    else:
        with pytest.raises(ValidationError):
            NewsCoordinate.model_validate(wire)


def test_mounted_news_retains_sibling_and_raw_identity_failure_for_a_malformed_host(news_http):
    client, rest = news_http
    rest.return_value = [NEUTRAL, dict(UNSCORED, link="https://999.999.999.999/article")]

    response = client.get("/api/v1/eodhd/news/company", params={"symbol": "AAPL", "chart_source": True})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert [row["url"] for row in payload["results"]] == [NEUTRAL["link"]]
    receipt, = payload["extra"]["results_metadata"]["chart_sources"]["receipts"]
    assert (receipt["raw_count"], receipt["normalized_count"], receipt["delivered_count"]) == (2, 1, 1)
    assert receipt["unsafe"]["invalid_identity"] is True
    assert receipt["diagnostics"] == ["invalid_identity"]
    assert receipt["coordinates"] == [{"article_id": NEUTRAL["link"], "utc_day": "2024-02-29", "scored": True}]
    rest.assert_called_once()
