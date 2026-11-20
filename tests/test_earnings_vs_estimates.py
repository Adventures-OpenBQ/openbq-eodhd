# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0

from datetime import date
from math import nextafter

import pytest

from openbq_eodhd.models.earnings_vs_estimates import (
    EODHDEarningsVsEstimatesData as D,
    EODHDEarningsVsEstimatesFetcher as Fetcher,
    EODHDEarningsVsEstimatesQueryParams as QP,
    band_check,
    fiscal_label,
)


def _trend(avg, low, high, rlow, ravg, rhigh, eg=None, rg=None):
    return {
        "earningsEstimateAvg": avg, "earningsEstimateLow": low, "earningsEstimateHigh": high,
        "earningsEstimateNumberOfAnalysts": 20.0,
        "revenueEstimateAvg": ravg, "revenueEstimateLow": rlow, "revenueEstimateHigh": rhigh,
        "revenueEstimateNumberOfAnalysts": 18.0,
        "earningsEstimateGrowth": eg, "revenueEstimateGrowth": rg,
    }


EARNINGS = {
    "History": {
        "2026-09-30": {"reportDate": "2026-10-30", "epsActual": 2.05, "epsEstimate": 1.98},
        "2026-06-30": {"reportDate": "2026-07-30", "epsActual": 2.02, "epsEstimate": 1.88},
        "2025-06-30": {"reportDate": "2025-07-31", "epsActual": 1.57, "epsEstimate": 1.4},
        "2026-12-31": {"reportDate": "2027-01-30", "epsActual": None, "epsEstimate": 2.5},
    },
    "Trend": {"Quarterly": {
        "2026-09-30": _trend(2.9149, 2.83, 3.18, 154257600000, 156430011230, 158132000000, 0.3, 0.2),
        "2026-06-30": _trend(1.8924, 1.82, 1.99, 107516300000, 108959817930, 112168000000, 0.2054, 0.1587),
    }},
}
BUNDLE = {
    "General": {"FiscalYearEnd": "September"},
    "Financials": {"Income_Statement": {"quarterly": {
        "2026-09-30": {"totalRevenue": 114477000000},
        "2026-06-30": {"totalRevenue": 109417000000},
        "2025-06-30": {"totalRevenue": 94036000000},
    }}},
}


def _rows(earnings=EARNINGS, bundle=BUNDLE):
    q = QP(symbol="AAPL")
    return {r.period_ending: r for r in Fetcher.transform_data(q, {"earnings": earnings, "bundle": bundle})}


def test_fiscal_labels():
    assert fiscal_label(date(2026, 6, 30), "September") == "FQ3 2026"
    assert fiscal_label(date(2025, 12, 31), "September") == "FQ1 2026"
    assert fiscal_label(date(2026, 9, 30), "September") == "FQ4 2026"
    assert fiscal_label(date(2026, 5, 31), "May") == "FQ4 2026"
    assert fiscal_label(date(2026, 8, 31), "May") == "FQ1 2027"
    assert fiscal_label(date(2026, 8, 31), "Smarch") is None


def test_q3_band_passes():
    r = _rows()[date(2026, 6, 30)]
    assert r.band_status == "ok" and r.band_note is None
    assert r.eps_avg == 1.8924
    assert r.revenue_low == 107516300000
    assert r.eps_growth_avg == 20.54
    assert abs(r.eps_growth_actual - 28.66) < 0.01
    assert abs(r.revenue_growth_actual - 16.36) < 0.01
    assert r.fiscal_period == "FQ3 2026"


def test_q4_band_withheld():
    r = _rows()[date(2026, 9, 30)]
    assert r.band_status == "withheld"
    for f in ("eps_low", "eps_avg", "eps_high", "revenue_low", "revenue_avg", "revenue_high",
              "eps_analysts", "revenue_analysts", "eps_growth_avg", "revenue_growth_avg"):
        assert getattr(r, f) is None, f
    assert r.eps_actual == 2.05 and r.eps_consensus == 1.98
    assert r.revenue_actual == 114477000000
    assert r.band_note == (
        "EODHD's estimate range for this quarter doesn't match its recorded consensus "
        "(2.91 vs 1.98); range withheld."
    )


def test_band_check_edges():
    note = "EODHD recorded no consensus for this quarter, so its estimate range can't be checked; range withheld."
    assert band_check(1.03, 1.00, True)[0] == "ok"
    assert band_check(1.11, 1.00, True)[0] == "withheld"
    assert band_check(1.0, None, True) == ("withheld", note)
    assert band_check(1.0, 0.0, True) == ("withheld", note)
    assert band_check(None, 1.0, False) == ("no_estimate", None)
    assert band_check(1.0999, 1.0, True)[0] == "ok"
    assert band_check(1.1001, 1.0, True)[0] == "withheld"
    assert band_check(-1.1001, -1.0, True)[0] == "withheld"


@pytest.mark.parametrize(
    ("estimate", "consensus"),
    [(1.10, 1.00), (0.90, 1.00), (-1.10, -1.00), (-0.90, -1.00)],
)
def test_band_check_includes_exact_ten_percent_boundary(estimate, consensus):
    assert band_check(estimate, consensus, True) == ("ok", None)


@pytest.mark.parametrize("sign", [1, -1])
def test_band_check_withholds_just_beyond_ten_percent_boundary(sign):
    assert band_check(sign * nextafter(1.10, 2.0), sign * 1.00, True)[0] == "withheld"


def test_rows_are_reported_quarters_ascending():
    rows = _rows()
    assert list(rows) == [date(2025, 6, 30), date(2026, 6, 30), date(2026, 9, 30)]
    assert rows[date(2025, 6, 30)].band_status == "no_estimate"


def test_annual_trend_does_not_fill_missing_quarterly_trend():
    earnings = {
        **EARNINGS,
        "Trend": {
            **EARNINGS["Trend"],
            "Annual": {
                "2025-06-30": _trend(1.4, 1.3, 1.5, 90e9, 94e9, 98e9, 0.1, 0.1),
            },
        },
    }
    r = _rows(earnings)[date(2025, 6, 30)]
    assert r.band_status == "no_estimate"
    assert r.eps_avg is None and r.revenue_avg is None
    assert r.eps_actual == 1.57 and r.eps_consensus == 1.4


def test_row_keeps_period_ending_when_fiscal_year_end_is_unknown():
    r = _rows(bundle={**BUNDLE, "General": {}})[date(2026, 6, 30)]
    assert r.fiscal_period is None
    assert r.period_ending == date(2026, 6, 30)
    assert r.report_date == date(2026, 7, 30)


def test_null_quarterly_revenue_stays_null_even_with_annual_revenue():
    bundle = {
        "General": BUNDLE["General"],
        "Financials": {
            "Income_Statement": {
                "quarterly": {
                    "2026-09-30": {"totalRevenue": None},
                    "2025-09-30": {"totalRevenue": 100e9},
                },
                "yearly": {"2026-09-30": {"totalRevenue": 400e9}},
            },
        },
    }
    r = _rows(bundle=bundle)[date(2026, 9, 30)]
    assert r.revenue_actual is None and r.revenue_growth_actual is None
    assert r.eps_actual == 2.05


def test_growth_actual_null_without_year_ago_quarter():
    r = _rows()[date(2025, 6, 30)]
    assert r.eps_growth_actual is None and r.revenue_growth_actual is None


def test_percent_and_basis_metadata():
    for f in ("eps_growth_avg", "eps_growth_actual", "revenue_growth_avg", "revenue_growth_actual"):
        assert D.model_fields[f].json_schema_extra == {"x-widget_config": {"formatterFn": "percent"}}
    assert "not necessarily GAAP diluted" in D.model_fields["eps_actual"].description


def test_growth_actual_matches_year_ago_quarter_by_month():
    earnings = {"History": {
        "2025-09-27": {"epsActual": 1.5, "epsEstimate": 1.4},
        "2026-09-26": {"epsActual": 1.8, "epsEstimate": 1.7},
    }, "Trend": {}}
    bundle = {"General": {"FiscalYearEnd": "September"}, "Financials": {"Income_Statement": {"quarterly": {
        "2025-09-27": {"totalRevenue": 100.0}, "2026-09-26": {"totalRevenue": 110.0},
    }}}}
    r = _rows(earnings, bundle)[date(2026, 9, 26)]
    assert abs(r.eps_growth_actual - 20.0) < 1e-6
    assert abs(r.revenue_growth_actual - 10.0) < 1e-6


def test_growth_from_a_negative_year_ago_value_is_positive_when_it_improves():
    from openbq_eodhd.models.earnings_vs_estimates import _growth

    assert _growth(1.0, -1.0) == 200.0
    assert _growth(-0.5, -1.0) == 50.0
    assert _growth(-2.0, -1.0) == -100.0
    assert _growth(1.5, 1.0) == 50.0


def test_growth_is_unavailable_when_year_ago_value_is_zero():
    from openbq_eodhd.models.earnings_vs_estimates import _growth

    assert _growth(1.0, 0.0) is None


@pytest.mark.parametrize("count", ["NaN", "Infinity", "-Infinity"])
def test_unusable_optional_analyst_counts_preserve_reported_quarter(count):
    end = "2026-06-30"
    quarterly = EARNINGS["Trend"]["Quarterly"]
    estimates = {**quarterly[end], "earningsEstimateNumberOfAnalysts": count,
                 "revenueEstimateNumberOfAnalysts": count}
    earnings = {**EARNINGS, "Trend": {"Quarterly": {end: estimates}}}
    row = _rows(earnings)[date.fromisoformat(end)]
    assert row.band_status == "ok" and row.eps_avg == 1.8924
    assert row.eps_analysts is None and row.revenue_analysts is None
