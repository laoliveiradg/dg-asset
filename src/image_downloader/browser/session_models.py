"""Public, non-secret models for persistent provider sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from image_downloader.providers.models import ProviderId


class SessionState(StrEnum):
    UNINITIALIZED = "UNINITIALIZED"
    UNVERIFIED = "UNVERIFIED"
    AUTHENTICATED = "AUTHENTICATED"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    CHECKING = "CHECKING"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class SessionStatus:
    provider: ProviderId
    state: SessionState
    reason: str
    checked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ProfilePaths:
    provider: ProviderId
    root: Path
    generation_root: Path
    persistent_storage: Path
    cache: Path
    generation: int


class UnsupportedSessionProviderError(ValueError):
    """Raised when an unsupported or unknown provider is used for a session."""


class SessionOperationInProgressError(RuntimeError):
    """Raised when a session operation conflicts with a pending profile clear."""


def require_supported_provider(provider: ProviderId | str) -> ProviderId:
    try:
        resolved = provider if isinstance(provider, ProviderId) else ProviderId(provider)
    except ValueError as error:
        message = "The requested provider has no browser session."
        raise UnsupportedSessionProviderError(message) from error
    if resolved == ProviderId.UNKNOWN:
        raise UnsupportedSessionProviderError("The requested provider has no browser session.")
    return resolved


def uninitialized_status(provider: ProviderId) -> SessionStatus:
    return SessionStatus(
        provider=provider,
        state=SessionState.UNINITIALIZED,
        reason="profile_not_opened",
        checked_at=None,
    )


def unverified_status(provider: ProviderId, reason: str = "login_not_verified") -> SessionStatus:
    return SessionStatus(
        provider=provider,
        state=SessionState.UNVERIFIED,
        reason=reason,
        checked_at=datetime.now(UTC),
    )