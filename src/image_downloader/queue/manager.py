"""Thread-safe in-memory queue manager with incremental URL deduplication."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import replace
from datetime import UTC, datetime
from threading import RLock
from uuid import uuid4

from image_downloader.input.models import UrlRecord
from image_downloader.providers.models import ProviderClassification, ProviderId
from image_downloader.providers.registry import DEFAULT_PROVIDER_REGISTRY, ProviderRegistry
from image_downloader.queue.exceptions import (
    QueueInvariantError,
    QueueItemNotFoundError,
)
from image_downloader.queue.models import QueueError, QueueItem, QueueState, QueueSummary
from image_downloader.queue.state_machine import validate_transition

logger = logging.getLogger(__name__)


class QueueManager:
    """Own item state, URL indexes, and aggregate summaries for one in-memory batch.

    Public operations are guarded by a reentrant lock so future download workers
    can safely submit independent state changes without changing this API.
    """

    def __init__(self, provider_registry: ProviderRegistry | None = None) -> None:
        self.batch_id = uuid4().hex
        self._provider_registry = provider_registry or DEFAULT_PROVIDER_REGISTRY
        self._items_by_id: dict[str, QueueItem] = {}
        self._id_by_url: dict[str, str] = {}
        self._lock = RLock()

    def add_records(
        self,
        records: list[UrlRecord],
        classifications: list[ProviderClassification] | None = None,
    ) -> list[QueueItem]:
        """Add input records, classifying locally unless associated results are supplied."""

        matches = {
            classification.provider_match.normalized_url: classification.provider_match
            for classification in classifications or []
        }
        prepared = [
            (
                record,
                matches.get(record.normalized_url)
                or self._provider_registry.classify(record.normalized_url),
            )
            for record in records
        ]
        changed_ids: list[str] = []
        changed_id_set: set[str] = set()
        timestamp = datetime.now(UTC)

        with self._lock:
            for record, match in prepared:
                if not record.normalized_url:
                    raise QueueInvariantError("A queue record must have a normalized URL.")

                item_id = self._id_by_url.get(record.normalized_url)
                if item_id is not None:
                    existing = self._items_by_id[item_id]
                    merged_occurrences = tuple(
                        dict.fromkeys((*existing.occurrences, *record.occurrences))
                    )
                    if merged_occurrences != existing.occurrences:
                        self._items_by_id[item_id] = replace(
                            existing,
                            occurrences=merged_occurrences,
                            updated_at=timestamp,
                        )
                    if item_id not in changed_id_set:
                        changed_ids.append(item_id)
                        changed_id_set.add(item_id)
                    continue

                item_id = hashlib.sha256(record.normalized_url.encode("utf-8")).hexdigest()
                if item_id in self._items_by_id:
                    raise QueueInvariantError("Queue item identifier collision detected.")

                item = QueueItem(
                    item_id=item_id,
                    original_url=record.original_url,
                    normalized_url=record.normalized_url,
                    occurrences=tuple(dict.fromkeys(record.occurrences)),
                    provider=match.provider,
                    asset_reference=match.asset_reference,
                    provider_details=dict(match.details),
                    state=QueueState.PENDING,
                    attempt_count=0,
                    error=None,
                    error_history=(),
                    blocked_reason=None,
                    batch_id=self.batch_id,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                target = (
                    QueueState.BLOCKED
                    if match.provider == ProviderId.UNKNOWN
                    else QueueState.READY
                )
                validate_transition(item.state, target)
                item = replace(
                    item,
                    state=target,
                    blocked_reason="unsupported_provider" if target == QueueState.BLOCKED else None,
                )
                self._items_by_id[item_id] = item
                self._id_by_url[record.normalized_url] = item_id
                changed_ids.append(item_id)
                changed_id_set.add(item_id)
                logger.info(
                    "Queue item added: item_id=%s provider=%s state=%s",
                    item.item_id,
                    item.provider.value,
                    item.state.value,
                )

            return [self._items_by_id[item_id] for item_id in changed_ids]

    def add_classifications(self, classifications: list[ProviderClassification]) -> list[QueueItem]:
        """Add stage-3 classified records while retaining their associated provider data."""

        records: list[UrlRecord] = []
        for classification in classifications:
            if not isinstance(classification.record, UrlRecord):
                raise QueueInvariantError("Provider classification must contain a UrlRecord.")
            records.append(classification.record)
        return self.add_records(records, classifications)

    def get_item(self, item_id: str) -> QueueItem:
        """Return an item by its opaque, stable identifier."""

        with self._lock:
            try:
                return self._items_by_id[item_id]
            except KeyError as error:
                raise QueueItemNotFoundError(f"Queue item {item_id} was not found.") from error

    def get_by_url(self, normalized_url: str) -> QueueItem | None:
        """Return an item by normalized URL, or None when it is not in the batch."""

        with self._lock:
            item_id = self._id_by_url.get(normalized_url)
            return self._items_by_id.get(item_id) if item_id is not None else None

    def list_items(self, state: QueueState | None = None) -> list[QueueItem]:
        """List items in first-inclusion order, optionally filtered by state."""

        with self._lock:
            items = list(self._items_by_id.values())
            if state is not None:
                items = [item for item in items if item.state == state]
            return items

    def filter_by_state(self, state: QueueState) -> list[QueueItem]:
        """Return items currently in the requested state."""

        return self.list_items(state)

    def transition(
        self,
        item_id: str,
        target: QueueState,
        error: QueueError | None = None,
    ) -> QueueItem:
        """Perform a validated state change; callers cannot mutate item state directly."""

        with self._lock:
            item = self._require_item(item_id)
            validate_transition(item.state, target)
            if target == QueueState.FAILED and error is None:
                raise QueueInvariantError("A failed queue item requires an operational error.")
            if target != QueueState.FAILED and error is not None:
                raise QueueInvariantError("An operational error is only valid for FAILED state.")

            history = item.error_history
            active_error = item.error
            blocked_reason = item.blocked_reason
            attempts = item.attempt_count
            if target == QueueState.PROCESSING:
                attempts += 1
            elif target == QueueState.FAILED:
                active_error = error
                history = (*history, error)
            elif target == QueueState.READY:
                active_error = None
                blocked_reason = None

            updated = replace(
                item,
                state=target,
                attempt_count=attempts,
                error=active_error,
                error_history=history,
                blocked_reason=blocked_reason,
                updated_at=datetime.now(UTC),
            )
            self._items_by_id[item_id] = updated
            logger.info(
                "Queue item state changed: item_id=%s provider=%s from=%s to=%s",
                item.item_id,
                item.provider.value,
                item.state.value,
                updated.state.value,
            )
            return updated

    def start_processing(self, item_id: str) -> QueueItem:
        """Move a ready item to processing and increment its attempt count."""

        return self.transition(item_id, QueueState.PROCESSING)

    def mark_completed(self, item_id: str) -> QueueItem:
        """Mark a processing item completed."""

        return self.transition(item_id, QueueState.COMPLETED)

    def mark_failed(self, item_id: str, error: QueueError) -> QueueItem:
        """Record a processing failure without exposing technical details in logs."""

        return self.transition(item_id, QueueState.FAILED, error)

    def prepare_retry(self, item_id: str) -> QueueItem:
        """Return a failed item to READY, keeping prior errors in error_history."""

        return self.transition(item_id, QueueState.READY)

    def remove_item(self, item_id: str) -> QueueItem:
        """Remove a non-processing item from this in-memory batch."""

        with self._lock:
            item = self._require_item(item_id)
            if item.state == QueueState.PROCESSING:
                raise QueueInvariantError("A processing queue item cannot be removed.")
            del self._items_by_id[item_id]
            del self._id_by_url[item.normalized_url]
            return item

    def summary(self) -> QueueSummary:
        """Return queue state and provider counts suitable for future UI progress."""

        with self._lock:
            items = tuple(self._items_by_id.values())
            state_counts = {state: 0 for state in QueueState}
            provider_counts = {provider: 0 for provider in ProviderId}
            for item in items:
                state_counts[item.state] += 1
                provider_counts[item.provider] += 1
            return QueueSummary(
                total=len(items),
                pending=state_counts[QueueState.PENDING],
                ready=state_counts[QueueState.READY],
                processing=state_counts[QueueState.PROCESSING],
                completed=state_counts[QueueState.COMPLETED],
                failed=state_counts[QueueState.FAILED],
                blocked=state_counts[QueueState.BLOCKED],
                provider_counts=provider_counts,
            )

    def _require_item(self, item_id: str) -> QueueItem:
        try:
            return self._items_by_id[item_id]
        except KeyError as error:
            raise QueueItemNotFoundError(f"Queue item {item_id} was not found.") from error