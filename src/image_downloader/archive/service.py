"""Create and export one validated batch archive under the ignored runtime tree."""

from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    temporary_path: Path
    file_count: int


class BatchArchiveService:
    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = runtime_root.resolve()
        self.archive_root = self.runtime_root / "archives"

    def create(self, batch_id: str, files: list[Path]) -> ArchiveResult:
        valid = [path.resolve() for path in files if path.is_file() and path.stat().st_size > 0]
        if not valid:
            raise ValueError("Nenhum arquivo validado está disponível para o ZIP.")
        batch_root = self.archive_root / batch_id
        batch_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        destination = self._available_path(batch_root / f"imagens_{timestamp}.zip")
        used_names: set[str] = set()
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for source in valid:
                archive.write(source, arcname=self._unique_name(source.name, used_names))
        return ArchiveResult(destination, len(valid))

    def export(self, archive: Path, destination_directory: Path) -> Path:
        destination_directory.mkdir(parents=True, exist_ok=True)
        destination = self._available_path(destination_directory / archive.name)
        shutil.copy2(archive, destination)
        return destination

    def cleanup_batch(self, batch_id: str) -> None:
        """Remove only this batch's ignored runtime artifacts after a successful export."""

        for root_name in ("downloads", "archives"):
            root = (self.runtime_root / root_name).resolve()
            target = (root / batch_id).resolve()
            if target.parent == root and target.is_dir():
                shutil.rmtree(target)

    @staticmethod
    def _available_path(path: Path) -> Path:
        if not path.exists():
            return path
        for index in range(1, 10_000):
            candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
            if not candidate.exists():
                return candidate
        raise FileExistsError("Não foi possível criar um nome exclusivo para o arquivo.")

    @staticmethod
    def _unique_name(name: str, used: set[str]) -> str:
        candidate = Path(name).name or "arquivo"
        key = candidate.casefold()
        if key not in used:
            used.add(key)
            return candidate
        path = Path(candidate)
        for index in range(2, 10_000):
            candidate = f"{path.stem}_{index}{path.suffix}"
            key = candidate.casefold()
            if key not in used:
                used.add(key)
                return candidate
        raise FileExistsError("Não foi possível gerar nomes únicos dentro do ZIP.")
