# Copyright 2026 SecretoftheUniverse.com LLC. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0

"""Closed ownership snapshots and the narrow ShareStatistics headline acquisition."""

import re
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator

from openbq_eodhd.models._chart_sources import ChartSourceFacts, ChartSourceReceipt

Diagnostic = Literal["identity_conflict", "unit_conflict", "date_conflict", "partition_conflict"]
Role = Literal["insiders", "institutions", "freeFloat"]
FieldOrigin = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")]
ShareClass = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")]
BucketKey = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")]
CertificateReference = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_./:-]{0,255}$")]
_SYMBOL = re.compile(r"[A-Z0-9^][A-Z0-9^_/-]*(?:\.[A-Z0-9^_/-]+)*\.[A-Z0-9][A-Z0-9_-]*")
_UTC_STAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
    r"(?:\.[0-9]{1,6})?(?:Z|\+00:00)",
)
_IDENTITY_FIELDS = {"symbol", "share_class", "as_of", "denominator", "constructor", "prototype"}
_HEADLINE_ORIGINS = {"insiders": "percent_insiders", "institutions": "percent_institutions"}
_US_VENUES = ("NASDAQ", "NYSE", "NYSE ARCA", "BATS")


class OwnershipUnsafe(ChartSourceFacts):
    identity_conflict: bool
    unit_conflict: bool
    date_conflict: bool
    partition_conflict: bool


class OwnershipMeasure(ChartSourceFacts):
    unit: Literal["percent-points", "fraction"]
    basis: Literal["shares-outstanding"] | None
    as_of: date | None


class OwnershipPartitionUnavailable(ChartSourceFacts):
    status: Literal["unavailable"]


class OwnershipPartitionVerified(ChartSourceFacts):
    status: Literal["verified"]
    as_of: date
    denominator: Literal["outstanding-shares"]
    exclusive: bool
    exhaustive: bool
    rounding_bound_percent: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
    reference: CertificateReference

    @model_validator(mode="after")
    def _verified_coverage(self):
        if not self.exclusive or not self.exhaustive:
            raise ValueError("Ownership partition must certify exclusive and exhaustive coverage")
        return self


class _OwnershipReceipt(ChartSourceReceipt):
    version: Literal[1]
    kind: Literal["ownership"]
    issuer: Annotated[str, Field(max_length=128)]
    share_class: ShareClass | None
    diagnostics: list[Diagnostic]
    unsafe: OwnershipUnsafe

    @field_validator("issuer")
    @classmethod
    def _qualified_issuer(cls, value):
        if not _SYMBOL.fullmatch(value):
            raise ValueError("Expected one qualified ownership issuer")
        return value

    @field_validator("retrieved_at", mode="before")
    @classmethod
    def _utc_wire_timestamp(cls, value, info: ValidationInfo):
        if info.mode != "json" or not isinstance(value, str):
            return value
        if not _UTC_STAMP.fullmatch(value):
            raise ValueError("Expected a version 1 UTC retrieval timestamp")
        return datetime.fromisoformat(value)

    @model_validator(mode="after")
    def _check_raw_facts(self):
        if any(flag != (key in self.diagnostics) for key, flag in self.unsafe.model_dump().items()):
            raise ValueError("Ownership diagnostics contradict unsafe facts")
        if self.normalized_count != self.delivered_count or not self.delivered_count:
            raise ValueError("Ownership requires delivery of all normalized snapshot rows")
        if self.raw_count > self.normalized_count and not self.diagnostics:
            raise ValueError("Unexplained ownership normalization drops")
        return self


def _check_origins(*origins: str):
    if len(set(origins)) != len(origins) or _IDENTITY_FIELDS.intersection(origins):
        raise ValueError("Ownership field origins must be distinct and cannot use reserved fields")


class _OwnershipBarsSource(_OwnershipReceipt):
    measures: dict[Role, OwnershipMeasure] = Field(min_length=1)
    field_origins: dict[Role, FieldOrigin] = Field(min_length=1)
    partition: OwnershipPartitionUnavailable

    @model_validator(mode="after")
    def _check_measures(self):
        if self.delivered_count != 1 or self.measures.keys() != self.field_origins.keys():
            raise ValueError("Independent bars require one row and an origin for each measure")
        _check_origins(*self.field_origins.values())
        return self


class OwnershipSnapshotBarsSource(_OwnershipBarsSource):
    origin: Literal["acquired-snapshot"]


class OwnershipLiveBarsSource(_OwnershipBarsSource):
    origin: Literal["eodhd-share-statistics"]
    share_class: None

    @model_validator(mode="after")
    def _check_headlines(self):
        if (
            not self.issuer.endswith(".US")
            or self.raw_count != 1
            or self.field_origins != _HEADLINE_ORIGINS
            or any(name != "unit_conflict" for name in self.diagnostics)
            or any(measure.unit != "percent-points" or measure.basis is not None or measure.as_of is not None
                   for measure in self.measures.values())
        ):
            raise ValueError("ShareStatistics can certify only the two headline ownership measures")
        return self


class OwnershipPartitionOrigins(ChartSourceFacts):
    bucket: FieldOrigin
    share: FieldOrigin


class OwnershipPartitionSource(_OwnershipReceipt):
    origin: Literal["acquired-snapshot"]
    share_class: ShareClass
    measures: dict[Role, OwnershipMeasure] = Field(max_length=0)
    field_origins: OwnershipPartitionOrigins
    partition: OwnershipPartitionVerified
    bucket_keys: list[BucketKey]

    @model_validator(mode="after")
    def _check_buckets(self):
        if len(self.bucket_keys) != self.delivered_count or len(set(self.bucket_keys)) != self.delivered_count:
            raise ValueError("Ownership partition requires one unique key per delivered bucket")
        _check_origins(self.field_origins.bucket, self.field_origins.share)
        return self


OwnershipSource = OwnershipLiveBarsSource | OwnershipSnapshotBarsSource | OwnershipPartitionSource


def share_statistics_source(
    bundle: dict, issuer: str, exchange: str, retrieved_at: datetime | None,
) -> tuple[OwnershipLiveBarsSource, dict] | None:
    """Bind only acquired primary U.S. common-stock headline percentages."""
    general, stats = bundle.get("General"), bundle.get("SharesStats")
    if (
        retrieved_at is None or exchange.upper() != "US" or len(issuer) > 128 or not _SYMBOL.fullmatch(issuer)
        or not issuer.endswith(".US") or not isinstance(general, dict) or not isinstance(stats, dict) or not stats
        or general.get("Code") != issuer.removesuffix(".US") or general.get("PrimaryTicker") != issuer
        or general.get("Exchange") not in _US_VENUES or general.get("Type") != "Common Stock"
    ):
        return None

    conflict = False
    values = {}
    for raw_field, field in (("PercentInsiders", "percent_insiders"), ("PercentInstitutions", "percent_institutions")):
        value = stats.get(raw_field)
        if value is not None and (type(value) not in (int, float) or not 0 <= value <= 100):
            conflict = True
            value = None
        values[field] = value

    return OwnershipLiveBarsSource(
        version=1, kind="ownership", origin="eodhd-share-statistics", issuer=issuer, share_class=None,
        retrieved_at=retrieved_at, raw_count=1, normalized_count=1, delivered_count=1,
        diagnostics=["unit_conflict"] if conflict else [],
        unsafe=OwnershipUnsafe(identity_conflict=False, unit_conflict=conflict, date_conflict=False, partition_conflict=False),
        field_origins=_HEADLINE_ORIGINS,
        measures={role: OwnershipMeasure(unit="percent-points", basis=None, as_of=None) for role in _HEADLINE_ORIGINS},
        partition=OwnershipPartitionUnavailable(status="unavailable"),
    ), values
