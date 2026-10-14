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
NON_MIRROR = {"/apps.json", "/ticks/fetch"}


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
