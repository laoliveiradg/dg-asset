"""Background input pipeline for PPTX and pasted text analysis."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from threading import get_ident
from time import perf_counter

from PySide6.QtCore import QObject, QRunnable, Signal

from image_downloader.input.models import InputMetrics, UrlRecord
from image_downloader.input.pptx_reader import extract_urls_from_pptx
from image_downloader.input.text_extractor import consolidate_urls, extract_urls_from_text
from image_downloader.providers.models import ProviderClassification
from image_downloader.providers.registry import ProviderRegistry
from image_downloader.queue.models import QueueItem, QueueSummary

logger = logging.getLogger(__name__)


class InputSource(StrEnum):
    TEXT = "text"
    FILES = "files"


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    request_id: str
    batch_generation: int
    text_generation: int | None
    source: InputSource
    text: str
    paths: tuple[str, ...]
    submitted_at_ns: int


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    batch_generation: int
    revision: int
    batch_id: str
    items: tuple[QueueItem, ...]
    summary: QueueSummary


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    request: AnalysisRequest
    snapshot: QueueSnapshot | None
    input_metrics: InputMetrics
    extraction_ms: float
    classification_ms: float
    queue_update_ms: float
    analysis_ms: float
    ui_elapsed_ms: float
    worker_thread_id: int
    accepted: bool
    warnings: tuple[str, ...]


class WorkerSignals(QObject):
    completed = Signal(object)


CommitCallback = Callable[
    [AnalysisRequest, list[ProviderClassification]], QueueSnapshot | None
]


class InputWorker(QRunnable):
    """Extract, normalize, classify, and commit queue items outside the UI thread."""

    def __init__(
        self,
        request: AnalysisRequest,
        provider_registry: ProviderRegistry,
        commit_callback: CommitCallback,
    ) -> None:
        super().__init__()
        self.request = request
        self.provider_registry = provider_registry
        self.commit_callback = commit_callback
        self.signals = WorkerSignals()

    def run(self) -> None:
        started = perf_counter()
        warnings: list[str] = []
        metrics = InputMetrics()
        records: list[UrlRecord] = []

        extraction_started = perf_counter()
        try:
            if self.request.source == InputSource.TEXT:
                occurrences = extract_urls_from_text(self.request.text, metrics=metrics)
                records = consolidate_urls(occurrences)
            else:
                records = self._extract_files(metrics, warnings)
            records = consolidate_urls(records)
        except Exception:
            logger.warning("Input extraction failed for request_id=%s", self.request.request_id)
            warnings.append("Não foi possível analisar esta entrada.")
            records = []
        extraction_ms = (perf_counter() - extraction_started) * 1000
        metrics.urls_unique = len(records)

        classification_started = perf_counter()
        try:
            classifications = self.provider_registry.classify_records(records)
        except Exception:
            logger.warning(
                "Provider classification failed for request_id=%s",
                self.request.request_id,
            )
            warnings.append("Não foi possível classificar os links desta entrada.")
            classifications = []
        classification_ms = (perf_counter() - classification_started) * 1000

        queue_started = perf_counter()
        try:
            snapshot = self.commit_callback(self.request, classifications)
        except Exception:
            logger.warning("Queue update failed for request_id=%s", self.request.request_id)
            warnings.append("Não foi possível atualizar a fila para esta entrada.")
            snapshot = None
        queue_update_ms = (perf_counter() - queue_started) * 1000

        elapsed_ms = (perf_counter() - started) * 1000
        output = AnalysisResult(
            request=self.request,
            snapshot=snapshot,
            input_metrics=metrics,
            extraction_ms=extraction_ms,
            classification_ms=classification_ms,
            queue_update_ms=queue_update_ms,
            analysis_ms=elapsed_ms,
            ui_elapsed_ms=0.0,
            worker_thread_id=get_ident(),
            accepted=snapshot is not None,
            warnings=tuple(warnings),
        )
        self.signals.completed.emit(output)

    def _extract_files(self, total_metrics: InputMetrics, warnings: list[str]) -> list[UrlRecord]:
        records: list[UrlRecord] = []
        for path in self.request.paths:
            file_metrics = InputMetrics()
            try:
                file_records = extract_urls_from_pptx(path, metrics=file_metrics)
            except Exception:
                logger.info("PPTX input rejected: file=%s", Path(path).name)
                warnings.append(f"{Path(path).name}: não foi possível ler este PPTX.")
                continue
            records.extend(file_records)
            total_metrics.read_time_ms += file_metrics.read_time_ms
            total_metrics.extraction_time_ms += file_metrics.extraction_time_ms
            total_metrics.urls_found += file_metrics.urls_found
        return records