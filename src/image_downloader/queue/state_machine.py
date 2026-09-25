"""Explicit, side-effect-free queue state transition rules."""

from image_downloader.queue.exceptions import InvalidTransitionError
from image_downloader.queue.models import QueueState

VALID_TRANSITIONS: dict[QueueState, frozenset[QueueState]] = {
    QueueState.PENDING: frozenset({QueueState.READY, QueueState.BLOCKED}),
    QueueState.READY: frozenset({QueueState.PROCESSING}),
    QueueState.PROCESSING: frozenset({QueueState.COMPLETED, QueueState.FAILED}),
    QueueState.FAILED: frozenset({QueueState.READY}),
    QueueState.COMPLETED: frozenset(),
    QueueState.BLOCKED: frozenset(),
}


def validate_transition(current: QueueState, target: QueueState) -> None:
    """Raise a domain error unless the requested transition is explicitly valid."""

    if target not in VALID_TRANSITIONS[current]:
        raise InvalidTransitionError(current, target)