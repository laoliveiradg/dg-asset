"""Typed models for queue items and aggregate queue progress."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from image_downloader.input.models import UrlOccurrence
from image_downloader.providers.models import ProviderId


class QueueState(StrEnum):
    """Operational state of a queue item."""

    PENDING = "PENDING"
    READY = "READY"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class QueueError:
    """Safe operational failure data; technical detail is kept out of logs."""

    code: str
    safe_message: str
    provider: ProviderId
    retryable: bool
    technical_detail: str | None = None


@dataclass(frozen=True, slots=True)
class QueueItem:
    """One normalized URL and the provider and origin data associated with it."""

    item_id: str
    original_url: str
    normalized_url: str
    occurrences: tuple[UrlOccurrence, ...]
    provider: ProviderId
    asset_reference: str | None
    provider_details: dict[str, object]
    state: QueueState
    attempt_count: int
    error: QueueError | None
    error_history: tuple[QueueError, ...]
    blocked_reason: str | None
    batch_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class QueueSummary:
    """Counts for rendering status and state-based progress outside the queue."""

    total: int
    pending: int
    ready: int
    processing: int
    completed: int
    failed: int
    blocked: int
    provider_counts: dict[ProviderId, int]

    @property
    def settled(self) -> int:
        """Items that have reached a terminal state for the current batch."""

        return self.completed + self.failed + self.blocked

    @property
    def progress_fraction(self) -> float:
        """State-based progress from 0.0 to 1.0; empty queues have no progress."""

        if self.total == 0:
            return 0.0
        return self.settled / self.total