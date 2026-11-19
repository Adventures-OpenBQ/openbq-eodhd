# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0

"""The SDK must honour OpenBB's configured HTTP settings.

Every other provider reaches the network through openbb_core's helpers, so a
proxy / CA bundle / client cert / timeout set under python_settings.http
applies to all of them. This extension uses the official EODHD SDK, which
builds its own bare requests.Session -- these tests pin the wiring that closes
that gap, and the SDK-internal attribute names it depends on.
"""

import pytest

from openbq_eodhd.models._client import apply_openbb_http_settings, get_client


def test_pinned_sdk_still_exposes_the_attributes_we_configure():
    """Guards the SDK pin. If a bump renames these, this fails in CI rather
    than silently reverting provider="eodhd" to unconfigured HTTP."""
    from openbq_eodhd._vendor.eodhd import APIClient

    client = APIClient("demo")
    assert hasattr(client, "_session"), "SDK no longer has _session -- pin moved"
    assert hasattr(client, "_timeout"), "SDK no longer has _timeout -- pin moved"


def test_session_is_replaced_with_openbbs_configured_session():
    from openbq_eodhd._vendor.eodhd import APIClient

    client = APIClient("demo")
    before = client._session
    apply_openbb_http_settings(client)
    after = client._session
    # openbb-core hands back its own configured Session, carrying at minimum a
    # User-Agent that the SDK's bare Session does not set.
    assert after is not before
    assert after.headers.get("User-Agent")


def test_configured_timeout_is_applied(monkeypatch):
    import openbq_eodhd.models._client as mod
    from openbq_eodhd._vendor.eodhd import APIClient

    client = APIClient("demo")
    monkeypatch.setattr(
        "openbb_core.provider.utils.helpers.get_python_request_settings",
        lambda: {"timeout": 42},
    )
    apply_openbb_http_settings(client)
    assert client._timeout == (42.0, 42.0)
    assert mod  # keep the import meaningful for linters


def test_no_configured_timeout_leaves_the_sdk_default():
    from openbq_eodhd._vendor.eodhd import APIClient

    client = APIClient("demo")
    default = client._timeout
    apply_openbb_http_settings(client)
    assert client._timeout == default


def test_settings_failure_never_breaks_the_client(monkeypatch):
    """Transport preferences are a nicety; a failure resolving them must not
    take down a data request."""
    from openbq_eodhd._vendor.eodhd import APIClient

    client = APIClient("demo")
    monkeypatch.setattr(
        "openbb_core.provider.utils.helpers.get_requests_session",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    apply_openbb_http_settings(client)  # must not raise


def test_get_client_requires_a_credential():
    from openbb_core.provider.utils.errors import UnauthorizedError

    with pytest.raises(UnauthorizedError):
        get_client({})


# --- error mapping as served (O1, O3) ------------------------------------------
FAKE_KEY = "FAKEKEY0123456789abcd"


def _served(exc_or_call):
    """Serve one sdk_call through OpenBB's own exception handlers; return the
    response and the API log text."""
    import io
    import logging

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openbb_core.api.app_loader import AppLoader

    from openbq_eodhd.models._client import sdk_call

    def call(client):
        if callable(exc_or_call):
            return exc_or_call(client)
        raise exc_or_call

    app = FastAPI()
    AppLoader.add_exception_handlers(app)
    app.get("/x")(lambda: sdk_call({"eodhd_api_key": FAKE_KEY}, call, "bars for 'AAPL.US'"))
    log = io.StringIO()
    handler = logging.StreamHandler(log)
    logging.getLogger("uvicorn.error").addHandler(handler)
    try:
        resp = TestClient(app, raise_server_exceptions=False).get("/x")
    finally:
        logging.getLogger("uvicorn.error").removeHandler(handler)
    return resp, log.getvalue()


def _http_error(status):
    from openbq_eodhd._vendor.eodhd.errors import EODHDHTTPError

    return EODHDHTTPError(status, "{}", f"({status}) https://eodhd.com/api - boom")


def _closed_port(client):
    """A real requests connection error: its text quotes the URL with the key."""
    from openbq_eodhd._vendor.eodhd.APIs.BaseAPI import BaseAPI

    api = BaseAPI(session=client._session, timeout=(2.0, 2.0))
    api._api_url = "http://127.0.0.1:9/api"
    return api._rest_get_method(api_key=client._api_key, endpoint="eod", uri="AAPL.US")


@pytest.mark.parametrize(
    "source, status, message",
    [
        (lambda: _http_error(401), 502, "verify EODHD_API_KEY"),
        (lambda: _http_error(402), 502, "request limit or plan exhausted"),
        (lambda: _http_error(429), 502, "request limit or plan exhausted"),
        (lambda: _http_error(500), 502, "upstream unavailable"),
        (lambda: _http_error(404), 400, "failed"),
        (lambda: _closed_port, 502, "upstream unavailable"),
    ],
    ids=["401", "402", "429", "500", "404", "connection"],
)
def test_sdk_errors_are_served_with_the_right_status_and_no_key(source, status, message):
    resp, log = _served(source())
    assert resp.status_code == status
    assert message in resp.json()["detail"]
    assert FAKE_KEY not in resp.text
    assert FAKE_KEY not in log


def test_quota_is_not_reported_as_a_bad_key():
    resp, _ = _served(_http_error(402))
    assert "verify EODHD_API_KEY" not in resp.json()["detail"]


def test_mapped_errors_do_not_chain_the_keyed_original():
    import requests

    from openbq_eodhd.models._client import raise_sdk_error

    with pytest.raises(Exception) as info:
        raise_sdk_error(
            requests.ConnectionError(f"url: /api/eod/AAPL.US?api_token={FAKE_KEY}&fmt=json"),
            "bars",
        )
    assert FAKE_KEY not in str(info.value)
    assert "api_token=***" in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__


@pytest.mark.parametrize("sep", ["%3D", "%3d"])
def test_url_encoded_key_is_redacted(sep):
    from openbq_eodhd.models._client import raise_sdk_error

    with pytest.raises(Exception) as info:
        raise_sdk_error(RuntimeError(f"/api/eod/AAPL.US%3Fapi_token{sep}{FAKE_KEY}%26fmt%3Djson"), "bars")
    assert FAKE_KEY not in str(info.value)
    assert f"api_token{sep}***%26fmt" in str(info.value)
