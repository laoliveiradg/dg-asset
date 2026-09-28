"""Track and control only Chrome processes started by this application."""

from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from time import perf_counter

from image_downloader.chrome.cdp_client import CdpClient
from image_downloader.chrome.config import PROVIDER_START_URLS, SUPPORTED_PROVIDERS
from image_downloader.chrome.locator import ChromeLocator
from image_downloader.chrome.models import (
    ChromeInstallation,
    ChromeMode,
    ChromeProfileInUseError,
    ChromeProfilePaths,
    ChromeRuntimeError,
    ChromeStartupError,
    ChromeTimings,
    ManagedChrome,
    UnsupportedChromeModeError,
    UnsupportedChromeProviderError,
)
from image_downloader.chrome.profile_factory import ChromeProfileFactory
from image_downloader.providers.models import ProviderId

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OwnedChromeProcess:
    process: subprocess.Popen
    cdp: CdpClient
    profile_paths: ChromeProfilePaths
    installation: ChromeInstallation
    target_id: str
    timings: ChromeTimings


class ChromeProcessManager:
    """Start interactive Chrome with an isolated profile and manage its Popen handle."""

    def __init__(
        self,
        *,
        locator: ChromeLocator | None = None,
        profile_factory: ChromeProfileFactory | None = None,
        popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        cdp_factory: Callable[..., CdpClient] = CdpClient,
        startup_timeout: float = 20.0,
        cdp_timeout: float = 5.0,
    ) -> None:
        self.locator = locator or ChromeLocator()
        self.profile_factory = profile_factory or ChromeProfileFactory()
        self._popen_factory = popen_factory
        self._cdp_factory = cdp_factory
        self.startup_timeout = startup_timeout
        self.cdp_timeout = cdp_timeout
        self._owned: dict[ProviderId, OwnedChromeProcess] = {}
        self._starting: set[ProviderId] = set()
        self._lock = RLock()

    def start_provider(
        self,
        provider: ProviderId | str,
        *,
        mode: ChromeMode = ChromeMode.INTERACTIVE,
        start_url: str | None = None,
    ) -> ManagedChrome:
        resolved = self._require_provider(provider)
        if mode != ChromeMode.INTERACTIVE:
            raise UnsupportedChromeModeError(
                "Only visible interactive Chrome is supported in this stage."
            )
        initial_url = PROVIDER_START_URLS[resolved] if start_url is None else start_url
        if initial_url not in (PROVIDER_START_URLS[resolved], "about:blank"):
            raise ChromeRuntimeError("Only the configured provider URL or about:blank is allowed.")
        with self._lock:
            existing = self._owned.get(resolved)
            if existing is not None and existing.process.poll() is None:
                existing_is_live = True
            else:
                existing_is_live = False
            if existing is not None and not existing_is_live:
                self._owned.pop(resolved, None)
            if self._starting and resolved in self._starting:
                raise ChromeStartupError("Chrome já está iniciando para este provider.")
            if not existing_is_live:
                self._starting.add(resolved)

        if existing is not None and not existing_is_live:
            self._close_cdp(existing.cdp)
            self._remove_owned_port_file(existing)
        if existing_is_live and existing is not None:
            page_started = perf_counter()
            target_id = existing.cdp.open_page(initial_url)
            timings = ChromeTimings(
                locate_ms=existing.timings.locate_ms,
                profile_ms=existing.timings.profile_ms,
                process_start_ms=existing.timings.process_start_ms,
                cdp_ready_ms=existing.timings.cdp_ready_ms,
                page_open_ms=(perf_counter() - page_started) * 1000,
            )
            with self._lock:
                if self._owned.get(resolved) is existing:
                    existing.target_id = target_id
                    existing.timings = timings
                    return self._managed_record(resolved, existing)
            raise ChromeStartupError(
                "A sessão gerenciada foi encerrada durante a abertura da página."
            )

        if existing is not None and not existing_is_live:
            self._remove_owned_port_file(existing)
        try:
            owned = self._start_new(resolved, initial_url)
        finally:
            with self._lock:
                self._starting.discard(resolved)
        with self._lock:
            self._owned[resolved] = owned
            return self._managed_record(resolved, owned)

    def is_running(self, provider: ProviderId | str) -> bool:
        resolved = self._require_provider(provider)
        with self._lock:
            owned = self._owned.get(resolved)
        if owned is None:
            return False
        if owned.process.poll() is not None:
            with self._lock:
                if self._owned.get(resolved) is owned:
                    self._owned.pop(resolved, None)
            self._close_cdp(owned.cdp)
            self._remove_owned_port_file(owned)
            return False
        return True

    def managed_record(self, provider: ProviderId | str) -> ManagedChrome | None:
        resolved = self._require_provider(provider)
        with self._lock:
            owned = self._owned.get(resolved)
        if owned is None or owned.process.poll() is not None:
            if owned is not None:
                with self._lock:
                    if self._owned.get(resolved) is owned:
                        self._owned.pop(resolved, None)
                self._close_cdp(owned.cdp)
                self._remove_owned_port_file(owned)
            return None
        return self._managed_record(resolved, owned)

    def close_provider(self, provider: ProviderId | str, *, timeout: float = 8.0) -> float:
        resolved = self._require_provider(provider)
        with self._lock:
            owned = self._owned.pop(resolved, None)
        if owned is None:
            return 0.0
        started = perf_counter()
        process = owned.process
        if process.poll() is None:
            try:
                owned.cdp.close_browser()
            except ChromeRuntimeError:
                logger.info("provider=%s cdp_graceful_close_unavailable", resolved.value)
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=2.0)
        self._close_cdp(owned.cdp)
        if process.poll() is not None:
            self._remove_owned_port_file(owned)
        elapsed_ms = (perf_counter() - started) * 1000
        logger.info(
            "provider=%s chrome_closed pid=%s close_ms=%.1f",
            resolved.value,
            process.pid,
            elapsed_ms,
        )
        return elapsed_ms

    def close_all(self, *, timeout: float = 8.0) -> dict[ProviderId, float]:
        with self._lock:
            providers = tuple(self._owned)
        return {
            provider: self.close_provider(provider, timeout=timeout)
            for provider in providers
        }

    def clear_provider(self, provider: ProviderId | str) -> ChromeProfilePaths:
        resolved = self._require_provider(provider)
        self.close_provider(resolved)
        return self.profile_factory.clear_provider(resolved)

    def owned_pids(self) -> dict[ProviderId, int]:
        with self._lock:
            return {
                provider: item.process.pid
                for provider, item in self._owned.items()
                if item.process.poll() is None
            }

    def _start_new(self, provider: ProviderId, initial_url: str) -> OwnedChromeProcess:
        locate_started = perf_counter()
        installation = self.locator.locate()
        locate_ms = (perf_counter() - locate_started) * 1000

        profile_started = perf_counter()
        paths = self.profile_factory.ensure_profile(provider)
        active_port_file = paths.user_data_dir / "DevToolsActivePort"
        if active_port_file.exists():
            raise ChromeProfileInUseError(
                "O profile deste provider já está em uso por uma instância não gerenciada."
            )
        profile_ms = (perf_counter() - profile_started) * 1000

        arguments = [
            str(installation.executable_path),
            f"--user-data-dir={paths.user_data_dir}",
            "--remote-debugging-port=0",
            "--remote-debugging-address=127.0.0.1",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        started = perf_counter()
        process = self._popen_factory(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        process_start_ms = (perf_counter() - started) * 1000
        logger.info("provider=%s chrome_started pid=%s", provider.value, process.pid)

        cdp: CdpClient | None = None
        try:
            cdp_started = perf_counter()
            port = self._wait_for_devtools_port(process, active_port_file)
            cdp = self._cdp_factory(port, timeout=self.cdp_timeout)
            cdp.check_connection()
            cdp_ready_ms = (perf_counter() - cdp_started) * 1000
            logger.info("provider=%s cdp_connected", provider.value)

            page_started = perf_counter()
            target_id = cdp.open_page(initial_url)
            page_open_ms = (perf_counter() - page_started) * 1000
            timings = ChromeTimings(
                locate_ms=locate_ms,
                profile_ms=profile_ms,
                process_start_ms=process_start_ms,
                cdp_ready_ms=cdp_ready_ms,
                page_open_ms=page_open_ms,
            )
            owned = OwnedChromeProcess(process, cdp, paths, installation, target_id, timings)
            return owned
        except Exception as error:
            self._terminate_only_owned_process(process)
            try:
                active_port_file.unlink(missing_ok=True)
            except OSError:
                logger.warning("provider=%s failed_start_port_cleanup_failed", provider.value)
            if cdp is not None:
                self._close_cdp(cdp)
            if isinstance(error, ChromeRuntimeError):
                raise
            raise ChromeStartupError(
                "Chrome iniciou, mas a conexão local de controle não ficou disponível."
            ) from error

    def _wait_for_devtools_port(self, process: subprocess.Popen, active_port_file: Path) -> int:
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise ChromeStartupError(
                    "Chrome encerrou durante a inicialização. Verifique se o profile está em uso."
                )
            if active_port_file.is_file():
                try:
                    lines = active_port_file.read_text(encoding="utf-8").splitlines()
                    port = int(lines[0])
                except (OSError, ValueError, IndexError):
                    time.sleep(0.05)
                    continue
                if not 1 <= port <= 65535:
                    raise ChromeStartupError("Chrome publicou uma porta DevTools inválida.")
                return port
            time.sleep(0.05)
        raise ChromeStartupError("Chrome não publicou a porta local DevTools no tempo esperado.")

    def _terminate_only_owned_process(self, process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2.0)

    @staticmethod
    def _managed_record(provider: ProviderId, owned: OwnedChromeProcess) -> ManagedChrome:
        return ManagedChrome(
            provider=provider,
            pid=owned.process.pid,
            port=owned.cdp.port,
            profile_paths=owned.profile_paths,
            installation=owned.installation,
            target_id=owned.target_id,
            timings=owned.timings,
        )

    @staticmethod
    def _close_cdp(cdp: CdpClient) -> None:
        close = getattr(cdp, "close", None)
        if close is not None:
            try:
                close()
            except Exception:
                pass

    @staticmethod
    def _remove_owned_port_file(owned: OwnedChromeProcess) -> None:
        port_file = owned.profile_paths.user_data_dir / "DevToolsActivePort"
        try:
            port = int(port_file.read_text(encoding="utf-8").splitlines()[0])
        except (OSError, ValueError, IndexError):
            return
        if port == owned.cdp.port:
            port_file.unlink(missing_ok=True)

    @staticmethod
    def _require_provider(provider: ProviderId | str) -> ProviderId:
        try:
            resolved = provider if isinstance(provider, ProviderId) else ProviderId(provider)
        except ValueError as error:
            message = "Provider has no managed Chrome runtime."
            raise UnsupportedChromeProviderError(message) from error
        if resolved not in SUPPORTED_PROVIDERS:
            raise UnsupportedChromeProviderError("Provider has no managed Chrome runtime.")
        return resolved