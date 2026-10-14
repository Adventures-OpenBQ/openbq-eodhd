"""Offline aggregation contract and mapping-drift tests."""

import json

import pytest

from openbq_eodhd.aggregation import (
    BALANCE_STOCKS,
    BAR_OPERATORS,
    CASH_FLOWS,
    HISTORICAL_MODELS,
    INCOME_FLOWS,
    get_aggregation_metadata,
)
from openbq_eodhd.models.fundamental import BALANCE_MAP, CASHFLOW_MAP, INCOME_MAP


@pytest.mark.parametrize(
    "mapping,reviewed",
    [
        (INCOME_MAP, set(INCOME_FLOWS)),
        (BALANCE_MAP, set(BALANCE_STOCKS) | {"commonStockSharesOutstanding"}),
        (CASHFLOW_MAP, set(CASH_FLOWS) | {"beginPeriodCashFlow", "endPeriodCashFlow"}),
    ],
)
def test_mapping_changes_require_semantic_review(mapping, reviewed):
    assert set(mapping) == reviewed
    assert len(set(mapping.values())) == len(mapping)


@pytest.mark.parametrize("period", ["quarter", "annual"])
@pytest.mark.parametrize(
    "model,mapping",
    [
        ("IncomeStatement", INCOME_MAP),
        ("BalanceSheet", BALANCE_MAP),
        ("CashFlowStatement", CASHFLOW_MAP),
    ],
)
def test_statement_catalog_covers_normalized_fields(model, mapping, period):
    result = get_aggregation_metadata(model, period=period)
    assert set(result["fields"]) == set(mapping.values()) | {
        "period_ending",
        "fiscal_year",
        "fiscal_period",
    }
    for raw, normalized in mapping.items():
        assert result["fields"][normalized]["source_field"] == raw
    assert result["order_by"] == "period_ending"
    assert json.loads(json.dumps(result)) == result


def test_cash_flow_statement_has_three_different_operators():
    result = get_aggregation_metadata("CashFlowStatement", period="quarter")["fields"]
    assert result["cash_at_beginning_of_period"]["suggested_time_aggregation"] == "first"
    assert result["cash_at_end_of_period"]["suggested_time_aggregation"] == "last"
    assert result["net_change_in_cash"]["suggested_time_aggregation"] == "sum"


def test_quarter_frequency_does_not_assert_standalone_basis():
    result = get_aggregation_metadata("IncomeStatement", period="quarter")
    revenue = result["fields"]["revenue"]
    assert result["reporting_basis"] == "unverified"
    assert "standalone_period_basis_verified" in revenue["conditions"]
    assert revenue["auto_apply"] is False
    assert revenue["entity_aggregation"] is None


def test_share_count_is_not_automatically_a_balance():
    fields = get_aggregation_metadata("BalanceSheet", period="annual")["fields"]
    assert fields["common_shares_outstanding"]["suggested_time_aggregation"] is None
    assert fields["total_assets"]["suggested_time_aggregation"] == "last"


@pytest.mark.parametrize("model", HISTORICAL_MODELS)
@pytest.mark.parametrize("interval", ["1d", "1W", "1M"])
def test_eod_ohlcv_rules(model, interval):
    result = get_aggregation_metadata(model, interval=interval)
    for field, operator in BAR_OPERATORS.items():
        rule = result["fields"][field]
        assert rule["suggested_time_aggregation"] == operator
        assert rule["auto_apply"] is False
    assert result["fields"]["close"]["adjustment_basis"] == "raw"
    assert result["fields"]["adjusted_close"]["adjustment_basis"] == "splits_and_dividends"
    assert result["fields"]["volume"]["adjustment_basis"] == "splits"
    assert result["order_by"] == "date"
    assert result["null_policy"] == "propagate"


@pytest.mark.parametrize("field", ["revenue_ttm", "eps", "profit_margin", "new_vendor_field"])
def test_unreviewed_extras_fail_closed(field):
    result = get_aggregation_metadata("IncomeStatement", period="quarter", fields=[field])
    assert result["fields"][field]["suggested_time_aggregation"] is None
    assert result["fields"][field]["status"] == "requires_selection"


def test_projection_preserves_requested_order_and_empty_list():
    result = get_aggregation_metadata(
        "EquityHistorical",
        interval="1M",
        fields=["volume", "close", "unknown"],
    )
    assert list(result["fields"]) == ["volume", "close", "unknown"]
    assert get_aggregation_metadata("EquityHistorical", interval="1M", fields=[])["fields"] == {}


def test_returned_objects_are_independent():
    first = get_aggregation_metadata("IncomeStatement", period="quarter")
    first["fields"]["revenue"]["conditions"].clear()
    first["fields"]["revenue"]["provenance"]["sources"].clear()
    second = get_aggregation_metadata("IncomeStatement", period="quarter")
    assert second["fields"]["revenue"]["conditions"]
    assert second["fields"]["revenue"]["provenance"]["sources"]
    assert first["fields"]["gross_profit"]["conditions"]


@pytest.mark.parametrize(
    "model,kwargs",
    [
        ("Unknown", {}),
        ("IncomeStatement", {}),
        ("IncomeStatement", {"period": "monthly"}),
        ("IncomeStatement", {"period": "quarter", "interval": "1M"}),
        ("EquityHistorical", {}),
        ("EquityHistorical", {"interval": "1m"}),
        ("EquityHistorical", {"interval": "1Q"}),
        ("EquityHistorical", {"interval": "1M", "period": "quarter"}),
    ],
)
def test_unsupported_context_is_rejected(model, kwargs):
    with pytest.raises(ValueError):
        get_aggregation_metadata(model, **kwargs)


@pytest.mark.parametrize("fields", ["close", [123]])
def test_invalid_field_projection_is_rejected(fields):
    with pytest.raises(TypeError):
        get_aggregation_metadata("EquityHistorical", interval="1M", fields=fields)
