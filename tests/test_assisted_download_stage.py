from __future__ import annotations

import pytest

from image_downloader.downloads.assisted import AssistedDownloadMonitor
from image_downloader.downloads.models import DownloadStatus
from image_downloader.input.models import SourceType, UrlOccurrence, UrlRecord
from image_downloader.providers.models import ProviderId
from image_downloader.queue.manager import QueueManager
from image_downloader.queue.models import QueueState


def add_shutterstock_item(queue: QueueManager):
    url = "https://www.shutterstock.com/image-photo/forest-123456789"
    return queue.add_records(
        [UrlRecord.from_occurrences([UrlOccurrence(url, url, SourceType.pasted_text)])]
    )[0]


def test_assisted_download_detects_copies_and_completes_one_item(tmp_path) -> None:
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    runtime = tmp_path / "runtime"
    queue = QueueManager()
    item = add_shutterstock_item(queue)
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
    assert (downloads / "licensed.jpg").is_file()
    assert queue.get_item(item.item_id).state == QueueState.COMPLETED


def test_assisted_download_ignores_preexisting_and_temporary_files(tmp_path) -> None:
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    existing = downloads / "existing.jpg"
    existing.write_bytes(b"\xff\xd8\xffold")
    queue = QueueManager()
    item = add_shutterstock_item(queue)
    monitor = AssistedDownloadMonitor(
        downloads,
        tmp_path / "runtime",
        timeout=0.1,
        poll_interval=0.001,
    )
    before = monitor.snapshot()
    existing.write_bytes(b"\xff\xd8\xffchanged")
    (downloads / "licensed.jpg.crdownload").write_bytes(b"partial")
    expected = downloads / "licensed.jpg"
    expected.write_bytes(b"\xff\xd8\xffdownload")

    result = monitor.wait_and_complete(queue, item.item_id, before)

    assert result.status == DownloadStatus.COMPLETED
    assert result.file_name == expected.name


def test_assisted_download_fails_when_two_new_files_are_detected(tmp_path) -> None:
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    queue = QueueManager()
    item = add_shutterstock_item(queue)
    monitor = AssistedDownloadMonitor(
        downloads,
        tmp_path / "runtime",
        timeout=0.1,
        poll_interval=0.001,
    )
    before = monitor.snapshot()
    (downloads / "first.jpg").write_bytes(b"\xff\xd8\xffone")
    (downloads / "second.jpg").write_bytes(b"\xff\xd8\xfftwo")

    result = monitor.wait_and_complete(queue, item.item_id, before)

    assert result.status == DownloadStatus.FAILED
    assert result.error is not None and result.error.code == "ambiguous_download"
    assert queue.get_item(item.item_id).state == QueueState.FAILED


def test_assisted_failure_is_associated_with_envato_item(tmp_path) -> None:
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    queue = QueueManager()
    url = "https://app.envato.com/photos/3e33fbad-d417-4368-9778-8c89c416cbf1"
    item = queue.add_records(
        [UrlRecord.from_occurrences([UrlOccurrence(url, url, SourceType.pasted_text)])]
    )[0]
    monitor = AssistedDownloadMonitor(
        downloads,
        tmp_path / "runtime",
        timeout=0.1,
        poll_interval=0.001,
    )
    before = monitor.snapshot()
    (downloads / "first.jpg").write_bytes(b"\xff\xd8\xffone")
    (downloads / "second.jpg").write_bytes(b"\xff\xd8\xfftwo")

    result = monitor.wait_and_complete(queue, item.item_id, before)

    failed = queue.get_item(item.item_id)
    assert result.provider == ProviderId.ENVATO
    assert result.error is not None and result.error.code == "ambiguous_download"
    assert failed.error is not None and failed.error.provider == ProviderId.ENVATO


@pytest.mark.parametrize(
    ("name", "content", "error_code"),
    [
        ("response.jpg", b"<!doctype html><html>error</html>", "assisted_download_invalid"),
        ("wrong.jpg", b"not-a-jpeg", "assisted_download_format_unverified"),
    ],
)
def test_assisted_download_rejects_invalid_content(
    tmp_path, name: str, content: bytes, error_code: str
) -> None:
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    queue = QueueManager()
    item = add_shutterstock_item(queue)
    monitor = AssistedDownloadMonitor(
        downloads,
        tmp_path / "runtime",
        timeout=0.1,
        poll_interval=0.001,
    )
    before = monitor.snapshot()
    (downloads / name).write_bytes(content)

    result = monitor.wait_and_complete(queue, item.item_id, before)

    assert result.status == DownloadStatus.FAILED
    assert result.error is not None and result.error.code == error_code
    assert queue.get_item(item.item_id).state == QueueState.FAILED
