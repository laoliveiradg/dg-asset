from __future__ import annotations

from image_downloader.downloads.assisted import AssistedDownloadMonitor
from image_downloader.downloads.models import DownloadStatus
from image_downloader.input.models import SourceType, UrlOccurrence, UrlRecord
from image_downloader.queue.manager import QueueManager
from image_downloader.queue.models import QueueState


def test_assisted_download_detects_copies_and_completes_one_item(tmp_path) -> None:
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    runtime = tmp_path / "runtime"
    queue = QueueManager()
    url = "https://www.shutterstock.com/image-photo/forest-123456789"
    item = queue.add_records(
        [
            UrlRecord.from_occurrences(
                [UrlOccurrence(url, url, SourceType.pasted_text)]
            )
        ]
    )[0]
    monitor = AssistedDownloadMonitor(
        downloads,
        runtime,
        timeout=0.1,
        poll_interval=0.001,
    )
    before = monitor.snapshot()
    (downloads / "licensed.jpg").write_bytes(b"\xff\xd8\xffdownload")

    result = monitor.wait_and_complete(queue, item.item_id, before)

    assert result.status == DownloadStatus.COMPLETED
    assert result.file_path is not None and result.file_path.is_file()
    assert result.file_path.is_relative_to(runtime / "downloads")
    assert queue.get_item(item.item_id).state == QueueState.COMPLETED
