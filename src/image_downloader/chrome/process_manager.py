"""Track and control only Chrome processes started by this application."""

from __future__ import annotations

import json
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
    UnsupportedChromeProviderError,
)
from image_downloader.chrome.process_recovery import ChromeProcessInspector, ProcessIdentity
from image_downloader.chrome.profile_factory import ChromeProfileFactory
from image_downloader.providers.models import ProviderId

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OwnedChromeProcess:
    process: subprocess.Popen
    mode: ChromeMode
    cdp: CdpClient
    profile_paths: ChromeProfilePaths
    installation: ChromeInstallation
    target_id: str
    timings: ChromeTimings


class ChromeProcessManager:
    """Start official managed Chrome modes with one isolated profile per provider."""

    def __init__(
        self,
        *,
        locator: ChromeLocator | None = None,
        profile_factory: ChromeProfileFactory | None = None,
        popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        cdp_factory: Callable[..., CdpClient] = CdpClient,
        process_inspector: ChromeProcessInspector | None = None,
        startup_timeout: float = 20.0,
        cdp_timeout: float = 5.0,
    ) -> None:
        self.locator = locator or ChromeLocator()
        self.profile_factory = profile_factory or ChromeProfileFactory()
        self._popen_factory = popen_factory
        self._cdp_factory = cdp_factory
        self._process_inspector = process_inspector or ChromeProcessInspector()
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
        if mode not in {
            ChromeMode.INTERACTIVE,
            ChromeMode.BACKGROUND,
            ChromeMode.BACKGROUND_HEADED,
        }:
            raise ChromeRuntimeError("Unsupported managed Chrome launch mode.")
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

        if existing_is_live and existing is not None and existing.mode != mode:
            self.close_provider(resolved)
            existing = None
            existing_is_live = False
            with self._lock:
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
            owned = self._start_new(resolved, initial_url, mode)
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
            self._remove_process_metadata(owned.profile_paths, expected_pid=owned.process.pid)
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
                self._remove_process_metadata(
                    owned.profile_paths,
                    expected_pid=owned.process.pid,
                )
            return None
        return self._managed_record(resolved, owned)

    def cdp_client_for(self, provider: ProviderId | str) -> CdpClient:
        """Return CDP for a live process handle owned by this manager only."""

        resolved = self._require_provider(provider)
        if not self.is_running(resolved):
            raise ChromeRuntimeError("No managed Chrome process is running for this provider.")
        with self._lock:
            owned = self._owned.get(resolved)
            if owned is None or owned.process.poll() is not None:
                raise ChromeRuntimeError("No managed Chrome process is running for this provider.")
            return owned.cdp

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
            self._remove_process_metadata(owned.profile_paths, expected_pid=process.pid)
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

    def recover_provider(self, provider: ProviderId | str) -> None:
        """Release only an orphan proven to use this app's exact provider profile."""

        resolved = self._require_provider(provider)
        installation = self.locator.locate()
        paths = self.profile_factory.ensure_profile(resolved)
        self._reconcile_managed_profile(resolved, paths, installation)

    def _start_new(
        self,
        provider: ProviderId,
        initial_url: str,
        mode: ChromeMode,
    ) -> OwnedChromeProcess:
        locate_started = perf_counter()
        installation = self.locator.locate()
        locate_ms = (perf_counter() - locate_started) * 1000

        profile_started = perf_counter()
        paths = self.profile_factory.ensure_profile(provider)
        self._reconcile_managed_profile(provider, paths, installation)
        active_port_file = paths.user_data_dir / "DevToolsActivePort"
        if active_port_file.exists():
            active_port_file.unlink(missing_ok=True)
            logger.info("provider=%s stale_devtools_port_removed", provider.value)
        profile_ms = (perf_counter() - profile_started) * 1000

        arguments = [
            str(installation.executable_path),
            f"--user-data-dir={paths.user_data_dir}",
            "--remote-debugging-port=0",
            "--remote-debugging-address=127.0.0.1",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        if mode == ChromeMode.BACKGROUND:
            arguments.append("--headless=new")
        elif mode == ChromeMode.BACKGROUND_HEADED:
            arguments.append("--start-minimized")
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
        logger.info(
            "provider=%s chrome_started mode=%s pid=%s",
            provider.value,
            mode.value,
            process.pid,
        )

        cdp: CdpClient | None = None
        try:
            self._write_process_metadata(provider, mode, paths, installation, process.pid)
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
            owned = OwnedChromeProcess(
                process,
                mode,
                cdp,
                paths,
                installation,
                target_id,
                timings,
            )
            return owned
        except Exception as error:
            self._terminate_only_owned_process(process)
            if process.poll() is not None:
                self._remove_process_metadata(paths, expected_pid=process.pid)
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

    def _reconcile_managed_profile(
        self,
        provider: ProviderId,
        paths: ChromeProfilePaths,
        installation: ChromeInstallation,
    ) -> None:
        self._require_dedicated_profile(paths)
        identities: dict[int, ProcessIdentity] = {}
        metadata = self._read_process_metadata(paths)
        if metadata is not None:
            identity = self._identity_from_metadata(metadata, provider, paths, installation)
            if identity is not None:
                identities[identity.pid] = identity
            else:
                self._remove_process_metadata(paths)

        for identity in self._process_inspector.find_exact(
            paths.user_data_dir,
            installation.executable_path,
        ):
            identities[identity.pid] = identity

        for identity in identities.values():
            current = self._process_inspector.snapshot(identity.pid)
            if current is None:
                continue
            if current != identity or not self._process_inspector.matches(
                current,
                paths.user_data_dir,
                installation.executable_path,
            ):
                continue
            logger.info(
                "provider=%s stale_managed_process_found pid=%s",
                provider.value,
                identity.pid,
            )
            if not self._process_inspector.stop_tree(identity, paths.user_data_dir):
                raise ChromeProfileInUseError(
                    "O processo gerenciado anterior ainda está usando o profile deste provider."
                )
            logger.info("provider=%s stale_managed_process_stopped", provider.value)

        remaining = self._process_inspector.find_exact(
            paths.user_data_dir,
            installation.executable_path,
        )
        if remaining:
            raise ChromeProfileInUseError(
                "O processo gerenciado anterior ainda está usando o profile deste provider."
            )
        self._remove_process_metadata(paths)
        self._remove_transient_profile_state(paths)
        logger.info("provider=%s profile_released", provider.value)

    def _write_process_metadata(
        self,
        provider: ProviderId,
        mode: ChromeMode,
        paths: ChromeProfilePaths,
        installation: ChromeInstallation,
        pid: int,
    ) -> None:
        identity = self._process_inspector.snapshot(pid)
        if identity is None or not self._process_inspector.matches(
            identity,
            paths.user_data_dir,
            installation.executable_path,
        ):
            raise ChromeStartupError("Não foi possível confirmar a identidade do Chrome iniciado.")
        metadata = {
            "pid": pid,
            "chrome_executable_path": str(installation.executable_path),
            "provider": provider.value,
            "profile_path": str(paths.user_data_dir),
            "launch_mode": mode.value,
            "create_time": identity.create_time,
            "recorded_at": time.time(),
        }
        metadata_path = self._metadata_path(paths)
        temporary_path = metadata_path.with_suffix(".json.tmp")
        temporary_path.write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
        temporary_path.replace(metadata_path)

    def _identity_from_metadata(
        self,
        metadata: dict[str, object],
        provider: ProviderId,
        paths: ChromeProfilePaths,
        installation: ChromeInstallation,
    ) -> ProcessIdentity | None:
        try:
            pid = int(metadata["pid"])
            create_time = float(metadata["create_time"])
            recorded_provider = str(metadata["provider"])
            profile_path = Path(str(metadata["profile_path"]))
            executable_path = Path(str(metadata["chrome_executable_path"]))
            ChromeMode(str(metadata["launch_mode"]))
        except (KeyError, TypeError, ValueError):
            return None
        if recorded_provider != provider.value:
            return None
        if not self._process_inspector._same_path(profile_path, paths.user_data_dir):
            return None
        if not self._process_inspector._same_path(
            executable_path,
            installation.executable_path,
        ):
            return None
        identity = self._process_inspector.snapshot(pid)
        if identity is None or abs(identity.create_time - create_time) > 0.01:
            return None
        if not self._process_inspector.matches(
            identity,
            paths.user_data_dir,
            installation.executable_path,
        ):
            return None
        return identity

    @staticmethod
    def _read_process_metadata(paths: ChromeProfilePaths) -> dict[str, object] | None:
        try:
            raw = json.loads(ChromeProcessManager._metadata_path(paths).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return raw if isinstance(raw, dict) else None

    @staticmethod
    def _remove_process_metadata(
        paths: ChromeProfilePaths,
        *,
        expected_pid: int | None = None,
    ) -> None:
        metadata_path = ChromeProcessManager._metadata_path(paths)
        if expected_pid is not None:
            metadata = ChromeProcessManager._read_process_metadata(paths)
            if metadata is not None and metadata.get("pid") != expected_pid:
                return
        metadata_path.unlink(missing_ok=True)
        metadata_path.with_suffix(".json.tmp").unlink(missing_ok=True)

    @staticmethod
    def _remove_transient_profile_state(paths: ChromeProfilePaths) -> None:
        for name in (
            "DevToolsActivePort",
            "SingletonCookie",
            "SingletonLock",
            "SingletonSocket",
        ):
            (paths.user_data_dir / name).unlink(missing_ok=True)

    def _require_dedicated_profile(self, paths: ChromeProfilePaths) -> None:
        runtime_root = self.profile_factory.runtime_root.resolve()
        profile_path = paths.user_data_dir.resolve()
        if not profile_path.is_relative_to(runtime_root):
            raise ChromeRuntimeError("Managed Chrome profile is outside the application runtime.")

    @staticmethod
    def _metadata_path(paths: ChromeProfilePaths) -> Path:
        return paths.provider_root / "managed_process.json"

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
            mode=owned.mode,
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
