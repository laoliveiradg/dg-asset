"""Offline provider detection package."""

from image_downloader.providers.models import (
    ProviderClassification,
    ProviderId,
    ProviderMatch,
)
from image_downloader.providers.registry import (
    DEFAULT_PROVIDER_REGISTRY,
    ProviderDetector,
    ProviderRegistry,
)


def classify_url(raw_url: str) -> ProviderMatch:
    return DEFAULT_PROVIDER_REGISTRY.classify(raw_url)


def classify_url_records(records: list[object]) -> list[ProviderClassification]:
    return DEFAULT_PROVIDER_REGISTRY.classify_records(records)


__all__ = [
    "ProviderClassification",
    "ProviderDetector",
    "ProviderId",
    "ProviderMatch",
    "ProviderRegistry",
    "classify_url",
    "classify_url_records",
]
