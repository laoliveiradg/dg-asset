"""Data models for extracted URLs and their origin metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SourceType(StrEnum):
    """Origin category for a URL occurrence."""

    pasted_text = "pasted_text"
    slide_text = "slide_text"
    hyperlink = "hyperlink"
    comment = "comment"
    note = "note"
    relationship = "relationship"
    unknown = "unknown"


@dataclass(frozen=True, slots=True)
class UrlOccurrence:
    """One found URL plus metadata about where it was found."""

    original_url: str
    normalized_url: str
    source_type: SourceType
    source_name: str | None = None
    file_name: str | None = None
    slide_number: int | None = None
    location: str | None = None
    context: str | None = None


@dataclass(frozen=True, slots=True)
class UrlRecord:
    """A unique URL and all of its known origins."""

    normalized_url: str
    original_url: str
    occurrences: tuple[UrlOccurrence, ...] = ()
    source_types: tuple[SourceType, ...] = ()

    @classmethod
    def from_occurrences(cls, occurrences: list[UrlOccurrence]) -> UrlRecord:
        if not occurrences:
            raise ValueError("At least one occurrence is required.")
        ordered = list(occurrences)
        unique_key = ordered[0].normalized_url
        source_types = tuple(dict.fromkeys(item.source_type for item in ordered))
        return cls(
            normalized_url=unique_key,
            original_url=ordered[0].original_url,
            occurrences=tuple(ordered),
            source_types=source_types,
        )


@dataclass(slots=True)
class InputMetrics:
    """Lightweight metrics for local diagnostics."""

    read_time_ms: float = 0.0
    extraction_time_ms: float = 0.0
    urls_found: int = 0
    urls_unique: int = 0
    extra_data: dict[str, float | int | str] = field(default_factory=dict)
