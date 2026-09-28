from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import fields
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

import pytest
from PySide6.QtCore import QEventLoop, QTimer, QUrl
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWidgets import QApplication, QMessageBox

from image_downloader.browser.browser_dialog import PROVIDER_START_URLS, BrowserDialog
from image_downloader.browser.controller import SessionController
from image_downloader.browser.profile_factory import ProfileFactory
from image_downloader.browser.session_manager import SessionManager
from image_downloader.browser.session_models import (
    SessionState,
    SessionStatus,
    UnsupportedSessionProviderError,
)
from image_downloader.chrome.process_manager import ChromeProcessManager
from image_downloader.chrome.profile_factory import ChromeProfileFactory
from image_downloader.chrome.runtime import ChromeRuntime
from image_downloader.providers.models import ProviderId
from image_downloader.ui.main_window import MainWindow


@pytest.fixture(scope="session")
def qt_app():
    return QApplication.instance() or QApplication([])


def wait_for_clear(manager: SessionManager, provider: ProviderId, action) -> bool:
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    results: list[bool] = []

    def on_finished(finished_provider, success) -> None:
        if finished_provider == provider:
            results.append(success)
            loop.quit()

    manager.clear_finished.connect(on_finished)
    timer.timeout.connect(loop.quit)
    timer.start(8000)
    action()
    if not results:
        loop.exec()
    manager.clear_finished.disconnect(on_finished)
    assert results, "Session clearing did not finish before the timeout."
    return results[0]


def test_profiles_are_isolated_deterministic_and_inside_runtime(tmp_path) -> None:
    first_factory = ProfileFactory(tmp_path / "runtime")
    second_factory = ProfileFactory(tmp_path / "runtime")
    profiles = [
        first_factory.paths_for(provider)
        for provider in (ProviderId.ASSETWAY, ProviderId.SHUTTERSTOCK, ProviderId.ENVATO)
    ]

    assert [paths.root.name for paths in profiles] == ["assetway", "shutterstock", "envato"]
    assert len({paths.root for paths in profiles}) == 3
    assert profiles[0].root != profiles[1].root
    assert profiles[1].root != profiles[2].root
    runtime_profiles = tmp_path / "runtime" / "browser_profiles"
    assert all(paths.root.is_relative_to(runtime_profiles) for paths in profiles)
    assert first_factory.paths_for(ProviderId.ASSETWAY) == second_factory.paths_for(
        ProviderId.ASSETWAY
    )


def test_runtime_browser_profiles_is_ignored_by_git() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["git", "check-ignore", "runtime/browser_profiles/assetway/storage/Cookies"],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def test_session_starts_uninitialized_and_cookie_presence_is_unverified(qt_app, tmp_path) -> None:
    manager = SessionManager(runtime_root=tmp_path / "runtime")
    assert manager.status(ProviderId.ASSETWAY).state == SessionState.UNINITIALIZED
    profile = manager.profile_for_provider(ProviderId.ASSETWAY)
    cookie = QNetworkCookie(b"session-marker", b"not-a-credential")
    cookie.setDomain("plataformaa.assetway.com.br")
    cookie.setPath("/")
    profile.cookieStore().setCookie(
        cookie,
        QUrl("https://plataformaa.assetway.com.br/"),
    )
    qt_app.processEvents()
    assert profile.persistentStoragePath() == str(
        manager.profile_paths(ProviderId.ASSETWAY).persistent_storage
    )
    assert manager.check_session(ProviderId.ASSETWAY).state == SessionState.UNVERIFIED
    manager.shutdown()


def test_restarting_manager_reuses_the_same_persistent_profile_path(qt_app, tmp_path) -> None:
    runtime_root = tmp_path / "runtime"
    first_manager = SessionManager(runtime_root=runtime_root)
    first_paths = first_manager.profile_paths(ProviderId.SHUTTERSTOCK)
    first_profile = first_manager.profile_for_provider(ProviderId.SHUTTERSTOCK)
    assert first_profile.persistentStoragePath() == str(first_paths.persistent_storage)
    first_manager.shutdown()

    second_manager = SessionManager(runtime_root=runtime_root)
    second_paths = second_manager.profile_paths(ProviderId.SHUTTERSTOCK)
    second_profile = second_manager.profile_for_provider(ProviderId.SHUTTERSTOCK)
    assert second_paths == first_paths
    assert second_profile.persistentStoragePath() == str(first_paths.persistent_storage)
    second_manager.shutdown()


def test_startup_reports_stale_generation_cleanup(qt_app, tmp_path) -> None:
    runtime_root = tmp_path / "runtime"
    factory = ProfileFactory(runtime_root)
    retired = factory.paths_for_generation(ProviderId.ENVATO, 0)
    active = factory.paths_for_generation(ProviderId.ENVATO, 1)
    retired.generation_root.mkdir(parents=True)
    active.generation_root.mkdir(parents=True)

    manager = SessionManager(runtime_root=runtime_root)
    status = manager.status(ProviderId.ENVATO)
    assert status.state == SessionState.UNVERIFIED
    assert status.reason == "previous_profile_cleanup_pending_restart"
    manager.shutdown()


def test_clear_assetway_rotates_only_its_profile_generation(qt_app, tmp_path) -> None:
    manager = SessionManager(runtime_root=tmp_path / "runtime")
    providers = (ProviderId.ASSETWAY, ProviderId.SHUTTERSTOCK, ProviderId.ENVATO)
    sentinels: dict[ProviderId, Path] = {}
    original_assetway_paths = manager.profile_paths(ProviderId.ASSETWAY)
    for provider in providers:
        manager.profile_for_provider(provider)
        storage = manager.profile_paths(provider).persistent_storage
        sentinel = storage / "test-marker"
        sentinel.write_text(provider.value, encoding="utf-8")
        sentinels[provider] = sentinel

    assert wait_for_clear(
        manager,
        ProviderId.ASSETWAY,
        lambda: manager.clear_provider(ProviderId.ASSETWAY),
    )
    assert manager.status(ProviderId.ASSETWAY).state == SessionState.UNVERIFIED
    active_assetway_paths = manager.profile_paths(ProviderId.ASSETWAY)
    assert active_assetway_paths.generation > original_assetway_paths.generation
    assert manager.profile_for_provider(ProviderId.ASSETWAY).persistentStoragePath() == str(
        active_assetway_paths.persistent_storage
    )
    assert not (active_assetway_paths.persistent_storage / "test-marker").exists()
    assert sentinels[ProviderId.SHUTTERSTOCK].read_text(encoding="utf-8") == "SHUTTERSTOCK"
    assert sentinels[ProviderId.ENVATO].read_text(encoding="utf-8") == "ENVATO"
    manager.shutdown()


def test_provider_input_validation_is_controlled(tmp_path) -> None:
    manager = SessionManager(runtime_root=tmp_path / "runtime")
    with pytest.raises(UnsupportedSessionProviderError):
        manager.profile_paths("NOT_A_PROVIDER")
    with pytest.raises(UnsupportedSessionProviderError):
        manager.profile_paths(ProviderId.UNKNOWN)
    manager.shutdown()


@pytest.mark.parametrize(
    ("provider", "expected_url"),
    [
        (ProviderId.ASSETWAY, "https://plataformaa.assetway.com.br/"),
        (ProviderId.SHUTTERSTOCK, "https://www.shutterstock.com/"),
        (ProviderId.ENVATO, "https://elements.envato.com/"),
    ],
)
def test_browser_dialog_has_provider_initial_url_without_navigating(
    qt_app,
    tmp_path,
    provider: ProviderId,
    expected_url: str,
) -> None:
    manager = SessionManager(runtime_root=tmp_path / "runtime")
    profile = manager.profile_for_provider(provider)
    dialog = BrowserDialog(provider, profile, navigate=False)
    assert PROVIDER_START_URLS[provider] == expected_url
    assert dialog.initial_url == expected_url
    dialog.close()
    dialog.deleteLater()
    manager.shutdown()


def test_browser_dialog_navigates_to_configured_url_without_network(qt_app, tmp_path) -> None:
    manager = SessionManager(runtime_root=tmp_path / "runtime")
    profile = manager.profile_for_provider(ProviderId.SHUTTERSTOCK)
    dialog = BrowserDialog(ProviderId.SHUTTERSTOCK, profile, navigate=False)
    set_url = Mock()
    dialog.web_view.setUrl = set_url

    dialog.navigate_to_start_page()

    set_url.assert_called_once()
    assert set_url.call_args.args[0].toString() == PROVIDER_START_URLS[ProviderId.SHUTTERSTOCK]
    dialog.close()
    dialog.deleteLater()
    manager.shutdown()


def test_closing_browser_dialog_does_not_remove_profile(qt_app, tmp_path) -> None:
    manager = SessionManager(runtime_root=tmp_path / "runtime")
    profile = manager.profile_for_provider(ProviderId.ENVATO)
    paths = manager.profile_paths(ProviderId.ENVATO)
    dialog = BrowserDialog(ProviderId.ENVATO, profile, navigate=False)
    manager.register_dialog(ProviderId.ENVATO, dialog)
    dialog.show()
    qt_app.processEvents()
    dialog.close()
    qt_app.processEvents()
    assert paths.persistent_storage.exists()
    assert paths.cache.exists()
    manager.shutdown()


def test_public_session_models_do_not_expose_secret_fields() -> None:
    field_names = {field.name.lower() for field in fields(SessionStatus)}
    forbidden_names = {"password", "cookie", "cookies", "token", "authorization", "storage"}
    assert field_names.isdisjoint(forbidden_names)
    status = SessionStatus(ProviderId.ASSETWAY, SessionState.UNVERIFIED, "manual_login")
    assert status.reason == "manual_login"


def test_session_controller_reuses_dialog_per_provider(qt_app, tmp_path) -> None:
    manager = SessionManager(runtime_root=tmp_path / "runtime")
    controller = SessionController(session_manager=manager)
    dialog = controller.open_provider(ProviderId.ASSETWAY, navigate=False)
    assert controller.open_provider(ProviderId.ASSETWAY, navigate=False) is dialog
    assert dialog.initial_url == PROVIDER_START_URLS[ProviderId.ASSETWAY]
    controller.shutdown()


def test_profile_operations_reject_non_ui_thread(qt_app, tmp_path) -> None:
    manager = SessionManager(runtime_root=tmp_path / "runtime")
    results: list[BaseException] = []

    def call_from_worker() -> None:
        try:
            manager.profile_for_provider(ProviderId.ASSETWAY)
        except BaseException as error:
            results.append(error)

    worker = threading.Thread(target=call_from_worker)
    worker.start()
    worker.join()
    assert len(results) == 1
    assert isinstance(results[0], RuntimeError)
    manager.shutdown()


def test_main_window_access_rows_start_uninitialized(qt_app) -> None:
    window = MainWindow()
    for provider in (ProviderId.ASSETWAY, ProviderId.SHUTTERSTOCK, ProviderId.ENVATO):
        assert window.access_status_labels[provider].text() == (
            "Chrome fechado · Sessão não verificada"
        )
        assert window.access_open_buttons[provider].text() == "Abrir"
        assert window.access_clear_buttons[provider].text() == "Limpar acesso"
    window.close()


def test_clear_access_requires_confirmation_and_targets_one_provider(
    qt_app,
    tmp_path,
    monkeypatch,
) -> None:
    process_manager = ChromeProcessManager(
        profile_factory=ChromeProfileFactory(tmp_path / "runtime")
    )
    runtime = ChromeRuntime(process_manager=process_manager)
    window = MainWindow(chrome_runtime=runtime)
    assetway_paths = runtime.profile_factory.ensure_profile(ProviderId.ASSETWAY)
    envato_paths = runtime.profile_factory.ensure_profile(ProviderId.ENVATO)
    (assetway_paths.user_data_dir / "test-marker").write_text("marker", encoding="utf-8")
    (envato_paths.user_data_dir / "test-marker").write_text("envato", encoding="utf-8")
    confirmations: list[str] = []

    def confirm(parent, title, message, buttons, default_button):
        confirmations.append(message)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(confirm))
    loop = QEventLoop()
    window.session_controller.clear_finished.connect(lambda provider, success: loop.quit())
    QTimer.singleShot(8000, loop.quit)
    window._confirm_clear_provider(ProviderId.ASSETWAY)
    loop.exec()

    assert confirmations == ["Limpar somente a sessão local de Assetway?"]
    active_assetway_paths = runtime.profile_factory.paths_for(ProviderId.ASSETWAY)
    assert active_assetway_paths.user_data_dir.exists()
    assert not (active_assetway_paths.user_data_dir / "test-marker").exists()
    assert (envato_paths.user_data_dir / "test-marker").read_text(encoding="utf-8") == "envato"
    assert window.access_open_buttons[ProviderId.ASSETWAY].isEnabled()
    window.close()


def test_clearing_provider_closes_its_open_dialog(qt_app, tmp_path) -> None:
    manager = SessionManager(runtime_root=tmp_path / "runtime")
    assetway_profile = manager.profile_for_provider(ProviderId.ASSETWAY)
    shutterstock_profile = manager.profile_for_provider(ProviderId.SHUTTERSTOCK)
    old_assetway_paths = manager.profile_paths(ProviderId.ASSETWAY)
    shutterstock_paths = manager.profile_paths(ProviderId.SHUTTERSTOCK)
    dialog = BrowserDialog(ProviderId.ASSETWAY, assetway_profile, navigate=False)
    manager.register_dialog(ProviderId.ASSETWAY, dialog)
    dialog_destroyed: list[bool] = []
    dialog.destroyed.connect(lambda: dialog_destroyed.append(True))
    dialog.show()
    qt_app.processEvents()

    assert wait_for_clear(
        manager,
        ProviderId.ASSETWAY,
        lambda: manager.clear_provider(ProviderId.ASSETWAY),
    )
    assert dialog_destroyed == [True]
    assert manager.profile_paths(ProviderId.ASSETWAY).generation > old_assetway_paths.generation
    assert manager.profile_for_provider(ProviderId.SHUTTERSTOCK) is shutterstock_profile
    assert manager.profile_paths(ProviderId.SHUTTERSTOCK) == shutterstock_paths
    manager.shutdown()