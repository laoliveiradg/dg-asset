"""Shared result models for provider download implementations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from image_downloader.providers.models import ProviderId


class DownloadStatus(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class DownloadTimings:
    navigation_ms: float = 0.0
    action_lookup_ms: float = 0.0
    quality_discovery_ms: float = 0.0
    download_start_ms: float = 0.0
    transfer_ms: float = 0.0
    validation_ms: float = 0.0
    total_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class DownloadFailure:
    code: str
    safe_message: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class DownloadResult:
    item_id: str
    provider: ProviderId
    status: DownloadStatus
    file_path: Path | None
    file_name: str | None
    extension: str | None
    bytes_received: int
    quality_label: str | None
    source_format: str | None
    timings: DownloadTimings
    error: DownloadFailure | None = None