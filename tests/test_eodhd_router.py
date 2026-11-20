# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0
"""obb.eodhd.* mirror parity: every standard-routable eodhd fetcher has a
mirror model mapped to the very same fetcher, and a mirror route at the
standard path under the eodhd prefix, offering only eodhd. Data-driven from
fetcher_dict and the loaded CommandMap -- NOT from eodhd_router.MIRRORS -- so
a missing or mistyped MIRRORS row (a generator bug) fails here on every line."""

import pytest
from openbb_core.app.router import CommandMap

from openbq_eodhd import eodhd_provider
from openbq_eodhd.eodhd_router import router

# Routes on the eodhd router that are not standard-path mirrors.
# /apps.json lands in Task 4; /ticks/fetch is the v12.4.13-only ticks alias.
# earnings_vs_estimates has a MIRRORS row but no standard path: eodhd-only.
NON_MIRROR = {"/apps.json", "/ticks/fetch", "/equity/estimates/earnings_vs_estimates"}


def _standard_paths() -> dict[str, str]:
    """Standard model -> path, from the loaded app (the mirrors' own /eodhd/ paths excluded)."""
    inv: dict[str, str] = {}
    for path, model in CommandMap().commands_model.items():
        if not path.startswith("/eodhd/"):
            inv.setdefault(model, path)
    return inv


def _mirror_routes() -> dict[str, object]:
    """Mirror path -> route object on the eodhd router."""
    return {r.path: r for r in router.api_router.routes if getattr(r, "path", "")}


# The keys present on THIS line, minus the Eodhd* aliases the __init__ loop added.
STANDARD_KEYS = [k for k in eodhd_provider.fetcher_dict if not k.startswith("Eodhd")]
PATHS = _standard_paths()
ROUTABLE = [k for k in STANDARD_KEYS if k in PATHS]  # excludes EquityTicks (no path)


@pytest.mark.parametrize("std_key", ROUTABLE)
def test_mirror_is_same_fetcher(std_key):
    # Spec Testing #3 (user ruling 2026-09-30): identical fetcher objects cannot
    # return different rows, so this identity check replaces a rows fixture.
    assert eodhd_provider.fetcher_dict[f"Eodhd{std_key}"] is eodhd_provider.fetcher_dict[std_key]


@pytest.mark.parametrize("std_key", ROUTABLE)
def test_mirror_route_present(std_key):
    assert PATHS[std_key] in _mirror_routes(), f"missing mirror for {std_key} at {PATHS[std_key]}"


@pytest.mark.parametrize("std_key", ROUTABLE)
def test_mirror_path_binds_its_own_model(std_key):
    # Guards a swapped MIRRORS row: exchanged paths would still exist, offer only
    # eodhd and have unique ids, yet serve the wrong model. The loaded CommandMap
    # maps each mounted path to the model it dispatches.
    assert CommandMap().commands_model["/eodhd" + PATHS[std_key]] == f"Eodhd{std_key}"


def test_no_extra_mirror_routes():
    # every mirror route corresponds to a routable standard key (no phantom routes)
    expected = {PATHS[k] for k in ROUTABLE}
    got = set(_mirror_routes()) - NON_MIRROR
    assert got == expected


def test_mirror_schema_offers_only_eodhd_with_unique_operation_ids():
    """In the REST schema each mirror exists, offers only eodhd, and has its own operationId."""
    from openbb_core.api.rest_api import app as rest_app

    paths = rest_app.openapi()["paths"]
    op_ids = []
    for std_key in ROUTABLE:
        full = "/api/v1/eodhd" + PATHS[std_key]
        assert full in paths, f"{full} not in schema"
        op = paths[full]["get"]
        op_ids.append(op["operationId"])
        prov = next((p for p in op.get("parameters", []) if p["name"] == "provider"), None)
        assert prov is not None, f"{full} has no provider param"
        # A single-provider param is emitted as {"const": "eodhd"}, not {"enum": [...]}
        # (verified on core 1.6.13); accept either so a core bump doesn't false-fail.
        schema = prov["schema"]
        assert (schema.get("enum") or [schema.get("const")]) == ["eodhd"], f"{full}: {schema}"
        assert op.get("widget_config", {}).get("category") == "EODHD", f"{full} widget_config"
    assert len(set(op_ids)) == len(ROUTABLE), "generated mirrors share an operationId"


def test_apps_json_widget_ids_exist():
    import json
    from pathlib import Path

    from openbb_core.api.rest_api import app
    from openbb_platform_api.utils.widgets import build_json

    from openbq_eodhd import eodhd_router as er

    apps = json.loads(Path(er.__file__).with_name("assets").joinpath("apps.json").read_text())
    ids = {w["i"] for a in apps for t in a.get("tabs", {}).values() for w in t.get("layout", [])}
    assert ids, "apps.json has no widgets"
    # the Workspace widget id is what /widgets.json emits, not the operationId.
    # Only the MIRROR family counts (endpoint under /api/v1/eodhd/, ids prefixed
    # eodhd_); the standard routes with provider=eodhd share the suffix but not
    # the endpoint.
    widgets = build_json(app.openapi(), [])
    known = {k for k, v in widgets.items() if v["endpoint"].startswith("/api/v1/eodhd/")}
    assert ids <= known, f"dangling widget ids: {ids - known}"


def test_standard_routes_are_loaded():
    # The parity tests are parametrized over ROUTABLE, which comes from the
    # loaded CommandMap. With only openbb-core installed it is empty and every
    # one of them passes by collecting nothing -- install the standard
    # extensions (CI: openbb[routers]==<Dockerfile OPENBB_VERSION>) so they check.
    assert ROUTABLE, "no standard routes loaded: install openbb, not just openbb-core"


def test_earnings_vs_estimates_route():
    path = "/equity/estimates/earnings_vs_estimates"
    commands = CommandMap().commands_model
    assert path not in commands
    assert (
        commands["/eodhd" + path] == "EodhdEarningsVsEstimates"
    )
    from openbb_core.api.rest_api import app as rest_app

    op = rest_app.openapi()["paths"]["/api/v1/eodhd" + path]["get"]
    prov = next(p for p in op["parameters"] if p["name"] == "provider")
    schema = prov["schema"]
    assert (schema.get("enum") or [schema.get("const")]) == ["eodhd"]


def test_widget_generator_formats_five_day_change_as_a_fractional_percent():
    from openbb_core.api.rest_api import app
    from openbb_platform_api.utils.widgets import build_json

    widgets = build_json(app.openapi(), [])
    screener = next(
        w for w in widgets.values() if w["endpoint"].endswith("/eodhd/equity/screener")
    )
    columns = {column["field"]: column for column in screener["data"]["table"]["columnsDefs"]}
    assert columns["price_change_5d"]["formatterFn"] == "normalizedPercent"
    assert columns["currency_symbol"]["cellDataType"] == "text"


@pytest.fixture
def chart_source_http(monkeypatch):
    from unittest.mock import MagicMock

    from fastapi.testclient import TestClient
    from openbb_core.api.rest_api import app
    from openbb_core.app.model.credentials import Credentials
    from openbb_core.app.model.user_settings import UserSettings
    from openbb_core.app.service.user_service import UserService

    from openbq_eodhd.models import _client, _fundamentals, corporate_actions

    monkeypatch.setattr(
        UserService, "read_from_file",
        staticmethod(lambda: UserSettings.model_construct(
            credentials=Credentials(eodhd_api_key="TEST_KEY")
        )),
    )
    sdk = MagicMock()
    monkeypatch.setattr(_client, "get_client", lambda _: sdk)
    monkeypatch.setattr(corporate_actions, "get_client", lambda _: sdk)
    monkeypatch.setattr(_fundamentals, "get_client", lambda _: sdk)
    monkeypatch.setattr(_fundamentals, "_cache", {})
    monkeypatch.setattr(_fundamentals, "_l2_get", lambda _: None)
    monkeypatch.setattr(_fundamentals, "_l2_put", lambda *_: None)
    return TestClient(app, raise_server_exceptions=False), sdk


@pytest.mark.parametrize("prefix", ["", "/eodhd"])
def test_chart_source_uses_the_normal_http_envelope(prefix, chart_source_http):
    from tests.test_chart_evidence import INCOME_SECTION
    from tests.test_phase3_market import SCREENER_ROW

    client, sdk = chart_source_http
    sdk.stock_market_screener.return_value = {"data": [dict(SCREENER_ROW, refund_5d_p=5)]}
    sdk.get_historical_dividends_data.return_value = []
    sdk.get_fundamentals_data.return_value = {"Financials": {"Income_Statement": INCOME_SECTION}}

    def get(path, **params):
        return client.get(
            "/api/v1" + prefix + path, params={"provider": "eodhd", **params},
        )

    screener = get("/equity/screener", chart_source=True)
    assert screener.status_code == 200, screener.text
    assert screener.json()["extra"]["results_metadata"]["chart_evidence"]["kind"] == "screener"
    assert screener.json()["results"][0]["price_change_5d"] == 0.05
    legacy = get("/equity/screener", chart_source=False)
    assert legacy.status_code == 200, legacy.text
    assert "results_metadata" not in legacy.json()["extra"]
    sdk.stock_market_screener.return_value = {"data": []}
    empty_screen = get("/equity/screener", chart_source=True)
    assert empty_screen.status_code == 200, empty_screen.text
    assert empty_screen.json()["results"] == []
    assert empty_screen.json()["extra"]["results_metadata"]["chart_evidence"]["raw_count"] == 0
    assert get("/equity/screener", chart_source=False).status_code == 204

    bounds = {"symbol": "SHEL.LSE", "start_date": "2026-01-01", "end_date": "2026-06-30"}
    dividend = get("/equity/fundamental/dividends", chart_source=True, **bounds)
    assert dividend.status_code == 200, dividend.text
    assert dividend.json()["results"] == []
    receipt = dividend.json()["extra"]["results_metadata"]["chart_evidence"]
    assert receipt["kind"] == "dividends"
    assert receipt["coverage"] == {"status": "unknown"}
    assert receipt["requested_start"] == "2026-01-01"
    assert receipt["requested_end"] == "2026-06-30"
    assert get("/equity/fundamental/dividends", chart_source=False, **bounds).status_code == 204

    income = get("/equity/fundamental/income", symbol="HSBA.LSE", chart_source=True, limit=1)
    assert income.status_code == 200, income.text
    receipt = income.json()["extra"]["results_metadata"]["chart_evidence"]
    assert receipt["kind"] == "income"
    assert receipt["reporting_currency"] == "USD"
    assert receipt["scale"] == {"status": "unknown"}
    assert income.json()["results"][0]["net_income"] == 20
    legacy_income = get("/equity/fundamental/income", symbol="HSBA.LSE", chart_source=False, limit=1)
    assert legacy_income.status_code == 200, legacy_income.text
    legacy_row, = legacy_income.json()["results"]
    chart_row, = income.json()["results"]
    assert legacy_row["fiscal_year"] == 2026
    assert chart_row.get("fiscal_year") is None
    assert {**chart_row, "fiscal_year": 2026} == legacy_row
    assert "results_metadata" not in legacy_income.json()["extra"]
    assert sdk.stock_market_screener.call_count == 4
    assert sdk.get_historical_dividends_data.call_count == 2
    assert sdk.get_fundamentals_data.call_count == 1


@pytest.mark.parametrize(("bundle", "chart_status", "legacy_status"), [
    pytest.param({"Financials": {"Income_Statement": {"yearly": {}}}}, 200, 204, id="empty-bucket"),
    pytest.param({"message": "Access denied"}, 502, 204, id="denial"),
    pytest.param([], 502, 204, id="wrong-bundle-type"),
    pytest.param({"Financials": []}, 502, 204, id="empty-financials-array"),
    pytest.param({"Financials": ["corrupt"]}, 502, 500, id="nonempty-financials-array"),
    pytest.param({"Financials": {}}, 502, 204, id="missing-income-section"),
    pytest.param({"Financials": {"Income_Statement": {}}}, 502, 204, id="missing-period-bucket"),
    pytest.param({"Financials": {"Income_Statement": {"yearly": []}}}, 502, 204, id="wrong-bucket-type"),
])
def test_income_chart_source_distinguishes_empty_bucket_from_acquisition_error(
    bundle, chart_status, legacy_status, chart_source_http,
):
    client, sdk = chart_source_http
    sdk.get_fundamentals_data.return_value = bundle
    endpoint = "/api/v1/eodhd/equity/fundamental/income"
    params = {"provider": "eodhd", "symbol": "HSBA.LSE"}
    opted_in = client.get(endpoint, params=dict(params, chart_source=True))
    assert opted_in.status_code == chart_status, opted_in.text
    if chart_status == 200:
        assert opted_in.json()["results"] == []
        receipt = opted_in.json()["extra"]["results_metadata"]["chart_evidence"]
        assert receipt["raw_count"] == receipt["delivered_count"] == 0
        assert receipt["periods"] == []
    else:
        assert "detail" in opted_in.json()
    sdk.get_fundamentals_data.assert_called_once_with("HSBA.LSE")
    assert client.get(endpoint, params=dict(params, chart_source=False)).status_code == legacy_status
