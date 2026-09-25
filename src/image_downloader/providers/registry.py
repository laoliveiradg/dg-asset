"""Central registry for local provider classification."""

from __future__ import annotations

from urllib.parse import urlsplit

from image_downloader.input.models import UrlRecord
from image_downloader.providers.models import ProviderClassification, ProviderId, ProviderMatch


class ProviderDetector:
    """Base interface for provider detectors."""

    provider_id: ProviderId = ProviderId.UNKNOWN

    def matches(self, hostname: str, path: str) -> bool:
        raise NotImplementedError

    def extract_reference(self, hostname: str, path: str, query: str) -> str | None:
        return None

    def build_details(self, hostname: str, path: str, query: str) -> dict[str, object]:
        return {}


class ProviderRegistry:
    """Register detectors and classify URLs offline."""

    def __init__(self, detectors: list[ProviderDetector] | None = None) -> None:
        self.detectors = detectors or []

    def register(self, detector: ProviderDetector) -> None:
        self.detectors.append(detector)

    def classify(self, raw_url: str) -> ProviderMatch:
        from image_downloader.providers.detectors import (
            ASSETWAY_DETECTOR,
            ENVATO_DETECTOR,
            SHUTTERSTOCK_DETECTOR,
        )

        if not raw_url or not raw_url.strip():
            return ProviderMatch(
                provider=ProviderId.UNKNOWN,
                normalized_url="",
                hostname="",
                asset_reference=None,
                details={"reason": "empty_url"},
            )

        try:
            parsed = urlsplit(raw_url)
        except ValueError:
            return ProviderMatch(
                provider=ProviderId.UNKNOWN,
                normalized_url=raw_url,
                hostname="",
                asset_reference=None,
                details={"reason": "invalid_url"},
            )

        hostname = parsed.hostname.lower() if parsed.hostname else ""
        path = parsed.path or ""
        query = parsed.query or ""
        normalized = raw_url.strip()

        detectors = [
            ASSETWAY_DETECTOR,
            SHUTTERSTOCK_DETECTOR,
            ENVATO_DETECTOR,
            *self.detectors,
        ]
        for detector in detectors:
            if detector.matches(hostname, path):
                asset_reference = detector.extract_reference(hostname, path, query)
                return ProviderMatch(
                    provider=detector.provider_id,
                    normalized_url=normalized,
                    hostname=hostname,
                    asset_reference=asset_reference,
                    details=detector.build_details(hostname, path, query),
                )

        return ProviderMatch(
            provider=ProviderId.UNKNOWN,
            normalized_url=normalized,
            hostname=hostname,
            asset_reference=None,
            details={"reason": "unknown_provider"},
        )

    def classify_many(self, urls: list[str]) -> list[ProviderMatch]:
        return [self.classify(url) for url in urls]

    def classify_record(self, record: UrlRecord) -> ProviderClassification:
        match = self.classify(record.normalized_url)
        return ProviderClassification(record=record, provider_match=match)

    def classify_records(self, records: list[UrlRecord]) -> list[ProviderClassification]:
        return [self.classify_record(record) for record in records]


DEFAULT_PROVIDER_REGISTRY = ProviderRegistry()
