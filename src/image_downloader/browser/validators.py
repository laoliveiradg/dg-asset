"""LEGACY INACTIVE: QtWebEngine session validator retained for rollback only."""

from __future__ import annotations

from typing import Protocol

from PySide6.QtWebEngineCore import QWebEngineProfile

from image_downloader.browser.session_models import SessionStatus, unverified_status
from image_downloader.providers.models import ProviderId


class SessionValidator(Protocol):
    """Provider contract for future reliable, provider-specific session checks."""

    def validate(
        self,
        provider: ProviderId,
        profile: QWebEngineProfile,
    ) -> SessionStatus: ...


class UnverifiedSessionValidator:
    """Never infers successful authentication from cookies or storage presence."""

    def validate(
        self,
        provider: ProviderId,
        profile: QWebEngineProfile,
    ) -> SessionStatus:
        return unverified_status(provider)