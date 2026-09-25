"""Text-based URL extraction for pasted content and generic strings."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

from image_downloader.input.models import InputMetrics, SourceType, UrlOccurrence, UrlRecord
from image_downloader.input.normalization import normalize_url

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"'`]+", re.IGNORECASE)


def _clean_candidate(value: str) -> str:
    cleaned = value.strip().strip("'\"`<>[](){}.,;:!?")
    return cleaned


def extract_urls_from_text(
    text: str,
    *,
    source_type: SourceType = SourceType.pasted_text,
    source_name: str | None = None,
    file_name: str | None = None,
    slide_number: int | None = None,
    location: str | None = None,
    metrics: InputMetrics | None = None,
) -> list[UrlOccurrence]:
    """Extract URLs from plain text without trying to identify the provider."""
    if not text or not text.strip():
        return []

    occurrences: list[UrlOccurrence] = []
    for match in _URL_RE.finditer(text):
        candidate = _clean_candidate(match.group(0))
        if not candidate:
            continue
        normalized = normalize_url(candidate)
        if not normalized:
            continue
        occurrences.append(
            UrlOccurrence(
                original_url=candidate,
                normalized_url=normalized,
                source_type=source_type,
                source_name=source_name or source_type.value,
                file_name=file_name,
                slide_number=slide_number,
                location=location,
                context=text[max(0, match.start() - 12) : match.end() + 12],
            )
        )

    if metrics is not None:
        metrics.urls_found += len(occurrences)
        metrics.urls_unique = len(consolidate_urls(occurrences))

    logger.debug("Extracted %d URL occurrences from text input.", len(occurrences))
    return occurrences


def consolidate_urls(items: Iterable[UrlOccurrence | UrlRecord]) -> list[UrlRecord]:
    """Merge duplicate normalized URLs while preserving all original occurrences."""
    by_normalized: dict[str, list[UrlOccurrence]] = {}

    for item in items:
        if isinstance(item, UrlRecord):
            occurrences = list(item.occurrences)
        else:
            occurrences = [item]

        for occurrence in occurrences:
            by_normalized.setdefault(occurrence.normalized_url, []).append(occurrence)

    consolidated: list[UrlRecord] = []
    for normalized_url, occurrences in by_normalized.items():
        ordered = sorted(
            occurrences,
            key=lambda occ: (
                occ.source_type.value,
                occ.source_name or "",
                occ.file_name or "",
                occ.slide_number or -1,
            ),
        )
        record = UrlRecord(
            normalized_url=normalized_url,
            original_url=ordered[0].original_url,
            occurrences=tuple(ordered),
            source_types=tuple(dict.fromkeys(item.source_type for item in ordered)),
        )
        consolidated.append(record)

    return sorted(consolidated, key=lambda record: record.normalized_url.lower())
