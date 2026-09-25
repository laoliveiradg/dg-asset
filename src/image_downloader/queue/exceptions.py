"""Domain exceptions raised by the queue engine."""

from __future__ import annotations

from image_downloader.queue.models import QueueState


class QueueInvariantError(ValueError):
    """Raised when an input or operation violates queue invariants."""


class QueueItemNotFoundError(LookupError):
    """Raised when an operation requires a missing queue item."""


class InvalidTransitionError(QueueInvariantError):
    """Raised when a requested item-state transition is not allowed."""

    def __init__(self, current: QueueState, target: QueueState) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Transition from {current.value} to {target.value} is not allowed.")