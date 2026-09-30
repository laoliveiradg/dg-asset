"""Background ZIP creation for validated batch files."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from image_downloader.archive.service import BatchArchiveService


class ArchiveSignals(QObject):
    completed = Signal(object)


class ArchiveWorker(QRunnable):
    def __init__(
        self,
        service: BatchArchiveService,
        batch_id: str,
        files: list[Path],
    ) -> None:
        super().__init__()
        self.service = service
        self.batch_id = batch_id
        self.files = list(files)
        self.signals = ArchiveSignals()

    def run(self) -> None:
        try:
            outcome = self.service.create(self.batch_id, self.files)
        except Exception as error:
            outcome = error
        self.signals.completed.emit(outcome)
