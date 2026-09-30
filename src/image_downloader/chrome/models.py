"""Non-secret models and domain errors for the managed Chrome runtime."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from image_downloader.providers.models import ProviderId


class ChromeMode(StrEnum):
    INTERACTIVE = "INTERACTIVE"
    BACKGROUND = "BACKGROUND"
    BACKGROUND_HEADED = "BACKGROUND_HEADED"


class ChromeSessionState(StrEnum):
    UNINITIALIZED = "UNINITIALIZED"
    UNVERIFIED = "UNVERIFIED"
    AUTHENTICATED = "AUTHENTICATED"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    CHECKING = "CHECKING"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class ChromeInstallation:
    executable_path: Path
    version: str | None


@dataclass(frozen=True, slots=True)
class ChromeProfilePaths:
    provider: ProviderId
    provider_root: Path
    user_data_dir: Path
    generation: int


@dataclass(frozen=True, slots=True)
class CdpTarget:
    target_id: str
    target_type: str
    url: str
    title: str


@dataclass(frozen=True, slots=True)
class ChromeTimings:
    locate_ms: float
    profile_ms: float
    process_start_ms: float
    cdp_ready_ms: float
    page_open_ms: float


@dataclass(frozen=True, slots=True)
class ManagedChrome:
    provider: ProviderId
    mode: ChromeMode
    pid: int
    port: int
    profile_paths: ChromeProfilePaths
    installation: ChromeInstallation
    target_id: str
    timings: ChromeTimings


@dataclass(frozen=True, slots=True)
class ChromeSessionStatus:
    provider: ProviderId
    state: ChromeSessionState
    reason: str
    chrome_running: bool
    pid: int | None


class ChromeRuntimeError(RuntimeError):
    """Safe, user-presentable Chrome runtime failure."""


class ChromeNotFoundError(ChromeRuntimeError):
    """Raised when a supported Google Chrome installation cannot be found."""


class ChromeStartupError(ChromeRuntimeError):
    """Raised when an app-owned Chrome process fails to expose local CDP."""


class ChromeProfileInUseError(ChromeStartupError):
    """Raised when a profile already exposes DevTools and ownership is unknown."""


class CdpError(ChromeRuntimeError):
    """Raised when a localhost Chrome DevTools Protocol operation fails."""


class UnsupportedChromeModeError(ChromeRuntimeError):
    """Raised when a mode not implemented in this stage is requested."""


class UnsupportedChromeProviderError(ChromeRuntimeError):
    """Raised when an unsupported provider is passed to the Chrome runtime."""


class ChromeProcessNotOwnedError(ChromeRuntimeError):
    """Raised when an operation attempts to manage a process not owned by this app."""
