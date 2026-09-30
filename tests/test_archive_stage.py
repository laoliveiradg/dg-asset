from __future__ import annotations

import zipfile

from image_downloader.archive.service import BatchArchiveService


def test_archive_contains_only_valid_files_and_exports_without_overwrite(tmp_path) -> None:
    runtime = tmp_path / "runtime"
    first_dir = runtime / "downloads" / "batch" / "first"
    second_dir = runtime / "downloads" / "batch" / "second"
    first_dir.mkdir(parents=True)
    second_dir.mkdir(parents=True)
    first = first_dir / "asset.png"
    second = second_dir / "asset.png"
    empty = second_dir / "empty.jpg"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    empty.write_bytes(b"")
    service = BatchArchiveService(runtime)

    result = service.create("batch", [first, second, empty])

    assert result.file_count == 2
    with zipfile.ZipFile(result.temporary_path) as archive:
        assert archive.namelist() == ["asset.png", "asset_2.png"]
    destination = tmp_path / "destination"
    exported = service.export(result.temporary_path, destination)
    exported_again = service.export(result.temporary_path, destination)
    assert exported.is_file()
    assert exported_again.is_file()
    assert exported_again != exported
    service.cleanup_batch("batch")
    assert not (runtime / "downloads" / "batch").exists()
    assert not (runtime / "archives" / "batch").exists()
    assert exported.is_file()
