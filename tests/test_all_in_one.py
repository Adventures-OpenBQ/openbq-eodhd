# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0

"""The All-In-One endpoints wired after 9.0.1: rates, credit risk, SEC filings
and segments, commodities, house prices, index constituents, real yields and
the options chain.

Row shapes are trimmed copies of live responses (2026-11-18), except the
options rows, which copy the example in EODHD's OpenAPI description because
the options feed is a separate subscription. Nothing here calls EODHD.
"""

from datetime import date, datetime

import pytest
from openbb_core.provider.utils.errors import EmptyDataError

from openbq_eodhd import eodhd_provider
from openbq_eodhd.models import _jsonapi
from openbq_eodhd.models._jsonapi import fetch_rows, stored_percent
from openbq_eodhd.models.commodity import (
    EODHDCommoditySpotPricesFetcher, EODHDCommoditySpotPricesQueryParams,
)
from openbq_eodhd.models.credit import (
    EODHDHighQualityMarketCorporateBondFetcher,
    EODHDHighQualityMarketCorporateBondQueryParams,
    EODHDRiskPremiumFetcher, EODHDRiskPremiumQueryParams,
)
from openbq_eodhd.models.filings import (
    EODHDCompanyFilingsFetcher, EODHDCompanyFilingsQueryParams,
    EODHDRevenueBusinessLineFetcher, EODHDRevenueBusinessLineQueryParams,
    EODHDRevenueGeographicFetcher, EODHDRevenueGeographicQueryParams,
    edgar_index_url,
)
from openbq_eodhd.models.index import (
    EODHDIndexConstituentsFetcher, EODHDIndexConstituentsQueryParams,
)
from openbq_eodhd.models.options import (
    EXTRA_FIELDS, STANDARD_FIELDS, EODHDOptionsChainsData, EODHDOptionsChainsFetcher,
    EODHDOptionsChainsQueryParams, _filters,
)
from openbq_eodhd.models.rates import (
    ECB_CODES, EODHDEuropeanCentralBankInterestRatesFetcher,
    EODHDEuropeanCentralBankInterestRatesParams, EODHDFederalFundsRateFetcher,
    EODHDSOFRData, EODHDSOFRFetcher, EODHDSOFRQueryParams, EODHDSONIAData, EODHDSONIAFetcher,
    EODHDSONIAQueryParams,
)
from openbq_eodhd.models.real_estate import (
    EODHDHousePriceIndexFetcher, EODHDHousePriceIndexQueryParams, quarter_end,
)
from openbq_eodhd.models.treasury import EODHDTipsYieldsFetcher, EODHDTipsYieldsQueryParams

NEW_KEYS = [
    "CompanyFilings", "RevenueBusinessLine", "RevenueGeographic", "IndexConstituents",
    "OptionsChains", "SOFR", "FederalFundsRate", "OvernightBankFundingRate", "SONIA",
    "EuroShortTermRate", "EuropeanCentralBankInterestRates", "TipsYields",
    "HighQualityMarketCorporateBond", "RiskPremium", "CommoditySpotPrices", "HousePriceIndex",
]


@pytest.mark.parametrize("key", NEW_KEYS)
def test_registered_with_a_mirror_alias(key):
    assert eodhd_provider.fetcher_dict[f"Eodhd{key}"] is eodhd_provider.fetcher_dict[key]


def test_factory_fetchers_have_their_own_names():
    assert EODHDSOFRFetcher.__name__ == "EODHDSOFRFetcher"
    assert EODHDFederalFundsRateFetcher.__name__ == "EODHDFederalFundsRateFetcher"
    assert EODHDSOFRFetcher is not EODHDSONIAFetcher


# --- paging ------------------------------------------------------------------

class TestFetchRows:
    def _patch(self, monkeypatch, pages):
        calls = []

        def fake_rest_json(client, endpoint, params):
            calls.append(params)
            return pages[len(calls) - 1]

        monkeypatch.setattr(_jsonapi, "rest_json", fake_rest_json)
        monkeypatch.setattr(_jsonapi, "sdk_call", lambda cred, call, ctx: call(object()))
        return calls

    def test_follows_pages_until_a_short_one(self, monkeypatch):
        calls = self._patch(monkeypatch, [{"data": [1, 2]}, {"data": [3]}, {"data": [9]}])
        assert fetch_rows({}, "x", {"a": 1}, "rows", page_limit=2, max_pages=5) == [1, 2, 3]
        assert [c["page[offset]"] for c in calls] == [0, 2]
        assert calls[0]["a"] == 1

    def test_stops_at_the_page_budget(self, monkeypatch):
        self._patch(monkeypatch, [{"data": [1, 2]}, {"data": [3, 4]}, {"data": [5, 6]}])
        assert fetch_rows({}, "x", {}, "rows", page_limit=2, max_pages=2) == [1, 2, 3, 4]

    def test_single_resource_is_one_row(self, monkeypatch):
        self._patch(monkeypatch, [{"data": {"cik": "0000320193"}}])
        assert fetch_rows({}, "x", {}, "overview") == [{"cik": "0000320193"}]

    def test_empty_raises_unless_allowed(self, monkeypatch):
        self._patch(monkeypatch, [{"data": []}, {"data": []}])
        with pytest.raises(EmptyDataError):
            fetch_rows({}, "x", {}, "rows")
        assert fetch_rows({}, "x", {}, "rows", allow_empty=True) == []


def test_stored_percent_follows_the_standard_field():
    assert stored_percent(EODHDSOFRData, "rate", 3.92) == 0.0392  # tagged x-frontend_multiply
    assert stored_percent(EODHDSONIAData, "rate", 3.7331) == 3.7331  # untagged: as shown
    assert stored_percent(EODHDSOFRData, "rate", None) is None


# --- rates -------------------------------------------------------------------

SOFR_ROWS = [
    {"date": "2026-11-17", "code": "SOFR", "currency": "USD", "rate_type": "overnight",
     "rate": 3.92, "source": "NY_FED", "source_series_id": "secured/sofr",
     "percentiles": {"p1": 3.87, "p25": 3.9, "p75": 3.97, "p99": 3.99},
     "volume_billion_usd": 3173},
    {"date": "2026-11-16", "code": "SOFR", "currency": "USD", "rate_type": "overnight",
     "rate": 3.93, "source": "NY_FED", "source_series_id": "secured/sofr",
     "percentiles": {"p1": 3.87, "p25": 3.9, "p75": 3.98, "p99": 4},
     "volume_billion_usd": 3170},
    {"date": "2026-11-15", "code": "SOFR", "rate": None},
]


class TestRates:
    def test_sofr_is_normalized_oldest_first(self):
        rows = EODHDSOFRFetcher.transform_data(EODHDSOFRQueryParams(), SOFR_ROWS)
        assert [r.date for r in rows] == [date(2026, 11, 16), date(2026, 11, 17)]
        last = rows[-1]
        assert (last.rate, last.percentile_1, last.percentile_99) == (0.0392, 0.0387, 0.0399)
        assert last.volume == 3173.0 and last.source == "NY_FED"

    def test_sonia_keeps_the_percent_and_has_no_percentiles(self):
        rows = EODHDSONIAFetcher.transform_data(
            EODHDSONIAQueryParams(),
            [{"date": "2026-11-13", "code": "SONIA", "rate": 3.9832, "source": "BOE"}],
        )
        assert rows[0].rate == 3.9832
        assert not hasattr(rows[0], "percentile_1")

    def test_no_usable_rows_raises(self):
        with pytest.raises(EmptyDataError):
            EODHDSOFRFetcher.transform_data(EODHDSOFRQueryParams(), [{"date": None, "rate": 1}])

    def test_ecb_codes_and_rows(self):
        assert ECB_CODES == {"deposit": "ECB_DFR", "lending": "ECB_MLF", "refinancing": "ECB_MRO"}
        q = EODHDEuropeanCentralBankInterestRatesParams(interest_rate_type="deposit")
        rows = EODHDEuropeanCentralBankInterestRatesFetcher.transform_data(q, [
            {"date": "2026-11-18", "code": "ECB_DFR", "rate": 2.5},
            {"date": "2026-01-01", "code": "ECB_DFR", "rate": 2},
        ])
        assert [(r.date, r.rate) for r in rows] == [(date(2026, 1, 1), 2.0), (date(2026, 11, 18), 2.5)]


# --- real yields -------------------------------------------------------------

def test_tips_yields_window_order_and_unit():
    data = [
        {"date": "2026-11-17", "tenor": "30Y", "rate": 3.28},
        {"date": "2026-11-17", "tenor": "5Y", "rate": 2.64},
        {"date": "2026-11-14", "tenor": "5Y", "rate": 2.6},
        {"date": "2026-11-17", "tenor": "1.5M", "rate": 9.9},  # no standard tenor
    ]
    rows = EODHDTipsYieldsFetcher.transform_data(
        EODHDTipsYieldsQueryParams(start_date=date(2026, 11, 17)), data
    )
    assert [(r.maturity, r.value) for r in rows] == [("year_5", 0.0264), ("year_30", 0.0328)]
    assert rows[0].name == "5Y Treasury real yield" and rows[0].due is None


# --- credit ------------------------------------------------------------------

def test_risk_premium_is_in_percent_and_respects_the_constraints():
    rows = EODHDRiskPremiumFetcher.transform_data(EODHDRiskPremiumQueryParams(), [
        {"country_iso3": "DEU", "country_name": "Germany", "as_of_date": "2026-01-01T00:00:00+00:00",
         "moodys_rating": "Aaa", "adj_default_spread": 0, "country_risk_premium": 0,
         "equity_risk_premium": 0.0423, "corporate_tax_rate": 0.2993, "sovereign_cds": 0.0021,
         "region": "Western Europe"},
        {"country_iso3": "AGO", "country_name": "Angola", "as_of_date": "2026-01-01T00:00:00+00:00",
         "moodys_rating": "B3", "adj_default_spread": 0.055235, "country_risk_premium": 0.084143,
         "equity_risk_premium": 0.126443, "corporate_tax_rate": 0.25, "sovereign_cds": 0.0624,
         "region": "Africa"},
        {"country_name": None},
    ])
    assert [r.country for r in rows] == ["Angola", "Germany"]
    angola, germany = rows
    assert angola.total_equity_risk_premium == 12.6443 and angola.country_risk_premium == 8.4143
    assert angola.adjusted_default_spread == 5.5235 and angola.sovereign_cds == 6.24
    assert germany.country_risk_premium == 0.0 and germany.date == date(2026, 1, 1)
    assert germany.continent == "Western Europe" and germany.corporate_tax_rate == 29.93


HQM_ROWS = [
    {"series_id": "HQMCB10YRP", "tenor_years": 10, "yield_type": "par",
     "as_of_date": "2026-10-01T00:00:00+00:00", "yield_value": 5.92},
    {"series_id": "HQMCB2YRP", "tenor_years": 2, "yield_type": "par",
     "as_of_date": "2026-10-01T00:00:00+00:00", "yield_value": 5.16},
    {"series_id": "HQMCB2YRP", "tenor_years": 2, "yield_type": "par",
     "as_of_date": "2026-09-01T00:00:00+00:00", "yield_value": 5.02},
]


class TestHqm:
    def test_latest_curve_by_default_in_tenor_order(self):
        q = EODHDHighQualityMarketCorporateBondQueryParams(yield_curve="par")
        rows = EODHDHighQualityMarketCorporateBondFetcher.transform_data(q, HQM_ROWS)
        assert [(r.date, r.maturity, r.rate) for r in rows] == [
            (date(2026, 10, 1), "year_2", 0.0516), (date(2026, 10, 1), "year_10", 0.0592),
        ]

    def test_as_of_a_date_is_the_month_at_or_before_it(self):
        q = EODHDHighQualityMarketCorporateBondQueryParams(date="2026-09-20", yield_curve="par")
        rows = EODHDHighQualityMarketCorporateBondFetcher.transform_data(q, HQM_ROWS)
        assert [(r.date, r.rate) for r in rows] == [(date(2026, 9, 1), 0.0502)]

    def test_before_the_first_month_raises(self):
        q = EODHDHighQualityMarketCorporateBondQueryParams(date="2020-01-01")
        with pytest.raises(EmptyDataError):
            EODHDHighQualityMarketCorporateBondFetcher.transform_data(q, HQM_ROWS)


# --- filings and segments ----------------------------------------------------

def test_edgar_index_url():
    assert edgar_index_url("0000320193", "0000320193-26-000026") == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000026/"
        "0000320193-26-000026-index.htm"
    )
    assert edgar_index_url(None, "x") is None and edgar_index_url("1", None) is None


class TestCompanyFilings:
    def test_10k_rows_link_to_edgar(self):
        q = EODHDCompanyFilingsQueryParams(symbol="AAPL")
        rows = EODHDCompanyFilingsFetcher.transform_data(q, {"cik": "0000320193", "filings": [
            {"accession_number": "0000320193-26-000026", "filed_at": "2026-11-03",
             "period_of_report": "2026-09-26", "revenue": 478834000000,
             "net_income": 132175000000, "eps_diluted": 8.98, "total_assets": 404548000000},
            {"accession_number": None, "filed_at": "2025-10-31"},  # no link possible: dropped
        ]})
        assert len(rows) == 1
        row = rows[0]
        assert (row.filing_date, row.report_type) == (date(2026, 11, 3), "10-K")
        assert row.report_url.endswith("/0000320193-26-000026-index.htm")
        assert row.period_ending == date(2026, 9, 26) and row.eps_diluted == 8.98

    def test_8k_items_are_joined(self):
        q = EODHDCompanyFilingsQueryParams(symbol="AAPL", form_type="8-K")
        rows = EODHDCompanyFilingsFetcher.transform_data(q, {"cik": "320193", "filings": [
            {"accession_number": "0001140361-26-042798", "filed_at": "2026-11-09",
             "period_of_report": "2026-11-05", "items": ["8.01", "9.01"]},
        ]})
        assert rows[0].items == "8.01, 9.01" and rows[0].report_type == "8-K"

    def test_nothing_usable_raises(self):
        with pytest.raises(EmptyDataError):
            EODHDCompanyFilingsFetcher.transform_data(
                EODHDCompanyFilingsQueryParams(symbol="AAPL"), {"cik": None, "filings": [{}]}
            )


SEGMENTS = [
    {"axis": "Geographic", "member": "CN", "value": 82378000000, "unit": "USD",
     "concept": "RevenueFromContractWithCustomerExcludingAssessedTax", "period_end": "2026-09-26"},
    {"axis": "Geographic", "member": "US", "value": 167746000000, "unit": "USD",
     "concept": "RevenueFromContractWithCustomerExcludingAssessedTax", "period_end": "2026-09-26"},
    {"axis": "ProductOrService", "member": "IPhone", "value": 254098000000, "unit": "USD",
     "concept": "RevenueFromContractWithCustomerExcludingAssessedTax", "period_end": "2026-09-26"},
    {"axis": "ProductOrService", "member": "Mac", "value": None, "period_end": "2026-09-26"},
]


class TestSegments:
    def test_business_line_takes_only_products_and_services(self):
        rows = EODHDRevenueBusinessLineFetcher.transform_data(
            EODHDRevenueBusinessLineQueryParams(symbol="AAPL"), SEGMENTS
        )
        assert [(r.business_line, r.revenue, r.fiscal_year) for r in rows] == [
            ("IPhone", 254098000000, 2026)
        ]

    def test_geography_is_largest_first_within_a_period(self):
        rows = EODHDRevenueGeographicFetcher.transform_data(
            EODHDRevenueGeographicQueryParams(symbol="AAPL"), SEGMENTS
        )
        assert [r.region for r in rows] == ["US", "CN"]
        assert rows[0].period_ending == date(2026, 9, 26) and rows[0].currency == "USD"

    def test_an_axis_with_no_rows_raises(self):
        with pytest.raises(EmptyDataError):
            EODHDRevenueGeographicFetcher.transform_data(
                EODHDRevenueGeographicQueryParams(symbol="AAPL"), SEGMENTS[2:]
            )


# --- commodities, house prices, index constituents ---------------------------

class TestCommodity:
    DATA = {
        "meta": {"name": "Global price of Copper", "unit": "U.S. Dollars per Metric Ton"},
        "start": "2024-01-01",
        "rows": [
            {"date": "2025-12-01", "value": 11790.964091},
            {"date": "2024-01-01", "value": 8351.339565},
            {"date": "2023-12-01", "value": 8394.0},  # the endpoint ignores `from`
            {"date": "2026-01-01", "value": None},
        ],
    }

    def test_code_is_normalized_and_checked(self):
        assert EODHDCommoditySpotPricesQueryParams(commodity="natural gas").commodity == "NATURAL_GAS"
        with pytest.raises(ValueError):
            EODHDCommoditySpotPricesQueryParams(commodity="GOLD")  # EODHD answers 404 for it

    def test_window_is_applied_here_oldest_first(self):
        q = EODHDCommoditySpotPricesQueryParams(commodity="COPPER", end_date=date(2025, 12, 31))
        rows = EODHDCommoditySpotPricesFetcher.transform_data(q, self.DATA)
        assert [r.date for r in rows] == [date(2024, 1, 1), date(2025, 12, 1)]
        assert rows[0].symbol == "COPPER" and rows[0].unit == "U.S. Dollars per Metric Ton"
        assert rows[0].commodity == "Global price of Copper"


class TestHousePriceIndex:
    def test_quarter_end(self):
        assert quarter_end("2024-Q3") == date(2024, 9, 30)
        assert quarter_end("1970-Q1") == date(1970, 3, 31)
        assert quarter_end("2024") is None and quarter_end(None) is None

    def test_rows(self):
        q = EODHDHousePriceIndexQueryParams()
        assert (q.country, q.frequency, q.transform, q.price_basis) == (
            "united_states", "quarter", "index", "nominal"
        )
        rows = EODHDHousePriceIndexFetcher.transform_data(q, {"country": "United States", "rows": [
            {"period": "2026-Q2", "value": 236.2362, "type": "nominal", "metric": "index"},
            {"period": "1970-Q1", "value": 10.5731, "type": "nominal", "metric": "index"},
            {"period": "bad", "value": 1},
        ]})
        assert [(r.date, r.value) for r in rows] == [
            (date(1970, 3, 31), 10.5731), (date(2026, 6, 30), 236.2362)
        ]
        assert rows[0].country == "United States"


def test_index_constituents_heaviest_first():
    data = {
        "0": {"Code": "AIZ", "Exchange": "US", "Name": "Assurant, Inc.",
              "Sector": "Financial Services", "Industry": "Insurance", "Weight": 0.0003},
        "1": {"Code": "AAPL", "Exchange": "US", "Name": "Apple Inc.", "Sector": "Technology",
              "Industry": "Consumer Electronics", "Weight": 0.0812},
        "2": {"Name": "no code"},
    }
    q = EODHDIndexConstituentsQueryParams(symbol="GSPC")
    for payload in (data, {"General": {}, "Components": data}):
        rows = EODHDIndexConstituentsFetcher.transform_data(q, payload)
        assert [(r.symbol, r.weight) for r in rows] == [("AAPL.US", 0.0812), ("AIZ.US", 0.0003)]
        assert rows[0].sector == "Technology" and rows[0].name == "Apple Inc."


# --- options -----------------------------------------------------------------

# The example row of EODHD's OpenAPI description (mp_unicornbay_options_eod.yaml).
OPTION_ATTRIBUTES = {
    "contract": "AAPL270115P00450000", "underlying_symbol": "AAPL", "exp_date": "2027-01-15",
    "expiration_type": "monthly", "type": "put", "strike": 450, "exchange": "NASDAQ",
    "currency": "USD", "open": 0, "high": 0, "low": 0, "last": 245.9, "last_size": 0,
    "change": 0, "pctchange": 1.5, "previous": 0, "previous_date": "", "bid": 218.4,
    "bid_date": "2025-08-12 19:59:59", "bid_size": 115, "ask": 222.2,
    "ask_date": "2025-08-12 19:59:59", "ask_size": 17, "moneyness": 0.96, "volume": 0,
    "volume_change": 0, "volume_pctchange": 0, "open_interest": 0, "open_interest_change": 0,
    "open_interest_pctchange": 0, "volatility": 0.4863, "volatility_change": 0,
    "volatility_pctchange": 0, "theoretical": 222.2, "delta": -0.877218, "gamma": 0.001594,
    "theta": -0.012408, "vega": 0.482281, "rho": -1.0, "tradetime": "2025-06-08",
    "vol_oi_ratio": 0, "dte": 521, "midpoint": 220.3,
}
OPTION_ROWS = [
    {"id": "AAPL270115P00450000-2025-08-12", "type": "options-eod", "attributes": OPTION_ATTRIBUTES},
    {"id": "x", "type": "options-eod", "attributes": {"contract": None}},  # dropped
]


class TestOptionsChains:
    def test_every_mapped_name_is_a_model_column(self):
        columns = set(EODHDOptionsChainsData.model_fields)
        assert set(STANDARD_FIELDS.values()) <= columns
        assert set(EXTRA_FIELDS.values()) <= columns
        assert not set(STANDARD_FIELDS) & set(EXTRA_FIELDS)

    def test_documented_row_maps_to_the_standard_columns(self):
        q = EODHDOptionsChainsQueryParams(symbol="aapl")
        chain = EODHDOptionsChainsFetcher.transform_data(q, OPTION_ROWS)
        assert chain.contract_symbol == ["AAPL270115P00450000"]
        assert chain.underlying_symbol == ["AAPL"] and chain.option_type == ["put"]
        assert chain.expiration == [date(2027, 1, 15)] and chain.eod_date == [date(2025, 6, 8)]
        assert chain.strike == [450.0] and chain.dte == [521]
        assert chain.bid == [218.4] and chain.ask == [222.2] and chain.mark == [220.3]
        assert chain.bid_time == [datetime(2025, 8, 12, 19, 59, 59)]
        assert chain.last_trade_price == [245.9] and chain.theoretical_price == [222.2]
        assert chain.implied_volatility == [0.4863]
        assert (chain.delta, chain.gamma, chain.theta, chain.vega, chain.rho) == (
            [-0.877218], [0.001594], [-0.012408], [0.482281], [-1.0]
        )
        assert chain.change_percent == [0.015]  # 1.5 percent, stored normalized
        assert chain.moneyness == [0.96] and chain.expiration_type == ["monthly"]
        assert chain.exchange == ["NASDAQ"] and chain.currency == ["USD"]

    def test_plain_rows_without_the_jsonapi_wrapper_also_map(self):
        chain = EODHDOptionsChainsFetcher.transform_data(
            EODHDOptionsChainsQueryParams(symbol="AAPL"), [OPTION_ATTRIBUTES]
        )
        assert chain.contract_symbol == ["AAPL270115P00450000"]

    def test_no_contracts_raises(self):
        with pytest.raises(EmptyDataError):
            EODHDOptionsChainsFetcher.transform_data(
                EODHDOptionsChainsQueryParams(symbol="AAPL"), OPTION_ROWS[1:]
            )

    def test_filters(self):
        q = EODHDOptionsChainsQueryParams(
            symbol="aapl.us", option_type="call", expiration_gte=date(2026, 12, 1),
            strike_lte=300,
        )
        assert _filters(q, date(2026, 11, 13)) == {
            "filter[underlying_symbol]": "AAPL",
            "filter[tradetime_eq]": "2026-11-13",
            "filter[type]": "call",
            "filter[exp_date_from]": "2026-12-01",
            "filter[exp_date_to]": None,
            "filter[strike_from]": None,
            "filter[strike_to]": 300.0,
            "sort": "exp_date",
        }
