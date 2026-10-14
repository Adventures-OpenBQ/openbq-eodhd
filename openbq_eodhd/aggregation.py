"""Provider-owned, versioned aggregation guidance, not an aggregation engine.

Rules are keyed by OpenBB model and normalized output field. Unknown fields
fail closed. Consumers must validate the listed conditions before using a
suggestion; this module never infers reporting basis from a frequency label.
"""

from collections.abc import Iterable
from typing import Any

SCHEMA_VERSION = "1.0.0"
RULESET_VERSION = "2026-09-20.1"
GLOSSARY = "https://eodhd.com/financial-academy/financial-faq/fundamentals-glossary-common-stock"
FUNDAMENTALS = "https://eodhd.com/financial-apis/stock-etfs-fundamental-data-feeds"
EOD = "https://eodhd.com/financial-apis/api-for-historical-data-and-volumes"

# Explicit reviewed allowlists, not "all numeric columns" or "all fields in a
# statement". Contract tests require new mapping entries to receive a review.
INCOME_FLOWS = (
    "totalRevenue",
    "costOfRevenue",
    "grossProfit",
    "researchDevelopment",
    "sellingGeneralAdministrative",
    "totalOperatingExpenses",
    "operatingIncome",
    "ebit",
    "ebitda",
    "depreciationAndAmortization",
    "interestExpense",
    "interestIncome",
    "incomeBeforeTax",
    "incomeTaxExpense",
    "netIncome",
    "netIncomeApplicableToCommonShares",
)
BALANCE_STOCKS = (
    "totalAssets",
    "totalCurrentAssets",
    "cashAndEquivalents",
    "cashAndShortTermInvestments",
    "netReceivables",
    "inventory",
    "propertyPlantAndEquipmentNet",
    "goodWill",
    "intangibleAssets",
    "totalLiab",
    "totalCurrentLiabilities",
    "accountsPayable",
    "shortTermDebt",
    "longTermDebt",
    "shortLongTermDebtTotal",
    "netDebt",
    "totalStockholderEquity",
    "retainedEarnings",
    "commonStock",
)
CASH_FLOWS = (
    "totalCashFromOperatingActivities",
    "totalCashflowsFromInvestingActivities",
    "totalCashFromFinancingActivities",
    "capitalExpenditures",
    "freeCashFlow",
    "depreciation",
    "stockBasedCompensation",
    "dividendsPaid",
    "netIncome",
    "changeInCash",
)
STATEMENT_MODELS = ("IncomeStatement", "BalanceSheet", "CashFlowStatement")


def _statement(model: str) -> tuple[str, dict[str, str]]:
    """The EODHD bundle section and raw->normalized field map for a statement.

    The maps live in models/fundamental.py, which in turn imports this module
    to stamp its Data classes with widget hints -- so they are read here, at
    call time, rather than at import time. Same maps, no cycle.
    """
    # pylint: disable=import-outside-toplevel
    from openbq_eodhd.models.fundamental import BALANCE_MAP, CASHFLOW_MAP, INCOME_MAP

    return {
        "IncomeStatement": ("Income_Statement", INCOME_MAP),
        "BalanceSheet": ("Balance_Sheet", BALANCE_MAP),
        "CashFlowStatement": ("Cash_Flow", CASHFLOW_MAP),
    }[model]


# The frequencies each model kind was reviewed at. A suggestion is honest only
# at these; a consumer bucketing to anything else must fall back to "choose".
STATEMENT_PERIODS = ("annual", "quarter")
HISTORICAL_INTERVALS = ("1d", "1W", "1M")
HISTORICAL_MODELS = (
    "EquityHistorical",
    "EtfHistorical",
    "CryptoHistorical",
    "CurrencyHistorical",
)
BAR_OPERATORS = {
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "adjusted_close": "last",
    "volume": "sum",
}
COMMON_CONDITIONS = [
    "same_entity",
    "compatible_units_and_currency",
    "unique_observations_after_revision_selection",
    "complete_target_period",
    "consistent_calendar_and_period_boundaries",
]


def _rule(
    raw_field: str | None,
    semantic_type: str,
    operator: str | None,
    reason: str,
    sources: list[str],
    conditions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "source_field": raw_field,
        "semantic_type": semantic_type,
        "suggested_time_aggregation": operator,
        # A suggestion is not authorization to calculate. This starter catalog
        # cannot verify runtime completeness, reporting basis or revision state.
        "auto_apply": False,
        "entity_aggregation": None,
        "status": "conditional" if operator else "requires_selection",
        "conditions": list(COMMON_CONDITIONS) + (conditions or []),
        "reason": reason,
        "provenance": {
            "kind": "provider_curated",
            "sources": sources,
            "reviewed_on": "2026-09-20",
        },
    }


def _statement_fields(model: str) -> dict[str, dict[str, Any]]:
    _, mapping = _statement(model)
    fields = {}
    flow_fields = INCOME_FLOWS if model == "IncomeStatement" else CASH_FLOWS
    if model == "BalanceSheet":
        for raw in BALANCE_STOCKS:
            fields[mapping[raw]] = _rule(
                raw,
                "period_end_stock",
                "last",
                "Ending balance, not an amount accumulated over time.",
                [GLOSSARY],
                ["boundary_observation_present"],
            )
        # The vendor glossary describes this as a weighted diluted share count.
        # Do not treat it as an ordinary end-of-period balance merely because
        # it resides in Balance_Sheet.
        raw = "commonStockSharesOutstanding"
        fields[mapping[raw]] = _rule(
            raw,
            "share_count_basis_requires_review",
            None,
            "Verify point-in-time versus weighted/diluted share-count basis.",
            [GLOSSARY],
        )
    else:
        for raw in flow_fields:
            fields[mapping[raw]] = _rule(
                raw,
                "period_flow",
                "sum",
                "Sum only verified standalone, non-overlapping period amounts; "
                "quarterly frequency alone does not establish that basis.",
                [GLOSSARY, FUNDAMENTALS],
                ["standalone_period_basis_verified", "non_overlapping_periods"],
            )
        if model == "CashFlowStatement":
            for raw, semantic, operator in (
                ("beginPeriodCashFlow", "period_start_stock", "first"),
                ("endPeriodCashFlow", "period_end_stock", "last"),
            ):
                fields[mapping[raw]] = _rule(
                    raw,
                    semantic,
                    operator,
                    "Cash at the period boundary, not a cash-flow total.",
                    [GLOSSARY],
                    ["boundary_observation_present"],
                )
    for field in ("period_ending", "fiscal_year", "fiscal_period"):
        raw = "date" if field == "period_ending" else None
        fields[field] = _rule(
            raw,
            "dimension",
            None,
            "Grouping or ordering key; never sum numeric fiscal-year labels.",
            [FUNDAMENTALS],
        )
        fields[field]["status"] = "dimension"
    return fields


def _bar_fields() -> dict[str, dict[str, Any]]:
    fields = {}
    for field, operator in BAR_OPERATORS.items():
        if field == "volume":
            semantic, adjustment = "period_volume", "splits"
        elif field == "adjusted_close":
            semantic, adjustment = "closing_price", "splits_and_dividends"
        else:
            semantic, adjustment = "bar_price", "raw"
        fields[field] = _rule(
            field,
            semantic,
            operator,
            "Roll up non-overlapping EOD bars within one instrument and "
            "consistent adjustment basis; never mix raw and adjusted prices.",
            [EOD],
            ["non_overlapping_periods", "consistent_adjustment_basis_and_vintage"],
        )
        fields[field]["adjustment_basis"] = adjustment
    for field in ("date", "symbol"):
        fields[field] = _rule(
            field,
            "dimension",
            None,
            "Grouping or ordering key.",
            [EOD],
        )
        fields[field]["status"] = "dimension"
    return fields


def get_aggregation_metadata(
    model: str,
    *,
    interval: str | None = None,
    period: str | None = None,
    fields: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Return fresh JSON-serializable metadata for normalized output fields.

    ``model`` is an OpenBB standard-model name. Historical guidance currently
    covers EOD intervals only; intraday adjustment semantics need separate
    review. ``fields`` should be the actual output columns, including extras,
    so unknown vendor passthrough fields are explicitly marked for selection.

    No network calls, row changes, aggregation execution, or global mutations.
    This is an opt-in library API; fetchers do not yet attach this metadata.
    """
    if model in STATEMENT_MODELS:
        if period not in STATEMENT_PERIODS or interval is not None:
            raise ValueError("Statements require period='annual' or 'quarter', without interval.")
        section, _ = _statement(model)
        catalog = _statement_fields(model)
        order_by = "period_ending"
        endpoint = "/api/fundamentals"
        frequency = period
        reporting_basis = "unverified"
    elif model in HISTORICAL_MODELS:
        if interval not in HISTORICAL_INTERVALS or period is not None:
            raise ValueError("Historical guidance requires EOD interval 1d, 1W or 1M only.")
        section = None
        catalog = _bar_fields()
        order_by = "date"
        endpoint = "/api/eod"
        frequency = interval
        reporting_basis = "bar"
    else:
        raise ValueError(f"Unsupported aggregation model: {model}")

    if fields is not None:
        if isinstance(fields, (str, bytes)):
            raise TypeError("fields must be an iterable of field names, not a string.")
        selected = list(fields)
        if not all(isinstance(field, str) for field in selected):
            raise TypeError("Every field name must be a string.")
        catalog = {
            field: catalog.get(field)
            or _rule(
                None,
                "unknown",
                None,
                "No reviewed rule for this normalized field. Do not infer from "
                "its name, numeric type, or parent statement.",
                [],
            )
            for field in selected
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "ruleset_version": RULESET_VERSION,
        "provider": "eodhd",
        "model": model,
        "source_endpoint": endpoint,
        "source_section": section,
        "source_frequency": frequency,
        "reporting_basis": reporting_basis,
        "order_by": order_by,
        "order_direction": "ascending",
        "null_policy": "propagate",
        "incomplete_period_policy": "require_selection",
        "unknown_field_policy": "require_selection",
        "entity_aggregation_policy": "require_selection",
        "fields": catalog,
    }


# --- Transport: the dictionary as a per-column widgets.json hint ---------------
#
# OpenBB's widget generator (openbb_platform_api.utils.openapi
# data_schema_to_columns_defs) merges a field's ``x-widget_config`` verbatim
# into that column's ``columnsDefs`` entry. So the provider's own Data models
# are the transport: each covered model carries, per field, a minimal
# projection of its dictionary entry, and a frontend reads it off widgets.json
# with no new plumbing on either side. The models never restate a rule -- the
# hint is computed from get_aggregation_metadata at import time, so the two
# cannot drift.
#
# The projection is deliberately small (~80 bytes a column): the operator, the
# frequencies it was reviewed at, the entity policy, the status and the ruleset
# version. Reasons, conditions and provenance stay in the dictionary, where
# get_aggregation_metadata answers for them.

WIDGET_HINT_KEY = "aggregation"


def widget_hint(entry: dict[str, Any], frequencies: tuple[str, ...]) -> dict[str, Any]:
    """The per-column hint for one dictionary entry.

    ``time`` is the suggested operator or None; ``frequencies`` the reviewed
    source frequencies (the provider's own tokens -- statement periods or EOD
    intervals -- a consumer maps to its own buckets); ``entity`` is always
    ``require_selection`` because no entry defines a cross-entity rule;
    ``status`` is the dictionary's (``conditional``, ``requires_selection`` or
    ``dimension``). An unreviewed field gets an explicit ``time: None`` /
    ``requires_selection`` hint rather than none at all, so a consumer can tell
    "reviewed, no rule" from "no dictionary answered".
    """
    return {
        "time": entry.get("suggested_time_aggregation"),
        "frequencies": list(frequencies),
        "entity": "require_selection",
        "status": entry.get("status", "requires_selection"),
        "ruleset": RULESET_VERSION,
    }


def attach_widget_hints(model_cls: type, model: str) -> type:
    """Stamp every field of a provider Data class with its widget hint.

    Reads the dictionary for ``model`` over the class's own field names (so
    unreviewed fields get their explicit unknown), writes the hint into each
    field's ``json_schema_extra["x-widget_config"]`` and rebuilds the class so
    ``model_json_schema()`` -- what the generator reads -- carries it. A
    subclass's FieldInfo objects are its own copies, so the OpenBB standard
    model the class inherits from is left untouched; tests pin that.

    Returns the class, so a module can write ``X = attach_widget_hints(X, ...)``
    or call it as a statement after the class body.
    """
    if model in STATEMENT_MODELS:
        frequencies = STATEMENT_PERIODS
        catalog = get_aggregation_metadata(
            model, period=frequencies[0], fields=list(model_cls.model_fields)
        )
    else:
        frequencies = HISTORICAL_INTERVALS
        catalog = get_aggregation_metadata(
            model, interval=frequencies[0], fields=list(model_cls.model_fields)
        )
    for name, info in model_cls.model_fields.items():
        extra = info.json_schema_extra if isinstance(info.json_schema_extra, dict) else {}
        widget_config = dict(extra.get("x-widget_config") or {})
        widget_config[WIDGET_HINT_KEY] = widget_hint(catalog["fields"][name], frequencies)
        info.json_schema_extra = {**extra, "x-widget_config": widget_config}
    model_cls.model_rebuild(force=True)
    return model_cls
