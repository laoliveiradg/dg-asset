from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from image_downloader.browser.session_models import SessionState
from image_downloader.chrome.cdp_client import CdpClient
from image_downloader.chrome.config import PROVIDER_START_URLS
from image_downloader.chrome.locator import ChromeLocator
from image_downloader.chrome.models import (
    CdpError,
    ChromeMode,
    ChromeNotFoundError,
    ChromeRuntimeError,
    ChromeStartupError,
    UnsupportedChromeProviderError,
)
from image_downloader.chrome.process_manager import ChromeProcessManager
from image_downloader.chrome.process_recovery import ChromeProcessInspector, ProcessIdentity
from image_downloader.chrome.profile_factory import ChromeProfileFactory
from image_downloader.chrome.runtime import ChromeRuntime
from image_downloader.providers.models import ProviderId


@pytest.fixture(scope="session")
def qt_app():
    return QApplication.instance() or QApplication([])


class FakeProcess:
    next_pid = 43000

    def __init__(self) -> None:
        self.pid = FakeProcess.next_pid
        FakeProcess.next_pid += 1
        self.returncode = None
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self):
        self.terminate_calls += 1
        self.returncode = 0

    def kill(self):
        self.kill_calls += 1
        self.returncode = -9


class FakeProcessInspector:
    def __init__(self) -> None:
        self.identities: dict[int, ProcessIdentity] = {}
        self.processes: dict[int, FakeProcess] = {}
        self.stopped_pids: list[int] = []
        self.next_create_time = 1000.0

    def register(
        self,
        process: FakeProcess,
        executable_path: Path,
        profile_path: Path,
        *,
        command_line: tuple[str, ...] | None = None,
        create_time: float | None = None,
    ) -> ProcessIdentity:
        identity = ProcessIdentity(
            pid=process.pid,
            executable_path=executable_path.resolve(),
            command_line=command_line
            or (
                str(executable_path),
                f"--user-data-dir={profile_path}",
            ),
            create_time=create_time or self.next_create_time,
        )
        self.next_create_time += 1.0
        self.identities[process.pid] = identity
        self.processes[process.pid] = process
        return identity

    def snapshot(self, pid: int) -> ProcessIdentity | None:
        return self.identities.get(pid)

    def find_exact(self, profile_path: Path, executable_path: Path) -> list[ProcessIdentity]:
        return [
            identity
            for identity in self.identities.values()
            if self.matches(identity, profile_path, executable_path)
        ]

    def stop_tree(self, identity: ProcessIdentity, profile_path: Path) -> bool:
        if self.identities.get(identity.pid) != identity:
            return False
        self.stopped_pids.append(identity.pid)
        self.identities.pop(identity.pid, None)
        process = self.processes.get(identity.pid)
        if process is not None:
            process.returncode = 0
        return True

    matches = staticmethod(ChromeProcessInspector.matches)
    _same_path = staticmethod(ChromeProcessInspector._same_path)


class FakeCdp:
    def __init__(self, port: int, process: FakeProcess | None = None) -> None:
        self.port = port
        self.process = process
        self.opened_urls: list[str] = []
        self.closed = False

    def check_connection(self):
        return {"Browser": "Chrome/test"}

    def open_page(self, url: str) -> str:
        self.opened_urls.append(url)
        return f"target-{len(self.opened_urls)}"

    def close_browser(self):
        if self.process is not None:
            self.process.returncode = 0

    def close(self):
        self.closed = True


class FakeSocket:
    def __init__(self) -> None:
        self.request_id = None
        self.result = {"targetId": "created-target", "success": True}
        self.closed = False

    def send(self, payload: str) -> None:
        self.request_id = json.loads(payload)["id"]

    def recv(self) -> str:
        return json.dumps({"id": self.request_id, "result": self.result})

    def close(self) -> None:
        self.closed = True


class FakeDownloadSocket:
    def __init__(self) -> None:
        self.messages = [
            json.dumps(
                {
                    "method": "Browser.downloadWillBegin",
                    "params": {
                        "guid": "download-id",
                        "suggestedFilename": "asset.png",
                        "frameId": "main-frame",
                    },
                }
            ),
            json.dumps(
                {
                    "method": "Browser.downloadProgress",
                    "params": {
                        "guid": "download-id",
                        "state": "inProgress",
                        "receivedBytes": 512,
                        "totalBytes": 2048,
                    },
                }
            ),
            json.dumps(
                {
                    "method": "Browser.downloadProgress",
                    "params": {
                        "guid": "download-id",
                        "state": "completed",
                        "receivedBytes": 2048,
                        "totalBytes": 2048,
                    },
                }
            ),
        ]
        self.commands = []
        self.closed = False

    def send(self, payload: str) -> None:
        command = json.loads(payload)
        self.commands.append(command)
        self.messages.insert(0, json.dumps({"id": command["id"], "result": {}}))

    def recv(self) -> str:
        return self.messages.pop(0)

    def close(self) -> None:
        self.closed = True


def make_process_manager(
    tmp_path,
    process: FakeProcess | None = None,
    inspector: FakeProcessInspector | None = None,
):
    chrome_path = tmp_path / "chrome.exe"
    chrome_path.write_bytes(b"fake executable marker")
    active_process = process or FakeProcess()
    popen_calls = []
    cdp_client = FakeCdp(43127, active_process)
    process_inspector = inspector or FakeProcessInspector()

    def popen_factory(arguments, **kwargs):
        popen_calls.append((arguments, kwargs))
        profile_value = next(
            argument.removeprefix("--user-data-dir=")
            for argument in arguments
            if argument.startswith("--user-data-dir=")
        )
        profile = Path(profile_value)
        profile.mkdir(parents=True, exist_ok=True)
        process_inspector.register(active_process, chrome_path, profile)
        (profile / "DevToolsActivePort").write_text(
            "43127\n/devtools/browser/test-id\n",
            encoding="utf-8",
        )
        return active_process

    manager = ChromeProcessManager(
        locator=ChromeLocator(
            registry_paths=[chrome_path],
            search_paths=[],
            path_lookup=lambda executable: None,
            version_reader=lambda executable: "test",
        ),
        profile_factory=ChromeProfileFactory(tmp_path / "runtime"),
        popen_factory=popen_factory,
        cdp_factory=lambda port, timeout: cdp_client,
        process_inspector=process_inspector,
    )
    manager._test_process_inspector = process_inspector
    return manager, active_process, cdp_client, popen_calls


def test_locator_prefers_configured_app_path_and_reads_version(tmp_path) -> None:
    registered = tmp_path / "registered" / "chrome.exe"
    fallback = tmp_path / "program-files" / "chrome.exe"
    registered.parent.mkdir()
    fallback.parent.mkdir()
    registered.touch()
    fallback.touch()
    reader = Mock(return_value="135.0.0.0")
    locator = ChromeLocator(
        registry_paths=[registered],
        search_paths=[fallback],
        path_lookup=lambda executable: None,
        version_reader=reader,
    )
    installation = locator.locate()
    assert installation.executable_path == registered.resolve()
    assert installation.version == "135.0.0.0"
    reader.assert_called_once_with(registered)


def test_locator_checks_installation_paths_then_path(tmp_path) -> None:
    by_path = tmp_path / "path" / "chrome.exe"
    by_path.parent.mkdir()
    by_path.touch()
    locator = ChromeLocator(
        registry_paths=[],
        search_paths=[],
        path_lookup=lambda executable: str(by_path) if executable == "chrome.exe" else None,
        version_reader=lambda executable: None,
    )
    assert locator.locate().executable_path == by_path.resolve()


def test_missing_chrome_raises_friendly_domain_error() -> None:
    locator = ChromeLocator(
        registry_paths=[],
        search_paths=[],
        path_lookup=lambda executable: None,
    )
    with pytest.raises(ChromeNotFoundError, match="Google Chrome não foi encontrado"):
        locator.locate()


def test_chrome_profiles_are_deterministic_and_provider_isolated(tmp_path) -> None:
    first_factory = ChromeProfileFactory(tmp_path / "runtime")
    second_factory = ChromeProfileFactory(tmp_path / "runtime")
    paths = {
        provider: first_factory.paths_for(provider)
        for provider in (ProviderId.ASSETWAY, ProviderId.SHUTTERSTOCK, ProviderId.ENVATO)
    }
    assert paths[ProviderId.ASSETWAY] != paths[ProviderId.SHUTTERSTOCK]
    assert paths[ProviderId.SHUTTERSTOCK] != paths[ProviderId.ENVATO]
    assert all("chrome_profiles" in str(item.user_data_dir) for item in paths.values())
    assert paths[ProviderId.ASSETWAY] == second_factory.paths_for(ProviderId.ASSETWAY)


def test_chrome_runtime_profiles_are_ignored_by_git() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["git", "check-ignore", "runtime/chrome_profiles/assetway/profile-0000/Cookies"],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def test_profile_factory_clear_rotates_only_one_provider(tmp_path) -> None:
    factory = ChromeProfileFactory(tmp_path / "runtime")
    assetway = factory.ensure_profile(ProviderId.ASSETWAY)
    shutterstock = factory.ensure_profile(ProviderId.SHUTTERSTOCK)
    envato = factory.ensure_profile(ProviderId.ENVATO)
    (assetway.user_data_dir / "test-marker").write_text("marker", encoding="utf-8")

    cleared = factory.clear_provider(ProviderId.ASSETWAY)

    assert cleared.provider == ProviderId.ASSETWAY
    assert cleared.user_data_dir.exists()
    assert not (cleared.user_data_dir / "test-marker").exists()
    assert shutterstock.user_data_dir.exists()
    assert envato.user_data_dir.exists()


def test_unknown_provider_is_rejected(tmp_path) -> None:
    with pytest.raises(UnsupportedChromeProviderError):
        ChromeProfileFactory(tmp_path).paths_for(ProviderId.UNKNOWN)
    with pytest.raises(UnsupportedChromeProviderError):
        ChromeProfileFactory(tmp_path).paths_for("OTHER")


def test_devtools_active_port_is_read_from_profile_not_fixed(tmp_path) -> None:
    manager, process, cdp, calls = make_process_manager(tmp_path)
    managed = manager.start_provider(ProviderId.ASSETWAY)
    assert managed.port == 43127
    assert managed.port != 9222
    assert managed.target_id == "target-1"
    assert cdp.opened_urls == [PROVIDER_START_URLS[ProviderId.ASSETWAY]]
    manager.close_provider(ProviderId.ASSETWAY)
    assert process.terminate_calls == 0
    assert process.kill_calls == 0
    assert not (
        managed.profile_paths.user_data_dir / "DevToolsActivePort"
    ).exists()
    assert len(calls) == 1


def test_chrome_arguments_bind_cdp_to_loopback_and_use_ephemeral_port(tmp_path) -> None:
    manager, _, _, calls = make_process_manager(tmp_path)
    manager.start_provider(ProviderId.SHUTTERSTOCK)
    arguments, options = calls[0]
    assert "--remote-debugging-port=0" in arguments
    assert "--remote-debugging-address=127.0.0.1" in arguments
    assert "--no-first-run" in arguments
    assert "--no-default-browser-check" in arguments
    assert options["shell"] is False
    assert all("stealth" not in argument.lower() for argument in arguments)
    manager.close_provider(ProviderId.SHUTTERSTOCK)


def test_process_manager_does_not_close_unowned_external_process(tmp_path) -> None:
    manager, external_process, _, _ = make_process_manager(tmp_path)
    assert manager.close_provider(ProviderId.ENVATO) == 0
    assert external_process.terminate_calls == 0
    assert external_process.kill_calls == 0
    assert external_process.returncode is None


def test_owned_process_is_reused_and_shutdown_closes_only_owned_pid(tmp_path) -> None:
    manager, process, cdp, _ = make_process_manager(tmp_path)
    first = manager.start_provider(ProviderId.ENVATO)
    second = manager.start_provider(ProviderId.ENVATO)
    assert first.pid == second.pid == process.pid
    assert cdp.opened_urls == [
        PROVIDER_START_URLS[ProviderId.ENVATO],
        PROVIDER_START_URLS[ProviderId.ENVATO],
    ]
    assert manager.owned_pids() == {ProviderId.ENVATO: process.pid}
    manager.close_all()
    assert process.returncode == 0
    assert process.terminate_calls == 0


def test_runtime_exposes_cdp_only_for_live_managed_process(tmp_path) -> None:
    manager, _, cdp, _ = make_process_manager(tmp_path)
    runtime = ChromeRuntime(process_manager=manager)

    with pytest.raises(ChromeRuntimeError, match="No managed Chrome process"):
        runtime.cdp_client_for(ProviderId.ASSETWAY)

    runtime.open_provider(ProviderId.ASSETWAY)
    assert runtime.cdp_client_for(ProviderId.ASSETWAY) is cdp
    runtime.shutdown()


def test_process_start_fails_if_popen_hands_off_to_existing_chrome(tmp_path) -> None:
    manager, process, _, calls = make_process_manager(tmp_path)

    def exited_process(arguments, **kwargs):
        calls.append((arguments, kwargs))
        process.returncode = 0
        return process

    manager._popen_factory = exited_process
    with pytest.raises(ChromeStartupError, match="confirmar a identidade"):
        manager.start_provider(ProviderId.ASSETWAY)
    assert process.terminate_calls == 0


def test_cdp_startup_failure_removes_port_file_from_owned_profile(tmp_path) -> None:
    manager, process, _, _ = make_process_manager(tmp_path)
    paths = manager.profile_factory.ensure_profile(ProviderId.ASSETWAY)
    active_port_file = paths.user_data_dir / "DevToolsActivePort"

    def fail_to_connect(port: int, *, timeout: float) -> FakeCdp:
        raise CdpError("Local CDP connection failed.")

    manager._cdp_factory = fail_to_connect
    with pytest.raises(CdpError, match="Local CDP connection failed"):
        manager.start_provider(ProviderId.ASSETWAY)

    assert process.returncode == 0
    assert not active_port_file.exists()


def test_preexisting_devtools_port_is_never_reused(tmp_path) -> None:
    manager, _, _, calls = make_process_manager(tmp_path)
    paths = manager.profile_factory.ensure_profile(ProviderId.ASSETWAY)
    active_port = paths.user_data_dir / "DevToolsActivePort"
    active_port.write_text("43129\n/devtools/browser/foreign\n", encoding="utf-8")
    managed = manager.start_provider(ProviderId.ASSETWAY)
    assert len(calls) == 1
    assert managed.port == 43127
    assert active_port.read_text(encoding="utf-8").startswith("43127")
    manager.close_provider(ProviderId.ASSETWAY)


def test_stale_devtools_port_is_removed_before_new_owned_launch(tmp_path) -> None:
    manager, _, _, calls = make_process_manager(tmp_path)
    paths = manager.profile_factory.ensure_profile(ProviderId.ASSETWAY)
    active_port = paths.user_data_dir / "DevToolsActivePort"
    active_port.write_text("43129\n/devtools/browser/stale\n", encoding="utf-8")

    managed = manager.start_provider(
        ProviderId.ASSETWAY,
        mode=ChromeMode.BACKGROUND_HEADED,
        start_url="about:blank",
    )

    assert len(calls) == 1
    assert managed.port == 43127
    assert active_port.read_text(encoding="utf-8").startswith("43127")
    manager.close_provider(ProviderId.ASSETWAY)


def test_orphan_using_exact_dedicated_profile_is_recovered_without_erasing_profile(
    tmp_path,
) -> None:
    inspector = FakeProcessInspector()
    first_manager, orphan, _, _ = make_process_manager(tmp_path, inspector=inspector)
    first = first_manager.start_provider(ProviderId.ASSETWAY)
    marker = first.profile_paths.user_data_dir / "session-marker"
    marker.write_text("keep", encoding="utf-8")

    replacement = FakeProcess()
    second_manager, _, _, _ = make_process_manager(
        tmp_path,
        process=replacement,
        inspector=inspector,
    )
    second = second_manager.start_provider(ProviderId.ASSETWAY)

    assert orphan.pid in inspector.stopped_pids
    assert second.pid == replacement.pid
    assert marker.read_text(encoding="utf-8") == "keep"
    metadata = json.loads(
        (second.profile_paths.provider_root / "managed_process.json").read_text(
            encoding="utf-8"
        )
    )
    assert metadata["pid"] == replacement.pid
    second_manager.close_provider(ProviderId.ASSETWAY)
    assert not (second.profile_paths.provider_root / "managed_process.json").exists()


def test_personal_chrome_with_other_profile_is_never_touched(tmp_path) -> None:
    inspector = FakeProcessInspector()
    manager, _, _, _ = make_process_manager(tmp_path, inspector=inspector)
    personal = FakeProcess()
    chrome_path = manager.locator.locate().executable_path
    inspector.register(personal, chrome_path, tmp_path / "personal-profile")

    managed = manager.start_provider(ProviderId.ASSETWAY)

    assert personal.pid not in inspector.stopped_pids
    assert personal.returncode is None
    assert managed.pid != personal.pid
    manager.close_provider(ProviderId.ASSETWAY)


def test_reused_pid_from_stale_metadata_is_not_stopped(tmp_path) -> None:
    inspector = FakeProcessInspector()
    first_manager, old_process, _, _ = make_process_manager(tmp_path, inspector=inspector)
    first = first_manager.start_provider(ProviderId.ASSETWAY)
    metadata_path = first.profile_paths.provider_root / "managed_process.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    reused = FakeProcess()
    reused.pid = old_process.pid
    inspector.identities.pop(old_process.pid)
    inspector.register(
        reused,
        first.installation.executable_path,
        tmp_path / "personal-profile",
        create_time=float(metadata["create_time"]) + 100.0,
    )
    replacement = FakeProcess()
    second_manager, _, _, _ = make_process_manager(
        tmp_path,
        process=replacement,
        inspector=inspector,
    )

    second_manager.start_provider(ProviderId.ASSETWAY)

    assert reused.pid not in inspector.stopped_pids
    assert reused.returncode is None
    second_manager.close_provider(ProviderId.ASSETWAY)


def test_smoke_mode_can_open_only_about_blank(tmp_path) -> None:
    manager, _, cdp, _ = make_process_manager(tmp_path)
    manager.start_provider(ProviderId.ASSETWAY, start_url="about:blank")
    assert cdp.opened_urls == ["about:blank"]
    manager.close_provider(ProviderId.ASSETWAY)
    with pytest.raises(ChromeRuntimeError):
        manager.start_provider(ProviderId.ASSETWAY, start_url="https://example.com/")


def test_background_mode_uses_official_headless_and_same_profile(tmp_path) -> None:
    manager, _, _, calls = make_process_manager(tmp_path)
    managed = manager.start_provider(ProviderId.ASSETWAY, mode=ChromeMode.BACKGROUND)

    arguments, _ = calls[0]
    assert managed.mode == ChromeMode.BACKGROUND
    assert "--headless=new" in arguments
    assert all("stealth" not in argument.casefold() for argument in arguments)
    assert managed.profile_paths == manager.profile_factory.paths_for(ProviderId.ASSETWAY)


def test_interactive_and_background_modes_reuse_persistent_provider_profile(tmp_path) -> None:
    background_manager, _, _, background_calls = make_process_manager(tmp_path)
    background = background_manager.start_provider(
        ProviderId.ASSETWAY,
        mode=ChromeMode.BACKGROUND,
    )
    background_manager.close_provider(ProviderId.ASSETWAY)

    interactive_manager, _, _, interactive_calls = make_process_manager(tmp_path)
    interactive = interactive_manager.start_provider(
        ProviderId.ASSETWAY,
        mode=ChromeMode.INTERACTIVE,
    )

    assert background.profile_paths.user_data_dir == interactive.profile_paths.user_data_dir
    assert "--headless=new" in background_calls[0][0]
    assert "--headless=new" not in interactive_calls[0][0]
    assert interactive.mode == ChromeMode.INTERACTIVE
    interactive_manager.close_provider(ProviderId.ASSETWAY)


def test_background_headed_is_minimized_without_headless_or_stealth(tmp_path) -> None:
    manager, _, _, calls = make_process_manager(tmp_path)
    managed = manager.start_provider(
        ProviderId.ASSETWAY,
        mode=ChromeMode.BACKGROUND_HEADED,
    )

    arguments, _ = calls[0]
    assert managed.mode == ChromeMode.BACKGROUND_HEADED
    assert "--start-minimized" in arguments
    assert "--headless=new" not in arguments
    assert all("stealth" not in argument.casefold() for argument in arguments)
    manager.close_provider(ProviderId.ASSETWAY)


def test_interactive_to_background_headed_releases_same_persistent_profile(tmp_path) -> None:
    first_process = FakeProcess()
    manager, _, _, calls = make_process_manager(tmp_path, process=first_process)
    interactive = manager.start_provider(ProviderId.ASSETWAY, mode=ChromeMode.INTERACTIVE)
    second_process = FakeProcess()

    def start_second_process(arguments, **kwargs):
        calls.append((arguments, kwargs))
        manager._test_process_inspector.register(
            second_process,
            interactive.installation.executable_path,
            interactive.profile_paths.user_data_dir,
            command_line=tuple(arguments),
        )
        (interactive.profile_paths.user_data_dir / "DevToolsActivePort").write_text(
            "43127\n/devtools/browser/test-id\n",
            encoding="utf-8",
        )
        return second_process

    manager._popen_factory = start_second_process

    background = manager.start_provider(
        ProviderId.ASSETWAY,
        mode=ChromeMode.BACKGROUND_HEADED,
    )

    assert first_process.returncode == 0
    assert background.pid == second_process.pid
    assert background.profile_paths == interactive.profile_paths
    assert "--start-minimized" in calls[-1][0]
    manager.close_provider(ProviderId.ASSETWAY)


def test_cdp_client_uses_localhost_and_opens_pages_with_browser_protocol() -> None:
    socket = FakeSocket()
    endpoints = []

    def json_getter(path: str, timeout: float):
        if path == "/json/version":
            return {"webSocketDebuggerUrl": "ws://127.0.0.1:43127/devtools/browser/id"}
        return []

    def websocket_factory(url: str, **kwargs):
        endpoints.append((url, kwargs))
        return socket

    client = CdpClient(43127, json_getter=json_getter, websocket_factory=websocket_factory)
    target_id = client.open_page("about:blank")
    assert target_id == "created-target"
    assert endpoints[0][0] == "ws://127.0.0.1:43127/devtools/browser/id"
    assert endpoints[0][1]["suppress_origin"] is True
    assert socket.closed


def test_cdp_rejects_remote_debugging_endpoint_outside_loopback() -> None:
    client = CdpClient(
        43127,
        json_getter=lambda path, timeout: {
            "webSocketDebuggerUrl": "ws://192.168.1.5:43127/devtools/browser/id"
        },
    )
    with pytest.raises(CdpError, match="loopback"):
        client.check_connection()


def test_cdp_page_listing_returns_only_public_page_metadata() -> None:
    client = CdpClient(
        43127,
        json_getter=lambda path, timeout: [
            {
                "id": "target-1",
                "type": "page",
                "url": "about:blank",
                "title": "",
                "webSocketDebuggerUrl": "ws://127.0.0.1:43127/devtools/page/target-1",
            },
            {"id": "worker-1", "type": "service_worker", "url": "", "title": ""},
        ],
    )
    pages = client.list_pages()
    assert len(pages) == 1
    assert pages[0].target_id == "target-1"
    assert pages[0].url == "about:blank"


def test_cdp_evaluates_page_by_target_and_returns_value() -> None:
    socket = FakeSocket()
    socket.result = {"result": {"type": "object", "value": {"ready": "complete"}}}
    commands = []

    def json_getter(path: str, timeout: float):
        return [
            {
                "id": "target-1",
                "type": "page",
                "url": "about:blank",
                "webSocketDebuggerUrl": "ws://127.0.0.1:43127/devtools/page/target-1",
            }
        ]

    def websocket_factory(url: str, **kwargs):
        commands.append(url)
        return socket

    client = CdpClient(43127, json_getter=json_getter, websocket_factory=websocket_factory)

    value = client.evaluate_page("target-1", "document.readyState")

    assert value == {"ready": "complete"}
    assert commands == ["ws://127.0.0.1:43127/devtools/page/target-1"]
    assert socket.closed


def test_cdp_download_monitor_sets_runtime_path_and_waits_for_completion(tmp_path) -> None:
    socket = FakeDownloadSocket()
    endpoints = []

    def json_getter(path: str, timeout: float):
        return {"webSocketDebuggerUrl": "ws://127.0.0.1:43127/devtools/browser/id"}

    def websocket_factory(url: str, **kwargs):
        endpoints.append((url, kwargs))
        return socket

    client = CdpClient(43127, json_getter=json_getter, websocket_factory=websocket_factory)
    download_path = tmp_path / "managed-downloads"
    download_path.mkdir()

    with client.monitor_downloads(download_path) as monitor:
        completed = monitor.wait_for_completion(timeout=1.0)

    assert completed.suggested_filename == "asset.png"
    assert completed.frame_id == "main-frame"
    assert completed.received_bytes == completed.total_bytes == 2048
    assert endpoints[0][0] == "ws://127.0.0.1:43127/devtools/browser/id"
    assert socket.commands[0]["method"] == "Browser.setDownloadBehavior"
    assert socket.commands[0]["params"] == {
        "behavior": "allow",
        "downloadPath": str(download_path),
        "eventsEnabled": True,
    }
    assert socket.commands[1]["params"] == {
        "behavior": "default",
        "eventsEnabled": False,
    }
    assert socket.closed


def test_runtime_reuses_profile_and_keeps_authentication_unverified(tmp_path) -> None:
    manager, _, _, _ = make_process_manager(tmp_path)
    runtime = ChromeRuntime(process_manager=manager)
    initial = runtime.status(ProviderId.ASSETWAY)
    assert initial.state == SessionState.UNVERIFIED
    assert initial.reason == "chrome_closed"
    result = runtime.open_provider(ProviderId.ASSETWAY)
    status = runtime.status(ProviderId.ASSETWAY)
    assert result.profile_paths == manager.profile_factory.paths_for(ProviderId.ASSETWAY)
    assert status.state == SessionState.UNVERIFIED
    assert status.chrome_running is True
    runtime.shutdown()


def test_public_chrome_models_do_not_include_secret_fields() -> None:
    from dataclasses import fields

    from image_downloader.chrome.runtime import ChromeSessionStatus

    names = {field.name.lower() for field in fields(ChromeSessionStatus)}
    assert names.isdisjoint({"password", "cookie", "cookies", "token", "authorization", "storage"})


def test_active_application_import_does_not_load_qtwebengine() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(repository_root / "src")
    result = subprocess.run(
        [
            str(Path(__import__("sys").executable)),
            "-c",
            "import sys; import image_downloader.app; "
            "assert not any(name.startswith('PySide6.QtWebEngine') for name in sys.modules)",
        ],
        cwd=repository_root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_startup_error_is_safe_and_persists_in_ui(qt_app, tmp_path) -> None:
    class FailingProcessManager:
        profile_factory = ChromeProfileFactory(tmp_path / "runtime")

        def start_provider(self, provider, **kwargs):
            raise ChromeNotFoundError("Google Chrome não foi encontrado.")

        def is_running(self, provider):
            return False

        def managed_record(self, provider):
            return None

        def close_all(self):
            return {}

    from image_downloader.ui.main_window import MainWindow

    runtime = ChromeRuntime(process_manager=FailingProcessManager())
    window = MainWindow(chrome_runtime=runtime)
    loop = QEventLoop()
    outcomes: list[bool] = []

    def on_finished(operation, provider, success) -> None:
        if operation == "open" and provider == ProviderId.ASSETWAY:
            outcomes.append(success)
            loop.quit()

    window.session_controller.operation_finished.connect(on_finished)
    QTimer.singleShot(8000, loop.quit)
    window._open_provider(ProviderId.ASSETWAY)
    loop.exec()
    assert outcomes == [False]
    assert window.access_status_labels[ProviderId.ASSETWAY].text() == "Erro ao iniciar"
    window.session_controller.refresh_statuses()
    assert window.access_status_labels[ProviderId.ASSETWAY].text() == "Erro ao iniciar"
    window.close()


def test_closing_main_window_shuts_down_only_owned_chrome(qt_app, tmp_path) -> None:
    process_manager, owned_process, _, _ = make_process_manager(tmp_path)
    process_manager.start_provider(ProviderId.ASSETWAY, start_url="about:blank")
    external_process = FakeProcess()
    runtime = ChromeRuntime(process_manager=process_manager)

    class SlowShutdownRuntime(ChromeRuntime):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.shutdown_started = Event()
            self.allow_shutdown = Event()

        def shutdown(self):
            self.shutdown_started.set()
            self.allow_shutdown.wait(timeout=4.0)
            return super().shutdown()

    runtime = SlowShutdownRuntime(process_manager=process_manager)

    from image_downloader.ui.main_window import MainWindow

    window = MainWindow(chrome_runtime=runtime)
    loop = QEventLoop()
    shutdown_completed: list[bool] = []

    def finish_loop() -> None:
        shutdown_completed.append(True)
        loop.quit()

    window.session_controller.shutdown_finished.connect(finish_loop)
    window.close()
    assert runtime.shutdown_started.wait(timeout=2.0)
    assert not window._shutdown_complete
    runtime.allow_shutdown.set()
    QTimer.singleShot(3000, loop.quit)
    loop.exec()

    assert shutdown_completed == [True]
    assert window._shutdown_complete
    assert owned_process.returncode == 0
    assert owned_process.terminate_calls == 0
    assert external_process.returncode is None
    assert external_process.terminate_calls == 0
    assert external_process.kill_calls == 0
