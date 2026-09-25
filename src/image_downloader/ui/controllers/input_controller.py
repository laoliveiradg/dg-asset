"""Coordinate UI input with the existing input, provider, and queue engines."""

from __future__ import annotations

import os
from pathlib import Path
from threading import RLock
from time import perf_counter_ns
from uuid import uuid4

from PySide6.QtCore import QObject, Qt, QThreadPool, QTimer, Signal, Slot

from image_downloader.providers.models import ProviderClassification
from image_downloader.providers.registry import ProviderRegistry
from image_downloader.queue.manager import QueueManager
from image_downloader.ui.workers.input_worker import (
    AnalysisRequest,
    AnalysisResult,
    InputSource,
    InputWorker,
    QueueSnapshot,
)


class InputController(QObject):
    """Keep orchestration, generations, and the queue outside the widgets."""

    queue_changed = Signal(object)
    analysis_finished = Signal(object)
    status_changed = Signal(str)
    warning_changed = Signal(str)

    DEBOUNCE_MS = 300

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.provider_registry = ProviderRegistry()
        self._queue_manager = QueueManager(self.provider_registry)
        self._batch_generation = 0
        self._text_generation = 0
        self._revision = 0
        self._last_published_revision = -1
        self._state_lock = RLock()
        self._submitted_files: set[str] = set()
        self._warnings: list[str] = []
        self._pending_text: tuple[str, int, int] | None = None
        self._active_requests: dict[str, str] = {}
        self._workers: dict[str, InputWorker] = {}
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(2)
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(self.DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(self._start_pending_text)

    @property
    def queue_manager(self) -> QueueManager:
        return self._queue_manager

    @property
    def batch_id(self) -> str:
        with self._state_lock:
            return self._queue_manager.batch_id

    @property
    def batch_generation(self) -> int:
        with self._state_lock:
            return self._batch_generation

    @property
    def text_generation(self) -> int:
        with self._state_lock:
            return self._text_generation

    def schedule_text_analysis(self, text: str) -> None:
        """Debounce editor changes and invalidate older text requests immediately."""

        with self._state_lock:
            self._text_generation += 1
            generation = self._text_generation
            self._pending_text = (text, generation, perf_counter_ns())
            self._debounce_timer.stop()
            if text.strip():
                self._debounce_timer.start()
                status = "Links atualizados; preparando análise..."
            else:
                status = "Pronto para receber apresentações ou links."
        self.status_changed.emit(status)

    def add_files(self, paths: list[str]) -> int:
        """Queue unique canonical PPTX paths for background processing."""

        accepted_paths: list[str] = []
        warnings: list[str] = []
        with self._state_lock:
            for raw_path in paths:
                path = Path(raw_path).expanduser()
                if path.suffix.lower() != ".pptx":
                    warnings.append(
                        f"{path.name or raw_path}: somente arquivos .pptx são aceitos."
                    )
                    continue
                canonical_path = os.path.normcase(str(path.resolve(strict=False)))
                if canonical_path in self._submitted_files:
                    warnings.append(f"{path.name}: este arquivo já foi adicionado ao lote.")
                    continue
                self._submitted_files.add(canonical_path)
                accepted_paths.append(canonical_path)
            request = (
                self._new_request(
                    source=InputSource.FILES,
                    paths=tuple(accepted_paths),
                )
                if accepted_paths
                else None
            )

        if warnings:
            self._append_warnings(warnings)
        if request is not None:
            label = f"Analisando {len(accepted_paths)} apresentações..."
            if len(accepted_paths) == 1:
                label = "Analisando 1 apresentação..."
            self._submit_request(request, label)
        return len(accepted_paths)

    def reject_files(self, paths: list[str]) -> None:
        """Show one consolidated inline warning for rejected file extensions."""

        messages = [
            f"{Path(path).name or path}: somente arquivos .pptx são aceitos."
            for path in paths
        ]
        self._append_warnings(messages)

    def clear_batch(self) -> None:
        """Invalidate running work and replace the in-memory queue with a new batch."""

        self._debounce_timer.stop()
        with self._state_lock:
            self._batch_generation += 1
            self._text_generation += 1
            self._pending_text = None
            self._submitted_files.clear()
            self._active_requests.clear()
            self._warnings.clear()
            self._queue_manager = QueueManager(self.provider_registry)
            self._revision += 1
            self._last_published_revision = self._revision
            snapshot = self._make_snapshot_locked()
        self.queue_changed.emit(snapshot)
        self.warning_changed.emit("")
        self.status_changed.emit("Lote limpo. Pronto para novas entradas.")

    def _new_request(
        self,
        *,
        source: InputSource,
        text: str = "",
        paths: tuple[str, ...] = (),
        text_generation: int | None = None,
        submitted_at_ns: int | None = None,
    ) -> AnalysisRequest:
        return AnalysisRequest(
            request_id=uuid4().hex,
            batch_generation=self._batch_generation,
            text_generation=text_generation,
            source=source,
            text=text,
            paths=paths,
            submitted_at_ns=submitted_at_ns or perf_counter_ns(),
        )

    @Slot()
    def _start_pending_text(self) -> None:
        with self._state_lock:
            pending = self._pending_text
            if pending is None:
                return
            text, generation, submitted_at_ns = pending
            if generation != self._text_generation or not text.strip():
                return
            request = self._new_request(
                source=InputSource.TEXT,
                text=text,
                text_generation=generation,
                submitted_at_ns=submitted_at_ns,
            )
        self._submit_request(request, "Processando links...")

    def _submit_request(self, request: AnalysisRequest, activity: str) -> None:
        worker = InputWorker(request, self.provider_registry, self._commit_request)
        worker.signals.completed.connect(
            self._handle_worker_result,
            Qt.ConnectionType.QueuedConnection,
        )
        with self._state_lock:
            self._workers[request.request_id] = worker
            self._active_requests[request.request_id] = activity
        self.status_changed.emit(self._current_activity())
        self._thread_pool.start(worker)

    def _commit_request(
        self,
        request: AnalysisRequest,
        classifications: list[ProviderClassification],
    ) -> QueueSnapshot | None:
        """Commit only current work; called by a worker and never touches widgets."""

        with self._state_lock:
            if request.batch_generation != self._batch_generation:
                return None
            if (
                request.text_generation is not None
                and request.text_generation != self._text_generation
            ):
                return None
            self._queue_manager.add_classifications(classifications)
            self._revision += 1
            return self._make_snapshot_locked()

    @Slot(object)
    def _handle_worker_result(self, result: AnalysisResult) -> None:
        ui_elapsed_ms = (perf_counter_ns() - result.request.submitted_at_ns) / 1_000_000
        accepted_result: AnalysisResult | None = None
        with self._state_lock:
            self._workers.pop(result.request.request_id, None)
            self._active_requests.pop(result.request.request_id, None)
            current = result.request.batch_generation == self._batch_generation
            if result.request.text_generation is not None:
                current = current and result.request.text_generation == self._text_generation
            if (
                result.accepted
                and current
                and result.snapshot is not None
                and result.snapshot.revision >= self._last_published_revision
            ):
                self._last_published_revision = result.snapshot.revision
                accepted_result = AnalysisResult(
                    request=result.request,
                    snapshot=result.snapshot,
                    input_metrics=result.input_metrics,
                    extraction_ms=result.extraction_ms,
                    classification_ms=result.classification_ms,
                    queue_update_ms=result.queue_update_ms,
                    analysis_ms=result.analysis_ms,
                    ui_elapsed_ms=ui_elapsed_ms,
                    worker_thread_id=result.worker_thread_id,
                    accepted=True,
                    warnings=result.warnings,
                )
                for warning in result.warnings:
                    if warning not in self._warnings:
                        self._warnings.append(warning)
            active_status = self._current_activity_locked()

        if accepted_result is not None:
            self.queue_changed.emit(accepted_result.snapshot)
            self.warning_changed.emit("\n".join(self._warnings))
            self.analysis_finished.emit(accepted_result)
            if active_status:
                self.status_changed.emit(active_status)
            else:
                summary = accepted_result.snapshot.summary
                if summary.total:
                    status = (
                        f"{summary.total} URLs únicas encontradas em "
                        f"{accepted_result.ui_elapsed_ms:.0f} ms."
                    )
                else:
                    status = f"Nenhuma URL encontrada em {accepted_result.ui_elapsed_ms:.0f} ms."
                self.status_changed.emit(status)
        elif active_status:
            self.status_changed.emit(active_status)

    def _append_warnings(self, messages: list[str]) -> None:
        with self._state_lock:
            for message in messages:
                if message and message not in self._warnings:
                    self._warnings.append(message)
            combined = "\n".join(self._warnings)
        self.warning_changed.emit(combined)

    def _current_activity(self) -> str:
        with self._state_lock:
            return self._current_activity_locked()

    def _current_activity_locked(self) -> str:
        activities = list(self._active_requests.values())
        if not activities:
            return ""
        if len(activities) == 1:
            return activities[0]
        return f"{len(activities)} análises em andamento..."

    def _make_snapshot_locked(self) -> QueueSnapshot:
        return QueueSnapshot(
            batch_generation=self._batch_generation,
            revision=self._revision,
            batch_id=self._queue_manager.batch_id,
            items=tuple(self._queue_manager.list_items()),
            summary=self._queue_manager.summary(),
        )