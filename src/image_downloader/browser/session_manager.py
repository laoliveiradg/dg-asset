"""Own provider-isolated persistent QtWebEngine profiles and their lifecycle."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QObject,
    QRunnable,
    QThread,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtWebEngineCore import QWebEngineProfile
from PySide6.QtWidgets import QDialog

from image_downloader.browser.profile_factory import SUPPORTED_PROVIDERS, ProfileFactory
from image_downloader.browser.session_models import (
    ProfilePaths,
    SessionOperationInProgressError,
    SessionState,
    SessionStatus,
    require_supported_provider,
    uninitialized_status,
    unverified_status,
)
from image_downloader.browser.validators import SessionValidator, UnverifiedSessionValidator
from image_downloader.providers.models import ProviderId

logger = logging.getLogger(__name__)


class CleanupSignals(QObject):
    completed = Signal(object, object, bool)


class ProfileCleanupWorker(QRunnable):
    """Remove profile files after every QtWebEngine object has been destroyed."""

    def __init__(self, provider: ProviderId, root: Path) -> None:
        super().__init__()
        self.provider = provider
        self.root = root
        self.signals = CleanupSignals()

    def run(self) -> None:
        try:
            shutil.rmtree(self.root, ignore_errors=False)
        except FileNotFoundError:
            success = True
        except OSError:
            success = False
        else:
            success = True
        self.signals.completed.emit(self.provider, self.root, success)


@dataclass(slots=True)
class ClearOperation:
    previous_paths: ProfilePaths
    replacement_paths: ProfilePaths
    remaining_dialogs: int


class SessionManager(QObject):
    """Create, reuse, validate and clear independent profiles on the UI thread."""

    status_changed = Signal(object)
    clear_finished = Signal(object, bool)

    def __init__(
        self,
        parent=None,
        *,
        runtime_root: str | Path | None = None,
        validators: dict[ProviderId, SessionValidator] | None = None,
    ) -> None:
        super().__init__(parent)
        self.profile_factory = ProfileFactory(runtime_root)
        self._validators = {
            provider: UnverifiedSessionValidator() for provider in SUPPORTED_PROVIDERS
        }
        if validators:
            self._validators.update(validators)
        self._profiles: dict[ProviderId, QWebEngineProfile] = {}
        self._dialogs: dict[ProviderId, dict[int, QDialog]] = {}
        self._statuses = {
            provider: uninitialized_status(provider) for provider in self._validators
        }
        self._clear_operations: dict[ProviderId, ClearOperation] = {}
        self._cleanup_workers: dict[Path, ProfileCleanupWorker] = {}
        self._pending_clear_cleanup: dict[Path, ProviderId] = {}
        self._stale_cleanup_roots: dict[Path, ProviderId] = {}
        self._failed_replacements: set[Path] = set()
        self._cleanup_pool = QThreadPool(self)
        self._cleanup_pool.setMaxThreadCount(1)
        self._schedule_stale_generation_cleanup()

    def profile_paths(self, provider: ProviderId | str) -> ProfilePaths:
        return self.profile_factory.paths_for(provider)

    def status(self, provider: ProviderId | str) -> SessionStatus:
        resolved = require_supported_provider(provider)
        return self._statuses.get(resolved, uninitialized_status(resolved))

    def profile_for_provider(self, provider: ProviderId | str) -> QWebEngineProfile:
        self._assert_ui_thread()
        resolved = require_supported_provider(provider)
        if resolved in self._clear_operations:
            raise SessionOperationInProgressError("The provider session is being cleared.")
        profile = self._profiles.get(resolved)
        if profile is not None:
            return profile

        paths = self.profile_factory.paths_for(resolved)
        profile = self.profile_factory.create_profile(paths, self)
        self._profiles[resolved] = profile
        status = self._validators[resolved].validate(resolved, profile)
        self._set_status(status)
        logger.info("provider=%s session_profile_opened", resolved.value)
        return profile

    def check_session(self, provider: ProviderId | str) -> SessionStatus:
        self._assert_ui_thread()
        resolved = require_supported_provider(provider)
        profile = self.profile_for_provider(resolved)
        status = self._validators[resolved].validate(resolved, profile)
        self._set_status(status)
        return status

    def register_dialog(self, provider: ProviderId | str, dialog: QDialog) -> None:
        self._assert_ui_thread()
        resolved = require_supported_provider(provider)
        if resolved in self._clear_operations:
            raise SessionOperationInProgressError("The provider session is being cleared.")
        dialog_key = id(dialog)
        self._dialogs.setdefault(resolved, {})[dialog_key] = dialog
        dialog.destroyed.connect(
            lambda: self._on_dialog_destroyed(resolved, dialog_key)
        )

    def clear_provider(self, provider: ProviderId | str) -> None:
        self._assert_ui_thread()
        resolved = require_supported_provider(provider)
        if resolved in self._clear_operations:
            raise SessionOperationInProgressError("The provider session is already being cleared.")

        paths = self.profile_factory.paths_for(resolved)
        replacement_paths = self.profile_factory.next_generation(paths)
        dialogs = list(self._dialogs.get(resolved, {}).values())
        self._clear_operations[resolved] = ClearOperation(
            paths,
            replacement_paths,
            len(dialogs),
        )
        self._set_status(
            SessionStatus(
                provider=resolved,
                state=SessionState.CHECKING,
                reason="profile_clear_in_progress",
            )
        )
        logger.info("provider=%s session_clear_started", resolved.value)

        for dialog in dialogs:
            dialog.close()
            dialog.deleteLater()
        if not dialogs:
            self._dispose_profile(resolved)

    def shutdown(self) -> None:
        """Close pages before profiles, then wait for non-Qt cleanup workers."""

        self._assert_ui_thread()
        for dialogs_by_id in tuple(self._dialogs.values()):
            for dialog in tuple(dialogs_by_id.values()):
                dialog.close()
                dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QCoreApplication.processEvents()

        profiles = tuple(self._profiles.values())
        self._profiles.clear()
        for profile in profiles:
            profile.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QCoreApplication.processEvents()
        self._cleanup_pool.waitForDone()

    def _assert_ui_thread(self) -> None:
        if QThread.currentThread() != self.thread():
            raise RuntimeError("QtWebEngine session operations must run on the UI thread.")

    def _on_dialog_destroyed(self, provider: ProviderId, dialog_key: int) -> None:
        dialogs = self._dialogs.get(provider)
        if dialogs is not None:
            dialogs.pop(dialog_key, None)
        operation = self._clear_operations.get(provider)
        if operation is None:
            return
        operation.remaining_dialogs -= 1
        if operation.remaining_dialogs == 0:
            self._dispose_profile(provider)

    def _dispose_profile(self, provider: ProviderId) -> None:
        profile = self._profiles.pop(provider, None)
        self._activate_replacement_profile(provider, profile)

    def _activate_replacement_profile(
        self,
        provider: ProviderId,
        previous_profile: QWebEngineProfile | None,
    ) -> None:
        operation = self._clear_operations.pop(provider, None)
        if operation is None:
            return
        try:
            profile = self.profile_factory.create_profile(operation.replacement_paths, self)
        except Exception:
            operation.replacement_paths.generation_root.mkdir(parents=True, exist_ok=True)
            self._failed_replacements.add(operation.previous_paths.generation_root)
            self._set_status(
                SessionStatus(provider, SessionState.ERROR, "profile_recreate_failed")
            )
            logger.warning("provider=%s session_recreate_failed", provider.value)
            self._cleanup_previous_profile(provider, operation, previous_profile)
            return

        self._profiles[provider] = profile
        status = unverified_status(provider, "retired_profile_cleanup_pending")
        self._set_status(status)
        logger.info("provider=%s session_cleared", provider.value)
        self._cleanup_previous_profile(provider, operation, previous_profile)

    def _cleanup_previous_profile(
        self,
        provider: ProviderId,
        operation: ClearOperation,
        previous_profile: QWebEngineProfile | None,
    ) -> None:
        previous_root = operation.previous_paths.generation_root
        self._pending_clear_cleanup[previous_root] = provider

        def cleanup() -> None:
            self._start_file_cleanup(provider, previous_root)

        if previous_profile is None:
            cleanup()
            return
        previous_profile.destroyed.connect(lambda: QTimer.singleShot(0, cleanup))
        previous_profile.deleteLater()

    def _start_file_cleanup(self, provider: ProviderId, root: Path) -> None:
        if root in self._cleanup_workers:
            return
        worker = ProfileCleanupWorker(provider, root)
        worker.signals.completed.connect(self._finish_file_cleanup)
        self._cleanup_workers[root] = worker
        self._cleanup_pool.start(worker)

    def _finish_file_cleanup(self, provider: ProviderId, root: Path, success: bool) -> None:
        self._cleanup_workers.pop(root, None)
        cleared_provider = self._pending_clear_cleanup.pop(root, None)
        stale_provider = self._stale_cleanup_roots.pop(root, None)
        if success:
            logger.info("provider=%s obsolete_profile_removed", provider.value)
        else:
            logger.warning("provider=%s obsolete_profile_cleanup_deferred", provider.value)
        if stale_provider is not None:
            if success:
                self._set_status(unverified_status(stale_provider))
            return
        if cleared_provider is not None:
            if root in self._failed_replacements:
                self._failed_replacements.remove(root)
                self._set_status(
                    SessionStatus(cleared_provider, SessionState.ERROR, "profile_recreate_failed")
                )
                self.clear_finished.emit(cleared_provider, False)
                return
            reason = (
                "login_not_verified"
                if success
                else "previous_profile_cleanup_pending_restart"
            )
            self._set_status(unverified_status(cleared_provider, reason))
            self.clear_finished.emit(cleared_provider, True)

    def _schedule_stale_generation_cleanup(self) -> None:
        for provider in SUPPORTED_PROVIDERS:
            active_paths = self.profile_factory.paths_for(provider)
            for stale_root in self.profile_factory.stale_generation_roots(active_paths):
                self._stale_cleanup_roots[stale_root] = provider
                self._set_status(
                    unverified_status(
                        provider,
                        "previous_profile_cleanup_pending_restart",
                    )
                )
                self._start_file_cleanup(provider, stale_root)

    def _set_status(self, status: SessionStatus) -> None:
        self._statuses[status.provider] = status
        logger.info(
            "provider=%s session_status=%s",
            status.provider.value,
            status.state.value,
        )
        self.status_changed.emit(status)