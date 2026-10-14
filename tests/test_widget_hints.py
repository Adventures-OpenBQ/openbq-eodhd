"""The aggregation dictionary reaches widgets.json as a per-column hint.

OpenBB's widget generator merges a field's ``x-widget_config`` straight into
that column's ``columnsDefs`` entry, so the provider's own Data models are the
transport: each covered model carries, per field, the minimal projection of its
dictionary entry. These tests pin three things -- the projection's shape, that
every field of a covered model (reviewed or not) carries one, and that
OpenBB's standard models are left untouched.
"""

import pytest

from openbb_core.provider.standard_models.balance_sheet import BalanceSheetData
from openbb_core.provider.standard_models.cash_flow import CashFlowStatementData
from openbb_core.provider.standard_models.equity_historical import EquityHistoricalData
from openbb_core.provider.standard_models.income_statement import IncomeStatementData
from openbq_eodhd.aggregation import (
    HISTORICAL_INTERVALS,
    RULESET_VERSION,
    STATEMENT_PERIODS,
    get_aggregation_metadata,
    widget_hint,
)
from openbq_eodhd.models.crypto_historical import EODHDCryptoHistoricalData
from openbq_eodhd.models.currency_historical import EODHDCurrencyHistoricalData
from openbq_eodhd.models.equity_historical import EODHDEquityHistoricalData
from openbq_eodhd.models.fundamental import (
    CASHFLOW_MAP,
    INCOME_MAP,
    EODHDBalanceSheetData,
    EODHDCashFlowStatementData,
    EODHDIncomeStatementData,
)

COVERED = [
    (EODHDIncomeStatementData, "IncomeStatement", {"period": "quarter"}),
    (EODHDBalanceSheetData, "BalanceSheet", {"period": "quarter"}),
    (EODHDCashFlowStatementData, "CashFlowStatement", {"period": "quarter"}),
    (EODHDEquityHistoricalData, "EquityHistorical", {"interval": "1d"}),
    (EODHDCryptoHistoricalData, "CryptoHistorical", {"interval": "1d"}),
    (EODHDCurrencyHistoricalData, "CurrencyHistorical", {"interval": "1d"}),
]


def hint_of(model_cls, field: str) -> dict:
    # by_alias=False: Data generates camelCase validation aliases, but the
    # response schema OpenBB's generator reads is the serialization one, whose
    # keys are the snake field names. Read by field name to match it.
    prop = model_cls.model_json_schema(by_alias=False)["properties"][field]
    return prop["x-widget_config"]["aggregation"]


def test_widget_hint_is_the_minimal_projection():
    entry = get_aggregation_metadata("CashFlowStatement", period="quarter")["fields"]
    assert widget_hint(entry["cash_at_end_of_period"], STATEMENT_PERIODS) == {
        "time": "last",
        "frequencies": ["annual", "quarter"],
        "entity": "require_selection",
        "status": "conditional",
        "ruleset": RULESET_VERSION,
    }
    assert widget_hint(entry["period_ending"], STATEMENT_PERIODS)["status"] == "dimension"
    unknown = get_aggregation_metadata("CashFlowStatement", period="quarter", fields=["mystery"])
    assert widget_hint(unknown["fields"]["mystery"], STATEMENT_PERIODS) == {
        "time": None,
        "frequencies": ["annual", "quarter"],
        "entity": "require_selection",
        "status": "requires_selection",
        "ruleset": RULESET_VERSION,
    }


@pytest.mark.parametrize("model_cls,model,kwargs", COVERED)
def test_every_field_of_a_covered_model_carries_its_hint(model_cls, model, kwargs):
    frequencies = STATEMENT_PERIODS if "period" in kwargs else HISTORICAL_INTERVALS
    catalog = get_aggregation_metadata(model, fields=list(model_cls.model_fields), **kwargs)[
        "fields"
    ]
    schema = model_cls.model_json_schema(by_alias=False)
    # The generator scopes a schema to a provider by its description.
    assert schema["description"].lower().startswith("eodhd")
    for field in model_cls.model_fields:
        assert hint_of(model_cls, field) == widget_hint(catalog[field], frequencies), field


def test_the_three_cash_boundaries_and_flows_are_what_the_handoff_promised():
    assert hint_of(EODHDCashFlowStatementData, "cash_at_beginning_of_period")["time"] == "first"
    assert hint_of(EODHDCashFlowStatementData, "cash_at_end_of_period")["time"] == "last"
    assert hint_of(EODHDCashFlowStatementData, "net_change_in_cash")["time"] == "sum"
    assert hint_of(EODHDEquityHistoricalData, "open")["time"] == "first"
    assert hint_of(EODHDEquityHistoricalData, "volume")["time"] == "sum"
    # Reviewed as needing review, so no operator -- never inherited from the balance sheet.
    assert hint_of(EODHDBalanceSheetData, "common_shares_outstanding") == {
        "time": None,
        "frequencies": ["annual", "quarter"],
        "entity": "require_selection",
        "status": "requires_selection",
        "ruleset": RULESET_VERSION,
    }


def test_unreviewed_fields_carry_an_explicit_unknown_not_nothing():
    # vwap is a standard historical field the dictionary never reviewed.
    assert hint_of(EODHDEquityHistoricalData, "vwap")["status"] == "requires_selection"
    assert hint_of(EODHDEquityHistoricalData, "vwap")["time"] is None


def test_statement_subclasses_declare_every_mapped_line_item():
    assert set(INCOME_MAP.values()) <= set(EODHDIncomeStatementData.model_fields)
    assert set(CASHFLOW_MAP.values()) <= set(EODHDCashFlowStatementData.model_fields)
    # ...and still accept EODHD's passthrough extras, as the standard model does.
    row = EODHDIncomeStatementData.model_validate(
        {
            "period_ending": "2024-03-31",
            "fiscal_period": "Q1",
            "fiscal_year": 2024,
            "revenue": 1.5,
            "oddball": 2,
        }
    )
    assert row.revenue == 1.5
    assert row.model_extra == {"oddball": 2}


@pytest.mark.parametrize(
    "standard", [IncomeStatementData, BalanceSheetData, CashFlowStatementData, EquityHistoricalData]
)
def test_openbb_standard_models_are_untouched(standard):
    for prop in standard.model_json_schema(by_alias=False)["properties"].values():
        assert "x-widget_config" not in prop


def test_the_generator_merges_the_hint_into_columns_defs():
    openapi = pytest.importorskip("openbb_platform_api.utils.openapi")
    # FastAPI documents a response in serialization mode, whose alias is
    # to_snake -- the same keys the rows carry and the generator reads.
    schema = EODHDCashFlowStatementData.model_json_schema(mode="serialization")
    spec = {
        "paths": {
            "/api/v1/equity/fundamental/cash": {
                "get": {
                    "operationId": "cash",
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/OBBject"}
                                }
                            }
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "OBBject": {
                    "properties": {
                        "results": {
                            "anyOf": [
                                {
                                    "items": {
                                        "$ref": "#/components/schemas/EODHDCashFlowStatementData"
                                    }
                                }
                            ]
                        }
                    }
                },
                "EODHDCashFlowStatementData": schema,
            }
        },
    }
    columns = openapi.data_schema_to_columns_defs(
        spec, "cash", "eodhd", route="/api/v1/equity/fundamental/cash"
    )
    by_field = {c["field"]: c for c in columns}
    assert by_field["cash_at_end_of_period"]["aggregation"]["time"] == "last"
    assert by_field["net_change_in_cash"]["aggregation"]["time"] == "sum"
    assert by_field["period_ending"]["aggregation"]["status"] == "dimension"
