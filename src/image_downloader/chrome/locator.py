"""Locate an existing Google Chrome installation on Windows without installing it."""

from __future__ import annotations

import ctypes
import os
import shutil
from collections.abc import Callable, Iterable
from ctypes import wintypes
from pathlib import Path

from image_downloader.chrome.models import ChromeInstallation, ChromeNotFoundError


class _FixedFileInfo(ctypes.Structure):
    _fields_ = [
        ("signature", wintypes.DWORD),
        ("structure_version", wintypes.DWORD),
        ("file_version_ms", wintypes.DWORD),
        ("file_version_ls", wintypes.DWORD),
        ("product_version_ms", wintypes.DWORD),
        ("product_version_ls", wintypes.DWORD),
        ("file_flags_mask", wintypes.DWORD),
        ("file_flags", wintypes.DWORD),
        ("file_os", wintypes.DWORD),
        ("file_type", wintypes.DWORD),
        ("file_subtype", wintypes.DWORD),
        ("file_date_ms", wintypes.DWORD),
        ("file_date_ls", wintypes.DWORD),
    ]


def read_executable_version(executable: Path) -> str | None:
    """Read PE version metadata without starting Chrome or contacting a browser."""

    try:
        version_library = ctypes.WinDLL("version", use_last_error=True)
    except (AttributeError, OSError):
        return None

    get_size = version_library.GetFileVersionInfoSizeW
    get_size.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD))
    get_size.restype = wintypes.DWORD
    ignored_handle = wintypes.DWORD()
    size = get_size(str(executable), ctypes.byref(ignored_handle))
    if not size:
        return None

    get_info = version_library.GetFileVersionInfoW
    get_info.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID)
    get_info.restype = wintypes.BOOL
    version_data = ctypes.create_string_buffer(size)
    if not get_info(str(executable), 0, size, version_data):
        return None

    query_value = version_library.VerQueryValueW
    query_value.argtypes = (
        wintypes.LPCVOID,
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.UINT),
    )
    query_value.restype = wintypes.BOOL
    value_pointer = ctypes.c_void_p()
    value_length = wintypes.UINT()
    if not query_value(version_data, "\\", ctypes.byref(value_pointer), ctypes.byref(value_length)):
        return None
    if value_length.value < ctypes.sizeof(_FixedFileInfo):
        return None

    fixed_info = ctypes.cast(value_pointer, ctypes.POINTER(_FixedFileInfo)).contents
    if fixed_info.signature != 0xFEEF04BD:
        return None
    major = fixed_info.file_version_ms >> 16
    minor = fixed_info.file_version_ms & 0xFFFF
    build = fixed_info.file_version_ls >> 16
    revision = fixed_info.file_version_ls & 0xFFFF
    return f"{major}.{minor}.{build}.{revision}"


class ChromeLocator:
    """Find Chrome from App Paths, standard installs, then PATH."""

    APP_PATH_KEYS = (
        ("HKEY_CURRENT_USER", r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
        ("HKEY_LOCAL_MACHINE", r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
        (
            "HKEY_LOCAL_MACHINE",
            r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
        ),
    )

    def __init__(
        self,
        *,
        registry_paths: Iterable[str | Path] | None = None,
        search_paths: Iterable[str | Path] | None = None,
        path_lookup: Callable[[str], str | None] = shutil.which,
        version_reader: Callable[[Path], str | None] = read_executable_version,
    ) -> None:
        self._registry_paths = list(registry_paths) if registry_paths is not None else None
        self._search_paths = list(search_paths) if search_paths is not None else None
        self._path_lookup = path_lookup
        self._version_reader = version_reader

    def locate(self) -> ChromeInstallation:
        candidates = self._candidate_paths()
        visited: set[str] = set()
        for candidate in candidates:
            path = Path(candidate).expanduser()
            normalized = os.path.normcase(str(path))
            if normalized in visited:
                continue
            visited.add(normalized)
            if not path.is_file():
                continue
            version = self._read_version(path)
            return ChromeInstallation(path.resolve(), version)
        raise ChromeNotFoundError(
            "Google Chrome não foi encontrado. Instale-o manualmente e tente novamente."
        )

    def _candidate_paths(self) -> list[Path]:
        registry_paths = (
            self._registry_paths
            if self._registry_paths is not None
            else self._read_registry_paths()
        )
        candidates = [Path(path) for path in registry_paths]
        if self._search_paths is not None:
            candidates.extend(Path(path) for path in self._search_paths)
        else:
            local_app_data = os.environ.get("LOCALAPPDATA")
            program_files = os.environ.get("ProgramFiles")
            program_files_x86 = os.environ.get("ProgramFiles(x86)")
            for root in (local_app_data, program_files, program_files_x86):
                if root:
                    candidates.append(
                        Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"
                    )
        path_result = self._path_lookup("chrome.exe") or self._path_lookup("chrome")
        if path_result:
            candidates.append(Path(path_result))
        return candidates

    def _read_registry_paths(self) -> list[Path]:
        if os.name != "nt":
            return []
        try:
            import winreg
        except ImportError:
            return []

        hives = {
            "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
            "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE,
        }
        found: list[Path] = []
        for hive_name, key_path in self.APP_PATH_KEYS:
            try:
                with winreg.OpenKey(hives[hive_name], key_path) as key:
                    executable, _ = winreg.QueryValueEx(key, "")
            except OSError:
                continue
            if executable:
                found.append(Path(executable))
        return found

    def _read_version(self, executable: Path) -> str | None:
        return self._version_reader(executable)