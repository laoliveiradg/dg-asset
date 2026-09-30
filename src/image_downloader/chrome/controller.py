"""Qt-thread-safe asynchronous coordination for the managed Chrome runtime."""

from __future__ import annotations

import logging
from collections.abc import Callable
from threading import RLock, Thread

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot

from image_downloader.chrome.config import SUPPORTED_PROVIDERS
from image_downloader.chrome.models import (
    ChromeRuntimeError,
    ChromeSessionState,
    ChromeSessionStatus,
)
from image_downloader.chrome.runtime import ChromeOpenResult, ChromeRuntime
from image_downloader.providers.capabilities import ProviderExecutionMode
from image_downloader.providers.execution_policy import (
    DEFAULT_PROVIDER_EXECUTION_POLICY,
    ProviderExecutionPolicy,
)
from image_downloader.providers.models import ProviderId

logger = logging.getLogger(__name__)


class RuntimeTaskSignals(QObject):
    completed = Signal(object, object, object)


class RuntimeTask(QRunnable):
    def __init__(
        self,
        operation: str,
        provider: ProviderId,
        callback: Callable[[], object],
    ) -> None:
        super().__init__()
        self.operation = operation
        self.provider = provider
        self.callback = callback
        self.signals = RuntimeTaskSignals()

    def run(self) -> None:
        try:
            result = self.callback()
        except Exception as error:
            self.signals.completed.emit(self.operation, self.provider, error)
        else:
            self.signals.completed.emit(self.operation, self.provider, result)


class ChromeSessionController(QObject):
    """Keep blocking process/CDP startup off the UI thread and emit safe status."""

    status_changed = Signal(object)
    operation_finished = Signal(str, object, bool)
    clear_finished = Signal(object, bool)
    shutdown_finished = Signal()

    def __init__(
        self,
        parent=None,
        *,
        chrome_runtime: ChromeRuntime | None = None,
        execution_policy: ProviderExecutionPolicy = DEFAULT_PROVIDER_EXECUTION_POLICY,
    ) -> None:
        super().__init__(parent)
        self.chrome_runtime = chrome_runtime
        self.execution_policy = execution_policy
        self._lock = RLock()
        self._active: dict[ProviderId, str] = {}
        self._tasks: set[RuntimeTask] = set()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(len(SUPPORTED_PROVIDERS))
        self._shutting_down = False
        self._shutdown_thread: Thread | None = None
        self._monitor = QTimer(self)
        self._monitor.setInterval(1000)
        self._monitor.timeout.connect(self.refresh_statuses)
        self._monitor.start()

    def status(self, provider: ProviderId | str) -> ChromeSessionStatus:
        resolved = self._require_provider(provider)
        if not self._uses_managed_chrome(resolved):
            return ChromeSessionStatus(
                resolved,
                ChromeSessionState.UNVERIFIED,
                "provider_not_managed_by_chrome",
                False,
                None,
            )
        if self.chrome_runtime is None:
            return ChromeSessionStatus(
                resolved,
                ChromeSessionState.UNVERIFIED,
                "chrome_closed",
                False,
                None,
            )
        return self.chrome_runtime.status(resolved)

    def open_provider(self, provider: ProviderId | str) -> bool:
        resolved = self._require_provider(provider)
        if not self._uses_managed_chrome(resolved):
            return False
        with self._lock:
            if self._shutting_down or resolved in self._active:
                return False
            self._active[resolved] = "open"
        runtime = self.ensure_runtime()
        self.status_changed.emit(
            ChromeSessionStatus(
                resolved,
                ChromeSessionState.CHECKING,
                "chrome_starting",
                runtime.process_manager.is_running(resolved),
                None,
            )
        )
        return self._submit(
            "open",
            resolved,
            lambda: runtime.open_provider(resolved),
        )

    def clear_provider(self, provider: ProviderId | str) -> bool:
        resolved = self._require_provider(provider)
        if not self._uses_managed_chrome(resolved):
            return False
        with self._lock:
            if self._shutting_down or resolved in self._active:
                return False
            self._active[resolved] = "clear"
        runtime = self.ensure_runtime()
        self.status_changed.emit(
            ChromeSessionStatus(
                resolved,
                ChromeSessionState.CHECKING,
                "chrome_clearing_profile",
                runtime.process_manager.is_running(resolved),
                None,
            )
        )
        return self._submit(
            "clear",
            resolved,
            lambda: runtime.clear_provider(resolved),
        )

    def refresh_statuses(self) -> None:
        with self._lock:
            active_providers = set(self._active)
        for provider in SUPPORTED_PROVIDERS:
            if (
                provider not in active_providers
                and self._uses_managed_chrome(provider)
            ):
                self.status_changed.emit(self.status(provider))

    def shutdown(self) -> None:
        self._monitor.stop()
        with self._lock:
            if self._shutting_down:
                return
            self._shutting_down = True
        self._shutdown_thread = Thread(
            target=self._shutdown_runtime,
            name="image-downloader-chrome-shutdown",
        )
        self._shutdown_thread.start()

    def submit_worker(self, worker: QRunnable) -> bool:
        """Run a provider worker in the pool awaited during managed shutdown."""

        with self._lock:
            if self._shutting_down:
                return False
            self._pool.start(worker)
        return True

    def _shutdown_runtime(self) -> None:
        self._pool.waitForDone()
        try:
            if self.chrome_runtime is not None:
                self.chrome_runtime.shutdown()
        except Exception:
            logger.warning("managed Chrome shutdown failed")
        finally:
            self.shutdown_finished.emit()

    def _submit(self, operation: str, provider: ProviderId, callback: Callable[[], object]) -> bool:
        task = RuntimeTask(operation, provider, callback)
        task.signals.completed.connect(self._task_finished)
        with self._lock:
            self._tasks.add(task)
        self._pool.start(task)
        return True

    @Slot(object, object, object)
    def _task_finished(self, operation: str, provider: ProviderId, result: object) -> None:
        with self._lock:
            matching_task = next(
                (
                    task
                    for task in self._tasks
                    if task.operation == operation and task.provider == provider
                ),
                None,
            )
            if matching_task is not None:
                self._tasks.discard(matching_task)
            self._active.pop(provider, None)

        if isinstance(result, Exception):
            logger.warning("provider=%s chrome_%s_failed", provider.value, operation)
            runtime = self.ensure_runtime()
            runtime.record_error(provider, operation)
            status = ChromeSessionStatus(
                provider,
                ChromeSessionState.ERROR,
                "chrome_start_failed" if operation == "open" else "chrome_clear_failed",
                runtime.process_manager.is_running(provider),
                None,
            )
            self.status_changed.emit(status)
            self.operation_finished.emit(operation, provider, False)
            if operation == "clear":
                self.clear_finished.emit(provider, False)
            return

        runtime = self.ensure_runtime()
        status = runtime.status(provider)
        if operation == "open" and isinstance(result, ChromeOpenResult):
            timings = result.timings
            logger.info(
                "provider=%s chrome_ready pid=%s locate_ms=%.1f profile_ms=%.1f "
                "process_start_ms=%.1f cdp_ready_ms=%.1f page_open_ms=%.1f",
                provider.value,
                result.pid,
                timings.locate_ms,
                timings.profile_ms,
                timings.process_start_ms,
                timings.cdp_ready_ms,
                timings.page_open_ms,
            )
        self.status_changed.emit(status)
        self.operation_finished.emit(operation, provider, True)
        if operation == "clear":
            self.clear_finished.emit(provider, True)

    def ensure_runtime(self) -> ChromeRuntime:
        if self.chrome_runtime is None:
            self.chrome_runtime = ChromeRuntime()
        return self.chrome_runtime

    def _uses_managed_chrome(self, provider: ProviderId) -> bool:
        return self.execution_policy.mode_for(provider) in {
            ProviderExecutionMode.AUTOMATED,
            ProviderExecutionMode.UNVALIDATED,
        }

    @staticmethod
    def _require_provider(provider: ProviderId | str) -> ProviderId:
        try:
            resolved = provider if isinstance(provider, ProviderId) else ProviderId(provider)
        except ValueError as error:
            raise ChromeRuntimeError("Provider has no managed Chrome session.") from error
        if resolved not in SUPPORTED_PROVIDERS:
            raise ChromeRuntimeError("Provider has no managed Chrome session.")
        return resolved
