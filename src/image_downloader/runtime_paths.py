"""User-writable runtime paths for the portable Windows application."""

from __future__ import annotations

import os
from pathlib import Path

APP_DATA_DIRECTORY = "Asset"


def application_data_root() -> Path:
    """Return the per-user application data directory without requiring elevation."""

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data)
    elif os.name == "nt":
        base = Path.home() / "AppData" / "Local"
    else:
        base = Path.home() / ".local" / "share"
    return (base / APP_DATA_DIRECTORY).resolve()


def default_runtime_root() -> Path:
    """Return the mutable runtime root used by the installed or portable app."""

    return application_data_root() / "runtime"
