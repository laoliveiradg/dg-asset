"""Conservative recovery for orphaned app-owned Chrome processes on Windows."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import psutil


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    pid: int
    executable_path: Path
    command_line: tuple[str, ...]
    create_time: float


class ChromeProcessInspector:
    """Inspect and stop only processes proven to use one exact managed profile."""

    def snapshot(self, pid: int) -> ProcessIdentity | None:
        try:
            process = psutil.Process(pid)
            return ProcessIdentity(
                pid=pid,
                executable_path=Path(process.exe()).resolve(),
                command_line=tuple(process.cmdline()),
                create_time=process.create_time(),
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            return None

    def find_exact(
        self,
        profile_path: Path,
        executable_path: Path,
    ) -> list[ProcessIdentity]:
        matches: list[ProcessIdentity] = []
        for process in psutil.process_iter(["pid"]):
            snapshot = self.snapshot(process.pid)
            if snapshot is not None and self.matches(snapshot, profile_path, executable_path):
                matches.append(snapshot)
        return matches

    def stop_tree(self, identity: ProcessIdentity, profile_path: Path) -> bool:
        current = self.snapshot(identity.pid)
        if current != identity or not self._has_exact_profile(current, profile_path):
            return False
        try:
            root = psutil.Process(identity.pid)
            children = root.children(recursive=True)
            targets = [*children, root]
            for process in targets:
                try:
                    process.terminate()
                except psutil.NoSuchProcess:
                    pass
            _, alive = psutil.wait_procs(targets, timeout=3.0)
            for process in alive:
                try:
                    process.kill()
                except psutil.NoSuchProcess:
                    pass
            _, alive = psutil.wait_procs(alive, timeout=2.0)
            return not alive and self.snapshot(identity.pid) is None
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            return self.snapshot(identity.pid) is None

    @classmethod
    def matches(
        cls,
        identity: ProcessIdentity,
        profile_path: Path,
        executable_path: Path,
    ) -> bool:
        return cls._same_path(identity.executable_path, executable_path) and cls._has_exact_profile(
            identity,
            profile_path,
        )

    @classmethod
    def _has_exact_profile(cls, identity: ProcessIdentity, profile_path: Path) -> bool:
        expected = cls._normalized_path(profile_path)
        prefix = "--user-data-dir="
        return any(
            argument.startswith(prefix)
            and cls._normalized_path(argument.removeprefix(prefix)) == expected
            for argument in identity.command_line
        )

    @classmethod
    def _same_path(cls, first: Path, second: Path) -> bool:
        return cls._normalized_path(first) == cls._normalized_path(second)

    @staticmethod
    def _normalized_path(path: str | Path) -> str:
        return os.path.normcase(os.path.abspath(os.fspath(path)))
