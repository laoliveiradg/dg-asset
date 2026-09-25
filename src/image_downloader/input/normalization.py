"""Conservative URL normalization for local input ingestion."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


def _clean_outer_punctuation(value: str) -> str:
    """Remove punctuation accidentally captured at the edges of a URL."""
    return value.strip().strip("'\"`<>[](){}.,;:!?")


def normalize_url(raw_url: str) -> str:
    """Normalize a URL conservatively while preserving query and fragment semantics.

    The strategy is deliberately narrow: lower-case scheme and host, simplify trailing
    slashes when safe, strip accidental surrounding punctuation, and keep query and
    fragment components to avoid surprising changes in behavior.
    """
    candidate = _clean_outer_punctuation(raw_url or "")
    if not candidate:
        return ""

    parsed = urlsplit(candidate)
    if not parsed.scheme or not parsed.netloc:
        return candidate

    scheme = parsed.scheme.lower()
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/") if parsed.path not in ("", "/") else parsed.path

    normalized = urlunsplit((scheme, host, path, parsed.query, parsed.fragment))
    return normalized
