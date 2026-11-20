# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0

"""Shared transport for opt-in chart sources. Each source defines a closed subtype."""

from datetime import datetime, timedelta
from typing import Annotated, Generic, Literal, TypeVar

from openbb_core.provider.abstract.annotated_result import AnnotatedResult
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ChartSourceKind = Literal[
    "news", "ownership", "overlay", "estimate-vintages", "market-map", "etf-hierarchy", "metric"
]
Count = Annotated[int, Field(ge=0, le=2**53 - 1)]


class ChartSourceFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, revalidate_instances="always")


class _Versioned(ChartSourceFacts):
    version: Literal[1] = 1

    @field_validator("version", mode="before")
    @classmethod
    def _integer_version(cls, value):
        if type(value) is not int:
            raise ValueError("version must be an integer")
        return value


class ChartSourceReceipt(_Versioned):
    """Subclass with a literal kind, diagnostic vocabulary and typed unsafe facts."""

    kind: ChartSourceKind
    retrieved_at: datetime
    raw_count: Count
    normalized_count: Count
    delivered_count: Count
    diagnostics: list[str]
    unsafe: ChartSourceFacts

    @model_validator(mode="after")
    def _check_common(self):
        if type(self) is ChartSourceReceipt:
            raise ValueError("A closed family receipt is required")
        if self.retrieved_at.utcoffset() != timedelta(0):
            raise ValueError("retrieved_at must be in UTC")
        if not self.delivered_count <= self.normalized_count <= self.raw_count:
            raise ValueError("Acquisition counts must be in delivery order")
        if len(self.diagnostics) != len(set(self.diagnostics)):
            raise ValueError("Duplicate diagnostic")
        return self


Receipt = TypeVar("Receipt", bound=ChartSourceReceipt)


class ChartSourceEnvelope(_Versioned, Generic[Receipt]):
    """Specialize with the exact family model or discriminated union for this route."""

    receipts: list[Receipt] = Field(min_length=1, max_length=7)

    @model_validator(mode="after")
    def _unique_kinds(self):
        if type(self) in (ChartSourceEnvelope, ChartSourceEnvelope[ChartSourceReceipt]):
            raise ValueError("A closed family envelope is required")
        if len({receipt.kind for receipt in self.receipts}) != len(self.receipts):
            raise ValueError("Duplicate chart source kind")
        return self


Row = TypeVar("Row")


def annotate_chart_sources(
    result: list[Row] | AnnotatedResult[list[Row]], envelope: ChartSourceEnvelope[Receipt]
) -> AnnotatedResult[list[Row]]:
    """Attach a fresh, typed envelope to the same delivered rows and retain other metadata."""
    validated = type(envelope).model_validate(envelope)
    rows = result.result if isinstance(result, AnnotatedResult) else result
    metadata = (result.metadata or {}) if isinstance(result, AnnotatedResult) else {}
    if "chart_sources" in metadata:
        raise ValueError("chart_sources is already annotated")
    if rows is None or any(receipt.delivered_count != len(rows) for receipt in validated.receipts):
        raise ValueError("Chart source counts must match the delivered rows")
    return AnnotatedResult(
        result=rows, metadata={**metadata, "chart_sources": validated.model_dump(mode="json")}
    )
