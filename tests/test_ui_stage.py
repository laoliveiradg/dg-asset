from __future__ import annotations

import os
import threading
import zipfile
from pathlib import Path
from threading import Event
from time import perf_counter
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QMimeData, QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication, QPushButton

from image_downloader.downloads.models import (
    DownloadFailure,
    DownloadResult,
    DownloadStatus,
    DownloadTimings,
)
from image_downloader.input.models import SourceType, UrlOccurrence, UrlRecord
from image_downloader.providers.assetway.errors import AssetwayDownloadError
from image_downloader.providers.capabilities import ProviderExecutionMode
from image_downloader.providers.execution_policy import (
    DEFAULT_PROVIDER_EXECUTION_MODES,
    ProviderExecutionPolicy,
)
from image_downloader.providers.models import ProviderId
from image_downloader.providers.registry import ProviderRegistry
from image_downloader.queue.models import QueueError, QueueState
from image_downloader.ui.controllers.input_controller import InputController
from image_downloader.ui.main_window import MainWindow
from image_downloader.ui.widgets.drop_zone import DropZone
from image_downloader.ui.workers.input_worker import AnalysisRequest, InputSource


@pytest.fixture(scope="session")
def qt_app():
    return QApplication.instance() or QApplication([])


def write_pptx(path: Path, text: str) -> Path:
    slide_xml = (
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        "<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>"
        f"{text}"
        "</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
    )
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("ppt/slides/slide1.xml", slide_xml)
    return path


def wait_for_analysis(window: MainWindow, action, timeout_ms: int = 8000):
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    results = []

    def on_finished(result) -> None:
        results.append(result)
        loop.quit()

    window.controller.analysis_finished.connect(on_finished)
    timer.timeout.connect(loop.quit)
    timer.start(timeout_ms)
    action()
    loop.exec()
    window.controller.analysis_finished.disconnect(on_finished)
    assert results, "Analysis did not finish before the timeout."
    return results[-1]


def test_main_window_starts_with_functional_controls(qt_app) -> None:
    window = MainWindow()
    assert window.windowTitle() == "Image Downloader"
    assert window.drop_zone.acceptDrops()
    assert window.links_edit.placeholderText() == "Cole um ou vários links aqui..."
    assert window.queue_model.rowCount() == 0
    window.close()


def test_drop_zone_accepts_multiple_pptx_and_rejects_other_extensions(qt_app) -> None:
    zone = DropZone()
    accepted, rejected = zone.partition_paths(
        ["lesson-one.pptx", "lesson-two.PPTX", "notes.pdf"]
    )
    assert accepted == ["lesson-one.pptx", "lesson-two.PPTX"]
    assert rejected == ["notes.pdf"]

    emitted_paths: list[list[str]] = []
    zone.files_requested.connect(emitted_paths.append)
    mime_data = QMimeData()
    mime_data.setUrls(
        [
            QUrl.fromLocalFile("C:/courses/one.pptx"),
            QUrl.fromLocalFile("C:/courses/two.pptx"),
            QUrl.fromLocalFile("C:/courses/notes.pdf"),
        ]
    )
    event = QDropEvent(
        QPointF(10, 10),
        Qt.DropAction.CopyAction,
        mime_data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    zone.dropEvent(event)
    assert event.isAccepted()
    assert len(emitted_paths[0]) == 2
    zone.close()


def test_text_input_runs_provider_queue_flow_and_updates_summary(qt_app) -> None:
    window = MainWindow()
    main_thread_id = threading.get_ident()
    ui_signal_thread_ids: list[int] = []
    window.controller.analysis_finished.connect(
        lambda result: ui_signal_thread_ids.append(threading.get_ident())
    )
    text = "\n".join(
        [
            "Assetway https://plataformaa.assetway.com.br/p/acervo/search?assetId=65507",
            "Shutterstock https://www.shutterstock.com/image-photo/forest-123456789",
            "Envato https://elements.envato.com/example-item-ABC1234",
            "Other https://example.com/image/unknown",
            "Repeated other https://example.com/image/unknown",
        ]
    )
    result = wait_for_analysis(window, lambda: window.links_edit.setPlainText(text))

    assert result.accepted
    assert result.worker_thread_id != main_thread_id
    assert ui_signal_thread_ids == [main_thread_id]
    summary = result.snapshot.summary
    assert summary.total == 4
    assert summary.provider_counts[ProviderId.ASSETWAY] == 1
    assert summary.provider_counts[ProviderId.SHUTTERSTOCK] == 1
    assert summary.provider_counts[ProviderId.ENVATO] == 1
    assert summary.blocked == 1
    assert window.queue_model.rowCount() == 4
    providers = [
        window.queue_model.data(window.queue_model.index(row, 1)) for row in range(4)
    ]
    assert set(providers) == {"Assetway", "Shutterstock", "Envato", "Não suportado"}
    states = [window.queue_model.data(window.queue_model.index(row, 0)) for row in range(4)]
    assert states.count("Não suportado") == 1
    unsupported_row = providers.index("Não suportado")
    assert window.queue_model.data(window.queue_model.index(unsupported_row, 3)) == "2 origens"
    displayed = [
        window.queue_model.data(window.queue_model.index(row, column))
        for row in range(4)
        for column in range(4)
    ]
    assert all("assetId" not in str(value) for value in displayed)
    assert result.analysis_ms >= 0
    assert result.ui_elapsed_ms >= result.analysis_ms
    previous_batch_id = window.controller.batch_id
    window.clear_button.click()
    assert window.queue_model.rowCount() == 0
    assert window.summary_values["total"].text() == "0"
    assert window.controller.queue_manager.summary().total == 0
    assert window.controller.batch_id != previous_batch_id
    window.close()


def test_blank_and_non_url_text_do_not_create_items(qt_app) -> None:
    window = MainWindow()
    result = wait_for_analysis(
        window,
        lambda: window.links_edit.setPlainText("there are no links here"),
    )
    assert result.snapshot.summary.total == 0
    assert window.queue_model.rowCount() == 0
    window.links_edit.setPlainText("   ")
    assert window.controller.queue_manager.summary().total == 0
    window.controller._debounce_timer.stop()
    window.close()


def test_invalid_pptx_warning_does_not_block_valid_file(qt_app, tmp_path) -> None:
    window = MainWindow()
    invalid_path = tmp_path / "broken.pptx"
    invalid_path.write_bytes(b"not a pptx")
    valid_path = write_pptx(
        tmp_path / "valid.pptx",
        "https://www.shutterstock.com/image-photo/forest-123456789",
    )
    existing_path = write_pptx(
        tmp_path / "existing.pptx",
        "https://elements.envato.com/example-item-ABC1234",
    )
    wait_for_analysis(window, lambda: window.controller.add_files([str(existing_path)]))
    result = wait_for_analysis(
        window,
        lambda: window.controller.add_files([str(invalid_path), str(valid_path)]),
    )
    assert result.snapshot.summary.total == 2
    assert {item.provider for item in result.snapshot.items} == {
        ProviderId.ENVATO,
        ProviderId.SHUTTERSTOCK,
    }
    assert invalid_path.name in window.warning_label.text()
    assert not window.warning_label.isHidden()
    window.close()


def test_same_canonical_file_is_only_submitted_once(qt_app, tmp_path) -> None:
    window = MainWindow()
    pptx_path = write_pptx(
        tmp_path / "same.pptx",
        "https://elements.envato.com/example-item-ABC1234",
    )
    result = wait_for_analysis(
        window,
        lambda: (
            window.controller.add_files([str(pptx_path)]),
            window.controller.add_files([str(pptx_path.resolve())]),
        ),
    )
    assert result.snapshot.summary.total == 1
    assert "já foi adicionado" in window.warning_label.text()
    window.close()


def test_text_and_pptx_origins_merge_into_one_queue_row(qt_app, tmp_path) -> None:
    window = MainWindow()
    url = "https://elements.envato.com/example-item-ABC1234"
    pptx_path = write_pptx(tmp_path / "source.pptx", url)
    wait_for_analysis(window, lambda: window.controller.add_files([str(pptx_path)]))
    result = wait_for_analysis(window, lambda: window.links_edit.setPlainText(url))
    assert result.snapshot.summary.total == 1
    assert len(result.snapshot.items[0].occurrences) == 2
    assert window.queue_model.rowCount() == 1
    assert window.queue_model.data(window.queue_model.index(0, 3)) == "2 origens"
    window.close()


def test_stale_text_generation_and_clear_reject_old_commit(qt_app) -> None:
    controller = InputController()
    controller.schedule_text_analysis("https://elements.envato.com/example-item-ABC1234")
    old_text_generation = controller.text_generation
    old_batch_generation = controller.batch_generation
    controller.schedule_text_analysis("https://www.shutterstock.com/search/forest")
    old_record = UrlRecord.from_occurrences(
        [
            UrlOccurrence(
                original_url="https://elements.envato.com/example-item-ABC1234",
                normalized_url="https://elements.envato.com/example-item-ABC1234",
                source_type=SourceType.pasted_text,
            )
        ]
    )
    old_request = AnalysisRequest(
        request_id="stale-request",
        batch_generation=old_batch_generation,
        text_generation=old_text_generation,
        source=InputSource.TEXT,
        text="stale",
        paths=(),
        submitted_at_ns=0,
    )
    old_classification = ProviderRegistry().classify_records([old_record])
    assert controller._commit_request(old_request, old_classification) is None
    assert controller.queue_manager.summary().total == 0

    controller.clear_batch()
    stale_after_clear = AnalysisRequest(
        request_id="pre-clear-request",
        batch_generation=old_batch_generation,
        text_generation=None,
        source=InputSource.FILES,
        text="",
        paths=(),
        submitted_at_ns=0,
    )
    assert controller._commit_request(stale_after_clear, old_classification) is None
    assert controller.queue_manager.summary().total == 0


def test_clear_removes_visual_and_domain_state_and_starts_new_batch(qt_app) -> None:
    window = MainWindow()
    wait_for_analysis(
        window,
        lambda: window.links_edit.setPlainText(
            "https://plataformaa.assetway.com.br/p/acervo/search?assetId=7"
        ),
    )
    previous_batch_id = window.controller.batch_id
    window.clear_button.click()
    assert window.links_edit.toPlainText() == ""
    assert window.queue_model.rowCount() == 0
    assert window.summary_values["total"].text() == "0"
    assert window.warning_label.text() == ""
    assert window.controller.queue_manager.summary().total == 0
    assert window.controller.batch_id != previous_batch_id
    window.close()


def test_queue_model_labels_unknown_as_unsupported(qt_app) -> None:
    window = MainWindow()
    result = wait_for_analysis(
        window,
        lambda: window.links_edit.setPlainText("https://example.com/unknown"),
    )
    assert result.snapshot.summary.blocked == 1
    assert window.queue_model.data(window.queue_model.index(0, 0)) == "Não suportado"
    assert window.queue_model.data(window.queue_model.index(0, 1)) == "Não suportado"
    window.close()


def test_responsive_splitter_changes_orientation(qt_app) -> None:
    window = MainWindow()
    window.show()
    window.resize(900, 760)
    qt_app.processEvents()
    assert window._splitter.orientation() == Qt.Orientation.Vertical
    window.resize(1120, 760)
    qt_app.processEvents()
    assert window._splitter.orientation() == Qt.Orientation.Horizontal
    window.close()


def test_access_routing_uses_execution_policy_without_provider_specific_ui_rule(
    qt_app, monkeypatch
) -> None:
    modes = dict(DEFAULT_PROVIDER_EXECUTION_MODES)
    modes[ProviderId.ASSETWAY] = ProviderExecutionMode.INTERACTIVE_REQUIRED
    modes[ProviderId.SHUTTERSTOCK] = ProviderExecutionMode.UNVALIDATED
    policy = ProviderExecutionPolicy(modes)
    interactive_open = Mock(return_value=True)
    monkeypatch.setattr(
        "image_downloader.ui.main_window.open_interactive_provider",
        interactive_open,
    )
    window = MainWindow(execution_policy=policy)
    managed_open = Mock(return_value=True)
    window.session_controller.open_provider = managed_open

    window._open_provider(ProviderId.ASSETWAY)
    window._open_provider(ProviderId.SHUTTERSTOCK)

    interactive_open.assert_called_once()
    assert interactive_open.call_args.args == (
        ProviderId.ASSETWAY,
        "https://plataformaa.assetway.com.br/",
    )
    managed_open.assert_called_once_with(ProviderId.SHUTTERSTOCK)
    window.close()


def test_shutterstock_access_uses_default_browser_without_chrome_runtime(
    qt_app, monkeypatch
) -> None:
    from image_downloader.chrome import controller as chrome_controller_module

    runtime_factory = Mock(side_effect=AssertionError("Shutterstock must not create ChromeRuntime"))
    monkeypatch.setattr(chrome_controller_module, "ChromeRuntime", runtime_factory)
    interactive_open = Mock(return_value=True)
    monkeypatch.setattr(
        "image_downloader.ui.main_window.open_interactive_provider",
        interactive_open,
    )
    window = MainWindow()

    window.access_open_buttons[ProviderId.SHUTTERSTOCK].click()

    interactive_open.assert_called_once_with(
        ProviderId.SHUTTERSTOCK,
        "https://www.shutterstock.com/",
        execution_policy=window.execution_policy,
    )
    assert window.session_controller.chrome_runtime is None
    runtime_factory.assert_not_called()
    window.close()
    window.session_controller._shutdown_thread.join(timeout=2.0)
    qt_app.processEvents()
    assert window._shutdown_complete


def test_selected_shutterstock_item_opens_exact_url_without_queue_transition(
    qt_app, monkeypatch
) -> None:
    url = "https://www.shutterstock.com/image-photo/forest-123?size=large&ref=queue#details"
    opened = Mock(return_value=True)
    monkeypatch.setattr(
        "image_downloader.ui.main_window.open_interactive_provider",
        opened,
    )
    window = MainWindow()
    result = wait_for_analysis(window, lambda: window.links_edit.setPlainText(url))
    assert result.snapshot.summary.ready == 1
    item = window.queue_model.items[0]
    assert item.state == QueueState.READY
    assert item.normalized_url == url

    window.queue_table.selectRow(0)
    assert window.open_item_button.isEnabled()
    window.open_item_button.click()

    opened.assert_called_once_with(
        ProviderId.SHUTTERSTOCK,
        url,
        execution_policy=window.execution_policy,
    )
    assert window.controller.queue_manager.get_item(item.item_id).state == QueueState.READY
    window.close()


def test_global_download_action_depends_on_batch_not_selection(qt_app) -> None:
    window = MainWindow()
    assert isinstance(window.download_item_button, QPushButton)
    assert "BAIXAR" in window.download_item_button.text()
    assert not window.download_item_button.isEnabled()
    text = "\n".join(
        [
            "https://plataformaa.assetway.com.br/p/acervo/search?modal=asset&assetId=7",
            "https://www.shutterstock.com/image-photo/forest-123456789",
            "https://elements.envato.com/example-item-ABC1234",
            "https://example.com/unknown",
        ]
    )
    result = wait_for_analysis(window, lambda: window.links_edit.setPlainText(text))
    assert result.snapshot.summary.ready == 3

    window.queue_table.clearSelection()
    assert window.download_item_button.isEnabled()
    assert not window.diagnose_item_button.isVisible()
    assert not window.retry_download_button.isEnabled()

    for row, item in enumerate(window.queue_model.items):
        if item.provider != ProviderId.ASSETWAY:
            window.queue_table.selectRow(row)
            assert window.download_item_button.isEnabled()
            assert not window.diagnose_item_button.isEnabled()
            assert not window.retry_download_button.isEnabled()
    window.close()


def test_simulated_assetway_download_runs_off_ui_thread(qt_app, monkeypatch) -> None:
    from image_downloader.ui import main_window as main_window_module

    main_thread_id = threading.get_ident()
    worker_thread_ids: list[int] = []
    started_events: list[Event] = []
    finished_events: list[Event] = []
    first_started = Event()
    release = Event()

    class BlockingDownloader:
        def __init__(self, runtime) -> None:
            pass

        def download_item(self, queue_manager, item_id, progress):
            worker_thread_ids.append(threading.get_ident())
            started = Event()
            finished = Event()
            started_events.append(started)
            finished_events.append(finished)
            if len(started_events) == 1:
                first_started.set()
            queue_manager.start_processing(item_id)
            progress("Preparando ativo...")
            started.set()
            release.wait(timeout=3.0)
            queue_manager.mark_failed(
                item_id,
                QueueError(
                    "authentication_required",
                    "Entre manualmente no Assetway e tente novamente.",
                    ProviderId.ASSETWAY,
                    True,
                ),
            )
            finished.set()
            return DownloadResult(
                item_id=item_id,
                provider=ProviderId.ASSETWAY,
                status=DownloadStatus.FAILED,
                file_path=None,
                file_name=None,
                extension=None,
                bytes_received=0,
                quality_label=None,
                source_format=None,
                timings=DownloadTimings(),
                error=DownloadFailure(
                    "authentication_required",
                    "Entre manualmente no Assetway e tente novamente.",
                    True,
                ),
            )

    monkeypatch.setattr(main_window_module, "AssetwayDownloader", BlockingDownloader)
    window = MainWindow()
    result = wait_for_analysis(
        window,
        lambda: window.links_edit.setPlainText(
            "https://plataformaa.assetway.com.br/p/acervo/search?modal=asset&assetId=7"
        ),
    )
    item = result.snapshot.items[0]
    window.queue_table.clearSelection()
    started_at = perf_counter()
    window.download_item_button.click()
    click_elapsed = perf_counter() - started_at

    assert click_elapsed < 0.2
    assert first_started.wait(timeout=2.0)
    progress_loop = QEventLoop()
    progress_poll = QTimer()
    progress_poll.setInterval(5)
    progress_poll.timeout.connect(
        lambda: progress_loop.quit()
        if window.status_label.text() == "Preparando downloads..."
        else None
    )
    progress_poll.start()
    QTimer.singleShot(1000, progress_loop.quit)
    progress_loop.exec()
    progress_poll.stop()
    assert worker_thread_ids and worker_thread_ids[0] != main_thread_id
    assert window.status_label.text() == "Preparando downloads..."
    assert window.download_progress_label.text() == "Preparando downloads..."
    assert window.controller.queue_manager.get_item(item.item_id).state == QueueState.PROCESSING
    assert not window.download_item_button.isEnabled()

    loop = QEventLoop()
    window._assetway_download_worker.signals.completed.connect(lambda *_: loop.quit())
    release.set()
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    assert window.controller.queue_manager.get_item(item.item_id).state == QueueState.FAILED
    assert window.controller.queue_manager.get_item(item.item_id).state == QueueState.FAILED
    window.close()


def test_global_action_collects_multiple_assetway_and_continues_to_other_modes(
    qt_app, monkeypatch
) -> None:
    from image_downloader.ui import main_window as main_window_module

    processed: list[str] = []

    class SequentialDownloader:
        def __init__(self, runtime) -> None:
            pass

        def download_item(self, queue_manager, item_id, progress):
            processed.append(item_id)
            queue_manager.start_processing(item_id)
            progress("Baixando...")
            if len(processed) == 1:
                queue_manager.mark_failed(
                    item_id,
                    QueueError(
                        "download_action_unverified",
                        "Controle oficial não identificado.",
                        ProviderId.ASSETWAY,
                        False,
                    ),
                )
                return DownloadResult(
                    item_id=item_id,
                    provider=ProviderId.ASSETWAY,
                    status=DownloadStatus.FAILED,
                    file_path=None,
                    file_name=None,
                    extension=None,
                    bytes_received=0,
                    quality_label=None,
                    source_format=None,
                    timings=DownloadTimings(),
                    error=DownloadFailure(
                        "download_action_unverified",
                        "Controle oficial não identificado.",
                        False,
                    ),
                )
            queue_manager.mark_completed(item_id)
            return DownloadResult(
                item_id=item_id,
                provider=ProviderId.ASSETWAY,
                status=DownloadStatus.COMPLETED,
                file_path=None,
                file_name="asset.png",
                extension=".png",
                bytes_received=10,
                quality_label="Original PNG",
                source_format="PNG",
                timings=DownloadTimings(),
            )

    monkeypatch.setattr(main_window_module, "AssetwayDownloader", SequentialDownloader)
    window = MainWindow()
    text = "\n".join(
        [
            "https://plataformaa.assetway.com.br/p/acervo/search?modal=asset&assetId=7",
            "https://plataformaa.assetway.com.br/p/acervo/search?modal=asset&assetId=8",
            "https://www.shutterstock.com/image-photo/forest-123456789",
            "https://elements.envato.com/example-item-ABC1234",
        ]
    )
    wait_for_analysis(window, lambda: window.links_edit.setPlainText(text))
    window.queue_table.clearSelection()

    def fail_assisted(item):
        window.controller.queue_manager.start_processing(item.item_id)
        window.controller.queue_manager.mark_failed(
            item.item_id,
            QueueError(
                "assisted_download_timeout",
                "Download assistido não detectado.",
                ProviderId.SHUTTERSTOCK,
                True,
            ),
        )
        window._batch_finished += 1
        window._batch_failed += 1
        window._start_next_batch_item()

    monkeypatch.setattr(window, "_submit_shutterstock_download", fail_assisted)

    window.download_item_button.click()
    loop = QEventLoop()
    poll_timer = QTimer()
    poll_timer.setInterval(10)
    poll_timer.timeout.connect(lambda: loop.quit() if window._batch_total == 0 else None)
    poll_timer.start()
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    poll_timer.stop()

    assert len(processed) == 2
    assetway_items = [
        item
        for item in window.controller.queue_manager.list_items()
        if item.provider == ProviderId.ASSETWAY
    ]
    assert [item.state for item in assetway_items] == [
        QueueState.FAILED,
        QueueState.COMPLETED,
    ]
    assert next(
        item
        for item in window.controller.queue_manager.list_items()
        if item.provider == ProviderId.SHUTTERSTOCK
    ).state == QueueState.FAILED
    assert next(
        item
        for item in window.controller.queue_manager.list_items()
        if item.provider == ProviderId.ENVATO
    ).state == QueueState.READY
    assert "1 concluídas" in window.download_progress_label.text()
    assert "2 com falha" in window.download_progress_label.text()
    window.close()


def test_technical_controls_are_secondary_and_details_are_hidden(qt_app) -> None:
    window = MainWindow()

    assert window.settings_button.isVisibleTo(window)
    assert window.access_dialog.isHidden()
    assert window.queue_table.isHidden()
    assert window.diagnose_item_button.isHidden()

    window.details_button.setChecked(True)
    assert not window.queue_table.isHidden()
    window.close()


def test_dom_diagnostic_is_visible_only_with_dev_tools(qt_app, monkeypatch) -> None:
    monkeypatch.setenv("IMAGE_DOWNLOADER_DEV_TOOLS", "1")
    window = MainWindow()

    assert not window.diagnose_item_button.isHidden()
    window.close()


def test_clear_resets_progress_and_rejects_stale_download_result(qt_app) -> None:
    window = MainWindow()
    window.download_progress_label.setText(
        "Processamento finalizado: 0 concluídas, 1 com falha de 1."
    )
    window._batch_total = 1
    window._batch_failed = 1
    stale_generation = window._download_generation

    window.clear_button.click()
    window._on_assetway_download_completed(
        AssetwayDownloadError("download_failed", "Falha antiga.", retryable=True),
        stale_generation,
    )

    assert window.controller.queue_manager.summary().total == 0
    assert window.download_progress_label.text() == "Aguardando imagens para processar."
    assert window._batch_total == 0
    assert window._batch_failed == 0
    window.close()


def test_assetway_login_action_uses_interactive_access(qt_app) -> None:
    window = MainWindow()
    opened: list[ProviderId] = []
    window.session_controller.open_provider = lambda provider: opened.append(provider) or True

    window._open_assetway_login()

    assert opened == [ProviderId.ASSETWAY]
    assert "Entre no Assetway" in window.download_progress_label.text()
    window.close()
