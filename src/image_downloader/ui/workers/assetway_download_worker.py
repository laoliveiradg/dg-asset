"""One-item Assetway download worker for the existing Qt thread pool."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QRunnable, Signal

from image_downloader.providers.assetway.downloader import AssetwayDownloader
from image_downloader.providers.assetway.errors import AssetwayDownloadError
from image_downloader.queue.manager import QueueManager

logger = logging.getLogger(__name__)


class AssetwayDownloadSignals(QObject):
    progress = Signal(str)
    completed = Signal(object)


class AssetwayDownloadWorker(QRunnable):
    def __init__(
        self,
        downloader: AssetwayDownloader,
        queue_manager: QueueManager,
        item_id: str,
        *,
        diagnostic_only: bool = False,
    ) -> None:
        super().__init__()
        self.downloader = downloader
        self.queue_manager = queue_manager
        self.item_id = item_id
        self.diagnostic_only = diagnostic_only
        self.signals = AssetwayDownloadSignals()

    def run(self) -> None:
        try:
            if self.diagnostic_only:
                self.signals.progress.emit("Inspecionando controles...")
                result = self.downloader.diagnose_item(self.queue_manager, self.item_id)
            else:
                result = self.downloader.download_item(
                    self.queue_manager,
                    self.item_id,
                    self.signals.progress.emit,
                )
        except AssetwayDownloadError as error:
            self.signals.completed.emit(error)
        except Exception:
            logger.warning("provider=ASSETWAY item_id=%s worker_failed", self.item_id)
            self.signals.completed.emit(
                AssetwayDownloadError(
                    "diagnostic_failed" if self.diagnostic_only else "download_failed",
                    (
                        "O diagnÃ³stico Assetway falhou com seguranÃ§a."
                        if self.diagnostic_only
                        else "O download Assetway falhou. Verifique o acesso e tente novamente."
                    ),
                    retryable=True,
                )
            )
        else:
            self.signals.completed.emit(result)
