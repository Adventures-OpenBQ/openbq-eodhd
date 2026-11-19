# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0

"""Acquisition receipts preserve financial facts across row normalization."""

from datetime import date, datetime, timezone

import pytest
from openbb_core.provider.utils.errors import EmptyDataError, UnauthorizedError

from openbq_eodhd.models.corporate_actions import EODHDHistoricalDividendsFetcher
from openbq_eodhd.models.fundamental import EODHDIncomeStatementFetcher
from openbq_eodhd.models.screener import EODHDEquityScreenerFetcher
from tests.conftest import run_async
from tests.test_phase3_market import SCREENER_ROW


def test_screener_chart_receipt_precedes_drop():
    fetcher = EODHDEquityScreenerFetcher
    query = fetcher.transform_query({
        "chart_source": True, "exchange": "us", "sector": "Technology",
        "limit": 10, "offset": 5, "signals": "cross_above_50d_ma",
    })
    result = fetcher.transform_data(query, [
        dict(SCREENER_ROW, refund_5d_p=5),
        dict(SCREENER_ROW, code=None, exchange="XETRA", currency_symbol="EUR"),
        dict(SCREENER_ROW, code="BADDATE", last_day_data_date=None),
        dict(SCREENER_ROW, code="BADDATE2", last_day_data_date="2026-02-31"),
        dict(SCREENER_ROW, code="SAP", exchange="XETRA", currency_symbol="EUR",
             last_day_data_date="2026-08-27", refund_5d_p="NaN"),
    ])

    assert result.result[0].price_change_5d == 0.05
    assert result.result[0].dividend_yield == 0.004
    assert result.result[0].currency_symbol == "$"
    assert result.result[-1].price_change_5d is None
    receipt = result.metadata["chart_evidence"]
    assert receipt["raw_count"] == 5
    assert receipt["normalized_count"] == receipt["delivered_count"] == 4
    assert receipt["unsafe"]["unassignable_identity"]
    assert receipt["unsafe"]["unassignable_date"]
    assert receipt["cohorts"][1]["price_date"] == "2026-08-28"
    assert receipt["cohorts"][1]["denominations"] == ["EUR", "USD"]
    assert receipt["cohorts"][0]["denominations"] == ["EUR"]
    assert receipt["scope"] == {
        "sort": "market_capitalization.desc",
        "limit": 10,
        "offset": 5,
        "signals": "cross_above_50d_ma",
        "filters": [["exchange", "=", "us"], ["sector", "=", "Technology"]],
    }
    assert datetime.fromisoformat(receipt["retrieved_at"]).utcoffset().total_seconds() == 0


def test_screener_raw_duplicate_conflicts_are_date_local():
    fetcher = EODHDEquityScreenerFetcher
    query = fetcher.transform_query({"chart_source": True})
    complete = dict(SCREENER_ROW, refund_5d_p=5)
    older = dict(complete, last_day_data_date="2026-08-27")
    result = fetcher.transform_data(query, [
        older, older,
        complete, dict(complete, market_capitalization=None),
        dict(complete, code="IBM", refund_5d_p=None), dict(complete, code="IBM"),
    ])
    older_cohort, recent_cohort = result.metadata["chart_evidence"]["cohorts"]
    assert older_cohort["exact_duplicate_count"] == 1
    assert older_cohort["conflicting_duplicates"] is False
    assert recent_cohort["conflicting_duplicates"] is True


def test_screener_denomination_comes_from_the_row_and_unit_conflict_is_global():
    fetcher = EODHDEquityScreenerFetcher
    query = fetcher.transform_query({"chart_source": True, "exchange": "us"})
    result = fetcher.transform_data(query, [
        dict(SCREENER_ROW, code="SHOP", exchange="TO", refund_5d_p=5),
        dict(SCREENER_ROW, code="ALT", exchange="US", refund_5d_p=5,
             refund_5d_p_unit="fraction"),
    ])
    receipt = result.metadata["chart_evidence"]
    assert receipt["cohorts"][0]["unknown_denomination"] is True
    assert receipt["cohorts"][0]["denominations"] == ["USD"]
    assert receipt["unsafe"]["unit_conflict"] is True


def test_screener_malformed_optional_fields_do_not_erase_identity_audit():
    fetcher = EODHDEquityScreenerFetcher
    query = fetcher.transform_query({"chart_source": True})
    result = fetcher.transform_data(query, [
        dict(SCREENER_ROW, code={"wrong": "shape"}),
        dict(SCREENER_ROW, sector=["Technology"]),
    ])
    assert len(result.result) == 1
    assert result.result[0].symbol == "AAPL.US"
    assert result.result[0].sector is None
    assert result.metadata["chart_evidence"]["unsafe"]["unassignable_identity"]


DIVIDEND_QUERY = {
    "symbol": "SHEL", "exchange": "LSE",
    "start_date": "2026-01-01", "end_date": "2026-06-30", "chart_source": True,
}
DIVIDEND = {
    "date": "2026-02-12", "value": 0.2, "unadjustedValue": 0.8, "currency": "GBP",
    "period": "Special", "quote_currency": "GBX",
}


def test_dividend_empty_coverage_is_not_inferred(monkeypatch):
    from openbq_eodhd.models import corporate_actions

    calls = []
    response = []

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def get_historical_dividends_data(self, **kwargs):
            calls.append(kwargs)
            return response

    monkeypatch.setattr(corporate_actions, "get_client", lambda _: Client())
    result = run_async(EODHDHistoricalDividendsFetcher.fetch_data, DIVIDEND_QUERY)
    assert calls == [{
        "ticker": "SHEL.LSE", "date_from": "2026-01-01", "date_to": "2026-06-30",
    }]
    assert result.result == []
    receipt = result.metadata["chart_evidence"]
    assert receipt["qualified_symbol"] == "SHEL.LSE"
    assert receipt["coverage"] == {"status": "unknown"}
    assert receipt["raw_count"] == receipt["normalized_count"] == receipt["delivered_count"] == 0

    with pytest.raises(EmptyDataError):
        run_async(EODHDHistoricalDividendsFetcher.fetch_data,
                  dict(DIVIDEND_QUERY, chart_source=False))

    response = {"message": "Access denied"}
    for enabled in (False, True):
        with pytest.raises(UnauthorizedError, match="Access denied"):
            run_async(EODHDHistoricalDividendsFetcher.fetch_data,
                      dict(DIVIDEND_QUERY, chart_source=enabled))

    response = None
    with pytest.raises(UnauthorizedError):
        run_async(EODHDHistoricalDividendsFetcher.fetch_data, DIVIDEND_QUERY)


def test_dividend_raw_dates_preserve_gaps_and_refusals():
    fetcher = EODHDHistoricalDividendsFetcher
    query = fetcher.transform_query(DIVIDEND_QUERY)
    result = fetcher.transform_data(query, [
        DIVIDEND, dict(DIVIDEND),
        dict(DIVIDEND, date="2026-02-30", value=0.15),
        dict(DIVIDEND, date="2026-03-12", value=None, unadjustedValue=9.99),
        dict(DIVIDEND, date="2026-04-12", value=-0.1),
        dict(DIVIDEND, date="2026-05-12", value="Infinity", currency="USD"),
        dict(DIVIDEND, date="2026-06-12", value=0, period=None),
    ])
    assert [(row.amount, row.currency, row.frequency) for row in result.result] == [
        (0.2, "GBP", "Special"), (0.2, "GBP", "Special"), (0, "GBP", None),
    ]
    assert result.result[0].unadjusted_amount == 0.8
    receipt = result.metadata["chart_evidence"]
    assert receipt["raw_count"] == 7
    assert receipt["normalized_count"] == receipt["delivered_count"] == 3
    assert receipt["amount_basis"] == "split-adjusted-current-share"
    assert receipt["event_dates"] == [
        "2026-02-12", "2026-03-12", "2026-04-12", "2026-05-12", "2026-06-12",
    ]
    assert receipt["invalid_amount_dates"] == ["2026-03-12", "2026-04-12", "2026-05-12"]
    assert receipt["duplicate_dates"] == ["2026-02-12"]
    assert receipt["payment_currencies"] == ["GBP", "USD"]
    assert receipt["unsafe"]["invalid_ex_date"]
    assert receipt["unsafe"]["currency_conflict"]
    assert "9.99" not in str(receipt)


def test_dividend_malformed_metadata_keeps_the_dated_currency_refusal():
    fetcher = EODHDHistoricalDividendsFetcher
    query = fetcher.transform_query(DIVIDEND_QUERY)
    result = fetcher.transform_data(query, [dict(DIVIDEND, currency=["GBP"], period=100)])
    assert result.result[0].ex_dividend_date == date(2026, 2, 12)
    assert result.result[0].currency is None
    assert result.result[0].frequency is None
    assert result.metadata["chart_evidence"]["unsafe"]["unknown_payment_currency"]


@pytest.mark.parametrize(("requested_start", "rows", "status", "count"), [
    ("2026-01-01", [DIVIDEND], "verified", 1),
    ("2026-01-01", [], "verified", 0),
    ("2025-12-31", [DIVIDEND], "clipped", 1),
])
def test_dividend_explicit_snapshot_coverage(requested_start, rows, status, count):
    from openbq_eodhd.models._chart_evidence import DividendSnapshotCoverage
    from openbq_eodhd.models.corporate_actions import normalize_dividend_snapshot

    acquisition = DividendSnapshotCoverage(
        qualified_symbol="SHEL.LSE", start=date(2026, 1, 1), end=date(2026, 6, 30),
        reference="fixtures/shel-dividends-2026h1-v1",
    )
    query = EODHDHistoricalDividendsFetcher.transform_query(
        dict(DIVIDEND_QUERY, start_date=requested_start)
    )
    result = normalize_dividend_snapshot(query, rows, acquisition)
    assert len(result.result) == count
    assert result.metadata["chart_evidence"]["coverage"] == {
        "status": status,
        "authority": "explicit-snapshot",
        "reference": "fixtures/shel-dividends-2026h1-v1",
        "start": "2026-01-01", "end": "2026-06-30",
    }


INCOME_COMPLETE = {
    "date": "2026-06-30", "currency_symbol": "USD", "totalRevenue": "100",
    "costOfRevenue": "40", "grossProfit": "60", "operatingIncome": "30",
    "incomeBeforeTax": "25", "netIncome": "20", "netIncomeApplicableToCommonShares": "19",
    "incomeTaxExpense": "5", "totalOperatingExpenses": "70",
}
INCOME_SECTION = {
    "currency_symbol": "USD",
    "yearly": {"2026-06-30": INCOME_COMPLETE, "2025-06-30": {"date": "2025-06-30"}},
}


def test_income_currency_is_statement_owned(monkeypatch):
    from openbq_eodhd.models import _fundamentals

    async def get_bundle(symbol, exchange, _):
        assert _fundamentals.qualify(symbol, exchange) == "HSBA.LSE"
        return {
            "General": {"CurrencyCode": "GBX", "FiscalYearEnd": "June"},
            "Financials": {"Income_Statement": INCOME_SECTION},
        }

    monkeypatch.setattr(_fundamentals, "get_bundle", get_bundle)
    result = run_async(EODHDIncomeStatementFetcher.fetch_data, {
        "symbol": "HSBA.LSE", "limit": 1, "chart_source": True,
    })
    assert result.result[0].revenue == 100
    assert result.result[0].net_income == 20
    receipt = result.metadata["chart_evidence"]
    assert receipt["qualified_symbol"] == "HSBA.LSE"
    assert receipt["reporting_currency"] == "USD"
    assert receipt["scale"] == {"status": "unknown"}
    assert receipt["period_kind"] == "FY"
    assert receipt["available_period_count"] == 2
    assert receipt["raw_count"] == receipt["normalized_count"] == receipt["delivered_count"] == 1
    assert receipt["periods"][0]["period_end"] == "2026-06-30"
    assert receipt["periods"][0]["record_coverage"] == "full-record"
    assert "net_income" in receipt["periods"][0]["present_fields"]


def test_income_explicit_scale_does_not_certify_truncated_fields_or_conflicting_currency():
    from openbq_eodhd.models._chart_evidence import ExplicitSnapshotScale
    from openbq_eodhd.models.fundamental import normalize_income_snapshot

    query = EODHDIncomeStatementFetcher.transform_query(
        {"symbol": "HSBA", "exchange": "LSE", "chart_source": True, "period": "quarter"}
    )
    scale = ExplicitSnapshotScale(
        qualified_symbol="HSBA.LSE", multiplier=1000, reference="fixtures/hsba-2026q2-v1",
    )
    block = {"currency_symbol": "USD", "quarterly": {
        "2026-06-30": dict(INCOME_COMPLETE, currency_symbol="EUR", netIncome=None),
        "2026-03-31": {"date": "2026-03-31", "totalRevenue": "80", "period_ending": "1999-01-01"},
    }}
    result = normalize_income_snapshot(
        query, block, scale, covered_periods={date(2026, 6, 30)}, fiscal_year_end=6,
        retrieved_at=datetime(2026, 11, 19, tzinfo=timezone.utc),
    )
    receipt = result.metadata["chart_evidence"]
    assert receipt["scale"] == {
        "status": "verified", "multiplier": 1000.0, "authority": "explicit-snapshot",
        "reference": "fixtures/hsba-2026q2-v1",
    }
    assert receipt["period_kind"] == "FQ"
    complete, truncated = receipt["periods"]
    assert complete["record_coverage"] == "full-record"
    assert complete["currency_conflict"]
    assert "net_income" not in complete["present_fields"]
    assert truncated["record_coverage"] == "unknown"
    assert truncated["identity_conflict"]


def test_income_explicit_snapshot_certifies_a_period_without_a_repeated_currency():
    from openbq_eodhd.models._chart_evidence import ExplicitSnapshotScale
    from openbq_eodhd.models.fundamental import normalize_income_snapshot

    fetcher = EODHDIncomeStatementFetcher
    query = fetcher.transform_query({"symbol": "HSBA.LSE", "chart_source": True, "period": "quarter"})
    scale = ExplicitSnapshotScale(
        qualified_symbol="HSBA.LSE", multiplier=1000, reference="fixtures/hsba-2026q2-v1",
    )
    block = {"currency_symbol": "USD", "quarterly": {
        "2026-06-30": dict(INCOME_COMPLETE, currency_symbol=None),
    }}
    result = normalize_income_snapshot(query, block, scale, covered_periods={date(2026, 6, 30)})
    receipt = result.metadata["chart_evidence"]
    assert receipt["reporting_currency"] == "USD"
    assert receipt["scale"]["multiplier"] == 1000
    assert result.result[0].revenue == 100
    assert result.result[0].fiscal_period is None
    period = receipt["periods"][0]
    assert period["period_kind"] == "FQ"
    assert period["record_coverage"] == "full-record"
    assert period["currency_conflict"] is False
    assert period["present_fields"] == [
        "cost_of_revenue", "gross_profit", "income_before_tax", "income_tax_expense",
        "net_income", "operating_income", "revenue",
    ]
