"""Wait for one browser-assisted download away from the UI thread."""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Signal

from image_downloader.downloads.assisted import AssistedDownloadMonitor
from image_downloader.queue.manager import QueueManager


class AssistedDownloadSignals(QObject):
    completed = Signal(object)


class AssistedDownloadWorker(QRunnable):
    def __init__(self, monitor, queue_manager: QueueManager, item_id: str, before: dict) -> None:
        super().__init__()
        self.monitor: AssistedDownloadMonitor = monitor
        self.queue_manager = queue_manager
        self.item_id = item_id
        self.before = before
        self.signals = AssistedDownloadSignals()

    def run(self) -> None:
        self.signals.completed.emit(
            self.monitor.wait_and_complete(self.queue_manager, self.item_id, self.before)
        )
