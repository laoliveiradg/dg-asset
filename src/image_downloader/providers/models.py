"""Provider classification data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ProviderId(StrEnum):
    """Supported provider identifiers."""

    ASSETWAY = "ASSETWAY"
    SHUTTERSTOCK = "SHUTTERSTOCK"
    ENVATO = "ENVATO"
    UNKNOWN = "UNKNOWN"


@dataclass(slots=True)
class ProviderMatch:
    """Classification result for a single URL."""

    provider: ProviderId
    normalized_url: str
    hostname: str
    asset_reference: str | None = None
    details: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class ProviderClassification:
    """A URL record enriched with provider metadata."""

    record: object
    provider_match: ProviderMatch
