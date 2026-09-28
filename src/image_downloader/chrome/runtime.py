"""High-level per-provider Chrome runtime and conservative session status."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from image_downloader.chrome.config import SUPPORTED_PROVIDERS
from image_downloader.chrome.models import (
    ChromeMode,
    ChromeProfilePaths,
    ChromeSessionState,
    ChromeSessionStatus,
    ChromeTimings,
)
from image_downloader.chrome.process_manager import ChromeProcessManager
from image_downloader.chrome.profile_factory import ChromeProfileFactory
from image_downloader.providers.models import ProviderId


@dataclass(frozen=True, slots=True)
class ChromeOpenResult:
    provider: ProviderId
    pid: int
    port: int
    target_id: str
    profile_paths: ChromeProfilePaths
    timings: ChromeTimings


class ChromeRuntime:
    """Share one visible, managed Chrome process and one stable profile per provider."""

    def __init__(
        self,
        *,
        process_manager: ChromeProcessManager | None = None,
        profile_factory: ChromeProfileFactory | None = None,
    ) -> None:
        self.profile_factory = profile_factory or (
            process_manager.profile_factory
            if process_manager is not None
            else ChromeProfileFactory()
        )
        self.process_manager = process_manager or ChromeProcessManager(
            profile_factory=self.profile_factory
        )
        self._lock = RLock()
        self._reasons = {provider: "chrome_closed" for provider in SUPPORTED_PROVIDERS}

    def status(self, provider: ProviderId | str) -> ChromeSessionStatus:
        resolved = ChromeProcessManager._require_provider(provider)
        running = self.process_manager.is_running(resolved)
        with self._lock:
            if running:
                reason = "chrome_open_session_unverified"
                state = ChromeSessionState.UNVERIFIED
            else:
                reason = self._reasons[resolved]
                state = (
                    ChromeSessionState.ERROR
                    if reason in {"chrome_start_failed", "chrome_clear_failed"}
                    else ChromeSessionState.UNVERIFIED
                )
            managed = self.process_manager.managed_record(resolved) if running else None
        return ChromeSessionStatus(
            provider=resolved,
            state=state,
            reason=reason,
            chrome_running=running,
            pid=managed.pid if managed else None,
        )

    def record_error(self, provider: ProviderId | str, operation: str) -> None:
        resolved = ChromeProcessManager._require_provider(provider)
        reason = "chrome_start_failed" if operation == "open" else "chrome_clear_failed"
        with self._lock:
            self._reasons[resolved] = reason

    def open_provider(
        self,
        provider: ProviderId | str,
        *,
        mode: ChromeMode = ChromeMode.INTERACTIVE,
    ) -> ChromeOpenResult:
        managed = self.process_manager.start_provider(provider, mode=mode)
        with self._lock:
            self._reasons[managed.provider] = "chrome_open_session_unverified"
        return ChromeOpenResult(
            provider=managed.provider,
            pid=managed.pid,
            port=managed.port,
            target_id=managed.target_id,
            profile_paths=managed.profile_paths,
            timings=managed.timings,
        )

    def close_provider(self, provider: ProviderId | str) -> float:
        resolved = ChromeProcessManager._require_provider(provider)
        close_ms = self.process_manager.close_provider(resolved)
        with self._lock:
            self._reasons[resolved] = "chrome_closed_session_unverified"
        return close_ms

    def clear_provider(self, provider: ProviderId | str) -> ChromeProfilePaths:
        resolved = ChromeProcessManager._require_provider(provider)
        self.process_manager.close_provider(resolved)
        paths = self.profile_factory.clear_provider(resolved)
        with self._lock:
            self._reasons[resolved] = "chrome_closed_session_unverified"
        return paths

    def shutdown(self) -> dict[ProviderId, float]:
        durations = self.process_manager.close_all()
        with self._lock:
            for provider in durations:
                self._reasons[provider] = "chrome_closed_session_unverified"
        return durations