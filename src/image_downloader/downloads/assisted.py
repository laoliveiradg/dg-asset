"""Observe one user-authorized browser download without controlling the browser."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from image_downloader.downloads.models import (
    DownloadFailure,
    DownloadResult,
    DownloadStatus,
    DownloadTimings,
)
from image_downloader.providers.models import ProviderId
from image_downloader.queue.manager import QueueManager
from image_downloader.queue.models import QueueError


class AssistedDownloadMonitor:
    def __init__(
        self,
        download_directory: Path,
        runtime_root: Path,
        *,
        timeout: float = 300.0,
        poll_interval: float = 0.5,
    ) -> None:
        self.download_directory = download_directory.resolve()
        self.runtime_root = runtime_root.resolve()
        self.timeout = timeout
        self.poll_interval = poll_interval

    def snapshot(self) -> dict[Path, tuple[int, int]]:
        if not self.download_directory.is_dir():
            return {}
        return {
            path.resolve(): (path.stat().st_size, path.stat().st_mtime_ns)
            for path in self.download_directory.iterdir()
            if path.is_file()
        }

    def wait_and_complete(
        self,
        queue_manager: QueueManager,
        item_id: str,
        before: dict[Path, tuple[int, int]],
    ) -> DownloadResult:
        item = queue_manager.start_processing(item_id)
        deadline = time.monotonic() + self.timeout
        previous: tuple[Path, int] | None = None
        stable_polls = 0
        while time.monotonic() < deadline:
            candidates = self._new_candidates(before)
            if len(candidates) == 1:
                candidate = candidates[0]
                size = candidate.stat().st_size
                current = (candidate, size)
                stable_polls = stable_polls + 1 if current == previous else 0
                previous = current
                if size > 0 and stable_polls >= 2:
                    return self._validate_and_copy(queue_manager, item, candidate)
            elif len(candidates) > 1:
                return self._fail(
                    queue_manager,
                    item_id,
                    "assisted_download_ambiguous",
                    "Mais de um arquivo novo foi detectado durante o download assistido.",
                )
            time.sleep(self.poll_interval)
        return self._fail(
            queue_manager,
            item_id,
            "assisted_download_timeout",
            "O download não foi detectado no tempo esperado.",
        )

    def _new_candidates(self, before: dict[Path, tuple[int, int]]) -> list[Path]:
        if not self.download_directory.is_dir():
            return []
        candidates: list[Path] = []
        for path in self.download_directory.iterdir():
            if not path.is_file() or path.suffix.casefold() in {".crdownload", ".tmp", ".part"}:
                continue
            resolved = path.resolve()
            current = (path.stat().st_size, path.stat().st_mtime_ns)
            if before.get(resolved) != current:
                candidates.append(resolved)
        return candidates

    def _validate_and_copy(self, queue_manager, item, source: Path) -> DownloadResult:
        with source.open("rb") as stream:
            prefix = stream.read(4096).lstrip().lower()
        lowered_name = source.name.casefold()
        if prefix.startswith((b"<!doctype html", b"<html", b"<head", b"<body")) or any(
            term in lowered_name for term in ("preview", "thumbnail", "thumb", "watermark")
        ):
            return self._fail(
                queue_manager,
                item.item_id,
                "assisted_download_invalid",
                "O arquivo detectado não é um download final válido.",
            )
        if not self._format_matches(source.suffix.casefold(), prefix):
            return self._fail(
                queue_manager,
                item.item_id,
                "assisted_download_format_unverified",
                "O formato do arquivo assistido não pôde ser comprovado.",
            )
        destination_dir = (
            self.runtime_root
            / "downloads"
            / item.batch_id
            / item.item_id
            / f"attempt-{item.attempt_count:04d}"
        )
        destination_dir.mkdir(parents=True, exist_ok=False)
        destination = destination_dir / source.name
        shutil.copy2(source, destination)
        size = destination.stat().st_size
        queue_manager.mark_completed(item.item_id)
        return DownloadResult(
            item_id=item.item_id,
            provider=item.provider,
            status=DownloadStatus.COMPLETED,
            file_path=destination,
            file_name=destination.name,
            extension=destination.suffix.casefold(),
            bytes_received=size,
            quality_label="Download oficial assistido",
            source_format=destination.suffix.lstrip(".").upper() or None,
            timings=DownloadTimings(),
        )

    @staticmethod
    def _fail(queue_manager, item_id: str, code: str, message: str) -> DownloadResult:
        item = queue_manager.get_item(item_id)
        error = QueueError(code, message, ProviderId.SHUTTERSTOCK, True)
        queue_manager.mark_failed(item_id, error)
        return DownloadResult(
            item_id=item_id,
            provider=item.provider,
            status=DownloadStatus.FAILED,
            file_path=None,
            file_name=None,
            extension=None,
            bytes_received=0,
            quality_label=None,
            source_format=None,
            timings=DownloadTimings(),
            error=DownloadFailure(code, message, True),
        )

    @staticmethod
    def _format_matches(extension: str, prefix: bytes) -> bool:
        if extension in {".jpg", ".jpeg"}:
            return prefix.startswith(b"\xff\xd8\xff")
        if extension == ".png":
            return prefix.startswith(b"\x89PNG\r\n\x1a\n")
        if extension == ".gif":
            return prefix.startswith((b"GIF87a", b"GIF89a"))
        if extension == ".webp":
            return len(prefix) >= 12 and prefix[:4] == b"RIFF" and prefix[8:12] == b"WEBP"
        if extension == ".svg":
            return b"<svg" in prefix[:512]
        if extension == ".eps":
            return prefix.startswith(b"%!PS-Adobe")
        if extension == ".ai":
            return prefix.startswith((b"%PDF-", b"%!PS-Adobe"))
        if extension == ".pdf":
            return prefix.startswith(b"%PDF-")
        return False
