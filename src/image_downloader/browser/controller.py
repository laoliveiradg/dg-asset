"""LEGACY INACTIVE: QtWebEngine access controller retained for rollback only."""

from __future__ import annotations

import logging
from time import perf_counter

from PySide6.QtCore import QObject, Signal

from image_downloader.browser.browser_dialog import PROVIDER_NAMES, BrowserDialog
from image_downloader.browser.session_manager import SessionManager
from image_downloader.browser.session_models import SessionStatus, require_supported_provider
from image_downloader.providers.models import ProviderId

logger = logging.getLogger(__name__)


class SessionController(QObject):
    """Open, reuse and clear provider-specific login areas without seeing secrets."""

    status_changed = Signal(object)
    clear_finished = Signal(object, bool)

    def __init__(self, parent=None, *, session_manager: SessionManager | None = None) -> None:
        super().__init__(parent)
        self.session_manager = session_manager or SessionManager(self)
        self._dialogs: dict[ProviderId, BrowserDialog] = {}
        self.session_manager.status_changed.connect(self.status_changed)
        self.session_manager.clear_finished.connect(self.clear_finished)

    def open_provider(
        self,
        provider: ProviderId | str,
        *,
        navigate: bool = True,
    ) -> BrowserDialog:
        resolved = require_supported_provider(provider)
        dialog = self._dialogs.get(resolved)
        if dialog is not None:
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
            return dialog

        profile_started = perf_counter()
        profile = self.session_manager.profile_for_provider(resolved)
        profile_created_ms = (perf_counter() - profile_started) * 1000
        dialog = BrowserDialog(
            resolved,
            profile,
            self.parent(),
            navigate=navigate,
        )
        dialog.presented.connect(
            lambda opened_provider, presented_ms: logger.info(
                "provider=%s session_opened profile_ms=%.1f presented_ms=%.1f",
                opened_provider.value,
                profile_created_ms,
                presented_ms,
            )
        )
        self.session_manager.register_dialog(resolved, dialog)
        dialog.destroyed.connect(lambda: self._dialogs.pop(resolved, None))
        self._dialogs[resolved] = dialog
        dialog.show()
        return dialog

    def clear_provider(self, provider: ProviderId | str) -> None:
        self.session_manager.clear_provider(provider)

    def status(self, provider: ProviderId | str) -> SessionStatus:
        return self.session_manager.status(provider)

    def shutdown(self) -> None:
        self.session_manager.shutdown()

    @staticmethod
    def provider_name(provider: ProviderId) -> str:
        return PROVIDER_NAMES[provider]